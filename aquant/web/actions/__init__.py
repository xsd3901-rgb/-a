from __future__ import annotations

from aquant.web.action_context import ActionContext
from aquant.web.actions.daily import ACTIONS as DAILY_ACTIONS
from aquant.web.actions.data import ACTIONS as DATA_ACTIONS
from aquant.web.actions.research import ACTIONS as RESEARCH_ACTIONS
from aquant.web.actions.system import ACTIONS as SYSTEM_ACTIONS


ACTION_HANDLERS = {
    **DAILY_ACTIONS,
    **DATA_ACTIONS,
    **RESEARCH_ACTIONS,
    **SYSTEM_ACTIONS,
}
ALLOWED_ACTIONS = frozenset(ACTION_HANDLERS)


def execute_action(
    action: str,
    payload: dict,
    progress,
) -> dict:
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        raise ValueError(f"未知任务: {action}")

    limit_raw = payload.get("limit")
    limit = (
        int(limit_raw)
        if limit_raw not in (None, "", 0, "0")
        else None
    )
    context = ActionContext(
        limit=limit,
        refresh=bool(payload.get("refresh", False)),
        horizon=int(payload.get("horizon") or 10),
        payload=payload,
        progress=progress,
    )
    return handler(context)
