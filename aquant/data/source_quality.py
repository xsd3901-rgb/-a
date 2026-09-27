from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path


_EVENT_LOCK = threading.Lock()


class SourceQualityLog:
    """记录免费行情源每一次真实抓取尝试。

    文件采用 JSONL 追加格式：
    - 多线程下由进程内锁保护；
    - 单条事件损坏不会影响其他历史记录；
    - 不记录代理、Cookie、账号等敏感配置。
    """

    def __init__(self, data_root: str | Path) -> None:
        self.root = Path(data_root)
        self.quality_dir = self.root / "quality"
        self.quality_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.quality_dir / "source_events.jsonl"

    def record(
        self,
        *,
        symbol: str,
        provider: str,
        adapter: str,
        adjust: str,
        start_date: str,
        end_date: str,
        attempt_order: int,
        outcome: str,
        rows: int = 0,
        elapsed_ms: float = 0.0,
        prefer_point_in_time: bool = False,
        detail: str = "",
    ) -> None:
        event = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "symbol": str(symbol).zfill(6),
            "provider": str(provider),
            "adapter": str(adapter),
            "adjust": str(adjust),
            "start_date": str(start_date),
            "end_date": str(end_date),
            "attempt_order": int(attempt_order),
            "outcome": str(outcome),
            "rows": int(rows),
            "elapsed_ms": round(float(elapsed_ms), 3),
            "prefer_point_in_time": bool(prefer_point_in_time),
            "detail": str(detail)[:1000],
        }
        line = json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        try:
            with _EVENT_LOCK:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:
            # 质量追踪不能反过来阻断真实行情抓取。
            return

    def read(self, max_events: int | None = None) -> list[dict]:
        if not self.path.exists():
            return []

        with _EVENT_LOCK:
            try:
                lines = self.path.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
            except Exception:
                return []

        if max_events and max_events > 0:
            lines = lines[-int(max_events):]

        events: list[dict] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                events.append(item)
        return events
