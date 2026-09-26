from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class FeatureBuilder(ABC):
    """所有特征工程实现的统一接口。"""

    name: str = "base"
    version: str = "1"

    @abstractmethod
    def transform(self, bars: pd.DataFrame) -> pd.DataFrame:
        """输入标准行情，输出只使用当时可见信息计算出的特征。"""
        raise NotImplementedError
