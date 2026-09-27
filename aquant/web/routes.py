from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from aquant.data.overview import data_overview
from aquant.models.registry import model_status
from aquant.runtime.resources import current_memory_gb, current_profile
from aquant.version import __version__
from aquant.web.actions import ALLOWED_ACTIONS
from aquant.web.jobs import active_job_id, get_job, start_task
from aquant.web.reports import report_inventory
from aquant.web.serializers import safe_dict
from config import SETTINGS, ensure_directories
from profile import load_strategy_profile


bp = Blueprint("terminal", __name__)


@bp.get("/")
def index():
    return render_template("index.html", version=__version__)


@bp.get("/api/status")
def api_status():
    ensure_directories()
    total, available = current_memory_gb()
    runtime = current_profile()
    return jsonify(
        {
            "ok": True,
            "version": __version__,
            "runtime": safe_dict(runtime.__dict__),
            "memory": {
                "total_gb": round(total, 2),
                "available_gb": round(available, 2),
            },
            "profile": load_strategy_profile(),
            "models": model_status(),
            "paths": {
                "project": str(SETTINGS.project_root),
                "data_store": str(SETTINGS.data_store_dir),
                "reports": str(SETTINGS.report_dir),
            },
            "reports": report_inventory(),
            "data_center": data_overview(),
            "active_job": active_job_id(),
        }
    )


@bp.post("/api/run/<action>")
def api_run(action: str):
    if action not in ALLOWED_ACTIONS:
        return jsonify(
            {"ok": False, "error": "未知任务"}
        ), 404
    payload = request.get_json(silent=True) or {}
    body, status = start_task(action, payload)
    return jsonify(body), status


@bp.get("/api/job/<job_id>")
def api_job(job_id: str):
    job = get_job(job_id)
    if job is None:
        return jsonify(
            {"ok": False, "error": "任务不存在"}
        ), 404
    return jsonify({"ok": True, "job": job})


@bp.get("/api/profile")
def api_profile():
    return jsonify(
        {"ok": True, "profile": load_strategy_profile()}
    )
