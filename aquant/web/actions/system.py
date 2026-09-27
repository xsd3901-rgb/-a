from __future__ import annotations

from aquant.research.local_prepare import run_local_prepare
from aquant.research.readiness import release_readiness
from aquant.web.action_context import ActionContext
from aquant.web.serializers import records
from eastmoney_formula import write_formula


def prepare_local_action(ctx: ActionContext) -> dict:
    validation_limit = (
        200
        if ctx.limit is None
        else max(1, min(int(ctx.limit), 500))
    )
    steps, summary = run_local_prepare(
        bootstrap_limit=ctx.limit,
        validation_limit=validation_limit,
        horizon=ctx.horizon,
        refresh=ctx.refresh,
        progress=ctx.progress,
    )
    return {
        "summary": summary,
        "rows": records(steps, 20),
    }


def readiness_action(ctx: ActionContext) -> dict:
    frame, summary = release_readiness(audit_limit=ctx.limit)
    return {
        "summary": summary,
        "rows": records(frame, 50),
    }


def formula_action(ctx: ActionContext) -> dict:
    del ctx
    path = write_formula()
    return {
        "summary": {
            "状态": "已导出",
            "文件": path,
            "说明": (
                "东方财富公式是 V1 参考版；ST、停牌和历史交易制度"
                "仍以本地系统为准。"
            ),
        }
    }


ACTIONS = {
    "prepare_local": prepare_local_action,
    "readiness": readiness_action,
    "formula": formula_action,
}
