from __future__ import annotations

import pandas as pd

from aquant.risk.base import RiskDecision, RiskFilter
from aquant.risk.trading_rules import historical_is_st, is_tradable_bar
from config import SETTINGS


class DailySwingRiskFilter(RiskFilter):
    """沪深 A 股日线波段的独立风险闸门。

    只负责阻断明显不适合进入候选池的样本，不对模型分数加减分。
    """

    name = "daily_swing"

    def evaluate(
        self,
        row: pd.Series,
        *,
        fallback_name: str = "",
    ) -> RiskDecision:
        reasons: list[str] = []
        level = "低"

        if not is_tradable_bar(row):
            return RiskDecision(
                allowed=False,
                level="高",
                reasons=("停牌或不可交易",),
            )

        if SETTINGS.exclude_st and historical_is_st(row, fallback_name):
            return RiskDecision(
                allowed=False,
                level="高",
                reasons=("ST/风险警示",),
            )

        atr_pct = pd.to_numeric(
            pd.Series([row.get("atr_pct")]),
            errors="coerce",
        ).iloc[0]
        if pd.notna(atr_pct):
            atr_pct = float(atr_pct)
            if atr_pct > SETTINGS.risk_max_atr_pct:
                reasons.append(
                    f"ATR波动{atr_pct:.2f}%超过{SETTINGS.risk_max_atr_pct:.1f}%"
                )
            elif atr_pct >= 7.0:
                level = "高"
            elif atr_pct >= 4.5:
                level = "中"

        amount_ma5 = pd.to_numeric(
            pd.Series([row.get("amount_ma5")]),
            errors="coerce",
        ).iloc[0]
        if pd.notna(amount_ma5) and float(amount_ma5) < SETTINGS.risk_min_amount_ma5:
            reasons.append(
                f"5日均成交额低于{SETTINGS.risk_min_amount_ma5 / 10_000:.0f}万元"
            )

        overheated = row.get("overheated", False)
        if pd.notna(overheated) and bool(overheated):
            level = "高" if level != "高" else level

        if reasons:
            return RiskDecision(
                allowed=False,
                level="高",
                reasons=tuple(reasons),
            )

        return RiskDecision(
            allowed=True,
            level=level,
            reasons=(),
        )


DEFAULT_DAILY_RISK_FILTER = DailySwingRiskFilter()


def apply_daily_risk_filter(
    frame: pd.DataFrame,
    *,
    fallback_name: str = "",
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame() if frame is None else frame.copy()

    out = frame.copy()
    allowed: list[bool] = []
    levels: list[str] = []
    reasons: list[str] = []

    for _, row in out.iterrows():
        decision = DEFAULT_DAILY_RISK_FILTER.evaluate(
            row,
            fallback_name=fallback_name,
        )
        allowed.append(decision.allowed)
        levels.append(decision.level)
        reasons.append("、".join(decision.reasons))

    out["risk_allowed"] = allowed
    out["risk_level"] = levels
    out["risk_reasons"] = reasons
    return out
