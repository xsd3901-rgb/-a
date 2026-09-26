from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from aquant.research.data_audit import run_data_audit
from aquant.research.readiness import release_readiness
from aquant.research.system_validation import run_system_validation
from bootstrap import bootstrap_market
from config import SETTINGS, ensure_directories


def run_local_prepare(
    *,
    bootstrap_limit: int | None = None,
    validation_limit: int | None = 200,
    horizon: int = 10,
    refresh: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """首次本地部署后的正式准备流程。

    流程：
    1. 建立/增量补齐本地沪深行情库；
    2. 审计完整性；
    3. 运行研究验收（特征 -> WF -> V1/V2 -> V1回测）；
    4. 最终就绪检查。

    不执行参数优化，不自动晋级 V2，也不会修改模型选择。
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
        _, bootstrap_summary = bootstrap_market(
            limit=bootstrap_limit,
            refresh=refresh,
        )
        ok = int(bootstrap_summary.get("成功股票", 0))
        total = int(bootstrap_summary.get("股票池", 0))
        failed = int(bootstrap_summary.get("失败股票", 0))
        record(
            "本地建库",
            "OK" if ok > 0 else "FAILED",
            f"成功 {ok}/{total}，失败 {failed}",
        )
        if ok <= 0:
            raise RuntimeError("本地建库没有成功写入任何股票数据")
    except Exception as exc:
        if not rows or rows[-1]["步骤"] != "本地建库":
            record("本地建库", "FAILED", str(exc)[:300])
        status = pd.DataFrame(rows)
        _persist(status, {
            "总体状态": "首次准备失败",
            "停止步骤": "本地建库",
            "说明": str(exc),
        })
        raise

    audit_details, audit_summary = run_data_audit(limit=bootstrap_limit)
    audit_state = str(audit_summary.get("状态", "未知"))
    audit_ok = audit_state in {"通过", "可用但有警告"}
    record(
        "本地数据审计",
        "OK" if audit_ok else "FAILED",
        f"{audit_state} | FAIL {audit_summary.get('FAIL', 0)} | "
        f"覆盖率中位数 {audit_summary.get('覆盖率中位数%', 0)}%",
    )
    if not audit_ok:
        status = pd.DataFrame(rows)
        summary = {
            "总体状态": "首次准备未通过",
            "停止步骤": "本地数据审计",
            "说明": (
                "本地行情存在明显缺口或滞后。请重新执行建库/刷新，"
                "不要带着残缺数据继续做正式验收。"
            ),
        }
        _persist(status, summary)
        return status, summary

    comparison, validation_summary = run_system_validation(
        feature_limit=validation_limit,
        backtest_limit=validation_limit,
        horizon=horizon,
        refresh=False,
    )
    validation_state = str(validation_summary.get("总体状态", ""))
    validation_ok = validation_state.startswith("系统链路通过")
    record(
        "系统研究验收",
        "OK" if validation_ok else "FAILED",
        validation_state,
    )

    readiness_frame, readiness_summary = release_readiness(
        audit_limit=bootstrap_limit,
    )
    ready_state = str(readiness_summary.get("总体状态", ""))
    ready_ok = ready_state == "本地运行链路已就绪"
    record(
        "最终就绪检查",
        "OK" if ready_ok else "PENDING",
        ready_state,
    )

    status = pd.DataFrame(rows)
    if validation_ok and ready_ok:
        overall = "首次准备完成"
        note = "本地数据库、研究验收和网页运行前检查均已完成。"
    elif validation_ok:
        overall = "研究验收完成，仍有就绪项待处理"
        note = str(readiness_summary.get("下一步", ""))
    else:
        overall = "首次准备未完全通过"
        note = "请查看 local_prepare_steps.csv 与系统验收报告。"

    summary = {
        "总体状态": overall,
        "建库股票限制": bootstrap_limit,
        "验收股票限制": validation_limit,
        "预测窗口": horizon,
        "数据审计": audit_state,
        "系统验收": validation_state,
        "就绪状态": ready_state,
        "活动模型": readiness_summary.get("活动模型", "V1"),
        "V2状态": readiness_summary.get("V2状态", ""),
        "自动晋级": False,
        "说明": note,
    }
    _persist(status, summary)
    return status, summary


def _persist(status: pd.DataFrame, summary: dict) -> None:
    status.to_csv(
        SETTINGS.report_dir / "local_prepare_steps.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (SETTINGS.report_dir / "local_prepare_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
