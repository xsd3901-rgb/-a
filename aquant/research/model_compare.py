from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from aquant.models.score_v2 import CandidateScoreV2, FeatureRule
from aquant.research.portfolio import simulate_portfolio
from aquant.research.walk_forward import evaluate_walk_forward_store
from backtest import backtest_scored_stock
from config import SETTINGS, ensure_directories
from evaluator import evaluate_trades
from profile import load_strategy_profile


BASE_COLUMNS = [
    "code",
    "name",
    "listing_date",
    "date",
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "turnover",
    "pct_change",
    "trade_status",
    "is_st",
    "atr14",
    "atr_pct",
    "ma10",
    "macd_dif",
    "macd_dea",
    "score",
    "exec_open",
    "exec_high",
    "exec_low",
    "exec_close",
    "exec_preclose",
    "exec_volume",
    "exec_amount",
    "exec_turnover",
    "exec_pct_change",
    "exec_trade_status",
    "exec_is_st",
]


def _feature_rules_for_fold(rules_df: pd.DataFrame, fold_no: int) -> list[FeatureRule]:
    subset = rules_df[rules_df["折次"] == fold_no].sort_values("排名")
    rules: list[FeatureRule] = []
    for _, row in subset.iterrows():
        rules.append(
            FeatureRule(
                feature=str(row["特征"]),
                kind=str(row["类型"]),
                direction=str(row["方向"]),
                weight=float(row["权重"]),
                low_threshold=(
                    None if pd.isna(row["低阈值"]) else float(row["低阈值"])
                ),
                high_threshold=(
                    None if pd.isna(row["高阈值"]) else float(row["高阈值"])
                ),
                quality=float(row["质量分"]),
                train_effect=float(row["训练收益差%"]),
                train_ic=(
                    None if pd.isna(row["训练IC"]) else float(row["训练IC"])
                ),
            )
        )
    return rules


def _tail_end_date(
    dates: pd.Series,
    validation_end: pd.Timestamp,
    extra_bars: int,
) -> pd.Timestamp:
    positions = dates[dates >= validation_end]
    if positions.empty:
        return validation_end
    end_pos = int(positions.index[0])
    final_pos = min(len(dates) - 1, end_pos + int(extra_bars))
    return pd.Timestamp(dates.iloc[final_pos]).normalize()


def _load_fold_frame(
    con: duckdb.DuckDBPyConnection,
    glob_path: str,
    rules: list[FeatureRule],
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.DataFrame:
    columns = list(BASE_COLUMNS)
    for rule in rules:
        if rule.feature not in columns:
            columns.append(rule.feature)

    quoted = ", ".join(f'"{col}"' for col in columns)
    try:
        return con.execute(
            f"""
            SELECT {quoted}
            FROM read_parquet('{glob_path}', union_by_name=true)
            WHERE date >= DATE '{start_date:%Y-%m-%d}'
              AND date <= DATE '{end_date:%Y-%m-%d}'
            ORDER BY code, date
            """
        ).df()
    except Exception:
        # 兼容旧研究缓存：逐列探测实际存在的字段。
        schema = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{glob_path}', union_by_name=true)"
        ).df()
        available = set(schema["column_name"].astype(str))
        actual = [col for col in columns if col in available]
        quoted = ", ".join(f'"{col}"' for col in actual)
        return con.execute(
            f"""
            SELECT {quoted}
            FROM read_parquet('{glob_path}', union_by_name=true)
            WHERE date >= DATE '{start_date:%Y-%m-%d}'
              AND date <= DATE '{end_date:%Y-%m-%d}'
            ORDER BY code, date
            """
        ).df()


def _prepare_v2_signals(
    frame: pd.DataFrame,
    rules: list[FeatureRule],
    validation_start: pd.Timestamp,
    validation_end: pd.Timestamp,
) -> pd.DataFrame:
    model = CandidateScoreV2(rules)
    out = model.predict(frame)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    in_validation = out["date"].between(validation_start, validation_end)
    out["v2_signal"] = False

    valid = out.loc[in_validation & out["v2_score"].notna()].copy()
    if not valid.empty:
        valid["_rank"] = valid.groupby("date")["v2_score"].rank(
            pct=True,
            method="average",
        )
        selected_idx = valid.index[
            valid["_rank"] >= (1.0 - SETTINGS.walk_forward_top_quantile)
        ]
        out.loc[selected_idx, "v2_signal"] = True
    return out


def _backtest_fold(
    frame: pd.DataFrame,
    rules: list[FeatureRule],
    validation_start: pd.Timestamp,
    validation_end: pd.Timestamp,
    fold_no: int,
) -> tuple[list[dict], list[dict]]:
    profile = load_strategy_profile()
    v1_trades: list[dict] = []
    v2_trades: list[dict] = []
    v2_frame = _prepare_v2_signals(
        frame,
        rules,
        validation_start,
        validation_end,
    )

    for code, group in frame.groupby("code", sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        if len(group) < 2:
            continue

        name = str(group["name"].dropna().iloc[0]) if "name" in group else str(code)
        listing_date = None
        if "listing_date" in group.columns:
            listed = pd.to_datetime(group["listing_date"], errors="coerce").dropna()
            if not listed.empty:
                listing_date = listed.iloc[0]

        v1 = backtest_scored_stock(
            code=str(code),
            name=name,
            scored=group,
            score_threshold=int(profile["score_threshold"]),
            stop_atr_multiple=float(profile["stop_atr_multiple"]),
            target_atr_multiple=float(profile["target_atr_multiple"]),
            max_hold_days=int(profile["max_hold_days"]),
            listing_date=listing_date,
            signal_start_date=validation_start,
            signal_end_date=validation_end,
            score_column="score",
            model_name="V1",
            min_signal_index=0,
        )
        for trade in v1:
            trade["折次"] = fold_no
        v1_trades.extend(v1)

        v2_group = v2_frame[v2_frame["code"].astype(str) == str(code)].copy()
        if len(v2_group) < 2:
            continue
        v2_group = v2_group.sort_values("date").reset_index(drop=True)
        v2 = backtest_scored_stock(
            code=str(code),
            name=name,
            scored=v2_group,
            score_threshold=0,
            stop_atr_multiple=float(profile["stop_atr_multiple"]),
            target_atr_multiple=float(profile["target_atr_multiple"]),
            max_hold_days=int(profile["max_hold_days"]),
            listing_date=listing_date,
            signal_start_date=validation_start,
            signal_end_date=validation_end,
            score_column="v2_score",
            signal_flag_column="v2_signal",
            model_name="V2",
            min_signal_index=0,
        )
        for trade in v2:
            trade["折次"] = fold_no
        v2_trades.extend(v2)

    return v1_trades, v2_trades


def run_model_comparison(
    *,
    horizon: int = 10,
) -> tuple[pd.DataFrame, dict]:
    """在同一 Walk-Forward 验证折中，用同一成交引擎比较 V1 与 V2。"""
    ensure_directories()
    work_dir = SETTINGS.data_store_dir / "research" / "feature_validation"
    files = list(work_dir.glob("chunk_*.parquet"))
    if not files:
        raise FileNotFoundError(
            "没有找到研究样本。请先运行 python main.py features --limit 200"
        )

    folds, rules_df, wf_summary = evaluate_walk_forward_store(
        work_dir,
        horizon=horizon,
    )
    if folds.empty:
        raise RuntimeError("没有足够历史数据生成 Walk-Forward 比较区间")

    glob_path = (work_dir / "chunk_*.parquet").as_posix().replace("'", "''")
    con = duckdb.connect()
    try:
        dates = con.execute(
            f"""
            SELECT DISTINCT date
            FROM read_parquet('{glob_path}', union_by_name=true)
            WHERE date IS NOT NULL
            ORDER BY date
            """
        ).df()
        trade_dates = (
            pd.to_datetime(dates["date"], errors="coerce")
            .dropna()
            .dt.normalize()
            .drop_duplicates()
            .sort_values()
            .reset_index(drop=True)
        )

        all_v1: list[dict] = []
        all_v2: list[dict] = []

        for _, fold in folds.iterrows():
            fold_no = int(fold["折次"])
            rules = _feature_rules_for_fold(rules_df, fold_no)
            if not rules:
                continue

            validation_start = pd.Timestamp(fold["验证开始"]).normalize()
            validation_end = pd.Timestamp(fold["验证结束"]).normalize()
            load_end = _tail_end_date(
                trade_dates,
                validation_end,
                int(load_strategy_profile()["max_hold_days"]) + 10,
            )
            frame = _load_fold_frame(
                con,
                glob_path,
                rules,
                validation_start,
                load_end,
            )
            if frame.empty or "score" not in frame.columns:
                raise RuntimeError(
                    "当前研究缓存缺少 V1/执行字段，请重新运行 python main.py features"
                )

            v1, v2 = _backtest_fold(
                frame,
                rules,
                validation_start,
                validation_end,
                fold_no,
            )
            all_v1.extend(v1)
            all_v2.extend(v2)
    finally:
        con.close()

    v1_trades = pd.DataFrame(all_v1)
    v2_trades = pd.DataFrame(all_v2)

    v1_metrics = evaluate_trades(v1_trades)
    v2_metrics = evaluate_trades(v2_trades)
    v1_exec, v1_equity, v1_portfolio = simulate_portfolio(v1_trades)
    v2_exec, v2_equity, v2_portfolio = simulate_portfolio(v2_trades)

    comparison = pd.DataFrame(
        [
            {
                "模型": "V1",
                **{f"独立_{k}": v for k, v in v1_metrics.items() if k != "说明"},
                **{f"组合_{k}": v for k, v in v1_portfolio.items() if k != "说明"},
            },
            {
                "模型": "V2",
                **{f"独立_{k}": v for k, v in v2_metrics.items() if k != "说明"},
                **{f"组合_{k}": v for k, v in v2_portfolio.items() if k != "说明"},
            },
        ]
    )

    v2_trade_n = int(v2_metrics["交易次数"])
    v2_better_avg = float(v2_metrics["平均收益%"]) > float(v1_metrics["平均收益%"])
    v2_better_portfolio = float(v2_portfolio["组合收益%"]) > float(
        v1_portfolio["组合收益%"]
    )
    v2_drawdown_ok = float(v2_portfolio["最大账面成本回撤%"]) <= max(
        5.0,
        float(v1_portfolio["最大账面成本回撤%"]) * 1.10,
    )
    if (
        v2_trade_n >= 30
        and v2_better_avg
        and v2_better_portfolio
        and v2_drawdown_ok
        and wf_summary.get("研究状态") == "可进入正式回测候选"
    ):
        status = "V2通过晋级候选检查"
        note = "V2 可进入更长区间正式验证；系统仍不自动替换 V1。"
    else:
        status = "保持V1"
        note = "V2 尚未同时通过样本外收益、组合收益和风险约束。"

    summary = {
        "状态": status,
        "说明": note,
        "预测窗口": horizon,
        "WalkForward状态": wf_summary.get("研究状态", ""),
        "V1交易数": int(v1_metrics["交易次数"]),
        "V2交易数": int(v2_metrics["交易次数"]),
        "V1平均收益%": float(v1_metrics["平均收益%"]),
        "V2平均收益%": float(v2_metrics["平均收益%"]),
        "V1组合收益%": float(v1_portfolio["组合收益%"]),
        "V2组合收益%": float(v2_portfolio["组合收益%"]),
        "V1组合回撤%": float(v1_portfolio["最大账面成本回撤%"]),
        "V2组合回撤%": float(v2_portfolio["最大账面成本回撤%"]),
    }

    v1_trades.to_csv(
        SETTINGS.report_dir / "model_compare_v1_trades.csv",
        index=False,
        encoding="utf-8-sig",
    )
    v2_trades.to_csv(
        SETTINGS.report_dir / "model_compare_v2_trades.csv",
        index=False,
        encoding="utf-8-sig",
    )
    comparison.to_csv(
        SETTINGS.report_dir / "model_compare_metrics.csv",
        index=False,
        encoding="utf-8-sig",
    )
    v1_exec.to_csv(
        SETTINGS.report_dir / "portfolio_v1_executed.csv",
        index=False,
        encoding="utf-8-sig",
    )
    v2_exec.to_csv(
        SETTINGS.report_dir / "portfolio_v2_executed.csv",
        index=False,
        encoding="utf-8-sig",
    )
    v1_equity.to_csv(
        SETTINGS.report_dir / "portfolio_v1_equity.csv",
        index=False,
        encoding="utf-8-sig",
    )
    v2_equity.to_csv(
        SETTINGS.report_dir / "portfolio_v2_equity.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame([summary]).to_csv(
        SETTINGS.report_dir / "model_compare_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (SETTINGS.data_store_dir / "research" / "models").mkdir(
        parents=True,
        exist_ok=True,
    )
    (
        SETTINGS.data_store_dir
        / "research"
        / "models"
        / "model_compare_summary.json"
    ).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return comparison, summary
