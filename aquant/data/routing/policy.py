from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    name: str
    timeout_seconds: float


# 股票基础表属于“元数据”，有本地种子兜底，因此远端失败应尽快返回。
STOCK_LIST_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("eastmoney", 12.0),
    SourceSpec("exchange", 18.0),
    SourceSpec("baostock", 25.0),
)

# 交易日历同样有随包种子和本地真实行情反推兜底。
CALENDAR_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("akshare", 12.0),
    SourceSpec("baostock", 20.0),
)

# 历史生命周期目前正式源仍是 BaoStock；失败后由调用方降级到当前股票池，
# 但正式审计不会把这种降级当成 historical lifecycle 验收通过。
LIFECYCLE_SOURCE = SourceSpec("baostock", 30.0)

# 未复权执行层优先点时字段更完整的 BaoStock。
DAILY_POINT_IN_TIME_ORDER: tuple[str, ...] = (
    "baostock",
    "eastmoney",
)

# 研究复权层优先 EastMoney/AKShare。
DAILY_RESEARCH_ORDER: tuple[str, ...] = (
    "eastmoney",
    "baostock",
)
