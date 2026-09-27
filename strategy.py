from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aquant.features.pipeline import FeaturePipeline
from aquant.risk.daily import DEFAULT_DAILY_RISK_FILTER
from config import SETTINGS
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
    rs20: float | None = None
    allowed: bool = True
    risk_reasons: str = ""


V1_COMPONENT_SPECS: dict[str, dict[str, object]] = {
    "v1c_close_above_ma20": {
        "名称": "站上MA20",
        "类别": "趋势",
        "权重": 12,
    },
    "v1c_short_ma_bull": {
        "名称": "MA5>MA10>MA20",
        "类别": "趋势",
        "权重": 14,
    },
    "v1c_ma20_above_ma60": {
        "名称": "MA20>MA60",
        "类别": "趋势",
        "权重": 8,
    },
    "v1c_macd_bull": {
        "名称": "MACD DIF>DEA",
        "类别": "动量",
        "权重": 12,
    },
    "v1c_rsi_healthy": {
        "名称": "RSI健康区",
        "类别": "动量",
        "权重": 10,
    },
    "v1c_kdj_bull": {
        "名称": "KDJ偏强",
        "类别": "动量",
        "权重": 8,
    },
    "v1c_volume_healthy": {
        "名称": "5日量比健康",
        "类别": "量能",
        "权重": 12,
    },
    "v1c_position_strong": {
        "名称": "20日强势位置",
        "类别": "强弱",
        "权重": 10,
    },
    "v1c_ret5_healthy": {
        "名称": "5日涨幅健康",
        "类别": "动量",
        "权重": 7,
    },
    "v1c_ret20_healthy": {
        "名称": "20日涨幅健康",
        "类别": "动量",
        "权重": 7,
    },
    "v1c_penalty_rsi_hot": {
        "名称": "RSI过热惩罚",
        "类别": "惩罚",
        "权重": -12,
    },
    "v1c_penalty_atr_high": {
        "名称": "ATR高波动惩罚",
        "类别": "惩罚",
        "权重": -10,
    },
    "v1c_penalty_ret5_hot": {
        "名称": "5日涨幅过热惩罚",
        "类别": "惩罚",
        "权重": -10,
    },
}

V1_COMPONENT_COLUMNS = tuple(V1_COMPONENT_SPECS)


def _attach_v1_components(out: pd.DataFrame) -> pd.DataFrame:
    """把 V1 每一条规则拆成独立贡献列，评分口径保持不变。"""
    conditions = {
        "v1c_close_above_ma20": out["close"] > out["ma20"],
        "v1c_short_ma_bull": (
            (out["ma5"] > out["ma10"])
            & (out["ma10"] > out["ma20"])
        ),
        "v1c_ma20_above_ma60": out["ma20"] > out["ma60"],
        "v1c_macd_bull": out["macd_dif"] > out["macd_dea"],
        "v1c_rsi_healthy": (
            (out["rsi6"] >= 45)
            & (out["rsi6"] <= 78)
        ),
        "v1c_kdj_bull": (
            (out["kdj_k"] > out["kdj_d"])
            & (out["kdj_j"] < 100)
        ),
        "v1c_volume_healthy": (
            (out["vol_ratio5"] >= 1.05)
            & (out["vol_ratio5"] <= 3.5)
        ),
        "v1c_position_strong": out["position_20"] >= 0.94,
        "v1c_ret5_healthy": (
            (out["ret_5d"] >= 0)
            & (out["ret_5d"] <= 18)
        ),
        "v1c_ret20_healthy": (
            (out["ret_20d"] >= -5)
            & (out["ret_20d"] <= 35)
        ),
        "v1c_penalty_rsi_hot": out["rsi6"] > 85,
        "v1c_penalty_atr_high": out["atr_pct"] > 9,
        "v1c_penalty_ret5_hot": out["ret_5d"] > 25,
    }

    for column, spec in V1_COMPONENT_SPECS.items():
        weight = int(spec["权重"])
        out[column] = np.where(
            conditions[column].fillna(False),
            weight,
            0,
        ).astype(np.int8)
    return out


def score_history(
    df: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    out = FeaturePipeline().transform(
        df,
        benchmark_bars=benchmark_bars,
    )
    out = _attach_v1_components(out)

    score = pd.Series(0, index=out.index, dtype="int16")
    for column in V1_COMPONENT_COLUMNS:
        score = score.add(
            pd.to_numeric(out[column], errors="coerce").fillna(0),
            fill_value=0,
        )

    profile = load_strategy_profile()
    out["score"] = score.clip(0, 100).round().astype("Int64")
    out["signal"] = (
        out["score"] >= int(profile["score_threshold"])
    )
    return out


def evaluate_latest(
    df: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None = None,
    *,
    fallback_name: str = "",
) -> Signal:
    scored = score_history(df, benchmark_bars=benchmark_bars)
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

    rs20 = None
    if "rs_20d" in row.index and pd.notna(row["rs_20d"]):
        rs20 = round(float(row["rs_20d"]), 2)
        if rs20 > 0:
            reasons.append("20日跑赢沪深300")
        elif rs20 < -5:
            reasons.append("20日弱于沪深300")

    risk_decision = DEFAULT_DAILY_RISK_FILTER.evaluate(
        row,
        fallback_name=fallback_name,
    )
    if risk_decision.level == "高":
        risk = "高"
    elif risk_decision.level == "中" and risk == "低":
        risk = "中"

    return Signal(
        score=score,
        reasons="、".join(reasons) or "信号不足",
        risk=risk,
        close=round(close, 3),
        stop=round(stop, 3),
        target=round(target, 3),
        atr_pct=round(atr_pct, 2),
        rs20=rs20,
        allowed=risk_decision.allowed,
        risk_reasons="、".join(risk_decision.reasons),
    )
