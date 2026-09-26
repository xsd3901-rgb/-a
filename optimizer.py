from __future__ import annotations

import gc
from itertools import product

import pandas as pd

from aquant.data.service import MarketDataService
from aquant.runtime.resources import current_profile
from backtest import (
    _historical_universe,
    attach_execution_bars,
    backtest_scored_stock,
)
from config import SETTINGS, ensure_directories
from evaluator import evaluate_trades
from profile import save_strategy_profile
from strategy import score_history


def _candidates() -> list[dict]:
    thresholds = [64, 68, 72]
    risk_pairs = [(1.6, 2.6), (1.8, 3.0), (2.0, 3.4)]
    return [
        {
            "score_threshold": threshold,
            "stop_atr_multiple": stop,
            "target_atr_multiple": target,
            "max_hold_days": SETTINGS.reference_hold_max_days,
        }
        for threshold, (stop, target) in product(thresholds, risk_pairs)
    ]


def _objective(metrics: dict) -> float:
    n = int(metrics["交易次数"])
    if n < 20:
        return -999.0
    avg_ret = float(metrics["平均收益%"])
    win_rate = float(metrics["胜率%"])
    payoff = min(float(metrics["盈亏比"]), 3.0)
    drawdown = float(metrics["最大回撤%"])
    return avg_ret * 2.0 + win_rate * 0.02 + payoff * 1.2 - drawdown * 0.05


def optimize_parameters(
    limit: int = 100,
    refresh: bool = False,
) -> tuple[pd.DataFrame, dict | None]:
    """在历史生命周期股票池上做点时数据训练/验证参数比较。

    信号特征使用复权序列，成交/涨跌停/停牌判断使用未复权序列。
    训练与验证之间保留交易日 gap，只有验证期通过稳健性检查才更新活动参数。
    """
    ensure_directories()
    provider = MarketDataService()
    runtime = current_profile()

    market_end = provider.latest_trade_date()
    signal_start = market_end - pd.Timedelta(days=SETTINGS.backtest_calendar_days)
    fetch_start = signal_start - pd.Timedelta(
        days=SETTINGS.backtest_warmup_calendar_days
    )

    stocks = _historical_universe(
        provider,
        signal_start,
        market_end,
        refresh=refresh,
    )
    if limit and limit > 0:
        stocks = stocks.head(limit)

    candidates = _candidates()
    train_trades: dict[int, list[dict]] = {
        i: [] for i in range(len(candidates))
    }
    valid_trades: dict[int, list[dict]] = {
        i: [] for i in range(len(candidates))
    }
    errors: list[dict] = []
    flush_every = max(1, runtime.batch_size)

    print(
        f"历史优化股票池: {len(stocks)} | "
        f"信号区间 {signal_start:%Y-%m-%d} ~ {market_end:%Y-%m-%d}"
    )
    print(
        f"资源档位: {runtime.name} | 批次 {runtime.batch_size} | "
        f"回测并发上限 {runtime.backtest_workers}"
    )

    for pos, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        listing_date = row.get("listing_date", pd.NaT)
        delisting_date = row.get("delisting_date", pd.NaT)

        stock_start = fetch_start
        if pd.notna(listing_date):
            stock_start = max(
                stock_start,
                pd.Timestamp(listing_date).normalize(),
            )

        stock_end = market_end
        if pd.notna(delisting_date):
            stock_end = min(
                stock_end,
                pd.Timestamp(delisting_date).normalize(),
            )
        if stock_start >= stock_end:
            continue

        try:
            hist, execution = provider.research_history_range(
                code,
                stock_start.strftime("%Y-%m-%d"),
                stock_end.strftime("%Y-%m-%d"),
                refresh=refresh,
            )
            if len(hist) < SETTINGS.min_bars + 60:
                continue

            scored = score_history(hist).reset_index(drop=True)
            scored = attach_execution_bars(scored, execution)
            scored["date"] = pd.to_datetime(
                scored["date"], errors="coerce"
            ).dt.normalize()
            scored = scored.dropna(subset=["date"]).reset_index(drop=True)

            formal = scored[scored["date"] >= signal_start]
            if len(formal) < 80:
                continue

            formal_indices = formal.index.to_list()
            split_pos = max(
                20,
                min(
                    len(formal_indices) - 25,
                    int(len(formal_indices) * 0.70),
                ),
            )
            if split_pos >= len(formal_indices) - 20:
                continue

            train_end_idx = formal_indices[split_pos - 1]
            gap = max(1, int(SETTINGS.walk_forward_gap_bars))
            valid_start_pos = split_pos + gap
            if valid_start_pos >= len(formal_indices) - 10:
                continue

            train_end = pd.Timestamp(
                scored.loc[train_end_idx, "date"]
            ).normalize()
            valid_start = pd.Timestamp(
                scored.loc[formal_indices[valid_start_pos], "date"]
            ).normalize()

            for idx, params in enumerate(candidates):
                train_trades[idx].extend(
                    backtest_scored_stock(
                        code=code,
                        name=name,
                        scored=scored,
                        listing_date=listing_date,
                        signal_start_date=signal_start,
                        signal_end_date=train_end,
                        model_name="V1_OPT_TRAIN",
                        **params,
                    )
                )
                valid_trades[idx].extend(
                    backtest_scored_stock(
                        code=code,
                        name=name,
                        scored=scored,
                        listing_date=listing_date,
                        signal_start_date=valid_start,
                        signal_end_date=stock_end,
                        model_name="V1_OPT_VALID",
                        **params,
                    )
                )
        except Exception as exc:
            errors.append(
                {"代码": code, "名称": name, "错误": str(exc)[:300]}
            )
        finally:
            if (pos + 1) % flush_every == 0:
                print(
                    f"参数优化进度 {pos + 1}/{len(stocks)} | "
                    f"失败 {len(errors)}"
                )
                gc.collect()

    rows: list[dict] = []
    best: dict | None = None
    best_score = -999.0

    for idx, params in enumerate(candidates):
        train_metrics = evaluate_trades(pd.DataFrame(train_trades[idx]))
        valid_metrics = evaluate_trades(pd.DataFrame(valid_trades[idx]))
        score = _objective(valid_metrics)
        robust = (
            int(valid_metrics["交易次数"]) >= 20
            and float(train_metrics["平均收益%"]) > 0
            and float(valid_metrics["平均收益%"]) > 0
            and float(valid_metrics["最大回撤%"]) <= 30
        )
        rows.append(
            {
                **params,
                "训练交易数": train_metrics["交易次数"],
                "训练平均收益%": train_metrics["平均收益%"],
                "验证交易数": valid_metrics["交易次数"],
                "验证胜率%": valid_metrics["胜率%"],
                "验证平均收益%": valid_metrics["平均收益%"],
                "验证盈亏比": valid_metrics["盈亏比"],
                "验证最大回撤%": valid_metrics["最大回撤%"],
                "目标分": round(score, 4),
                "通过稳健性检查": robust,
            }
        )
        if robust and score > best_score:
            best_score = score
            best = params.copy()

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            ["通过稳健性检查", "目标分"],
            ascending=[False, False],
        ).reset_index(drop=True)

    result.to_csv(
        SETTINGS.report_dir / "optimizer_results.csv",
        index=False,
        encoding="utf-8-sig",
    )
    if errors:
        pd.DataFrame(errors).to_csv(
            SETTINGS.report_dir / "optimizer_errors.csv",
            index=False,
            encoding="utf-8-sig",
        )

    if best is not None:
        best = save_strategy_profile(best)
    return result, best
