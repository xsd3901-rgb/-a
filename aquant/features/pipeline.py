from __future__ import annotations

import pandas as pd

from aquant.features.relative_strength import add_relative_strength
from aquant.features.technical import TechnicalFeatureBuilder


class FeaturePipeline:
    """日线特征流水线。

    基准指数可选；未提供基准时仍能完成个股技术/量价特征。
    """

    def __init__(self) -> None:
        self.technical = TechnicalFeatureBuilder()

    def transform(
        self,
        bars: pd.DataFrame,
        benchmark_bars: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        out = self.technical.transform(bars)
        if out.empty:
            return out
        if benchmark_bars is not None and not benchmark_bars.empty:
            out = add_relative_strength(out, benchmark_bars)
        return out
