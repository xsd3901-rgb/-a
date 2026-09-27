from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path


_HEALTH_LOCK = threading.Lock()


class SourceHealthRegistry:
    """轻量数据源健康/熔断状态。

    目标不是永久禁用某个免费源，而是在连续网络失败时避免对几千只股票
    反复等待同一个超时。冷却期结束后会自动再次允许探测。
    """

    def __init__(self, data_root: str | Path) -> None:
        root = Path(data_root)
        self.path = root / "quality" / "source_health.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _write(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp.json")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def state(self, key: str) -> dict:
        with _HEALTH_LOCK:
            return dict(self._read().get(str(key), {}))

    def available(self, key: str) -> bool:
        item = self.state(key)
        until = item.get("cooldown_until")
        if not until:
            return True
        try:
            return datetime.fromisoformat(str(until)) <= datetime.now()
        except Exception:
            return True

    def cooldown_until(self, key: str) -> datetime:
        item = self.state(key)
        raw = item.get("cooldown_until")
        if raw:
            try:
                return datetime.fromisoformat(str(raw))
            except Exception:
                pass
        return datetime.min

    def mark_success(self, key: str, elapsed_ms: float = 0.0) -> None:
        now = datetime.now()
        with _HEALTH_LOCK:
            state = self._read()
            old = dict(state.get(str(key), {}))
            previous = float(old.get("avg_elapsed_ms", 0.0) or 0.0)
            elapsed = float(elapsed_ms or 0.0)
            avg = (
                elapsed
                if previous <= 0
                else previous * 0.75 + elapsed * 0.25
            )
            state[str(key)] = {
                **old,
                "consecutive_failures": 0,
                "last_success": now.isoformat(timespec="seconds"),
                "avg_elapsed_ms": round(avg, 2),
                "cooldown_until": None,
                "last_error": "",
            }
            self._write(state)

    def mark_failure(self, key: str, error: str = "") -> None:
        now = datetime.now()
        with _HEALTH_LOCK:
            state = self._read()
            old = dict(state.get(str(key), {}))
            failures = int(old.get("consecutive_failures", 0) or 0) + 1
            # 30s, 60s, 120s ... capped at 15 minutes.
            cooldown_seconds = min(900, 30 * (2 ** min(failures - 1, 5)))
            state[str(key)] = {
                **old,
                "consecutive_failures": failures,
                "last_failure": now.isoformat(timespec="seconds"),
                "cooldown_until": (
                    now + timedelta(seconds=cooldown_seconds)
                ).isoformat(timespec="seconds"),
                "last_error": str(error)[:500],
            }
            self._write(state)

    def choose(self, keys: list[str]) -> list[str]:
        """返回本轮应该尝试的源。

        有健康源时跳过仍在冷却的源；如果全部在冷却，只允许最早恢复的
        一个源进行探测，避免整个任务永久饿死。
        """
        available = [key for key in keys if self.available(key)]
        if available:
            return available
        if not keys:
            return []
        return [min(keys, key=self.cooldown_until)]
