from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ProviderInfo:
    provider: str
    adapter: str
    volume_unit: str = "share"


class DailyBarProvider(ABC):
    """日线数据源统一接口。返回数据必须已经转换到 A-Quant 标准字段。"""

    info: ProviderInfo

    @abstractmethod
    def fetch_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "none",
    ) -> pd.DataFrame:
        raise NotImplementedError


def normalize_symbol(symbol: str) -> str:
    digits = "".join(ch for ch in str(symbol) if ch.isdigit())
    if len(digits) < 6:
        digits = digits.zfill(6)
    return digits[-6:]


def to_baostock_code(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz.{code}"
    raise ValueError(f"BaoStock 当前适配器暂不处理该市场代码: {code}")


def is_shsz_a_share(symbol: str) -> bool:
    """当前项目主股票池：沪深 A 股，不含北交所和 B 股。"""
    code = normalize_symbol(symbol)
    return code.startswith(("0", "3", "6"))
