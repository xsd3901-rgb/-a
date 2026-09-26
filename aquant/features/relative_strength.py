from __future__ import annotations

import pandas as pd


def add_relative_strength(
    stock_features: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
) -> pd.DataFrame:
    """加入相对基准强弱特征。

    两边按交易日对齐，只使用各自当日及历史收盘数据。
    """
    if stock_features is None or stock_features.empty:
        return pd.DataFrame()
    if benchmark_bars is None or benchmark_bars.empty:
        out = stock_features.copy()
        out["rs_5d"] = pd.NA
        out["rs_20d"] = pd.NA
        out["rs_60d"] = pd.NA
        return out

    out = stock_features.copy()
    date_col = "date" if "date" in out.columns else "trade_date"
    bench_date_col = "trade_date" if "trade_date" in benchmark_bars.columns else "date"

    out[date_col] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
    bench = benchmark_bars[[bench_date_col, "close"]].copy()
    bench[bench_date_col] = pd.to_datetime(bench[bench_date_col], errors="coerce").dt.normalize()
    bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
    bench = bench.sort_values(bench_date_col).drop_duplicates(bench_date_col, keep="last")

    for n in (5, 20, 60):
        bench[f"bench_ret_{n}d"] = bench["close"].pct_change(n) * 100

    merged = out.merge(
        bench[
            [
                bench_date_col,
                "bench_ret_5d",
                "bench_ret_20d",
                "bench_ret_60d",
            ]
        ],
        left_on=date_col,
        right_on=bench_date_col,
        how="left",
    )

    if bench_date_col != date_col:
        merged = merged.drop(columns=[bench_date_col], errors="ignore")

    stock_close = pd.to_numeric(merged["close"], errors="coerce")
    for n in (5, 20, 60):
        stock_ret = stock_close.pct_change(n) * 100
        merged[f"rs_{n}d"] = stock_ret - merged[f"bench_ret_{n}d"]

    return merged
