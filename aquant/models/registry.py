from __future__ import annotations

import json
from pathlib import Path

from config import SETTINGS
from profile import load_strategy_profile


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def model_status() -> dict:
    """汇总正式模型和研究候选状态，不执行自动晋级。"""
    model_dir = SETTINGS.data_store_dir / "research" / "models"
    comparison = _read_json(model_dir / "model_compare_summary.json")
    walk_forward = _read_json(model_dir / "walk_forward_summary.json")
    candidate = _read_json(model_dir / "score_v2_candidate.json")

    active = load_strategy_profile()
    v2_state = comparison.get("状态")
    if not v2_state:
        v2_state = walk_forward.get("研究状态", "尚未验证")

    return {
        "active_model": "V1",
        "active_profile": active,
        "v2_state": v2_state,
        "v2_rule_count": len(candidate.get("rules", []))
        if isinstance(candidate.get("rules"), list)
        else 0,
        "walk_forward_state": walk_forward.get("研究状态", "尚未运行"),
        "comparison_state": comparison.get("状态", "尚未运行"),
        "auto_promotion": False,
        "note": "模型升级必须经过本地样本外和真实成交对照；系统不会自动把研究候选切换成正式模型。",
    }
