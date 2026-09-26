from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from config import SETTINGS

PROFILE_PATH = Path("strategy_profile.json")


def _defaults() -> dict:
    return {
        "score_threshold": SETTINGS.score_threshold,
        "stop_atr_multiple": SETTINGS.stop_atr_multiple,
        "target_atr_multiple": SETTINGS.target_atr_multiple,
        "max_hold_days": SETTINGS.reference_hold_max_days,
        "source": "default",
    }


def _sanitize(profile: dict) -> dict:
    base = _defaults()
    base.update(profile or {})
    # 限定调整范围，防止一次回测把参数推到极端值
    base["score_threshold"] = int(min(85, max(55, int(base["score_threshold"]))))
    base["stop_atr_multiple"] = float(min(3.0, max(1.0, float(base["stop_atr_multiple"]))))
    base["target_atr_multiple"] = float(min(5.0, max(1.5, float(base["target_atr_multiple"]))))
    base["max_hold_days"] = int(min(45, max(10, int(base["max_hold_days"]))))
    return base


def load_strategy_profile() -> dict:
    if not PROFILE_PATH.exists():
        return _defaults()
    try:
        return _sanitize(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
    except Exception:
        return _defaults()


def save_strategy_profile(profile: dict) -> dict:
    clean = _sanitize(profile)
    clean["source"] = "optimizer"
    clean["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROFILE_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean
