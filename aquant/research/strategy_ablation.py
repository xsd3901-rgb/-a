from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from config import SETTINGS, ensure_directories
from profile import load_strategy_profile
from strategy import V1_COMPONENT_COLUMNS, V1_COMPONENT_SPECS


HORIZONS = (5, 10, 20)


def _glob_path(work_dir: Path) -> str:
    return (
        work_dir / "chunk_*.parquet"
    ).as_posix().replace("'", "''")


def _clip_score(expr: str) -> str:
    return f"LEAST(100, GREATEST(0, ({expr})))"


def _split_date(
    con: duckdb.DuckDBPyConnection,
    glob_path: str,
) -> pd.Timestamp:
    dates = con.execute(
        f"""
        SELECT DISTINCT date
        FROM read_parquet(
            '{glob_path}',
            union_by_name=true
        )
        WHERE date IS NOT NULL
        ORDER BY date
        """
    ).df()
    if len(dates) < 10:
        raise RuntimeError("研究样本交易日不足，无法执行策略消融")

    series = (
        pd.to_datetime(
            dates["date"],
            errors="coerce",
        )
        .dropna()
        .dt.normalize()
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )
    split_pos = min(
        len(series) - 2,
        max(
            1,
            int(
                len(series)
                * float(
                    SETTINGS.feature_validation_train_ratio
                )
            )
            - 1,
        ),
    )
    return pd.Timestamp(series.iloc[split_pos]).normalize()


def _available_columns(
    con: duckdb.DuckDBPyConnection,
    glob_path: str,
) -> set[str]:
    schema = con.execute(
        f"""
        DESCRIBE SELECT *
        FROM read_parquet(
            '{glob_path}',
            union_by_name=true
        )
        """
    ).df()
    return set(schema["column_name"].astype(str))


def _evaluate_one(
    con: duckdb.DuckDBPyConnection,
    glob_path: str,
    *,
    component: str,
    target: str,
    split_date: pd.Timestamp,
    threshold: int,
) -> dict:
    raw_expr = " + ".join(
        f'COALESCE(CAST("{col}" AS DOUBLE), 0)'
        for col in V1_COMPONENT_COLUMNS
    )
    full_score = _clip_score(raw_expr)
    ablated_raw = (
        f"({raw_expr}) - "
        f'COALESCE(CAST("{component}" AS DOUBLE), 0)'
    )
    ablated_score = _clip_score(ablated_raw)

    weight = int(
        V1_COMPONENT_SPECS[component]["权重"]
    )
    if weight >= 0:
        marginal_condition = (
            f"({full_score} >= {threshold}) "
            f"AND ({ablated_score} < {threshold})"
        )
        helpful_multiplier = 1.0
        marginal_type = "规则新增入选"
    else:
        marginal_condition = (
            f"({full_score} < {threshold}) "
            f"AND ({ablated_score} >= {threshold})"
        )
        helpful_multiplier = -1.0
        marginal_type = "惩罚排除样本"

    query = f"""
        WITH sample AS (
            SELECT
                CAST("{target}" AS DOUBLE) AS target,
                {full_score} AS full_score,
                {ablated_score} AS ablated_score,
                COALESCE(
                    CAST("{component}" AS DOUBLE),
                    0
                ) AS component_value
            FROM read_parquet(
                '{glob_path}',
                union_by_name=true
            )
            WHERE date > DATE '{split_date:%Y-%m-%d}'
              AND "{target}" IS NOT NULL
        )
        SELECT
            COUNT(*) AS validation_n,
            SUM(
                CASE WHEN component_value != 0
                THEN 1 ELSE 0 END
            ) AS triggered_n,
            SUM(
                CASE WHEN full_score >= {threshold}
                THEN 1 ELSE 0 END
            ) AS full_selected_n,
            SUM(
                CASE WHEN ablated_score >= {threshold}
                THEN 1 ELSE 0 END
            ) AS ablated_selected_n,
            SUM(
                CASE WHEN
                    (full_score >= {threshold})
                    !=
                    (ablated_score >= {threshold})
                THEN 1 ELSE 0 END
            ) AS changed_n,
            AVG(
                CASE WHEN full_score >= {threshold}
                THEN target END
            ) AS full_avg_ret,
            AVG(
                CASE WHEN ablated_score >= {threshold}
                THEN target END
            ) AS ablated_avg_ret,
            AVG(
                CASE WHEN {marginal_condition}
                THEN target END
            ) AS marginal_avg_ret,
            AVG(
                CASE WHEN full_score >= {threshold}
                THEN CASE WHEN target > 0
                    THEN 1.0 ELSE 0.0 END
                END
            ) AS full_hit,
            AVG(
                CASE WHEN ablated_score >= {threshold}
                THEN CASE WHEN target > 0
                    THEN 1.0 ELSE 0.0 END
                END
            ) AS ablated_hit
        FROM sample
    """
    row = con.execute(query).fetchone()
    if row is None:
        return {}

    validation_n = int(row[0] or 0)
    triggered_n = int(row[1] or 0)
    full_selected_n = int(row[2] or 0)
    ablated_selected_n = int(row[3] or 0)
    changed_n = int(row[4] or 0)
    full_avg = (
        float(row[5])
        if row[5] is not None
        else math.nan
    )
    ablated_avg = (
        float(row[6])
        if row[6] is not None
        else math.nan
    )
    marginal_avg = (
        float(row[7])
        if row[7] is not None
        else math.nan
    )
    full_hit = (
        float(row[8]) * 100.0
        if row[8] is not None
        else math.nan
    )
    ablated_hit = (
        float(row[9]) * 100.0
        if row[9] is not None
        else math.nan
    )

    full_delta = (
        full_avg - ablated_avg
        if math.isfinite(full_avg)
        and math.isfinite(ablated_avg)
        else math.nan
    )
    marginal_help = (
        marginal_avg * helpful_multiplier
        if math.isfinite(marginal_avg)
        else math.nan
    )

    if changed_n < 50:
        horizon_state = "边际样本不足"
    elif math.isfinite(marginal_help):
        if marginal_help > 0.10:
            horizon_state = "边际有帮助"
        elif marginal_help < -0.10:
            horizon_state = "边际拖累"
        else:
            horizon_state = "边际接近中性"
    else:
        horizon_state = "无法判断"

    return {
        "验证样本": validation_n,
        "触发样本": triggered_n,
        "完整策略入选": full_selected_n,
        "移除规则后入选": ablated_selected_n,
        "边际改变样本": changed_n,
        "完整策略平均收益%": round(
            full_avg, 4
        )
        if math.isfinite(full_avg)
        else np.nan,
        "移除规则后平均收益%": round(
            ablated_avg, 4
        )
        if math.isfinite(ablated_avg)
        else np.nan,
        "完整减消融收益差%": round(
            full_delta, 4
        )
        if math.isfinite(full_delta)
        else np.nan,
        "边际样本平均收益%": round(
            marginal_avg, 4
        )
        if math.isfinite(marginal_avg)
        else np.nan,
        "边际帮助%": round(
            marginal_help, 4
        )
        if math.isfinite(marginal_help)
        else np.nan,
        "完整策略正收益率%": round(
            full_hit, 2
        )
        if math.isfinite(full_hit)
        else np.nan,
        "消融后正收益率%": round(
            ablated_hit, 2
        )
        if math.isfinite(ablated_hit)
        else np.nan,
        "边际类型": marginal_type,
        "窗口结论": horizon_state,
    }


def _summarize(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for component, group in detail.groupby(
        "规则列",
        sort=False,
    ):
        spec = V1_COMPONENT_SPECS[component]
        marginal = pd.to_numeric(
            group["边际帮助%"],
            errors="coerce",
        ).dropna()
        changed = pd.to_numeric(
            group["边际改变样本"],
            errors="coerce",
        ).fillna(0)

        supported = int(
            (
                group["窗口结论"]
                == "边际有帮助"
            ).sum()
        )
        dragged = int(
            (
                group["窗口结论"]
                == "边际拖累"
            ).sum()
        )

        median_help = (
            float(marginal.median())
            if not marginal.empty
            else math.nan
        )

        if int(changed.sum()) < 100:
            conclusion = "样本不足，继续观察"
        elif supported >= 2 and dragged == 0:
            conclusion = "规则贡献稳定"
        elif dragged >= 2 and supported == 0:
            conclusion = "规则需要重点复核"
        elif supported > dragged:
            conclusion = "总体偏正向"
        elif dragged > supported:
            conclusion = "总体偏拖累"
        else:
            conclusion = "不同窗口表现混合"

        rows.append(
            {
                "规则列": component,
                "规则": spec["名称"],
                "类别": spec["类别"],
                "当前权重": int(spec["权重"]),
                "正向窗口数": supported,
                "拖累窗口数": dragged,
                "边际改变样本合计": int(
                    changed.sum()
                ),
                "边际帮助中位数%": round(
                    median_help, 4
                )
                if math.isfinite(median_help)
                else np.nan,
                "研究结论": conclusion,
            }
        )

    result = pd.DataFrame(rows)
    if not result.empty:
        order = {
            "规则需要重点复核": 0,
            "总体偏拖累": 1,
            "不同窗口表现混合": 2,
            "样本不足，继续观察": 3,
            "总体偏正向": 4,
            "规则贡献稳定": 5,
        }
        result["_order"] = (
            result["研究结论"]
            .map(order)
            .fillna(9)
        )
        result = (
            result.sort_values(
                [
                    "_order",
                    "边际帮助中位数%",
                ],
                ascending=[True, True],
            )
            .drop(columns="_order")
            .reset_index(drop=True)
        )
    return result


def run_v1_ablation(
    *,
    work_dir: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """用时间后段样本做 V1 逐规则消融，不自动改正式权重。"""
    ensure_directories()
    work_dir = Path(
        work_dir
        or (
            SETTINGS.data_store_dir
            / "research"
            / "feature_validation"
        )
    )
    files = list(
        work_dir.glob("chunk_*.parquet")
    )
    if not files:
        raise FileNotFoundError(
            "没有特征研究样本。请先运行 "
            "python main.py features --limit 200"
        )

    glob_path = _glob_path(work_dir)
    con = duckdb.connect()
    try:
        available = _available_columns(
            con,
            glob_path,
        )
        missing = [
            col
            for col in V1_COMPONENT_COLUMNS
            if col not in available
        ]
        if missing:
            raise RuntimeError(
                "现有研究缓存还没有 V1 规则贡献列。"
                "请运行 python main.py features "
                "--limit 200 --refresh 重新生成一次。"
            )

        split_date = _split_date(
            con,
            glob_path,
        )
        threshold = int(
            load_strategy_profile()["score_threshold"]
        )

        detail_rows: list[dict] = []
        for component in V1_COMPONENT_COLUMNS:
            spec = V1_COMPONENT_SPECS[component]
            for horizon in HORIZONS:
                target = f"fwd_ret_{horizon}d"
                if target not in available:
                    continue
                metrics = _evaluate_one(
                    con,
                    glob_path,
                    component=component,
                    target=target,
                    split_date=split_date,
                    threshold=threshold,
                )
                if not metrics:
                    continue
                detail_rows.append(
                    {
                        "规则列": component,
                        "规则": spec["名称"],
                        "类别": spec["类别"],
                        "当前权重": int(
                            spec["权重"]
                        ),
                        "预测窗口": f"{horizon}日",
                        "验证切分日": (
                            split_date.strftime(
                                "%Y-%m-%d"
                            )
                        ),
                        **metrics,
                    }
                )
    finally:
        con.close()

    detail = pd.DataFrame(detail_rows)
    summary_table = _summarize(detail)

    detail.to_csv(
        SETTINGS.report_dir
        / "strategy_ablation_v1.csv",
        index=False,
        encoding="utf-8-sig",
    )
    summary_table.to_csv(
        SETTINGS.report_dir
        / "strategy_ablation_v1_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    review_count = (
        int(
            summary_table["研究结论"]
            .isin(
                [
                    "规则需要重点复核",
                    "总体偏拖累",
                ]
            )
            .sum()
        )
        if not summary_table.empty
        else 0
    )
    stable_count = (
        int(
            summary_table["研究结论"]
            .isin(
                [
                    "规则贡献稳定",
                    "总体偏正向",
                ]
            )
            .sum()
        )
        if not summary_table.empty
        else 0
    )

    summary = {
        "状态": "V1规则消融完成",
        "验证切分日": (
            detail["验证切分日"].iloc[0]
            if not detail.empty
            else ""
        ),
        "评分阈值": int(
            load_strategy_profile()["score_threshold"]
        ),
        "规则数量": len(
            V1_COMPONENT_COLUMNS
        ),
        "稳定/偏正向规则": stable_count,
        "需重点复核/偏拖累规则": review_count,
        "自动修改权重": False,
        "说明": (
            "消融只用于识别规则边际贡献。"
            "正式 V1 权重保持不变，"
            "后续只有样本外和真实成交验证通过后才调整。"
        ),
    }
    (
        SETTINGS.report_dir
        / "strategy_ablation_v1_summary.json"
    ).write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return detail, summary_table, summary
