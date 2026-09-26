from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd

from config import SETTINGS

from aquant.data.reference import ReferenceStore
from aquant.data.safe_fetch import fetch_trade_calendar_with_timeout


class ReferenceDataService:
    """维护股票基础库和交易日历；网络失败时优先退回本地快照。"""

    def __init__(self, store_root: str | None = None) -> None:
        root = str(SETTINGS.data_store_dir) if store_root is None else store_root
        self.store = ReferenceStore(root)

    @staticmethod
    def _fresh(path, max_age_hours: float) -> bool:
        if not path.exists():
            return False
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        return age_hours <= max_age_hours

    def stock_list(self, fetcher, max_age_hours: float = 18.0, force: bool = False) -> pd.DataFrame:
        local = self.store.read_security_master()
        if not force and not local.empty and self._fresh(self.store.security_path, max_age_hours):
            return local[["code", "name"]].copy()

        try:
            remote = fetcher()
            if remote is not None and not remote.empty:
                out = remote.rename(columns={"symbol": "code"}).copy()
                out["code"] = out["code"].astype(str).str.zfill(6)
                out["name"] = out["name"].astype(str).str.strip()
                out = out[["code", "name"]].drop_duplicates("code").reset_index(drop=True)
                self.store.save_security_master(out)
                self.store.refresh_catalog()
                return out
        except Exception:
            if not local.empty:
                return local[["code", "name"]].copy()
            raise

        if not local.empty:
            return local[["code", "name"]].copy()
        raise RuntimeError("股票基础库获取失败且本地没有可用快照")

    def refresh_trade_calendar(
        self,
        start_date: str = "2000-01-01",
        end_date: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> pd.DataFrame:
        if end_date is None:
            end_date = (
                pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None) + timedelta(days=370)
            ).strftime("%Y-%m-%d")

        errors: list[str] = []
        for source in ("akshare", "baostock"):
            try:
                source_timeout = 20.0 if source == "akshare" else 35.0
                frame = fetch_trade_calendar_with_timeout(
                    source=source,
                    start_date=start_date,
                    end_date=end_date,
                    timeout_seconds=max(timeout_seconds, source_timeout),
                )
                if frame is not None and not frame.empty:
                    self.store.save_trade_calendar(frame)
                    self.store.refresh_catalog()
                    return self.store.read_trade_calendar()
                errors.append(f"{source}: empty")
            except Exception as exc:
                errors.append(f"{source}: {exc}")

        local = self.store.read_trade_calendar()
        if not local.empty:
            return local
        raise RuntimeError("交易日历获取失败: " + " | ".join(errors))

    def trade_calendar(self, max_age_hours: float = 18.0, force: bool = False) -> pd.DataFrame:
        local = self.store.read_trade_calendar()
        if not force and not local.empty and self._fresh(self.store.calendar_path, max_age_hours):
            return local
        try:
            return self.refresh_trade_calendar()
        except Exception:
            if not local.empty:
                return local
            raise

    def latest_trade_date(
        self,
        on_or_before: str | pd.Timestamp | None = None,
        max_age_hours: float = 18.0,
    ) -> pd.Timestamp | None:
        self.trade_calendar(max_age_hours=max_age_hours)
        return self.store.latest_trade_date(on_or_before=on_or_before)
