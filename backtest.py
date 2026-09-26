from __future__ import annotations

import gc

import pandas as pd

from config import SETTINGS, ensure_directories
from data import FreeAStockData
from profile import load_strategy_profile
from strategy import score_history

ROUND_TRIP_COST_PCT = 0.20  # 粗略计入手续费/滑点，后续可再细化


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
    provider = FreeAStockData()
    stocks = provider.stock_list()
    if limit and limit > 0:
        stocks = stocks.head(limit)

    all_trades: list[dict] = []
    errors: list[dict] = []
    total = len(stocks)

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
            if (i + 1) % SETTINGS.flush_every == 0:
                print(f"回测进度 {i + 1}/{total}，累计交易 {len(all_trades)}，失败 {len(errors)}")
                gc.collect()

    trades = pd.DataFrame(all_trades)
    if persist:
        trades.to_csv(SETTINGS.report_dir / "backtest_trades.csv", index=False, encoding="utf-8-sig")
        if errors:
            pd.DataFrame(errors).to_csv(SETTINGS.report_dir / "backtest_errors.csv", index=False, encoding="utf-8-sig")
    return trades
