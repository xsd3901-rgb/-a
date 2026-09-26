from __future__ import annotations

import math

import numpy as np
import pandas as pd

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
        "最大同时持仓": 0,
        "说明": "没有可用于组合模拟的完整交易。",
    }


def simulate_portfolio(
    trades: pd.DataFrame,
    *,
    initial_capital: float | None = None,
    max_positions: int | None = None,
    max_position_pct: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """把独立交易信号放进有限资金账户，执行 A 股 100 股整数手约束。

    组合权益曲线在持仓期间按买入成本记账，因此这里的回撤是
    “账面成本回撤”，不是逐日盯市回撤。它主要用于解决独立交易
    回测没有资金/持仓上限的问题。
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
    equity_rows: list[dict] = []
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
        # 先卖出，再处理当日新信号，释放的资金可在同日后续买入研究中复用。
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
        equity_rows.append(
            {
                "日期": current_date,
                "现金": round(cash, 2),
                "持仓成本": round(book_equity - cash, 2),
                "账面成本权益": round(book_equity, 2),
                "持仓数": len(open_positions),
            }
        )

    executed = pd.DataFrame(accepted_rows)
    equity = pd.DataFrame(equity_rows)
    final_capital = cash + sum(pos["cash_out"] for pos in open_positions.values())

    if not equity.empty:
        curve = pd.to_numeric(equity["账面成本权益"], errors="coerce")
        peak = curve.cummax()
        drawdown = (curve / peak - 1.0) * 100.0
        max_drawdown = abs(float(drawdown.min())) if not drawdown.empty else 0.0
    else:
        max_drawdown = 0.0

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
        "最大账面成本回撤%": round(max_drawdown, 3),
        "最大同时持仓": int(max_concurrent),
        "说明": (
            "已计佣金最低5元、历史印花税/过户费和滑点；"
            "持仓期间权益按买入成本记账，正式逐日盯市回撤仍需完整组合日线。"
        ),
    }
    return executed, equity, metrics
