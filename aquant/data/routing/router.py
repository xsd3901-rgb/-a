from __future__ import annotations

from collections.abc import Iterable

from aquant.data.source_health import SourceHealthRegistry


class DataSourceRouter:
    """按数据类型选择当前可用的数据源。

    路由器只决定尝试顺序和熔断，不负责具体下载逻辑。
    """

    def __init__(self, data_root) -> None:
        self.health = SourceHealthRegistry(data_root)

    def names(
        self,
        preferred: Iterable[str],
    ) -> list[str]:
        keys = [str(item) for item in preferred]
        return self.health.choose(keys)

    def providers(
        self,
        preferred,
    ) -> list:
        items = list(preferred)
        by_name = {
            str(item.info.provider): item
            for item in items
        }
        ordered = self.names(list(by_name))
        return [by_name[name] for name in ordered if name in by_name]

    def success(
        self,
        source: str,
        elapsed_ms: float = 0.0,
    ) -> None:
        self.health.mark_success(source, elapsed_ms=elapsed_ms)

    def failure(
        self,
        source: str,
        error: str = "",
    ) -> None:
        self.health.mark_failure(source, error=error)

    def snapshot(self) -> list[dict]:
        return self.health.snapshot()
