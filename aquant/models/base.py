from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ModelOutput:
    score: float
    expected_return: float | None = None
    risk_probability: float | None = None
    metadata: dict | None = None


class QuantModel(ABC):
    """规则模型、统计模型、机器学习模型统一接口。"""

    name: str = "base"
    version: str = "1"

    @abstractmethod
    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """输出模型分数/概率，不直接产生交易指令。"""
        raise NotImplementedError
