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
    """校验日线，同时允许历史停牌行缺少 OHLC。

    BaoStock 可提供 trade_status=0 的停牌日期。停牌行本身对真实成交
    很重要，因此不能因为价格为空就把整只股票的数据判废；核心价格
    完整性只要求在可交易行成立。
    """
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

    if FIELDS.trade_status in df.columns:
        status = pd.to_numeric(df[FIELDS.trade_status], errors="coerce")
        tradable = status.eq(1)
        unknown_status = status.isna()
        if unknown_status.any():
            issues.append(
                QualityIssue(
                    "invalid_trade_status",
                    "部分记录缺少有效交易状态",
                )
            )
    else:
        tradable = pd.Series(True, index=df.index)

    numeric = [FIELDS.open, FIELDS.high, FIELDS.low, FIELDS.close, FIELDS.volume]
    active = df.loc[tradable, numeric]
    if not active.empty and active.isna().any().any():
        issues.append(QualityIssue("nan_core", "可交易行情的核心字段存在空值"))

    active_high = pd.to_numeric(
        df.loc[tradable, FIELDS.high], errors="coerce"
    )
    active_low = pd.to_numeric(
        df.loc[tradable, FIELDS.low], errors="coerce"
    )
    if (active_high < active_low).fillna(False).any():
        issues.append(QualityIssue("high_lt_low", "存在最高价低于最低价的可交易记录"))

    active_prices = df.loc[
        tradable,
        [FIELDS.open, FIELDS.high, FIELDS.low, FIELDS.close],
    ].apply(pd.to_numeric, errors="coerce")
    if (active_prices <= 0).fillna(False).any().any():
        issues.append(QualityIssue("non_positive_price", "可交易记录存在非正价格"))

    volume = pd.to_numeric(df[FIELDS.volume], errors="coerce")
    if (volume.dropna() < 0).any():
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
