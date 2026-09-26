from __future__ import annotations

import gc

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from aquant.research.market_regime import build_market_regime_history
from aquant.runtime.resources import current_profile
from config import SETTINGS, ensure_directories
from profile import load_strategy_profile
from strategy import score_history

ROUND_TRIP_COST_PCT = 0.20  # 粗略计入手续费/滑点，后续可再细化


def _attach_market_regime(
    trades: pd.DataFrame,
    regime_history: pd.DataFrame,
) -> pd.DataFrame:
    if trades is None or trades.empty or regime_history is None or regime_history.empty:
        return trades
    if "信号日" not in trades.columns:
        return trades

    out = trades.copy()
    out["_signal_date"] = pd.to_datetime(out["信号日"], errors="coerce").dt.normalize()
    regime = regime_history[
        ["trade_date", "market_regime", "market_score"]
    ].copy()
    regime["trade_date"] = pd.to_datetime(
        regime["trade_date"], errors="coerce"
    ).dt.normalize()
    out = out.merge(
        regime,
        left_on="_signal_date",
        right_on="trade_date",
        how="left",
    )
    out = out.rename(
        columns={
            "market_regime": "市场环境",
            "market_score": "环境分",
        }
    )
    return out.drop(columns=["_signal_date", "trade_date"], errors="ignore")


def backtest_scored_stock(
    code: str,
    name: str,
    scored: pd.DataFrame,
    score_threshold: int,
    stop_atr_multiple: float,
    target_atr_multiple: float,
    max_hold_days: int,
) -> list[dict]:
    df = scored.reset_index(drop=True)
    trades: list[dict] = []
    i = max(60, SETTINGS.min_bars - 1)

    while i < len(df) - 2:
        signal_row = df.iloc[i]
        if int(signal_row["score"]) < score_threshold:
            i += 1
            continue

        entry_idx = i + 1
        entry_price = float(df.iloc[entry_idx]["open"])
        atr = float(signal_row["atr14"]) if pd.notna(signal_row["atr14"]) else entry_price * 0.03
        stop_price = entry_price - stop_atr_multiple * atr
        target_price = entry_price + target_atr_multiple * atr

        last_idx = min(entry_idx + max_hold_days - 1, len(df) - 1)
        exit_idx = last_idx
        exit_price = float(df.iloc[last_idx]["close"])
        exit_reason = "观察窗口结束"

        for j in range(entry_idx, last_idx + 1):
            day = df.iloc[j]
            held = j - entry_idx + 1

            # 同一天同时触发止损/止盈时采用保守假设：先按止损处理
            if float(day["low"]) <= stop_price:
                exit_idx = j
                exit_price = stop_price
                exit_reason = "ATR止损"
                break
            if float(day["high"]) >= target_price:
                exit_idx = j
                exit_price = target_price
                exit_reason = "ATR目标"
                break

            if held >= SETTINGS.trend_exit_min_days:
                ma10 = day["ma10"]
                weak_trend = pd.notna(ma10) and float(day["close"]) < float(ma10)
                weak_macd = (
                    pd.notna(day["macd_dif"])
                    and pd.notna(day["macd_dea"])
                    and day["macd_dif"] < day["macd_dea"]
                )
                if weak_trend and weak_macd:
                    exit_idx = j
                    exit_price = float(day["close"])
                    exit_reason = "趋势转弱"
                    break

        gross_return = (exit_price / entry_price - 1) * 100
        net_return = gross_return - ROUND_TRIP_COST_PCT
        trades.append(
            {
                "代码": code,
                "名称": name,
                "信号日": pd.to_datetime(signal_row["date"]).strftime("%Y-%m-%d"),
                "买入日": pd.to_datetime(df.iloc[entry_idx]["date"]).strftime("%Y-%m-%d"),
                "卖出日": pd.to_datetime(df.iloc[exit_idx]["date"]).strftime("%Y-%m-%d"),
                "信号评分": int(signal_row["score"]),
                "买入价": round(entry_price, 3),
                "卖出价": round(exit_price, 3),
                "持有交易日": exit_idx - entry_idx + 1,
                "退出原因": exit_reason,
                "净收益%": round(net_return, 3),
            }
        )
        i = exit_idx + 1

    return trades


def backtest_stock(
    code: str,
    name: str,
    hist: pd.DataFrame,
    score_threshold: int | None = None,
    stop_atr_multiple: float | None = None,
    target_atr_multiple: float | None = None,
    max_hold_days: int | None = None,
) -> list[dict]:
    profile = load_strategy_profile()
    scored = score_history(hist)
    return backtest_scored_stock(
        code=code,
        name=name,
        scored=scored,
        score_threshold=int(score_threshold if score_threshold is not None else profile["score_threshold"]),
        stop_atr_multiple=float(stop_atr_multiple if stop_atr_multiple is not None else profile["stop_atr_multiple"]),
        target_atr_multiple=float(target_atr_multiple if target_atr_multiple is not None else profile["target_atr_multiple"]),
        max_hold_days=int(max_hold_days if max_hold_days is not None else profile["max_hold_days"]),
    )


def run_backtest(
    limit: int | None = None,
    refresh: bool = False,
    score_threshold: int | None = None,
    stop_atr_multiple: float | None = None,
    target_atr_multiple: float | None = None,
    max_hold_days: int | None = None,
    persist: bool = True,
) -> pd.DataFrame:
    ensure_directories()
    provider = MarketDataService()
    runtime = current_profile()
    stocks = provider.stock_list()
    if limit and limit > 0:
        stocks = stocks.head(limit)

    regime_history = pd.DataFrame()
    try:
        market_end = provider.latest_trade_date()
        market_start = market_end - pd.Timedelta(days=max(500, int(SETTINGS.history_days * 2.1)))
        regime_history = build_market_regime_history(
            MarketContextService(),
            market_start,
            market_end,
        )
        if not regime_history.empty:
            print(
                f"已加载沪深市场环境历史 {len(regime_history)} 个交易日，"
                "用于回测分阶段评估（不改变当前买卖信号）。"
            )
    except Exception as exc:
        print(f"市场环境历史暂不可用，回测继续: {exc}")

    all_trades: list[dict] = []
    errors: list[dict] = []
    total = len(stocks)
    flush_every = max(1, runtime.batch_size)

    print(
        f"资源档位: {runtime.name} | 批次 {runtime.batch_size} | "
        f"回测并发上限 {runtime.backtest_workers}"
    )

    for i, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        try:
            hist = provider.history(code, refresh=refresh)
            if len(hist) >= SETTINGS.min_bars:
                all_trades.extend(
                    backtest_stock(
                        code,
                        name,
                        hist,
                        score_threshold=score_threshold,
                        stop_atr_multiple=stop_atr_multiple,
                        target_atr_multiple=target_atr_multiple,
                        max_hold_days=max_hold_days,
                    )
                )
        except Exception as exc:
            errors.append({"代码": code, "名称": name, "错误": str(exc)[:300]})
        finally:
            if (i + 1) % flush_every == 0:
                print(f"回测进度 {i + 1}/{total}，累计交易 {len(all_trades)}，失败 {len(errors)}")
                gc.collect()

    trades = _attach_market_regime(pd.DataFrame(all_trades), regime_history)
    if persist:
        trades.to_csv(SETTINGS.report_dir / "backtest_trades.csv", index=False, encoding="utf-8-sig")
        if errors:
            pd.DataFrame(errors).to_csv(SETTINGS.report_dir / "backtest_errors.csv", index=False, encoding="utf-8-sig")
    return trades
