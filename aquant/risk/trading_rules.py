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



def historical_is_st(row: pd.Series, fallback_name: str = "") -> bool:
    """读取点时 ST 状态；只有没有历史字段时才退回名称判断。"""
    if "is_st" in row and pd.notna(row["is_st"]):
        try:
            return int(float(row["is_st"])) == 1
        except (TypeError, ValueError):
            pass
    name = str(fallback_name or "").upper().strip()
    return "ST" in name


def trading_age(
    bars: pd.DataFrame,
    row_index: int,
    listing_date: str | pd.Timestamp | None,
) -> int | None:
    """估算上市后的交易日序号，只用于判断前 1/5 个交易日的特殊规则。"""
    if listing_date is None or pd.isna(listing_date):
        return None

    listing = pd.Timestamp(listing_date).normalize()
    row_date = pd.Timestamp(bars.iloc[row_index]["date"]).normalize()
    if row_date < listing:
        return None

    # 若已上市超过两周，肯定不再处于前 5 个交易日，直接返回一个安全的大值。
    if (row_date - listing).days > 14:
        return 20

    window = bars.loc[:row_index].copy()
    dates = pd.to_datetime(window["date"], errors="coerce").dt.normalize()
    active = (dates >= listing) & (dates <= row_date)

    # 停牌日期属于真实执行时间轴，但不应计入“上市后第几个交易日”。
    status_col = None
    if "exec_trade_status" in window.columns:
        status_col = "exec_trade_status"
    elif "trade_status" in window.columns:
        status_col = "trade_status"
    if status_col is not None:
        status = pd.to_numeric(window[status_col], errors="coerce")
        active &= status.eq(1)

    count = int(active.sum())
    return max(1, count) if count > 0 else 1


def row_limit_prices(
    code: str,
    row: pd.Series,
    *,
    fallback_name: str = "",
    trading_days_since_listing: int | None = None,
) -> tuple[TradingRule, float | None, float | None]:
    """按历史 ST 状态和板块规则计算当日涨跌停参考价。"""
    is_st = historical_is_st(row, fallback_name)
    rule = price_limit_rule(
        code,
        row["date"],
        is_st=is_st,
        trading_days_since_listing=trading_days_since_listing,
    )

    previous_close = None
    if "preclose" in row and pd.notna(row["preclose"]):
        previous_close = float(row["preclose"])
    if previous_close is None or previous_close <= 0:
        return rule, None, None

    up, down = limit_prices(previous_close, rule)
    return rule, up, down


def _pct_near(value: float | None, target: float | None, tolerance_pct: float = 0.35) -> bool:
    if value is None or target is None:
        return False
    return abs(float(value) - float(target)) <= tolerance_pct


def is_one_price_limit_down(
    row: pd.Series,
    down_limit_price: float | None,
    *,
    down_limit_pct: float | None = None,
    tolerance: float = 0.011,
) -> bool:
    """识别一字跌停；一字跌停时长仓通常不能按模型价格卖出。"""
    prices = [float(row[c]) for c in ("open", "high", "low", "close")]
    one_price = max(prices) - min(prices) <= tolerance

    if down_limit_price is not None:
        return one_price and all(abs(price - down_limit_price) <= tolerance for price in prices)

    if "pct_change" in row and pd.notna(row["pct_change"]) and down_limit_pct is not None:
        return one_price and _pct_near(float(row["pct_change"]), -float(down_limit_pct))

    return False


def is_locked_limit_up(
    row: pd.Series,
    up_limit_price: float | None,
    *,
    up_limit_pct: float | None = None,
    tolerance: float = 0.011,
) -> bool:
    """识别买入基本无法成交的一字涨停。"""
    prices = [float(row[c]) for c in ("open", "high", "low", "close")]
    one_price = max(prices) - min(prices) <= tolerance

    if up_limit_price is not None:
        return one_price and all(abs(price - up_limit_price) <= tolerance for price in prices)

    if "pct_change" in row and pd.notna(row["pct_change"]) and up_limit_pct is not None:
        return one_price and _pct_near(float(row["pct_change"]), float(up_limit_pct))

    return False


def open_is_limit_up(
    row: pd.Series,
    up_limit_price: float | None,
    *,
    up_limit_pct: float | None = None,
    price_tolerance: float = 0.011,
) -> bool:
    """保守判断开盘即涨停；这种情况下不假定能在开盘价买到。"""
    if up_limit_price is not None:
        return abs(float(row["open"]) - up_limit_price) <= price_tolerance

    if "pct_change" in row and pd.notna(row["pct_change"]) and up_limit_pct is not None:
        # 没有可靠未复权涨停价时，仅把一字板视为不可买，避免误伤开板日。
        return is_locked_limit_up(
            row,
            up_limit_price=None,
            up_limit_pct=up_limit_pct,
            tolerance=price_tolerance,
        )
    return False


def sellable_bar(
    row: pd.Series,
    *,
    down_limit_price: float | None = None,
    down_limit_pct: float | None = None,
) -> bool:
    """判断长仓当日是否具备基本卖出条件。"""
    if not is_tradable_bar(row):
        return False
    if is_one_price_limit_down(
        row,
        down_limit_price,
        down_limit_pct=down_limit_pct,
    ):
        return False
    return True
