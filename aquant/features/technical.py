from __future__ import annotations

import numpy as np
import pandas as pd

from aquant.features.base import FeatureBuilder
from indicators import add_indicators


class TechnicalFeatureBuilder(FeatureBuilder):
    """沪深 A 股日线短期波段特征。

    只使用当前及历史已知数据，不使用未来数据。
    """

    name = "technical_daily"
    version = "1"

    def transform(self, bars: pd.DataFrame) -> pd.DataFrame:
        if bars is None or bars.empty:
            return pd.DataFrame()

        out = add_indicators(bars).copy()

        close = pd.to_numeric(out["close"], errors="coerce")
        high = pd.to_numeric(out["high"], errors="coerce")
        low = pd.to_numeric(out["low"], errors="coerce")
        volume = pd.to_numeric(out["volume"], errors="coerce")
        amount = (
            pd.to_numeric(out["amount"], errors="coerce")
            if "amount" in out.columns
            else pd.Series(np.nan, index=out.index)
        )

        # 趋势位置与斜率
        out["close_ma20_gap_pct"] = (close / out["ma20"] - 1.0) * 100
        out["ma20_ma60_gap_pct"] = (out["ma20"] / out["ma60"] - 1.0) * 100
        out["ma20_slope_5_pct"] = out["ma20"].pct_change(5) * 100
        out["ma60_slope_10_pct"] = out["ma60"].pct_change(10) * 100

        # 动量
        out["ret_1d"] = close.pct_change() * 100
        out["ret_10d"] = close.pct_change(10) * 100
        out["ret_60d"] = close.pct_change(60) * 100
        out["momentum_5_20"] = out["ret_5d"] - out["ret_20d"] / 4.0

        # 突破与位置。前高使用 shift(1)，避免把当天最高价提前当作已知阻力位。
        out["prev_high_20"] = high.shift(1).rolling(20).max()
        out["prev_low_20"] = low.shift(1).rolling(20).min()
        out["breakout_20_pct"] = (close / out["prev_high_20"] - 1.0) * 100
        range20 = (out["prev_high_20"] - out["prev_low_20"]).replace(0, np.nan)
        out["range_position_20"] = (close - out["prev_low_20"]) / range20

        # 量能和流动性
        out["vol_ma20"] = volume.rolling(20).mean()
        out["vol_ratio20"] = volume / out["vol_ma20"].replace(0, np.nan)
        out["amount_ma5"] = amount.rolling(5).mean()
        out["amount_ma20"] = amount.rolling(20).mean()
        out["amount_ratio5_20"] = out["amount_ma5"] / out["amount_ma20"].replace(0, np.nan)

        # 波动与回撤
        out["volatility_20"] = close.pct_change().rolling(20).std() * np.sqrt(252) * 100
        out["drawdown_20"] = (close / close.rolling(20).max() - 1.0) * 100
        out["amplitude_20"] = (high.rolling(20).max() / low.rolling(20).min() - 1.0) * 100

        # 可解释布尔特征，后续策略层可以选择是否赋分
        out["trend_up"] = (
            (close > out["ma20"])
            & (out["ma20"] > out["ma60"])
            & (out["ma20_slope_5_pct"] > 0)
        )
        out["short_ma_bull"] = (out["ma5"] > out["ma10"]) & (out["ma10"] > out["ma20"])
        out["near_breakout"] = out["breakout_20_pct"].between(-2.0, 3.0)
        out["healthy_volume"] = out["vol_ratio20"].between(0.8, 3.0)
        out["overheated"] = (
            (out["rsi6"] > 85)
            | (out["ret_5d"] > 25)
            | (out["atr_pct"] > 9)
        )

        return out
