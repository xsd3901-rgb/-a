from __future__ import annotations

import time

import pandas as pd

from aquant.data.safe_fetch import fetch_security_lifecycle_with_timeout
from aquant.data.universe_store import HistoricalUniverseStore


class HistoricalUniverseService:
    """历史股票池服务，避免回测只使用当前仍上市股票。"""

    def __init__(self, store_root: str = "data_store") -> None:
        self.store = HistoricalUniverseStore(store_root)

    @staticmethod
    def _fresh(path, max_age_hours: float) -> bool:
        if not path.exists():
            return False
        return (time.time() - path.stat().st_mtime) / 3600 <= max_age_hours

    def refresh(self, force: bool = False, max_age_hours: float = 72.0) -> pd.DataFrame:
        local = self.store.read()
        if not force and not local.empty and self._fresh(self.store.path, max_age_hours):
            return local

        try:
            remote = fetch_security_lifecycle_with_timeout(timeout_seconds=45.0)
            if remote is not None and not remote.empty:
                self.store.save(remote)
                self.store.refresh_catalog()
                return self.store.read()
        except Exception:
            if not local.empty:
                return local
            raise

        if not local.empty:
            return local
        raise RuntimeError("历史股票生命周期资料获取失败且本地没有可用快照")

    def universe_on(
        self,
        as_of_date: str | pd.Timestamp,
        refresh: bool = False,
    ) -> pd.DataFrame:
        self.refresh(force=refresh)
        return self.store.universe_on(as_of_date)

    def all_securities(self, refresh: bool = False) -> pd.DataFrame:
        return self.refresh(force=refresh)
