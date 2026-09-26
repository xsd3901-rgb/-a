from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd


@dataclass(frozen=True)
class TradingRule:
    board: str
    up_limit_pct: float | None
    down_limit_pct: float | None
    no_price_limit: bool
    rule_id: str


def board_of(code: str) -> str:
    symbol = str(code).strip().zfill(6)[-6:]
    if symbol.startswith(("688", "689")):
        return "star"
    if symbol.startswith(("300", "301")):
        return "chinext"
    if symbol.startswith(("4", "8", "92")):
        return "bse"
    if symbol.startswith(("6", "9")):
        return "sse_main"
    if symbol.startswith(("0", "2")):
        return "szse_main"
    return "other"


def market_of(code: str) -> str:
    board = board_of(code)
    if board in {"star", "sse_main"}:
        return "SSE"
    if board in {"chinext", "szse_main"}:
        return "SZSE"
    if board == "bse":
        return "BSE"
    return "OTHER"


def price_limit_rule(
    code: str,
    trade_date: str | pd.Timestamp,
    *,
    is_st: bool = False,
    trading_days_since_listing: int | None = None,
) -> TradingRule:
    """返回指定交易日的基础涨跌停制度。

    trading_days_since_listing 从 1 开始计数。注册制新股前若干交易日
    无价格涨跌幅限制时，返回 no_price_limit=True。

    历史 ST 身份必须由点时状态数据提供；本函数不会用当前名称反推历史状态。
    """
    date = pd.Timestamp(trade_date).normalize()
    board = board_of(code)
    age = trading_days_since_listing

    if board == "bse":
        if age is not None and age <= 1:
            return TradingRule(board, None, None, True, "bse_first_day_no_limit")
        return TradingRule(board, 30.0, 30.0, False, "bse_30pct")

    if board == "star":
        if age is not None and age <= 5:
            return TradingRule(board, None, None, True, "star_first5_no_limit")
        return TradingRule(board, 20.0, 20.0, False, "star_20pct")

    if board == "chinext":
        reform = pd.Timestamp("2020-08-24")
        if date >= reform:
            if age is not None and age <= 5:
                return TradingRule(board, None, None, True, "chinext_first5_no_limit")
            return TradingRule(board, 20.0, 20.0, False, "chinext_20pct")
        pct = 5.0 if is_st else 10.0
        return TradingRule(board, pct, pct, False, "chinext_pre_reform")

    if board in {"sse_main", "szse_main"}:
        registration_start = pd.Timestamp("2023-04-10")
        if date >= registration_start and age is not None and age <= 5:
            return TradingRule(board, None, None, True, "main_registration_first5_no_limit")
        pct = 5.0 if is_st else 10.0
        return TradingRule(board, pct, pct, False, "main_board_standard")

    pct = 5.0 if is_st else 10.0
    return TradingRule(board, pct, pct, False, "fallback_standard")


def _round_price(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_prices(
    previous_close: float,
    rule: TradingRule,
) -> tuple[float | None, float | None]:
    if rule.no_price_limit or rule.up_limit_pct is None or rule.down_limit_pct is None:
        return None, None
    prev = float(previous_close)
    up = _round_price(prev * (1.0 + rule.up_limit_pct / 100.0))
    down = _round_price(prev * (1.0 - rule.down_limit_pct / 100.0))
    return up, down


def is_tradable_bar(row: pd.Series) -> bool:
    """基于日线记录判断该日是否可交易，不把缺失数据误当成可交易。"""
    if "trade_status" in row and pd.notna(row["trade_status"]):
        try:
            if int(float(row["trade_status"])) != 1:
                return False
        except (TypeError, ValueError):
            return False
    if "volume" in row and pd.notna(row["volume"]) and float(row["volume"]) <= 0:
        return False
    for col in ("open", "high", "low", "close"):
        if col in row and (pd.isna(row[col]) or float(row[col]) <= 0):
            return False
    return True


def is_one_price_limit_up(
    row: pd.Series,
    up_limit_price: float | None,
    tolerance: float = 0.011,
) -> bool:
    """识别一字涨停；用于后续回测避免把明显买不到的价格当成交。"""
    if up_limit_price is None:
        return False
    prices = [float(row[c]) for c in ("open", "high", "low", "close")]
    return all(abs(price - up_limit_price) <= tolerance for price in prices)
