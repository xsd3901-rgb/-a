from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime

from aquant.web.actions import execute_action
from aquant.web.serializers import safe_dict


_JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()
_ACTIVE_JOB_ID: str | None = None


def _set_job(job_id: str, **values) -> None:
    with _JOB_LOCK:
        job = _JOBS.setdefault(job_id, {})
        job.update(values)
        job["updated_at"] = datetime.now().isoformat(
            timespec="seconds"
        )


def _execute_task(
    job_id: str,
    action: str,
    payload: dict,
) -> None:
    global _ACTIVE_JOB_ID
    try:
        _set_job(
            job_id,
            status="running",
            message="任务正在运行",
        )
        result = execute_action(
            action,
            payload,
            progress=lambda message: _set_job(
                job_id,
                status="running",
                message=message,
            ),
        )
        _set_job(
            job_id,
            status="completed",
            message="任务完成",
            result=safe_dict(result),
            finished_at=datetime.now().isoformat(
                timespec="seconds"
            ),
        )
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            message=str(exc),
            error=str(exc),
            traceback=traceback.format_exc(limit=12),
            finished_at=datetime.now().isoformat(
                timespec="seconds"
            ),
        )
    finally:
        with _JOB_LOCK:
            if _ACTIVE_JOB_ID == job_id:
                _ACTIVE_JOB_ID = None


def start_task(
    action: str,
    payload: dict,
) -> tuple[dict, int]:
    global _ACTIVE_JOB_ID

    with _JOB_LOCK:
        if _ACTIVE_JOB_ID:
            active = _JOBS.get(_ACTIVE_JOB_ID, {})
            if active.get("status") in {"queued", "running"}:
                return {
                    "ok": False,
                    "error": (
                        "已有任务正在运行，请等待完成后再启动新任务。"
                    ),
                    "job_id": _ACTIVE_JOB_ID,
                }, 409

        job_id = uuid.uuid4().hex[:12]
        _ACTIVE_JOB_ID = job_id
        _JOBS[job_id] = {
            "id": job_id,
            "action": action,
            "status": "queued",
            "message": "任务已进入队列",
            "started_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

    thread = threading.Thread(
        target=_execute_task,
        args=(job_id, action, payload),
        daemon=True,
        name=f"aquant-{action}-{job_id}",
    )
    thread.start()
    return {"ok": True, "job_id": job_id}, 202


def get_job(job_id: str) -> dict | None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        return safe_dict(job) if job is not None else None


def active_job_id() -> str | None:
    with _JOB_LOCK:
        if not _ACTIVE_JOB_ID:
            return None
        active = _JOBS.get(_ACTIVE_JOB_ID, {})
        if active.get("status") not in {"queued", "running"}:
            return None
        return _ACTIVE_JOB_ID
