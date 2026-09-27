from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _json_default(value):
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass
    return str(value)


@dataclass
class CheckpointState:
    signature: str
    completed: dict[str, dict]
    failed: dict[str, dict]
    updated_at: str = ""


class JsonCheckpoint:
    """小型原子 JSON 检查点，供长任务断点续跑。

    - signature 不一致时自动开始新任务，不沿用旧完成状态；
    - completed / failed 以股票代码为键；
    - 每次写入使用临时文件 + replace，避免中途断电留下半个 JSON。
    """

    def __init__(self, path: str | Path, signature: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.signature = str(signature)
        self.state = self._load()

    def _load(self) -> CheckpointState:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if str(data.get("signature", "")) == self.signature:
                    return CheckpointState(
                        signature=self.signature,
                        completed=dict(data.get("completed") or {}),
                        failed=dict(data.get("failed") or {}),
                        updated_at=str(data.get("updated_at") or ""),
                    )
            except Exception:
                pass
        return CheckpointState(
            signature=self.signature,
            completed={},
            failed={},
        )

    def is_completed(self, key: str) -> bool:
        return str(key) in self.state.completed

    def mark_completed(self, key: str, payload: dict | None = None) -> None:
        code = str(key)
        self.state.completed[code] = dict(payload or {})
        self.state.failed.pop(code, None)
        self.save()

    def mark_many_completed(
        self,
        items: dict[str, dict | None],
    ) -> None:
        for key, payload in items.items():
            code = str(key)
            self.state.completed[code] = dict(payload or {})
            self.state.failed.pop(code, None)
        self.save()

    def mark_failed(
        self,
        key: str,
        *,
        error: str,
        attempts: int,
        payload: dict | None = None,
    ) -> None:
        code = str(key)
        value = dict(payload or {})
        value.update(
            {
                "error": str(error)[:1000],
                "attempts": int(attempts),
            }
        )
        self.state.failed[code] = value
        self.state.completed.pop(code, None)
        self.save()

    def update_batch(
        self,
        *,
        completed: dict[str, dict | None] | None = None,
        failed: dict[str, dict] | None = None,
    ) -> None:
        """一次原子写入一批完成/失败状态。

        长任务按批次落盘，既避免每只股票都重写整个 JSON，
        又保证中断后最多只重算尚未落盘的当前批次。
        """
        for key, payload in (completed or {}).items():
            code = str(key)
            self.state.completed[code] = dict(payload or {})
            self.state.failed.pop(code, None)

        for key, payload in (failed or {}).items():
            code = str(key)
            self.state.failed[code] = dict(payload or {})
            self.state.completed.pop(code, None)

        if completed or failed:
            self.save()

    def clear(self) -> None:
        self.state = CheckpointState(
            signature=self.signature,
            completed={},
            failed={},
        )
        self.save()

    def save(self) -> None:
        self.state.updated_at = datetime.now().isoformat(timespec="seconds")
        payload = {
            "signature": self.state.signature,
            "completed": self.state.completed,
            "failed": self.state.failed,
            "updated_at": self.state.updated_at,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    @property
    def completed_count(self) -> int:
        return len(self.state.completed)

    @property
    def failed_count(self) -> int:
        return len(self.state.failed)
