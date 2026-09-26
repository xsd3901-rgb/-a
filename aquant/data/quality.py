from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aquant.data.schema import CORE_BAR_COLUMNS, FIELDS


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"


def validate_bar_frame(df: pd.DataFrame) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    missing = [c for c in CORE_BAR_COLUMNS if c not in df.columns]
    if missing:
        issues.append(QualityIssue("missing_columns", f"缺少必要字段: {missing}"))
        return issues

    if df.empty:
        issues.append(QualityIssue("empty", "行情数据为空"))
        return issues

    if df[FIELDS.trade_date].duplicated().any():
        issues.append(QualityIssue("duplicate_date", "存在重复交易日"))

    numeric = [FIELDS.open, FIELDS.high, FIELDS.low, FIELDS.close, FIELDS.volume]
    if df[numeric].isna().any().any():
        issues.append(QualityIssue("nan_core", "核心行情字段存在空值"))

    bad_high_low = df[FIELDS.high] < df[FIELDS.low]
    if bad_high_low.any():
        issues.append(QualityIssue("high_lt_low", "存在最高价低于最低价的记录"))

    bad_ohlc = (
        (df[FIELDS.open] <= 0)
        | (df[FIELDS.high] <= 0)
        | (df[FIELDS.low] <= 0)
        | (df[FIELDS.close] <= 0)
    )
    if bad_ohlc.any():
        issues.append(QualityIssue("non_positive_price", "存在非正价格"))

    if (df[FIELDS.volume] < 0).any():
        issues.append(QualityIssue("negative_volume", "存在负成交量"))

    return issues


def cross_check_bars(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    price_tolerance_pct: float = 0.20,
    volume_tolerance_pct: float = 5.0,
) -> pd.DataFrame:
    """对关键字段做多源交叉检查；返回差异明细，不直接覆盖主源数据。"""
    keys = [FIELDS.symbol, FIELDS.trade_date]
    cols = keys + [FIELDS.close, FIELDS.volume]
    left = primary[cols].copy()
    right = secondary[cols].copy()
    merged = left.merge(right, on=keys, suffixes=("_primary", "_secondary"), how="inner")
    if merged.empty:
        return merged

    close_base = merged[f"{FIELDS.close}_primary"].abs().replace(0, pd.NA)
    vol_base = merged[f"{FIELDS.volume}_primary"].abs().replace(0, pd.NA)

    merged["close_diff_pct"] = (
        (merged[f"{FIELDS.close}_secondary"] - merged[f"{FIELDS.close}_primary"]).abs()
        / close_base
        * 100
    )
    merged["volume_diff_pct"] = (
        (merged[f"{FIELDS.volume}_secondary"] - merged[f"{FIELDS.volume}_primary"]).abs()
        / vol_base
        * 100
    )
    merged["quality_status"] = "ok"
    mismatch = (
        (merged["close_diff_pct"] > price_tolerance_pct)
        | (merged["volume_diff_pct"] > volume_tolerance_pct)
    )
    merged.loc[mismatch, "quality_status"] = "mismatch"
    return merged
