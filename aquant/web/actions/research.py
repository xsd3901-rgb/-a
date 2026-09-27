from __future__ import annotations

from aquant.research.feature_selection import build_v2_candidate
from aquant.research.feature_validation import run_feature_validation
from aquant.research.model_compare import run_model_comparison
from aquant.research.strategy_ablation import run_v1_ablation
from aquant.research.system_validation import run_system_validation
from aquant.research.walk_forward import run_walk_forward
from aquant.web.action_context import ActionContext
from aquant.web.serializers import records
from optimizer import optimize_parameters
from profile import load_strategy_profile


def features_action(ctx: ActionContext) -> dict:
    feature_limit = 200 if ctx.limit is None else ctx.limit
    frame = run_feature_validation(
        limit=feature_limit,
        refresh=ctx.refresh,
        progress=ctx.progress,
    )
    return {
        "summary": {"结果行数": len(frame)},
        "rows": records(frame, 80),
    }


def ablation_action(ctx: ActionContext) -> dict:
    detail, summary_table, summary = run_v1_ablation()
    return {
        "summary": summary,
        "rows": records(summary_table, 50),
        "ablation_detail": records(detail, 100),
    }


def select_features_action(ctx: ActionContext) -> dict:
    selected, candidate = build_v2_candidate()
    return {
        "candidate": candidate,
        "rows": records(selected, 50),
    }


def walk_forward_action(ctx: ActionContext) -> dict:
    folds, summary = run_walk_forward(horizon=ctx.horizon)
    return {
        "summary": summary,
        "rows": records(folds, 80),
    }


def compare_models_action(ctx: ActionContext) -> dict:
    comparison, summary = run_model_comparison(horizon=ctx.horizon)
    return {
        "summary": summary,
        "rows": records(comparison, 10),
    }


def validate_system_action(ctx: ActionContext) -> dict:
    limit = int(ctx.limit or 200)
    comparison, summary = run_system_validation(
        feature_limit=limit,
        backtest_limit=limit,
        horizon=ctx.horizon,
        refresh=ctx.refresh,
    )
    return {
        "summary": summary,
        "rows": records(comparison, 10),
    }


def optimize_action(ctx: ActionContext) -> dict:
    limit = int(ctx.limit or 100)
    frame, best = optimize_parameters(
        limit=limit,
        refresh=ctx.refresh,
    )
    return {
        "profile": best or load_strategy_profile(),
        "rows": records(frame, 50),
    }


ACTIONS = {
    "features": features_action,
    "ablation": ablation_action,
    "select_features": select_features_action,
    "walk_forward": walk_forward_action,
    "compare_models": compare_models_action,
    "validate_system": validate_system_action,
    "optimize": optimize_action,
}
