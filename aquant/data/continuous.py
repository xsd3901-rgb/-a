from __future__ import annotations

import numpy as np
import pandas as pd


def build_point_in_time_continuous(raw_bars: pd.DataFrame) -> pd.DataFrame:
    """用逐日已知涨跌幅构造无未来信息的连续研究价格。

    目的不是生成可成交价格，而是给技术指标提供连续序列：
    - 成交仍必须使用未复权原始 OHLC；
    - 连续收盘价仅由截至当日已知的 pct_change 递推；
    - 当 pct_change 缺失时才退回原始收盘价日收益。

    这样避免把“今天查询得到的前复权历史”直接当成过去当时可见的价格序列。
    """
    if raw_bars is None or raw_bars.empty:
        return pd.DataFrame()

    out = raw_bars.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["date"]).sort_values("date").drop_duplicates(
        "date", keep="last"
    ).reset_index(drop=True)

    # 连续研究价只在真正可交易的个股K线序列上计算。
    # 停牌日期留在独立 execution 时间轴中，由回测层负责阻断成交；
    # 不把停牌日当成一根“平盘K线”参与 MA/动量窗口。
    if "trade_status" in out.columns:
        status = pd.to_numeric(out["trade_status"], errors="coerce")
        out = out[status.eq(1)].copy()

    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    valid_price = out[["open", "high", "low", "close"]].notna().all(axis=1)
    valid_price &= (out[["open", "high", "low", "close"]] > 0).all(axis=1)
    out = out[valid_price].reset_index(drop=True)
    if out.empty:
        return out

    first_raw_preclose = None
    if "preclose" in out.columns:
        candidate = pd.to_numeric(out["preclose"], errors="coerce").dropna()
        candidate = candidate[candidate > 0]
        if not candidate.empty:
            first_raw_preclose = float(candidate.iloc[0])

    raw_close = out["close"].astype(float)
    fallback_ret = raw_close.pct_change()

    if "pct_change" in out.columns:
        pct = pd.to_numeric(out["pct_change"], errors="coerce") / 100.0
        daily_ret = pct.where(pct.notna(), fallback_ret)
    else:
        daily_ret = fallback_ret

    daily_ret = daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    daily_ret = daily_ret.where(daily_ret > -0.999, fallback_ret.fillna(0.0))

    continuous_close = pd.Series(index=out.index, dtype=float)
    continuous_close.iloc[0] = float(raw_close.iloc[0])
    for i in range(1, len(out)):
        factor = 1.0 + float(daily_ret.iloc[i])
        if not np.isfinite(factor) or factor <= 0:
            prev_raw = float(raw_close.iloc[i - 1])
            curr_raw = float(raw_close.iloc[i])
            factor = curr_raw / prev_raw if prev_raw > 0 else 1.0
        continuous_close.iloc[i] = continuous_close.iloc[i - 1] * factor

    scale = continuous_close / raw_close.replace(0, np.nan)
    scale = scale.replace([np.inf, -np.inf], np.nan).ffill().fillna(1.0)

    for col in ("open", "high", "low"):
        out[col] = pd.to_numeric(out[col], errors="coerce") * scale
    out["close"] = continuous_close
    out["preclose"] = out["close"].shift(1)
    if len(out):
        out.loc[out.index[0], "preclose"] = (
            first_raw_preclose
            if first_raw_preclose is not None and first_raw_preclose > 0
            else out.loc[out.index[0], "close"]
        )

    out["signal_price_mode"] = "point_in_time_continuous"
    return out.reset_index(drop=True)
