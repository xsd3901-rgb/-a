from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from aquant.research.feature_selection import build_v2_candidate
from aquant.research.data_audit import run_data_audit
from aquant.research.feature_validation import run_feature_validation
from aquant.research.model_compare import run_model_comparison
from aquant.research.portfolio import simulate_portfolio
from aquant.research.walk_forward import run_walk_forward
from backtest import run_backtest
from config import SETTINGS, ensure_directories
from evaluator import evaluate_trades


def run_system_validation(
    *,
    feature_limit: int | None = 200,
    backtest_limit: int | None = 200,
    horizon: int = 10,
    refresh: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """执行正式交付前的一键研究验证链路。

    该流程不会自动修改 V1/V2 晋级状态，也不会自动运行参数优化。
    它只把当前数据上的特征、Walk-Forward、模型对比和真实成交回测
    串成一次可复现验收。
    """
    ensure_directories()
    rows: list[dict] = []

    def record(step: str, status: str, detail: str = "") -> None:
        rows.append(
            {
                "步骤": step,
                "状态": status,
                "说明": detail,
                "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    try:
        audit_details, audit_summary = run_data_audit(limit=backtest_limit)
        audit_state = str(audit_summary.get("状态", ""))
        audit_ok = (
            audit_state in {"通过", "可用但有警告"}
            and audit_summary.get("股票池来源") == "security_lifecycle"
        )
        record(
            "本地数据审计",
            "OK" if audit_ok else "FAILED",
            f"{audit_state} | 检查 {audit_summary.get('检查股票数', 0)} 只 | "
            f"FAIL {audit_summary.get('FAIL', 0)}",
        )
        if not audit_ok:
            raise RuntimeError(
                "本地行情完整性未通过，请先执行 bootstrap/修复后再验收。"
            )
    except Exception as exc:
        if not rows or rows[-1]["步骤"] != "本地数据审计":
            record("本地数据审计", "FAILED", str(exc))
        raise

    try:
        features = run_feature_validation(
            limit=feature_limit,
            refresh=refresh,
        )
        record("特征有效性", "OK", f"结果 {len(features)} 行")
    except Exception as exc:
        record("特征有效性", "FAILED", str(exc))
        raise

    try:
        selected, candidate = build_v2_candidate()
        record(
            "V2候选特征",
            "OK" if not selected.empty else "NO_CANDIDATE",
            f"候选特征 {len(selected)} 个，规则 {len(candidate.get('rules', []))} 条",
        )
    except Exception as exc:
        record("V2候选特征", "FAILED", str(exc))
        raise

    try:
        folds, wf_summary = run_walk_forward(horizon=horizon)
        record(
            "Walk-Forward",
            "OK" if not folds.empty else "INSUFFICIENT",
            f"{wf_summary.get('研究状态', '')} | 折数 {wf_summary.get('折数', 0)}",
        )
    except Exception as exc:
        record("Walk-Forward", "FAILED", str(exc))
        raise

    comparison = pd.DataFrame()
    compare_summary: dict = {}
    try:
        comparison, compare_summary = run_model_comparison(horizon=horizon)
        record(
            "V1/V2真实成交对比",
            "OK",
            str(compare_summary.get("状态", "")),
        )
    except Exception as exc:
        record("V1/V2真实成交对比", "FAILED", str(exc))
        raise

    try:
        trades = run_backtest(
            limit=backtest_limit,
            refresh=False,
            persist=True,
        )
        trade_metrics = evaluate_trades(trades)
        _, _, portfolio_metrics = simulate_portfolio(trades)
        record(
            "V1正式回测",
            "OK",
            f"交易 {trade_metrics.get('交易次数', 0)} 笔，"
            f"组合收益 {portfolio_metrics.get('组合收益%', 0)}%",
        )
    except Exception as exc:
        record("V1正式回测", "FAILED", str(exc))
        raise

    status_frame = pd.DataFrame(rows)

    failed = int((status_frame["状态"] == "FAILED").sum()) if not status_frame.empty else 0
    candidate_state = compare_summary.get("状态", "")
    if failed:
        overall = "未通过"
    elif candidate_state == "V2通过晋级候选检查":
        overall = "系统链路通过，V2进入候选"
    else:
        overall = "系统链路通过，继续使用V1"

    summary = {
        "总体状态": overall,
        "验证时间": datetime.now().isoformat(timespec="seconds"),
        "预测窗口": horizon,
        "特征样本股票数": feature_limit,
        "正式回测股票数": backtest_limit,
        "V2对比状态": candidate_state,
        "WalkForward状态": wf_summary.get("研究状态", ""),
        "说明": (
            "该验收只基于当前本地历史数据；不会自动承诺收益，"
            "也不会在没有通过对照验证时替换正式模型。"
        ),
    }

    status_frame.to_csv(
        SETTINGS.report_dir / "system_validation_steps.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (SETTINGS.report_dir / "system_validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return comparison, summary
