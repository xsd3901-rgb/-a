from __future__ import annotations

from aquant.research.data_audit import run_data_audit
from aquant.research.data_repair import repair_failed_market_data
from aquant.data.reference_refresh import refresh_reference_data
from aquant.research.source_quality import run_source_quality
from aquant.web.action_context import ActionContext
from aquant.web.serializers import records
from bootstrap import bootstrap_market



def refresh_reference_action(ctx: ActionContext) -> dict:
    frame, summary, health = refresh_reference_data(
        progress=ctx.progress,
    )
    return {
        "summary": summary,
        "rows": records(frame, 20),
        "source_health": health,
    }


def bootstrap_action(ctx: ActionContext) -> dict:
    frame, summary = bootstrap_market(
        limit=ctx.limit,
        refresh=ctx.refresh,
        progress=ctx.progress,
    )
    return {
        "summary": summary,
        "rows": records(frame.tail(100), 100),
    }


def audit_data_action(ctx: ActionContext) -> dict:
    frame, summary = run_data_audit(limit=ctx.limit)
    problems = (
        frame[frame["状态"] != "OK"]
        if not frame.empty
        else frame
    )
    return {
        "summary": summary,
        "rows": records(problems, 100),
    }


def source_quality_action(ctx: ActionContext) -> dict:
    providers, usage, crosscheck, summary = run_source_quality(
        crosscheck_limit=ctx.limit,
        include_crosscheck=True,
    )
    problems = (
        crosscheck[crosscheck["状态"].astype(str) != "一致"]
        if not crosscheck.empty
        else crosscheck
    )
    return {
        "summary": summary,
        "rows": records(providers, 50),
        "usage": records(usage, 50),
        "crosscheck": records(problems, 100),
    }


def repair_data_action(ctx: ActionContext) -> dict:
    frame, summary = repair_failed_market_data(
        max_symbols=ctx.limit,
        progress=ctx.progress,
    )
    return {
        "summary": summary,
        "rows": records(frame, 100),
    }


ACTIONS = {
    "refresh_reference": refresh_reference_action,
    "bootstrap": bootstrap_action,
    "audit_data": audit_data_action,
    "source_quality": source_quality_action,
    "repair_data": repair_data_action,
}
