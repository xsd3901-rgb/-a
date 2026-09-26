from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class MarketDataContract:
    """A-Quant 统一数据字段契约。任何数据源进入正式库前都必须转换到这里。"""

    symbol: str = "symbol"
    trade_date: str = "trade_date"
    open: str = "open"
    high: str = "high"
    low: str = "low"
    close: str = "close"
    preclose: str = "preclose"
    volume: str = "volume"          # 统一单位：股
    amount: str = "amount"          # 统一单位：人民币元
    turnover: str = "turnover"      # 统一单位：%
    pct_change: str = "pct_change"  # 日涨跌幅，单位：%
    trade_status: str = "trade_status"  # 1=可交易，0=停牌/不可交易（数据源提供时）
    is_st: str = "is_st"            # 点时 ST 标记，数据源提供时使用
    adj_factor: str = "adj_factor"

    # 来源元数据：真实数据提供方和 Python 适配器分开记录。
    provider: str = "provider"
    adapter: str = "adapter"
    adjustment: str = "adjustment"  # none / qfq / hfq

    fetched_at: str = "fetched_at"
    data_version: str = "data_version"
    quality_status: str = "quality_status"


FIELDS: Final = MarketDataContract()

CORE_BAR_COLUMNS: Final[tuple[str, ...]] = (
    FIELDS.symbol,
    FIELDS.trade_date,
    FIELDS.open,
    FIELDS.high,
    FIELDS.low,
    FIELDS.close,
    FIELDS.volume,
)

OPTIONAL_BAR_COLUMNS: Final[tuple[str, ...]] = (
    FIELDS.preclose,
    FIELDS.amount,
    FIELDS.turnover,
    FIELDS.pct_change,
    FIELDS.trade_status,
    FIELDS.is_st,
    FIELDS.adj_factor,
)

META_COLUMNS: Final[tuple[str, ...]] = (
    FIELDS.provider,
    FIELDS.adapter,
    FIELDS.adjustment,
    FIELDS.fetched_at,
    FIELDS.data_version,
    FIELDS.quality_status,
)
