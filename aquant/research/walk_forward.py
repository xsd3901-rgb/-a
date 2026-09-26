from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from aquant.models.score_v2 import CandidateScoreV2, FeatureRule, fit_single_rule
from aquant.research.feature_validation import FEATURE_SPECS, HORIZONS
from aquant.research.validation import TimeWindow, walk_forward_windows
from config import SETTINGS, ensure_directories


def _glob_path(work_dir: Path) -> str:
    return (work_dir / "chunk_*.parquet").as_posix().replace("'", "''")


def _available_dates(con: duckdb.DuckDBPyConnection, glob_path: str) -> pd.Series:
    frame = con.execute(
        f"""
        SELECT DISTINCT date
        FROM read_parquet('{glob_path}', union_by_name=true)
        WHERE date IS NOT NULL
        ORDER BY date
        """
    ).df()
    if frame.empty:
        return pd.Series(dtype="datetime64[ns]")
    return (
        pd.to_datetime(frame["date"], errors="coerce")
        .dropna()
        .dt.normalize()
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )


def _load_feature_pair(
    con: duckdb.DuckDBPyConnection,
    glob_path: str,
    feature: str,
    target: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    return con.execute(
        f"""
        SELECT date, "{feature}", "{target}"
        FROM read_parquet('{glob_path}', union_by_name=true)
        WHERE date >= DATE '{start_date}'
          AND date <= DATE '{end_date}'
          AND "{feature}" IS NOT NULL
          AND "{target}" IS NOT NULL
        """
    ).df()


def _fit_fold_rules(
    con: duckdb.DuckDBPyConnection,
    glob_path: str,
    window: TimeWindow,
    target: str,
) -> list[FeatureRule]:
    rules: list[FeatureRule] = []
    train_start = window.train_start.isoformat()
    train_end = window.train_end.isoformat()

    for feature, kind in FEATURE_SPECS.items():
        try:
            pair = _load_feature_pair(
                con,
                glob_path,
                feature,
                target,
                train_start,
                train_end,
            )
        except Exception:
            continue

        rule = fit_single_rule(pair, feature, target, kind)
        if rule is not None:
            rules.append(rule)

    rules.sort(key=lambda item: item.quality, reverse=True)
    selected = rules[: SETTINGS.v2_max_features]
    if not selected:
        return []

    max_quality = max(rule.quality for rule in selected) or 1.0
    weighted: list[FeatureRule] = []
    for rule in selected:
        weighted.append(
            FeatureRule(
                feature=rule.feature,
                kind=rule.kind,
                direction=rule.direction,
                weight=round(1.0 + min(1.0, rule.quality / max_quality), 4),
                low_threshold=rule.low_threshold,
                high_threshold=rule.high_threshold,
                quality=rule.quality,
                train_effect=rule.train_effect,
                train_ic=rule.train_ic,
            )
        )
    return weighted


def _load_validation_frame(
    con: duckdb.DuckDBPyConnection,
    glob_path: str,
    window: TimeWindow,
    rules: list[FeatureRule],
    target: str,
) -> pd.DataFrame:
    features = [rule.feature for rule in rules]
    quoted = ", ".join(f'"{name}"' for name in features)
    extra = f", {quoted}" if quoted else ""
    return con.execute(
        f"""
        SELECT code, name, date, "{target}"{extra}
        FROM read_parquet('{glob_path}', union_by_name=true)
        WHERE date >= DATE '{window.validation_start.isoformat()}'
          AND date <= DATE '{window.validation_end.isoformat()}'
          AND "{target}" IS NOT NULL
        """
    ).df()


def _spearman(x: pd.Series, y: pd.Series) -> float:
    pair = pd.concat(
        [
            pd.to_numeric(x, errors="coerce"),
            pd.to_numeric(y, errors="coerce"),
        ],
        axis=1,
    ).dropna()
    if len(pair) < 3:
        return math.nan
    value = pair.iloc[:, 0].rank().corr(pair.iloc[:, 1].rank())
    return float(value) if pd.notna(value) else math.nan


def _evaluate_fold(
    validation: pd.DataFrame,
    rules: list[FeatureRule],
    target: str,
    top_quantile: float,
) -> dict:
    if validation is None or validation.empty or not rules:
        return {
            "验证样本": 0,
            "入选样本": 0,
            "入选平均收益%": np.nan,
            "全体平均收益%": np.nan,
            "超额收益%": np.nan,
            "入选正收益率%": np.nan,
            "评分IC": np.nan,
        }

    model = CandidateScoreV2(rules)
    scored = model.predict(validation)
    scored[target] = pd.to_numeric(scored[target], errors="coerce")
    scored = scored.dropna(subset=["date", target, "v2_score"]).copy()
    if scored.empty:
        return {
            "验证样本": 0,
            "入选样本": 0,
            "入选平均收益%": np.nan,
            "全体平均收益%": np.nan,
            "超额收益%": np.nan,
            "入选正收益率%": np.nan,
            "评分IC": np.nan,
        }

    scored["date"] = pd.to_datetime(scored["date"], errors="coerce").dt.normalize()
    threshold = 1.0 - float(top_quantile)
    scored["_pct_rank"] = scored.groupby("date")["v2_score"].rank(
        pct=True,
        method="average",
    )
    selected = scored[scored["_pct_rank"] >= threshold].copy()

    universe_avg = float(scored[target].mean())
    selected_avg = float(selected[target].mean()) if not selected.empty else math.nan
    excess = (
        selected_avg - universe_avg
        if math.isfinite(selected_avg) and math.isfinite(universe_avg)
        else math.nan
    )
    hit_rate = (
        float((selected[target] > 0).mean() * 100.0)
        if not selected.empty
        else math.nan
    )
    ic = _spearman(scored["v2_score"], scored[target])

    return {
        "验证样本": len(scored),
        "入选样本": len(selected),
        "入选平均收益%": round(selected_avg, 4)
        if math.isfinite(selected_avg)
        else np.nan,
        "全体平均收益%": round(universe_avg, 4)
        if math.isfinite(universe_avg)
        else np.nan,
        "超额收益%": round(excess, 4) if math.isfinite(excess) else np.nan,
        "入选正收益率%": round(hit_rate, 2)
        if math.isfinite(hit_rate)
        else np.nan,
        "评分IC": round(ic, 4) if math.isfinite(ic) else np.nan,
    }


def evaluate_walk_forward_store(
    work_dir: str | Path,
    *,
    horizon: int = 10,
    train_bars: int | None = None,
    validation_bars: int | None = None,
    gap_bars: int | None = None,
    step_bars: int | None = None,
    top_quantile: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """逐折训练 V2 候选规则，再只在后续验证窗评分。"""
    work_dir = Path(work_dir)
    files = list(work_dir.glob("chunk_*.parquet"))
    if not files:
        raise FileNotFoundError(
            f"没有找到特征研究样本: {work_dir}。请先运行 python main.py features"
        )

    if horizon not in HORIZONS:
        raise ValueError(f"horizon 必须是 {HORIZONS} 之一")

    target = f"fwd_ret_{horizon}d"
    train_bars = int(train_bars or SETTINGS.walk_forward_train_bars)
    validation_bars = int(
        validation_bars or SETTINGS.walk_forward_validation_bars
    )
    requested_gap = int(
        SETTINGS.walk_forward_gap_bars if gap_bars is None else gap_bars
    )
    effective_gap = max(requested_gap, int(horizon))
    step_bars = int(step_bars or SETTINGS.walk_forward_step_bars)
    top_quantile = float(
        top_quantile or SETTINGS.walk_forward_top_quantile
    )

    glob_path = _glob_path(work_dir)
    con = duckdb.connect()
    try:
        dates = _available_dates(con, glob_path)
        windows = walk_forward_windows(
            dates,
            train_bars=train_bars,
            validation_bars=validation_bars,
            gap_bars=effective_gap,
            step_bars=step_bars,
        )

        fold_rows: list[dict] = []
        rule_rows: list[dict] = []

        for fold_no, window in enumerate(windows, start=1):
            rules = _fit_fold_rules(con, glob_path, window, target)
            validation = _load_validation_frame(
                con,
                glob_path,
                window,
                rules,
                target,
            )
            metrics = _evaluate_fold(
                validation,
                rules,
                target,
                top_quantile,
            )

            fold_rows.append(
                {
                    "折次": fold_no,
                    "训练开始": window.train_start.isoformat(),
                    "训练结束": window.train_end.isoformat(),
                    "验证开始": window.validation_start.isoformat(),
                    "验证结束": window.validation_end.isoformat(),
                    "Gap自然日": window.gap_days,
                    "预测窗口": f"{horizon}日",
                    "规则数量": len(rules),
                    "规则": "、".join(rule.feature for rule in rules),
                    **metrics,
                }
            )

            for rank, rule in enumerate(rules, start=1):
                rule_rows.append(
                    {
                        "折次": fold_no,
                        "排名": rank,
                        "特征": rule.feature,
                        "类型": rule.kind,
                        "方向": rule.direction,
                        "权重": rule.weight,
                        "质量分": round(rule.quality, 4),
                        "训练收益差%": round(rule.train_effect, 4),
                        "训练IC": round(rule.train_ic, 4)
                        if rule.train_ic is not None
                        else np.nan,
                        "低阈值": rule.low_threshold,
                        "高阈值": rule.high_threshold,
                    }
                )
    finally:
        con.close()

    folds = pd.DataFrame(fold_rows)
    rules_df = pd.DataFrame(rule_rows)

    if folds.empty:
        summary = {
            "折数": 0,
            "正超额折数": 0,
            "正超额占比%": 0.0,
            "超额收益均值%": 0.0,
            "超额收益中位数%": 0.0,
            "评分IC均值": 0.0,
            "研究状态": "样本不足",
            "说明": "交易日期不足以生成 Walk-Forward 窗口。",
        }
        return folds, rules_df, summary

    excess = pd.to_numeric(folds["超额收益%"], errors="coerce").dropna()
    ic = pd.to_numeric(folds["评分IC"], errors="coerce").dropna()
    positive_folds = int((excess > 0).sum())
    positive_ratio = (
        positive_folds / len(excess) * 100.0 if len(excess) else 0.0
    )
    mean_excess = float(excess.mean()) if len(excess) else 0.0
    median_excess = float(excess.median()) if len(excess) else 0.0
    mean_ic = float(ic.mean()) if len(ic) else 0.0

    if len(folds) < 3:
        status = "样本不足"
        note = "至少需要 3 个滚动验证折，暂不进入正式评分。"
    elif (
        positive_ratio >= 60.0
        and mean_excess > 0
        and median_excess > 0
        and mean_ic >= 0
    ):
        status = "可进入正式回测候选"
        note = "Walk-Forward 基础稳定性通过；下一步仍需真实成交回测，不自动启用 V2。"
    else:
        status = "继续观察"
        note = "滚动样本外表现尚不稳定，V2 保持研究状态。"

    summary = {
        "折数": int(len(folds)),
        "正超额折数": positive_folds,
        "正超额占比%": round(positive_ratio, 2),
        "超额收益均值%": round(mean_excess, 4),
        "超额收益中位数%": round(median_excess, 4),
        "评分IC均值": round(mean_ic, 4),
        "研究状态": status,
        "说明": note,
        "有效Gap交易日": effective_gap,
        "预测窗口": horizon,
    }
    return folds, rules_df, summary


def run_walk_forward(
    *,
    horizon: int = 10,
) -> tuple[pd.DataFrame, dict]:
    ensure_directories()
    work_dir = SETTINGS.data_store_dir / "research" / "feature_validation"
    folds, rules, summary = evaluate_walk_forward_store(
        work_dir,
        horizon=horizon,
    )

    folds.to_csv(
        SETTINGS.report_dir / "walk_forward_folds.csv",
        index=False,
        encoding="utf-8-sig",
    )
    rules.to_csv(
        SETTINGS.report_dir / "walk_forward_rules.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame([summary]).to_csv(
        SETTINGS.report_dir / "walk_forward_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    model_dir = SETTINGS.data_store_dir / "research" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "walk_forward_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return folds, summary
