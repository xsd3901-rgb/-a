from __future__ import annotations

from aquant.research.data_audit import run_data_audit
from aquant.research.data_repair import repair_failed_market_data
from aquant.data.service import MarketDataService
from aquant.research.source_quality import run_source_quality
from aquant.web.action_context import ActionContext
from aquant.web.serializers import records
from bootstrap import bootstrap_market



def refresh_reference_action(ctx: ActionContext) -> dict:
    service = MarketDataService()
    summary: dict[str, object] = {}
    rows: list[dict] = []

    try:
        stocks = service.stock_list(refresh=True)
        summary["股票基础库"] = len(stocks)
        rows.append({"项目": "股票基础库", "状态": "OK", "数量": len(stocks), "说明": "远端更新成功，已写入本地基础库"})
    except Exception as exc:
        local = service.reference.store.read_security_master()
        summary["股票基础库"] = len(local)
        rows.append({"项目": "股票基础库", "状态": "WARN", "数量": len(local), "说明": f"远端更新失败，继续使用本地/内置快照: {exc}"})

    try:
        calendar = service.trade_calendar(refresh=True)
        summary["交易日历"] = len(calendar)
        rows.append({"项目": "交易日历", "状态": "OK", "数量": len(calendar), "说明": "交易日历已更新"})
    except Exception as exc:
        local = service.reference.store.read_trade_calendar()
        summary["交易日历"] = len(local)
        rows.append({"项目": "交易日历", "状态": "WARN", "数量": len(local), "说明": f"远端更新失败，继续使用本地/内置日历: {exc}"})

    try:
        lifecycle = service.historical_securities(refresh=True)
        summary["历史生命周期"] = len(lifecycle)
        rows.append({"项目": "历史生命周期", "状态": "OK", "数量": len(lifecycle), "说明": "正式历史股票生命周期资料已更新"})
    except Exception as exc:
        summary["历史生命周期"] = 0
        rows.append({"项目": "历史生命周期", "状态": "WARN", "数量": 0, "说明": f"暂未更新成功；正式回测验收仍保持严格检查: {exc}"})

    return {
        "summary": summary,
        "rows": rows,
        "source_health": service.router.snapshot(),
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
