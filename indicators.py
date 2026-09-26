from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int = 6) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for n in (5, 10, 20, 60):
        out[f"ma{n}"] = out["close"].rolling(n).mean()

    ema12 = out["close"].ewm(span=12, adjust=False).mean()
    ema26 = out["close"].ewm(span=26, adjust=False).mean()
    out["macd_dif"] = ema12 - ema26
    out["macd_dea"] = out["macd_dif"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = 2 * (out["macd_dif"] - out["macd_dea"])

    low9 = out["low"].rolling(9).min()
    high9 = out["high"].rolling(9).max()
    rsv = (out["close"] - low9) / (high9 - low9).replace(0, np.nan) * 100
    out["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean().fillna(50)
    out["kdj_d"] = out["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean().fillna(50)
    out["kdj_j"] = 3 * out["kdj_k"] - 2 * out["kdj_d"]

    out["rsi6"] = _rsi(out["close"], 6)
    out["rsi12"] = _rsi(out["close"], 12)

    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()
    out["atr_pct"] = out["atr14"] / out["close"] * 100

    out["vol_ma5"] = out["volume"].rolling(5).mean()
    out["vol_ratio5"] = out["volume"] / out["vol_ma5"].replace(0, np.nan)
    out["ret_5d"] = out["close"].pct_change(5) * 100
    out["ret_20d"] = out["close"].pct_change(20) * 100
    out["high_20"] = out["high"].rolling(20).max()
    out["position_20"] = out["close"] / out["high_20"].replace(0, np.nan)

    return out
