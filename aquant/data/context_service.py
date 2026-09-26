from __future__ import annotations

import time

import pandas as pd

from aquant.data.context_store import MarketContextStore
from aquant.data.safe_fetch import (
    fetch_index_daily_with_timeout,
    fetch_industry_map_with_timeout,
)


DEFAULT_INDICES: dict[str, tuple[str, str]] = {
    "sh000001": ("上证指数", "sh000001"),
    "sz399001": ("深证成指", "sz399001"),
    "sz399006": ("创业板指", "sz399006"),
    "sh000300": ("沪深300", "sh000300"),
    "sh000905": ("中证500", "sh000905"),
    "sh000852": ("中证1000", "sh000852"),
}


class MarketContextService:
    """指数日线和行业映射的统一服务，优先本地、缺失时增量更新。"""

    def __init__(self, store_root: str = "data_store") -> None:
        self.store = MarketContextStore(store_root)

    @staticmethod
    def _fresh(path, max_age_hours: float) -> bool:
        if not path.exists():
            return False
        return (time.time() - path.stat().st_mtime) / 3600 <= max_age_hours

    def industry_map(self, force: bool = False, max_age_hours: float = 72.0) -> pd.DataFrame:
        local = self.store.read_industry_map()
        if not force and not local.empty and self._fresh(self.store.industry_path, max_age_hours):
            return local
        try:
            remote = fetch_industry_map_with_timeout(timeout_seconds=35.0)
            if remote is not None and not remote.empty:
                self.store.save_industry_map(remote)
                self.store.refresh_catalog()
                return self.store.read_industry_map()
        except Exception:
            if not local.empty:
                return local
            raise
        if not local.empty:
            return local
        raise RuntimeError("行业映射获取失败且本地无可用快照")

    def index_daily(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
        force: bool = False,
    ) -> pd.DataFrame:
        if index_code not in DEFAULT_INDICES:
            raise ValueError(f"未配置指数: {index_code}")
        index_name, provider_symbol = DEFAULT_INDICES[index_code]
        local = self.store.read_index_daily(index_code, start_date, end_date)
        latest = self.store.latest_index_date(index_code)

        need_fetch = force or latest is None or latest < pd.Timestamp(end_date).normalize()
        if need_fetch:
            fetch_start = start_date if force or latest is None else (
                latest + pd.Timedelta(days=1)
            ).strftime("%Y-%m-%d")
            errors: list[str] = []
            for source in ("akshare", "baostock"):
                try:
                    remote = fetch_index_daily_with_timeout(
                        source=source,
                        index_symbol=provider_symbol,
                        start_date=fetch_start,
                        end_date=end_date,
                        timeout_seconds=20.0 if source == "akshare" else 35.0,
                    )
                    if remote is not None and not remote.empty:
                        self.store.save_index_daily(index_code, index_name, remote)
                        self.store.refresh_catalog()
                        break
                    errors.append(f"{source}: empty")
                except Exception as exc:
                    errors.append(f"{source}: {exc}")

            refreshed = self.store.read_index_daily(index_code, start_date, end_date)
            if refreshed.empty and not local.empty:
                return local
            if refreshed.empty:
                raise RuntimeError(
                    f"{index_name} 指数日线获取失败: " + " | ".join(errors)
                )
            return refreshed

        return local

    def refresh_core_indices(self, start_date: str, end_date: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for code in DEFAULT_INDICES:
            try:
                frame = self.index_daily(code, start_date, end_date)
                result[code] = len(frame)
            except Exception:
                result[code] = 0
        return result
