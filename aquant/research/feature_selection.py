from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from config import SETTINGS, ensure_directories


def _normalize_direction(text: str) -> str | None:
    value = str(text or "").strip()
    if value in {"高值更优", "True更优"}:
        return "high"
    if value in {"低值更优", "False更优"}:
        return "low"
    return None


def select_candidate_features(
    validation: pd.DataFrame,
    *,
    min_supported_horizons: int = 2,
    max_features: int | None = None,
) -> pd.DataFrame:
    """把特征验证报告压缩成 V2 候选特征，不直接启用到正式策略。"""
    if validation is None or validation.empty:
        return pd.DataFrame()

    max_features = int(max_features or SETTINGS.v2_max_features)
    frame = validation.copy()
    required = {
        "特征",
        "类型",
        "验证样本",
        "验证SpearmanIC",
        "验证高低组收益差%",
        "验证t值",
        "训练方向",
        "稳定性",
        "训练30%分位",
        "训练70%分位",
    }
    if not required.issubset(frame.columns):
        missing = sorted(required - set(frame.columns))
        raise ValueError(f"特征验证报告缺少字段: {missing}")

    frame["验证样本"] = pd.to_numeric(frame["验证样本"], errors="coerce")
    frame["验证SpearmanIC"] = pd.to_numeric(frame["验证SpearmanIC"], errors="coerce")
    frame["验证高低组收益差%"] = pd.to_numeric(
        frame["验证高低组收益差%"], errors="coerce"
    )
    frame["验证t值"] = pd.to_numeric(frame["验证t值"], errors="coerce")
    frame["训练30%分位"] = pd.to_numeric(frame["训练30%分位"], errors="coerce")
    frame["训练70%分位"] = pd.to_numeric(frame["训练70%分位"], errors="coerce")
    frame["方向"] = frame["训练方向"].map(_normalize_direction)

    stable = frame[
        frame["稳定性"].eq("方向一致")
        & frame["方向"].notna()
        & frame["验证样本"].ge(100)
    ].copy()
    if stable.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for feature, group in stable.groupby("特征"):
        directions = set(group["方向"].dropna().astype(str))
        if len(directions) != 1:
            continue

        supported = len(group)
        if supported < min_supported_horizons:
            continue

        kind = str(group["类型"].iloc[0])
        direction = next(iter(directions))
        median_effect = float(group["验证高低组收益差%"].median())
        median_abs_t = float(group["验证t值"].abs().median())
        ic_values = group["验证SpearmanIC"].dropna().abs()
        median_abs_ic = float(ic_values.median()) if not ic_values.empty else 0.0

        # 至少要有一个统计证据，不允许只因为“方向碰巧一致”就进入 V2。
        evidence_ok = (
            abs(median_effect) >= SETTINGS.v2_min_abs_return_spread
            or median_abs_ic >= SETTINGS.v2_min_abs_ic
            or median_abs_t >= 1.0
        )
        if not evidence_ok:
            continue

        quality = (
            supported
            + min(3.0, abs(median_effect))
            + min(2.0, median_abs_t * 0.25)
            + min(1.5, median_abs_ic * 10.0)
        )

        q30 = np.nan
        q70 = np.nan
        if kind == "continuous":
            q30 = float(group["训练30%分位"].median())
            q70 = float(group["训练70%分位"].median())
            if not math.isfinite(q30) or not math.isfinite(q70) or q30 >= q70:
                continue

        rows.append(
            {
                "特征": feature,
                "类型": kind,
                "方向": direction,
                "支持窗口数": supported,
                "验证收益差中位数%": round(median_effect, 4),
                "验证|t|中位数": round(median_abs_t, 4),
                "验证|IC|中位数": round(median_abs_ic, 4),
                "训练30%分位": round(q30, 6) if math.isfinite(q30) else np.nan,
                "训练70%分位": round(q70, 6) if math.isfinite(q70) else np.nan,
                "质量分": round(quality, 4),
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    result = (
        result.sort_values(
            ["支持窗口数", "质量分", "验证|t|中位数"],
            ascending=[False, False, False],
        )
        .head(max_features)
        .reset_index(drop=True)
    )
    return result


def build_v2_candidate(
    validation_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    ensure_directories()
    path = Path(validation_path or (SETTINGS.report_dir / "feature_validation.csv"))
    if not path.exists():
        raise FileNotFoundError(
            f"没有找到特征验证结果: {path}。请先运行 python main.py features --limit 200"
        )

    validation = pd.read_csv(path)
    selected = select_candidate_features(validation)
    selected.to_csv(
        SETTINGS.report_dir / "feature_selection_v2.csv",
        index=False,
        encoding="utf-8-sig",
    )

    candidate = {
        "model": "candidate_score_v2",
        "version": "2",
        "status": "research_only",
        "source": str(path),
        "max_features": SETTINGS.v2_max_features,
        "rules": [],
        "note": "候选模型只供研究；Walk-Forward 通过前不会自动切换正式评分。",
    }

    for _, row in selected.iterrows():
        quality = float(row["质量分"])
        candidate["rules"].append(
            {
                "feature": str(row["特征"]),
                "kind": str(row["类型"]),
                "direction": str(row["方向"]),
                "weight": round(1.0 + min(1.0, quality / 10.0), 4),
                "low_threshold": (
                    None
                    if pd.isna(row["训练30%分位"])
                    else float(row["训练30%分位"])
                ),
                "high_threshold": (
                    None
                    if pd.isna(row["训练70%分位"])
                    else float(row["训练70%分位"])
                ),
                "quality": quality,
            }
        )

    model_dir = SETTINGS.data_store_dir / "research" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "score_v2_candidate.json"
    model_path.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return selected, candidate
