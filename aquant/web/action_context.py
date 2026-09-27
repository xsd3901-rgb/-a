from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ActionContext:
    limit: int | None
    refresh: bool
    horizon: int
    payload: dict
    progress: Callable[[str], None]
