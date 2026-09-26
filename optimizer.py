from __future__ import annotations

import gc
from itertools import product

import pandas as pd

from backtest import backtest_scored_stock
from config import SETTINGS, ensure_directories
from data import FreeAStockData
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


def optimize_parameters(limit: int = 100, refresh: bool = False) -> tuple[pd.DataFrame, dict | None]:
    """在受控候选参数中做训练/验证分段比较，并把通过验证的最佳参数写入策略档。"""
    ensure_directories()
    provider = FreeAStockData()
    stocks = provider.stock_list().head(limit)
    candidates = _candidates()
    train_trades: dict[int, list[dict]] = {i: [] for i in range(len(candidates))}
    valid_trades: dict[int, list[dict]] = {i: [] for i in range(len(candidates))}

    for pos, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        try:
            hist = provider.history(code, refresh=refresh)
            if len(hist) < SETTINGS.min_bars + 40:
                continue
            scored = score_history(hist).reset_index(drop=True)
            split_idx = max(SETTINGS.min_bars, int(len(scored) * 0.70))
            if split_idx >= len(scored) - 10:
                continue

            train_df = scored.iloc[: split_idx + 1].copy()
            warmup_start = max(0, split_idx - SETTINGS.min_bars)
            valid_df = scored.iloc[warmup_start:].copy().reset_index(drop=True)
            cutoff = pd.to_datetime(scored.iloc[split_idx]["date"])

            for idx, params in enumerate(candidates):
                train_trades[idx].extend(
                    backtest_scored_stock(code=code, name=name, scored=train_df, **params)
                )
                vt = backtest_scored_stock(code=code, name=name, scored=valid_df, **params)
                for trade in vt:
                    if pd.to_datetime(trade["信号日"]) >= cutoff:
                        valid_trades[idx].append(trade)
        except Exception as exc:
            print(f"优化跳过 {code} {name}: {exc}")
        finally:
            if (pos + 1) % SETTINGS.flush_every == 0:
                print(f"参数优化进度 {pos + 1}/{len(stocks)}")
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
            and float(train_metrics["平均收益%"] ) > 0
            and float(valid_metrics["平均收益%"] ) > 0
            and float(valid_metrics["最大回撤%"] ) <= 30
        )
        row = {
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
        rows.append(row)
        if robust and score > best_score:
            best_score = score
            best = params.copy()

    result = pd.DataFrame(rows).sort_values(["通过稳健性检查", "目标分"], ascending=[False, False])
    result.to_csv(SETTINGS.report_dir / "optimizer_results.csv", index=False, encoding="utf-8-sig")

    if best is not None:
        best = save_strategy_profile(best)
    return result.reset_index(drop=True), best
