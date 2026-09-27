from __future__ import annotations

import time

import pandas as pd

from config import SETTINGS

from aquant.data.routing.policy import LIFECYCLE_SOURCE
from aquant.data.routing.router import DataSourceRouter
from aquant.data.safe_fetch import fetch_security_lifecycle_with_timeout
from aquant.data.universe_store import HistoricalUniverseStore


class HistoricalUniverseService:
    """历史股票池服务，避免回测只使用当前仍上市股票。"""

    def __init__(self, store_root: str | None = None) -> None:
        root = str(SETTINGS.data_store_dir) if store_root is None else store_root
        self.store = HistoricalUniverseStore(root)
        self.router = DataSourceRouter(root)

    @staticmethod
    def _fresh(path, max_age_hours: float) -> bool:
        if not path.exists():
            return False
        return (time.time() - path.stat().st_mtime) / 3600 <= max_age_hours

    def refresh(
        self,
        force: bool = False,
        max_age_hours: float = 72.0,
    ) -> pd.DataFrame:
        local = self.store.read()
        if (
            not force
            and not local.empty
            and self._fresh(self.store.path, max_age_hours)
        ):
            return local

        sources = self.router.names(
            [LIFECYCLE_SOURCE.name],
            capability="lifecycle",
        )
        if LIFECYCLE_SOURCE.name in sources:
            started = time.monotonic()
            try:
                remote = fetch_security_lifecycle_with_timeout(
                    timeout_seconds=LIFECYCLE_SOURCE.timeout_seconds
                )
                if remote is not None and not remote.empty:
                    self.store.save(remote)
                    self.store.refresh_catalog()
                    self.router.success(
                        "baostock",
                        capability="lifecycle",
                        elapsed_ms=(
                            time.monotonic() - started
                        ) * 1000.0,
                    )
                    return self.store.read()
                self.router.failure(
                    "baostock",
                    capability="lifecycle",
                    error="empty lifecycle response",
                )
            except Exception as exc:
                self.router.failure(
                    "baostock",
                    capability="lifecycle",
                    error=str(exc),
                )
                if not local.empty:
                    return local
                raise

        if not local.empty:
            return local
        raise RuntimeError(
            "历史股票生命周期资料暂不可用；"
            "BaoStock 正在冷却或远端失败，且本地没有正式生命周期快照"
        )

    def universe_on(
        self,
        as_of_date: str | pd.Timestamp,
        refresh: bool = False,
    ) -> pd.DataFrame:
        self.refresh(force=refresh)
        return self.store.universe_on(as_of_date)

    def all_securities(self, refresh: bool = False) -> pd.DataFrame:
        return self.refresh(force=refresh)
