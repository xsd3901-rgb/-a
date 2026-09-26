from __future__ import annotations

import math

import numpy as np
import pandas as pd

from aquant.data.schema import FIELDS
from aquant.data.storage import MarketStore
from aquant.risk.costs import AShareCostModel
from config import SETTINGS


def _cost_model() -> AShareCostModel:
    return AShareCostModel(
        commission_bps=SETTINGS.commission_bps,
        min_commission_cny=SETTINGS.min_commission_cny,
        slippage_bps=SETTINGS.slippage_bps,
    )


def _empty_metrics() -> dict:
    return {
        "初始资金": round(float(SETTINGS.portfolio_initial_capital), 2),
        "期末资金": round(float(SETTINGS.portfolio_initial_capital), 2),
        "组合收益%": 0.0,
        "实际成交交易": 0,
        "因仓位跳过": 0,
        "胜率%": 0.0,
        "平均单笔净收益%": 0.0,
        "最大账面成本回撤%": 0.0,
        "最大盯市回撤%": 0.0,
        "盯市覆盖率%": 0.0,
        "最大同时持仓": 0,
        "说明": "没有可用于组合模拟的完整交易。",
    }


def _normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "close"])

    out = frame.copy()
    date_col = "date" if "date" in out.columns else FIELDS.trade_date
    if date_col not in out.columns or "close" not in out.columns:
        return pd.DataFrame(columns=["date", "close"])

    out["date"] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["date", "close"])
    out = out[out["close"] > 0]
    return (
        out[["date", "close"]]
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def _load_price_frames(
    executed: pd.DataFrame,
    price_frames: dict[str, pd.DataFrame] | None,
) -> dict[str, pd.DataFrame]:
    """优先使用调用方提供的未复权价格；否则只读本地 none 层，不触发网络。"""
    supplied = {
        str(code): _normalize_price_frame(frame)
        for code, frame in (price_frames or {}).items()
    }
    if executed is None or executed.empty:
        return supplied

    store = MarketStore(SETTINGS.data_store_dir)
    result = dict(supplied)

    for code, group in executed.groupby("代码"):
        symbol = str(code).zfill(6)
        if symbol in result and not result[symbol].empty:
            continue

        start = pd.to_datetime(group["买入日"], errors="coerce").min()
        end = pd.to_datetime(group["卖出日"], errors="coerce").max()
        if pd.isna(start) or pd.isna(end):
            result[symbol] = pd.DataFrame(columns=["date", "close"])
            continue

        try:
            local = store.read_daily(
                symbol,
                "none",
                start_date=pd.Timestamp(start).strftime("%Y-%m-%d"),
                end_date=pd.Timestamp(end).strftime("%Y-%m-%d"),
            )
            result[symbol] = _normalize_price_frame(local)
        except Exception:
            result[symbol] = pd.DataFrame(columns=["date", "close"])

    return result


def _mark_to_market_equity(
    executed: pd.DataFrame,
    *,
    initial_capital: float,
    price_frames: dict[str, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, float, float]:
    """按未复权日线收盘价生成逐日盯市权益曲线。

    缺失某只股票当日价格（例如停牌）时沿用最近一次可见收盘价。
    若本地完全没有该股票价格，则退回买入成交前的原始买入价作为持仓估值。
    """
    if executed is None or executed.empty:
        return pd.DataFrame(), 0.0, 0.0

    frame = executed.copy().reset_index(drop=True)
    frame["_portfolio_trade_id"] = frame.index.astype(int)
    frame["买入日"] = pd.to_datetime(frame["买入日"], errors="coerce").dt.normalize()
    frame["卖出日"] = pd.to_datetime(frame["卖出日"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["买入日", "卖出日"])
    if frame.empty:
        return pd.DataFrame(), 0.0, 0.0

    prices = _load_price_frames(frame, price_frames)

    price_maps: dict[str, dict[pd.Timestamp, float]] = {}
    all_dates: set[pd.Timestamp] = set()
    total_expected_marks = 0
    available_marks = 0

    for code, price_frame in prices.items():
        if price_frame.empty:
            price_maps[str(code)] = {}
            continue
        mapping = {
            pd.Timestamp(row["date"]).normalize(): float(row["close"])
            for _, row in price_frame.iterrows()
        }
        price_maps[str(code)] = mapping
        all_dates.update(mapping.keys())

    all_dates.update(frame["买入日"].tolist())
    all_dates.update(frame["卖出日"].tolist())
    if not all_dates:
        return pd.DataFrame(), 0.0, 0.0

    start_date = frame["买入日"].min()
    end_date = frame["卖出日"].max()
    calendar = sorted(d for d in all_dates if start_date <= d <= end_date)

    entry_map = {
        date: group.copy()
        for date, group in frame.groupby("买入日", sort=True)
    }
    exit_map = {
        date: group.copy()
        for date, group in frame.groupby("卖出日", sort=True)
    }

    cash = float(initial_capital)
    positions: dict[int, dict] = {}
    last_price: dict[str, float] = {}
    rows: list[dict] = []

    for current_date in calendar:
        # 先卖出，再买入，与组合成交模拟口径一致。
        exits = exit_map.get(current_date)
        if exits is not None:
            for _, row in exits.iterrows():
                trade_id = int(row["_portfolio_trade_id"])
                if trade_id in positions:
                    cash += float(row["卖出现金回收"])
                    positions.pop(trade_id, None)

        entries = entry_map.get(current_date)
        if entries is not None:
            for _, row in entries.iterrows():
                trade_id = int(row["_portfolio_trade_id"])
                code = str(row["代码"]).zfill(6)
                cash -= float(row["买入现金支出"])
                raw_entry = pd.to_numeric(
                    pd.Series([row.get("买入价")]),
                    errors="coerce",
                ).iloc[0]
                if pd.notna(raw_entry) and float(raw_entry) > 0:
                    last_price[code] = float(raw_entry)
                positions[trade_id] = {
                    "code": code,
                    "shares": int(row["实际股数"]),
                }

        market_value = 0.0
        for pos in positions.values():
            code = pos["code"]
            total_expected_marks += 1
            daily = price_maps.get(code, {})
            if current_date in daily:
                last_price[code] = float(daily[current_date])
                available_marks += 1

            px = last_price.get(code)
            if px is None or not math.isfinite(px) or px <= 0:
                continue
            market_value += float(pos["shares"]) * px

        equity = cash + market_value
        rows.append(
            {
                "日期": current_date,
                "现金": round(cash, 2),
                "持仓市值": round(market_value, 2),
                "盯市权益": round(equity, 2),
                "持仓数": len(positions),
            }
        )

    equity = pd.DataFrame(rows)
    if equity.empty:
        return equity, 0.0, 0.0

    curve = pd.to_numeric(equity["盯市权益"], errors="coerce").dropna()
    if curve.empty:
        max_drawdown = 0.0
    else:
        peak = curve.cummax()
        drawdown = (curve / peak - 1.0) * 100.0
        max_drawdown = abs(float(drawdown.min()))

    coverage = (
        available_marks / total_expected_marks * 100.0
        if total_expected_marks
        else 100.0
    )
    return equity, max_drawdown, coverage


def simulate_portfolio(
    trades: pd.DataFrame,
    *,
    initial_capital: float | None = None,
    max_positions: int | None = None,
    max_position_pct: float | None = None,
    price_frames: dict[str, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """把独立交易信号放进有限资金账户，并生成逐日盯市权益。

    - A 股 100 股整数手；
    - 有限初始资金、最大持仓数、单股仓位上限；
    - 佣金最低 5 元、历史印花税/过户费、滑点；
    - 逐日盯市优先读取未复权本地行情，不触发网络。
    """
    if trades is None or trades.empty:
        return pd.DataFrame(), pd.DataFrame(), _empty_metrics()

    required = {"代码", "买入日", "卖出日", "买入价", "卖出价"}
    if not required.issubset(trades.columns):
        missing = sorted(required - set(trades.columns))
        raise ValueError(f"组合模拟缺少交易字段: {missing}")

    frame = trades.copy().reset_index(drop=True)
    frame["_trade_id"] = frame.index.astype(int)
    frame["买入日"] = pd.to_datetime(frame["买入日"], errors="coerce").dt.normalize()
    frame["卖出日"] = pd.to_datetime(frame["卖出日"], errors="coerce").dt.normalize()
    frame["买入价"] = pd.to_numeric(frame["买入价"], errors="coerce")
    frame["卖出价"] = pd.to_numeric(frame["卖出价"], errors="coerce")
    frame["信号评分"] = pd.to_numeric(
        frame.get("信号评分", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    frame = frame.dropna(subset=["买入日", "卖出日", "买入价", "卖出价"])
    frame = frame[(frame["买入价"] > 0) & (frame["卖出价"] > 0)].copy()
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame(), _empty_metrics()

    capital = float(
        SETTINGS.portfolio_initial_capital
        if initial_capital is None
        else initial_capital
    )
    max_positions = int(
        SETTINGS.portfolio_max_positions if max_positions is None else max_positions
    )
    max_position_pct = float(
        SETTINGS.portfolio_max_position_pct
        if max_position_pct is None
        else max_position_pct
    )
    if capital <= 0 or max_positions <= 0 or not (0 < max_position_pct <= 1):
        raise ValueError("组合资金/持仓参数不合法")

    cost_model = _cost_model()
    cash = capital
    open_positions: dict[int, dict] = {}
    accepted_rows: list[dict] = []
    book_equity_rows: list[dict] = []
    skipped = 0
    max_concurrent = 0

    entry_map = {
        date: group.copy()
        for date, group in frame.groupby("买入日", sort=True)
    }
    exit_map: dict[pd.Timestamp, list[int]] = {}
    for _, row in frame.iterrows():
        exit_map.setdefault(row["卖出日"], []).append(int(row["_trade_id"]))

    all_dates = sorted(set(entry_map) | set(exit_map))

    for current_date in all_dates:
        for trade_id in exit_map.get(current_date, []):
            position = open_positions.pop(trade_id, None)
            if position is None:
                continue

            row = position["row"]
            sell_price = cost_model.sell_fill_price(float(row["卖出价"]))
            sell_notional = sell_price * position["shares"]
            sell_cost = cost_model.cash_cost(
                sell_notional,
                current_date,
                side="sell",
                apply_min_commission=True,
            )
            proceeds = sell_notional - sell_cost.total
            cash += proceeds

            pnl = proceeds - position["cash_out"]
            net_ret = pnl / position["cash_out"] * 100.0
            accepted_rows.append(
                {
                    **{
                        col: row[col]
                        for col in row.index
                        if not str(col).startswith("_")
                    },
                    "实际股数": int(position["shares"]),
                    "实际买入价": round(position["buy_price"], 4),
                    "实际卖出价": round(sell_price, 4),
                    "买入现金支出": round(position["cash_out"], 2),
                    "卖出现金回收": round(proceeds, 2),
                    "实际净盈亏": round(pnl, 2),
                    "组合单笔净收益%": round(net_ret, 4),
                }
            )

        candidates = entry_map.get(current_date)
        if candidates is not None and not candidates.empty:
            candidates = candidates.sort_values(
                ["信号评分", "代码"],
                ascending=[False, True],
                na_position="last",
            )
            open_codes = {
                str(pos["row"]["代码"])
                for pos in open_positions.values()
            }

            for _, row in candidates.iterrows():
                if len(open_positions) >= max_positions:
                    skipped += 1
                    continue
                if str(row["代码"]) in open_codes:
                    skipped += 1
                    continue

                book_equity = cash + sum(
                    pos["cash_out"] for pos in open_positions.values()
                )
                target_cash = min(
                    cash,
                    book_equity * max_position_pct,
                )
                buy_price = cost_model.buy_fill_price(float(row["买入价"]))
                lot_value = buy_price * 100.0
                if target_cash < lot_value:
                    skipped += 1
                    continue

                shares = int(target_cash // lot_value) * 100
                while shares >= 100:
                    buy_notional = buy_price * shares
                    buy_cost = cost_model.cash_cost(
                        buy_notional,
                        current_date,
                        side="buy",
                        apply_min_commission=True,
                    )
                    cash_out = buy_notional + buy_cost.total
                    if cash_out <= cash + 1e-9:
                        break
                    shares -= 100

                if shares < 100:
                    skipped += 1
                    continue

                cash -= cash_out
                trade_id = int(row["_trade_id"])
                open_positions[trade_id] = {
                    "row": row,
                    "shares": shares,
                    "buy_price": buy_price,
                    "cash_out": cash_out,
                }
                open_codes.add(str(row["代码"]))
                max_concurrent = max(max_concurrent, len(open_positions))

        book_equity = cash + sum(
            pos["cash_out"] for pos in open_positions.values()
        )
        book_equity_rows.append(
            {
                "日期": current_date,
                "现金": round(cash, 2),
                "持仓成本": round(book_equity - cash, 2),
                "账面成本权益": round(book_equity, 2),
                "持仓数": len(open_positions),
            }
        )

    executed = pd.DataFrame(accepted_rows)
    book_equity = pd.DataFrame(book_equity_rows)
    final_capital = cash + sum(pos["cash_out"] for pos in open_positions.values())

    if not book_equity.empty:
        curve = pd.to_numeric(book_equity["账面成本权益"], errors="coerce")
        peak = curve.cummax()
        drawdown = (curve / peak - 1.0) * 100.0
        book_drawdown = abs(float(drawdown.min())) if not drawdown.empty else 0.0
    else:
        book_drawdown = 0.0

    mark_equity, mark_drawdown, coverage = _mark_to_market_equity(
        executed,
        initial_capital=capital,
        price_frames=price_frames,
    )
    equity = mark_equity if not mark_equity.empty else book_equity

    if executed.empty:
        win_rate = 0.0
        avg_trade = 0.0
    else:
        returns = pd.to_numeric(
            executed["组合单笔净收益%"], errors="coerce"
        ).dropna()
        win_rate = float((returns > 0).mean() * 100.0) if len(returns) else 0.0
        avg_trade = float(returns.mean()) if len(returns) else 0.0

    metrics = {
        "初始资金": round(capital, 2),
        "期末资金": round(final_capital, 2),
        "组合收益%": round((final_capital / capital - 1.0) * 100.0, 3),
        "实际成交交易": int(len(executed)),
        "因仓位跳过": int(skipped),
        "胜率%": round(win_rate, 2),
        "平均单笔净收益%": round(avg_trade, 4),
        "最大账面成本回撤%": round(book_drawdown, 3),
        "最大盯市回撤%": round(mark_drawdown, 3),
        "盯市覆盖率%": round(coverage, 2),
        "最大同时持仓": int(max_concurrent),
        "说明": (
            "已计佣金最低5元、历史印花税/过户费和滑点；"
            "最大盯市回撤优先使用本地未复权日线逐日估值，停牌日沿用最近收盘价。"
        ),
    }
    return executed, equity, metrics
