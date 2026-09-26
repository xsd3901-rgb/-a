from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    level: str
    reasons: tuple[str, ...] = ()


class RiskFilter(ABC):
    """独立于模型的风险过滤接口。模型高分不能绕过这里。"""

    name: str = "base"

    @abstractmethod
    def evaluate(self, row: pd.Series) -> RiskDecision:
        raise NotImplementedError
