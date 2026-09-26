from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from aquant.data.context_service import MarketContextService


CORE_SHSZ_INDICES: dict[str, str] = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000852": "中证1000",
}


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    trade_date: str
    regime: str
    score: float
    strong_count: int
    weak_count: int
    index_count: int
    details: pd.DataFrame


def _index_features(frame: pd.DataFrame) -> dict[str, float | bool | str]:
    if frame is None or frame.empty or len(frame) < 60:
        raise ValueError("指数K线不足 60 根")

    df = frame.copy().sort_values("trade_date").reset_index(drop=True)
    close = pd.to_numeric(df["close"], errors="coerce")
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ret5 = close.pct_change(5) * 100
    ret20 = close.pct_change(20) * 100

    last = len(df) - 1
    close_v = float(close.iloc[last])
    ma20_v = float(ma20.iloc[last])
    ma60_v = float(ma60.iloc[last])
    ret5_v = float(ret5.iloc[last])
    ret20_v = float(ret20.iloc[last])

    score = 0.0
    score += 1.0 if close_v > ma20_v else -1.0
    score += 1.0 if ma20_v > ma60_v else -1.0
    score += 1.0 if ret20_v > 0 else -1.0
    if ret5_v > 2.0:
        score += 0.5
    elif ret5_v < -2.0:
        score -= 0.5

    return {
        "trade_date": pd.to_datetime(df.iloc[last]["trade_date"]).strftime("%Y-%m-%d"),
        "close": close_v,
        "ma20": ma20_v,
        "ma60": ma60_v,
        "ret_5d": ret5_v,
        "ret_20d": ret20_v,
        "above_ma20": close_v > ma20_v,
        "ma20_above_ma60": ma20_v > ma60_v,
        "score": score,
    }


def classify_market_regime(details: pd.DataFrame) -> tuple[str, float, int, int]:
    if details is None or details.empty:
        return "未知", 0.0, 0, 0

    score = float(pd.to_numeric(details["score"], errors="coerce").mean())
    strong_count = int((pd.to_numeric(details["score"], errors="coerce") >= 2.0).sum())
    weak_count = int((pd.to_numeric(details["score"], errors="coerce") <= -2.0).sum())
    n = len(details)

    if score >= 2.0 and strong_count >= max(3, int(np.ceil(n * 0.6))):
        regime = "强势上行"
    elif score >= 0.75:
        regime = "偏强"
    elif score <= -2.0 and weak_count >= max(3, int(np.ceil(n * 0.6))):
        regime = "弱势下行"
    elif score <= -0.75:
        regime = "偏弱"
    else:
        regime = "震荡"

    normalized = round(max(-100.0, min(100.0, score / 3.5 * 100.0)), 2)
    return regime, normalized, strong_count, weak_count


def detect_market_regime(
    context: MarketContextService,
    as_of_date: str | pd.Timestamp,
    *,
    lookback_calendar_days: int = 180,
) -> MarketRegimeSnapshot:
    end = pd.Timestamp(as_of_date).normalize()
    start = end - pd.Timedelta(days=lookback_calendar_days)

    rows: list[dict] = []
    for code, name in CORE_SHSZ_INDICES.items():
        try:
            frame = context.index_daily(
                code,
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d"),
            )
            feat = _index_features(frame)
            rows.append({"index_code": code, "index_name": name, **feat})
        except Exception as exc:
            rows.append(
                {
                    "index_code": code,
                    "index_name": name,
                    "trade_date": end.strftime("%Y-%m-%d"),
                    "score": np.nan,
                    "error": str(exc)[:200],
                }
            )

    details = pd.DataFrame(rows)
    valid = details[pd.to_numeric(details["score"], errors="coerce").notna()].copy()
    if valid.empty:
        return MarketRegimeSnapshot(
            trade_date=end.strftime("%Y-%m-%d"),
            regime="未知",
            score=0.0,
            strong_count=0,
            weak_count=0,
            index_count=0,
            details=details,
        )

    regime, score, strong_count, weak_count = classify_market_regime(valid)
    trade_date = str(valid["trade_date"].dropna().max())
    return MarketRegimeSnapshot(
        trade_date=trade_date,
        regime=regime,
        score=score,
        strong_count=strong_count,
        weak_count=weak_count,
        index_count=len(valid),
        details=details,
    )
