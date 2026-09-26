from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd

from aquant.models.base import QuantModel
from config import SETTINGS


@dataclass(frozen=True)
class FeatureRule:
    feature: str
    kind: str
    direction: str
    weight: float
    low_threshold: float | None = None
    high_threshold: float | None = None
    quality: float = 0.0
    train_effect: float = 0.0
    train_ic: float | None = None


class CandidateScoreV2(QuantModel):
    """研究用 V2 评分模型。

    规则必须由历史训练样本拟合；该类只负责把已经冻结的规则应用到新样本。
    """

    name = "candidate_score_v2"
    version = "2"

    def __init__(self, rules: list[FeatureRule]) -> None:
        self.rules = list(rules)

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        out = features.copy()
        if out.empty:
            out["v2_score"] = pd.Series(dtype=float)
            return out

        raw = pd.Series(0.0, index=out.index)
        available = pd.Series(0.0, index=out.index)

        for rule in self.rules:
            if rule.feature not in out.columns:
                continue

            values = out[rule.feature]
            valid = values.notna()
            if not valid.any():
                continue

            points = pd.Series(0.0, index=out.index)
            if rule.kind == "boolean":
                flag = values.fillna(False).astype(bool)
                favorable = flag if rule.direction == "high" else ~flag
                points.loc[valid & favorable] = rule.weight
            else:
                numeric = pd.to_numeric(values, errors="coerce")
                valid = numeric.notna()
                low = rule.low_threshold
                high = rule.high_threshold
                if low is None or high is None or not math.isfinite(low) or not math.isfinite(high):
                    continue

                if rule.direction == "high":
                    points.loc[valid & (numeric >= high)] = rule.weight
                    middle = valid & (numeric > low) & (numeric < high)
                    points.loc[middle] = rule.weight * 0.5
                else:
                    points.loc[valid & (numeric <= low)] = rule.weight
                    middle = valid & (numeric > low) & (numeric < high)
                    points.loc[middle] = rule.weight * 0.5

            raw += points
            available.loc[valid] += rule.weight

        out["v2_score"] = np.where(
            available > 0,
            (raw / available * 100.0).clip(0, 100),
            np.nan,
        )
        return out

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "rules": [asdict(rule) for rule in self.rules],
        }


def _spearman_ic(x: pd.Series, y: pd.Series) -> float:
    valid = pd.concat([x, y], axis=1).dropna()
    if len(valid) < 3:
        return math.nan
    value = valid.iloc[:, 0].rank().corr(valid.iloc[:, 1].rank())
    return float(value) if pd.notna(value) else math.nan


def _welch_t(a: pd.Series, b: pd.Series) -> float:
    a = pd.to_numeric(a, errors="coerce").dropna()
    b = pd.to_numeric(b, errors="coerce").dropna()
    if len(a) < 2 or len(b) < 2:
        return math.nan
    va = float(a.var(ddof=1))
    vb = float(b.var(ddof=1))
    se = math.sqrt(va / len(a) + vb / len(b))
    if se <= 0:
        return math.nan
    return float((a.mean() - b.mean()) / se)


def fit_single_rule(
    samples: pd.DataFrame,
    feature: str,
    target: str,
    kind: str,
) -> FeatureRule | None:
    """只使用训练样本拟合一个候选规则。"""
    if feature not in samples.columns or target not in samples.columns:
        return None

    pair = samples[[feature, target]].copy()
    pair[target] = pd.to_numeric(pair[target], errors="coerce")
    pair = pair.dropna(subset=[feature, target])
    if len(pair) < SETTINGS.v2_min_feature_samples:
        return None

    if kind == "boolean":
        flag = pair[feature].astype(bool)
        true_ret = pair.loc[flag, target]
        false_ret = pair.loc[~flag, target]
        if len(true_ret) < 50 or len(false_ret) < 50:
            return None

        effect = float(true_ret.mean() - false_ret.mean())
        if abs(effect) < SETTINGS.v2_min_abs_return_spread:
            return None

        t_value = _welch_t(true_ret, false_ret)
        quality = abs(effect) + (abs(t_value) * 0.05 if math.isfinite(t_value) else 0.0)
        return FeatureRule(
            feature=feature,
            kind=kind,
            direction="high" if effect > 0 else "low",
            weight=1.0,
            quality=quality,
            train_effect=effect,
            train_ic=None,
        )

    pair[feature] = pd.to_numeric(pair[feature], errors="coerce")
    pair = pair.dropna(subset=[feature])
    if len(pair) < SETTINGS.v2_min_feature_samples:
        return None

    q30 = float(pair[feature].quantile(0.30))
    q70 = float(pair[feature].quantile(0.70))
    if not math.isfinite(q30) or not math.isfinite(q70) or q30 >= q70:
        return None

    low_ret = pair.loc[pair[feature] <= q30, target]
    high_ret = pair.loc[pair[feature] >= q70, target]
    if len(low_ret) < 50 or len(high_ret) < 50:
        return None

    effect = float(high_ret.mean() - low_ret.mean())
    ic = _spearman_ic(pair[feature], pair[target])
    if (
        abs(effect) < SETTINGS.v2_min_abs_return_spread
        and (not math.isfinite(ic) or abs(ic) < SETTINGS.v2_min_abs_ic)
    ):
        return None

    t_value = _welch_t(high_ret, low_ret)
    quality = (
        abs(effect)
        + (abs(ic) * 4.0 if math.isfinite(ic) else 0.0)
        + (abs(t_value) * 0.05 if math.isfinite(t_value) else 0.0)
    )
    return FeatureRule(
        feature=feature,
        kind=kind,
        direction="high" if effect > 0 else "low",
        weight=1.0,
        low_threshold=q30,
        high_threshold=q70,
        quality=quality,
        train_effect=effect,
        train_ic=ic if math.isfinite(ic) else None,
    )


def fit_candidate_model(
    samples: pd.DataFrame,
    feature_specs: dict[str, str],
    *,
    target: str = "fwd_ret_10d",
    max_features: int | None = None,
) -> CandidateScoreV2:
    max_features = int(max_features or SETTINGS.v2_max_features)
    rules: list[FeatureRule] = []

    for feature, kind in feature_specs.items():
        rule = fit_single_rule(samples, feature, target, kind)
        if rule is not None:
            rules.append(rule)

    rules.sort(key=lambda item: item.quality, reverse=True)
    selected = rules[:max_features]
    if not selected:
        return CandidateScoreV2([])

    max_quality = max(rule.quality for rule in selected) or 1.0
    weighted: list[FeatureRule] = []
    for rule in selected:
        weight = 1.0 + min(1.0, rule.quality / max_quality)
        weighted.append(
            FeatureRule(
                feature=rule.feature,
                kind=rule.kind,
                direction=rule.direction,
                weight=round(weight, 4),
                low_threshold=rule.low_threshold,
                high_threshold=rule.high_threshold,
                quality=rule.quality,
                train_effect=rule.train_effect,
                train_ic=rule.train_ic,
            )
        )
    return CandidateScoreV2(weighted)
