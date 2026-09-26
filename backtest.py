from __future__ import annotations

import gc

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from aquant.research.market_regime import build_market_regime_history
from aquant.risk.costs import AShareCostModel
from aquant.risk.daily import DEFAULT_DAILY_RISK_FILTER
from aquant.risk.trading_rules import (
    historical_is_st,
    is_tradable_bar,
    open_is_limit_up,
    row_limit_prices,
    sellable_bar,
    trading_age,
)
from aquant.runtime.resources import current_profile
from config import SETTINGS, ensure_directories
from profile import load_strategy_profile
from strategy import score_history

EXECUTION_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "turnover",
    "pct_change",
    "trade_status",
    "is_st",
)


def _cost_model() -> AShareCostModel:
    return AShareCostModel(
        commission_bps=SETTINGS.commission_bps,
        min_commission_cny=SETTINGS.min_commission_cny,
        slippage_bps=SETTINGS.slippage_bps,
    )


def _execution_row(row: pd.Series) -> pd.Series:
    """若存在未复权执行字段，则用它们进行成交/涨跌停判断。"""
    out = row.copy()
    for col in EXECUTION_COLUMNS:
        exec_col = f"exec_{col}"
        if exec_col in row.index and pd.notna(row[exec_col]):
            out[col] = row[exec_col]
    return out


def attach_execution_bars(
    scored: pd.DataFrame,
    execution_bars: pd.DataFrame | None,
    *,
    preserve_execution_dates: bool = True,
) -> pd.DataFrame:
    """把未复权执行日线以 exec_ 前缀附到信号特征上。

    默认保留 execution 独有日期（例如停牌日）。这些行没有技术特征，
    但会留在回测时间轴中，从而让“次日停牌”“持仓中停牌”等约束真正生效。
    """
    if scored is None or scored.empty:
        return pd.DataFrame() if scored is None else scored.copy()
    if execution_bars is None or execution_bars.empty:
        return scored.copy()

    left = scored.copy()
    right = execution_bars.copy()
    left["date"] = pd.to_datetime(left["date"], errors="coerce").dt.normalize()
    right["date"] = pd.to_datetime(right["date"], errors="coerce").dt.normalize()

    keep = ["date"] + [col for col in EXECUTION_COLUMNS if col in right.columns]
    right = right[keep].drop_duplicates("date", keep="last")
    right = right.rename(
        columns={col: f"exec_{col}" for col in keep if col != "date"}
    )
    how = "outer" if preserve_execution_dates else "left"
    merged = left.merge(right, on="date", how=how)
    return (
        merged.sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


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


def _historical_universe(
    provider: MarketDataService,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    refresh: bool,
) -> pd.DataFrame:
    """优先使用历史生命周期股票池，失败时退回当前沪深股票列表。"""
    try:
        stocks = provider.historical_securities(refresh=refresh).copy()
        if stocks.empty:
            raise RuntimeError("历史股票池为空")

        listing = (
            stocks["listing_date"]
            if "listing_date" in stocks.columns
            else pd.Series(pd.NaT, index=stocks.index)
        )
        delisting = (
            stocks["delisting_date"]
            if "delisting_date" in stocks.columns
            else pd.Series(pd.NaT, index=stocks.index)
        )
        stocks["listing_date"] = pd.to_datetime(
            listing, errors="coerce"
        ).dt.normalize()
        stocks["delisting_date"] = pd.to_datetime(
            delisting, errors="coerce"
        ).dt.normalize()

        listed = stocks["listing_date"].isna() | (stocks["listing_date"] <= end_date)
        alive = stocks["delisting_date"].isna() | (stocks["delisting_date"] >= start_date)
        stocks = stocks[listed & alive].copy()

        keep = [
            c
            for c in [
                "code",
                "name",
                "listing_date",
                "delisting_date",
                "market",
                "board",
            ]
            if c in stocks.columns
        ]
        return stocks[keep].drop_duplicates("code").reset_index(drop=True)
    except Exception as exc:
        print(f"历史股票池暂不可用，退回当前沪深股票列表: {exc}")
        stocks = provider.stock_list().copy()
        stocks["listing_date"] = pd.NaT
        stocks["delisting_date"] = pd.NaT
        return stocks


def _entry_allowed(
    code: str,
    name: str,
    df: pd.DataFrame,
    entry_idx: int,
    listing_date: pd.Timestamp | None,
) -> tuple[bool, str, object, float | None, float | None]:
    row = _execution_row(df.iloc[entry_idx])

    if not is_tradable_bar(row):
        return False, "停牌或不可交易", None, None, None

    if SETTINGS.exclude_st and historical_is_st(row, name):
        return False, "历史ST状态", None, None, None

    age = trading_age(df, entry_idx, listing_date)
    rule, up_limit, down_limit = row_limit_prices(
        code,
        row,
        fallback_name=name,
        trading_days_since_listing=age,
    )

    if open_is_limit_up(
        row,
        up_limit,
        up_limit_pct=rule.up_limit_pct,
    ):
        return False, "开盘涨停无法保证成交", rule, up_limit, down_limit

    return True, "", rule, up_limit, down_limit


def backtest_scored_stock(
    code: str,
    name: str,
    scored: pd.DataFrame,
    score_threshold: int,
    stop_atr_multiple: float,
    target_atr_multiple: float,
    max_hold_days: int,
    *,
    listing_date: str | pd.Timestamp | None = None,
    signal_start_date: str | pd.Timestamp | None = None,
    signal_end_date: str | pd.Timestamp | None = None,
    score_column: str = "score",
    signal_flag_column: str | None = None,
    model_name: str = "V1",
    min_signal_index: int | None = None,
) -> list[dict]:
    df = scored.reset_index(drop=True).copy()
    if df.empty:
        return []

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    listing_ts = (
        pd.Timestamp(listing_date).normalize()
        if listing_date is not None and pd.notna(listing_date)
        else None
    )
    signal_start = (
        pd.Timestamp(signal_start_date).normalize()
        if signal_start_date is not None
        else None
    )
    signal_end = (
        pd.Timestamp(signal_end_date).normalize()
        if signal_end_date is not None
        else None
    )

    trades: list[dict] = []
    i = (
        max(60, SETTINGS.min_bars - 1)
        if min_signal_index is None
        else max(0, int(min_signal_index))
    )

    while i < len(df) - 1:
        signal_row = df.iloc[i]
        signal_date = pd.Timestamp(signal_row["date"]).normalize()

        if signal_start is not None and signal_date < signal_start:
            i += 1
            continue
        if signal_end is not None and signal_date > signal_end:
            break

        if signal_flag_column is not None:
            flag_value = signal_row.get(signal_flag_column, False)
            if pd.isna(flag_value) or not bool(flag_value):
                i += 1
                continue
        else:
            signal_score = pd.to_numeric(
                pd.Series([signal_row.get(score_column)]),
                errors="coerce",
            ).iloc[0]
            if pd.isna(signal_score) or float(signal_score) < float(score_threshold):
                i += 1
                continue

        if SETTINGS.exclude_st and historical_is_st(
            _execution_row(signal_row),
            name,
        ):
            i += 1
            continue

        risk_decision = DEFAULT_DAILY_RISK_FILTER.evaluate(
            _execution_row(signal_row),
            fallback_name=name,
        )
        if not risk_decision.allowed:
            i += 1
            continue

        entry_idx = i + 1
        allowed, blocked_reason, entry_rule, _, _ = _entry_allowed(
            code,
            name,
            df,
            entry_idx,
            listing_ts,
        )
        if not allowed:
            i += 1
            continue

        entry_row = _execution_row(df.iloc[entry_idx])
        entry_price = float(entry_row["open"])
        if entry_price <= 0:
            i += 1
            continue

        signal_close = float(signal_row["close"]) if pd.notna(signal_row["close"]) else 0.0
        signal_atr = (
            float(signal_row["atr14"])
            if pd.notna(signal_row["atr14"])
            else signal_close * 0.03
        )
        atr_ratio = (
            max(0.001, signal_atr / signal_close)
            if signal_close > 0
            else 0.03
        )
        execution_atr = entry_price * atr_ratio
        stop_price = entry_price - stop_atr_multiple * execution_atr
        target_price = entry_price + target_atr_multiple * execution_atr

        # 沪深 A 股股票实行 T+1：买入当日不能卖出。
        first_sell_idx = entry_idx + 1
        if first_sell_idx >= len(df):
            break

        decision_end_idx = min(entry_idx + max_hold_days - 1, len(df) - 1)
        decision_end_idx = max(first_sell_idx, decision_end_idx)
        exit_idx: int | None = None
        exit_price: float | None = None
        exit_reason = ""

        for j in range(first_sell_idx, decision_end_idx + 1):
            day = df.iloc[j]
            exec_day = _execution_row(day)
            age = trading_age(df, j, listing_ts)
            rule, _, down_limit = row_limit_prices(
                code,
                exec_day,
                fallback_name=name,
                trading_days_since_listing=age,
            )

            if not sellable_bar(
                exec_day,
                down_limit_price=down_limit,
                down_limit_pct=rule.down_limit_pct,
            ):
                continue

            open_price = float(exec_day["open"])
            low_price = float(exec_day["low"])
            high_price = float(exec_day["high"])

            # 同一天同时触发止损/止盈时采用保守假设：先按止损处理。
            if low_price <= stop_price:
                fill = stop_price
                if open_price < stop_price:
                    fill = open_price
                if down_limit is not None:
                    fill = max(fill, down_limit)
                exit_idx = j
                exit_price = float(fill)
                exit_reason = "ATR止损"
                break

            if high_price >= target_price:
                exit_idx = j
                exit_price = float(target_price)
                exit_reason = "ATR目标"
                break

            held = j - entry_idx + 1
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
                    exit_price = float(exec_day["close"])
                    exit_reason = "趋势转弱"
                    break

        # 观察窗口到期时也必须真正可卖出；若一字跌停/停牌则顺延到下一可卖日。
        if exit_idx is None:
            for j in range(decision_end_idx, len(df)):
                day = df.iloc[j]
                exec_day = _execution_row(day)
                age = trading_age(df, j, listing_ts)
                rule, _, down_limit = row_limit_prices(
                    code,
                    exec_day,
                    fallback_name=name,
                    trading_days_since_listing=age,
                )
                if not sellable_bar(
                    exec_day,
                    down_limit_price=down_limit,
                    down_limit_pct=rule.down_limit_pct,
                ):
                    continue
                exit_idx = j
                exit_price = float(exec_day["close"])
                exit_reason = (
                    "观察窗口结束"
                    if j == decision_end_idx
                    else "观察窗口结束_延迟成交"
                )
                break

        # 数据结束仍无法卖出，不把未完成持仓伪造成已完成交易。
        if exit_idx is None or exit_price is None:
            break

        gross_return = (exit_price / entry_price - 1) * 100
        buy_date = pd.to_datetime(df.iloc[entry_idx]["date"]).normalize()
        sell_date = pd.to_datetime(df.iloc[exit_idx]["date"]).normalize()
        estimated_cost_pct = _cost_model().estimated_round_trip_pct(
            buy_date,
            sell_date,
        )
        net_return = gross_return - estimated_cost_pct
        delayed_exit_days = max(0, exit_idx - decision_end_idx)
        displayed_score = signal_row.get(score_column, pd.NA)

        trades.append(
            {
                "代码": code,
                "名称": name,
                "模型": model_name,
                "信号日": signal_date.strftime("%Y-%m-%d"),
                "买入日": buy_date.strftime("%Y-%m-%d"),
                "卖出日": sell_date.strftime("%Y-%m-%d"),
                "信号评分": round(float(displayed_score), 3)
                if pd.notna(displayed_score)
                else pd.NA,
                "买入价": round(entry_price, 3),
                "卖出价": round(exit_price, 3),
                "持有交易日": exit_idx - entry_idx + 1,
                "延迟卖出交易日": delayed_exit_days,
                "买入规则": getattr(entry_rule, "rule_id", ""),
                "退出原因": exit_reason,
                "毛收益%": round(gross_return, 3),
                "估算交易成本%": round(estimated_cost_pct, 4),
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
    *,
    listing_date: str | pd.Timestamp | None = None,
    signal_start_date: str | pd.Timestamp | None = None,
    signal_end_date: str | pd.Timestamp | None = None,
    execution_bars: pd.DataFrame | None = None,
) -> list[dict]:
    profile = load_strategy_profile()
    scored = score_history(hist)
    scored = attach_execution_bars(scored, execution_bars)
    return backtest_scored_stock(
        code=code,
        name=name,
        scored=scored,
        score_threshold=int(
            score_threshold
            if score_threshold is not None
            else profile["score_threshold"]
        ),
        stop_atr_multiple=float(
            stop_atr_multiple
            if stop_atr_multiple is not None
            else profile["stop_atr_multiple"]
        ),
        target_atr_multiple=float(
            target_atr_multiple
            if target_atr_multiple is not None
            else profile["target_atr_multiple"]
        ),
        max_hold_days=int(
            max_hold_days if max_hold_days is not None else profile["max_hold_days"]
        ),
        listing_date=listing_date,
        signal_start_date=signal_start_date,
        signal_end_date=signal_end_date,
        model_name="V1",
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

    regime_history = pd.DataFrame()
    try:
        regime_history = build_market_regime_history(
            MarketContextService(),
            signal_start,
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
        f"历史股票池: {total} 只 | 正式信号区间 "
        f"{signal_start:%Y-%m-%d} ~ {market_end:%Y-%m-%d}"
    )
    print(
        f"资源档位: {runtime.name} | 批次 {runtime.batch_size} | "
        f"回测并发上限 {runtime.backtest_workers}"
    )

    for i, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        listing_date = row.get("listing_date", pd.NaT)
        delisting_date = row.get("delisting_date", pd.NaT)

        stock_start = fetch_start
        if pd.notna(listing_date):
            stock_start = max(stock_start, pd.Timestamp(listing_date).normalize())

        stock_end = market_end
        if pd.notna(delisting_date):
            stock_end = min(stock_end, pd.Timestamp(delisting_date).normalize())

        if stock_start >= stock_end:
            continue

        try:
            hist, execution_bars = provider.research_history_range(
                code,
                stock_start.strftime("%Y-%m-%d"),
                stock_end.strftime("%Y-%m-%d"),
                refresh=refresh,
            )
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
                        listing_date=listing_date,
                        signal_start_date=signal_start,
                        execution_bars=execution_bars,
                    )
                )
        except Exception as exc:
            errors.append({"代码": code, "名称": name, "错误": str(exc)[:300]})
        finally:
            if (i + 1) % flush_every == 0:
                print(
                    f"回测进度 {i + 1}/{total}，累计交易 {len(all_trades)}，"
                    f"失败 {len(errors)}"
                )
                gc.collect()

    trades = _attach_market_regime(pd.DataFrame(all_trades), regime_history)
    if persist:
        trades.to_csv(
            SETTINGS.report_dir / "backtest_trades.csv",
            index=False,
            encoding="utf-8-sig",
        )
        if errors:
            pd.DataFrame(errors).to_csv(
                SETTINGS.report_dir / "backtest_errors.csv",
                index=False,
                encoding="utf-8-sig",
            )
    return trades
