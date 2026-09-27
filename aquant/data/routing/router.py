from __future__ import annotations

from collections.abc import Iterable

from aquant.data.source_health import SourceHealthRegistry


class DataSourceRouter:
    """按数据类型选择当前可用的数据源。

    健康状态按 capability:source 分开记录，例如 stock_list:eastmoney
    与 daily:eastmoney 互不误伤。
    """

    def __init__(self, data_root) -> None:
        self.health = SourceHealthRegistry(data_root)

    @staticmethod
    def key(capability: str, source: str) -> str:
        return f"{capability}:{source}"

    def names(
        self,
        preferred: Iterable[str],
        *,
        capability: str,
    ) -> list[str]:
        names = [str(item) for item in preferred]
        keys = [self.key(capability, name) for name in names]
        selected = self.health.choose(keys)
        prefix = f"{capability}:"
        return [
            key[len(prefix):]
            for key in selected
            if key.startswith(prefix)
        ]

    def providers(
        self,
        preferred,
        *,
        capability: str,
    ) -> list:
        items = list(preferred)
        by_name = {
            str(item.info.provider): item
            for item in items
        }
        ordered = self.names(
            list(by_name),
            capability=capability,
        )
        return [
            by_name[name]
            for name in ordered
            if name in by_name
        ]

    def success(
        self,
        source: str,
        *,
        capability: str,
        elapsed_ms: float = 0.0,
    ) -> None:
        self.health.mark_success(
            self.key(capability, source),
            elapsed_ms=elapsed_ms,
        )

    def failure(
        self,
        source: str,
        *,
        capability: str,
        error: str = "",
    ) -> None:
        self.health.mark_failure(
            self.key(capability, source),
            error=error,
        )

    def snapshot(self) -> list[dict]:
        return self.health.snapshot()
