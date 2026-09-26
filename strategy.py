from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import SETTINGS
from indicators import add_indicators
from profile import load_strategy_profile


@dataclass
class Signal:
    score: int
    reasons: str
    risk: str
    close: float
    stop: float
    target: float
    atr_pct: float


def score_history(df: pd.DataFrame) -> pd.DataFrame:
    out = add_indicators(df)
    score = pd.Series(0.0, index=out.index)

    # 趋势
    score += np.where(out["close"] > out["ma20"], 12, 0)
    score += np.where((out["ma5"] > out["ma10"]) & (out["ma10"] > out["ma20"]), 14, 0)
    score += np.where(out["ma20"] > out["ma60"], 8, 0)

    # 动量
    score += np.where(out["macd_dif"] > out["macd_dea"], 12, 0)
    score += np.where((out["rsi6"] >= 45) & (out["rsi6"] <= 78), 10, 0)
    score += np.where((out["kdj_k"] > out["kdj_d"]) & (out["kdj_j"] < 100), 8, 0)

    # 量能与强弱
    score += np.where((out["vol_ratio5"] >= 1.05) & (out["vol_ratio5"] <= 3.5), 12, 0)
    score += np.where(out["position_20"] >= 0.94, 10, 0)
    score += np.where((out["ret_5d"] >= 0) & (out["ret_5d"] <= 18), 7, 0)
    score += np.where((out["ret_20d"] >= -5) & (out["ret_20d"] <= 35), 7, 0)

    # 过热/波动惩罚
    score -= np.where(out["rsi6"] > 85, 12, 0)
    score -= np.where(out["atr_pct"] > 9, 10, 0)
    score -= np.where(out["ret_5d"] > 25, 10, 0)

    profile = load_strategy_profile()
    out["score"] = score.clip(0, 100).round().astype("Int64")
    out["signal"] = out["score"] >= int(profile["score_threshold"])
    return out


def evaluate_latest(df: pd.DataFrame) -> Signal:
    scored = score_history(df)
    if len(scored) < SETTINGS.min_bars:
        raise ValueError(f"有效K线不足 {SETTINGS.min_bars} 根")

    row = scored.iloc[-1]
    score = int(row["score"])
    reasons: list[str] = []

    if row["close"] > row["ma20"]:
        reasons.append("站上MA20")
    if row["ma5"] > row["ma10"] > row["ma20"]:
        reasons.append("短均线多头")
    if row["macd_dif"] > row["macd_dea"]:
        reasons.append("MACD偏强")
    if 45 <= row["rsi6"] <= 78:
        reasons.append("RSI健康")
    if 1.05 <= row["vol_ratio5"] <= 3.5:
        reasons.append("量能配合")
    if row["position_20"] >= 0.94:
        reasons.append("接近20日强势区")

    profile = load_strategy_profile()
    atr = float(row["atr14"]) if pd.notna(row["atr14"]) else float(row["close"] * 0.03)
    close = float(row["close"])
    stop = max(0.01, close - float(profile["stop_atr_multiple"]) * atr)
    target = close + float(profile["target_atr_multiple"]) * atr
    atr_pct = float(row["atr_pct"]) if pd.notna(row["atr_pct"]) else 0.0

    if atr_pct >= 7 or row["rsi6"] >= 82:
        risk = "高"
    elif atr_pct >= 4.5 or row["rsi6"] >= 75:
        risk = "中"
    else:
        risk = "低"

    return Signal(
        score=score,
        reasons="、".join(reasons) or "信号不足",
        risk=risk,
        close=round(close, 3),
        stop=round(stop, 3),
        target=round(target, 3),
        atr_pct=round(atr_pct, 2),
    )
