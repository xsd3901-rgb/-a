from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd

from aquant.data.reference import ReferenceStore


class ReferenceDataService:
    """维护股票基础库和交易日历；网络失败时优先退回本地快照。"""

    def __init__(self, store_root: str = "data_store") -> None:
        self.store = ReferenceStore(store_root)

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

    def _fetch_calendar_akshare(self, start_date: str, end_date: str) -> pd.DataFrame:
        import akshare as ak

        raw = ak.tool_trade_date_hist_sina()
        if raw is None or raw.empty:
            return pd.DataFrame()
        date_col = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
        out = pd.DataFrame({"trade_date": pd.to_datetime(raw[date_col], errors="coerce")})
        out = out.dropna(subset=["trade_date"])
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        out = out[(out["trade_date"] >= start) & (out["trade_date"] <= end)].copy()
        out["is_open"] = True
        out["provider"] = "sina"
        return out.reset_index(drop=True)

    def _fetch_calendar_baostock(self, start_date: str, end_date: str) -> pd.DataFrame:
        import baostock as bs

        login = bs.login()
        if getattr(login, "error_code", "-1") != "0":
            raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
        try:
            rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
            if rs.error_code != "0":
                raise RuntimeError(f"BaoStock 交易日历失败: {rs.error_code} {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            raw = pd.DataFrame(rows, columns=rs.fields)
        finally:
            bs.logout()

        if raw.empty:
            return pd.DataFrame()
        out = raw.rename(columns={"calendar_date": "trade_date", "is_trading_day": "is_open"}).copy()
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
        out["is_open"] = out["is_open"].astype(str).eq("1")
        out["provider"] = "baostock"
        return out[["trade_date", "is_open", "provider"]].dropna(subset=["trade_date"]).reset_index(drop=True)

    def refresh_trade_calendar(self, start_date: str = "2000-01-01", end_date: str | None = None) -> pd.DataFrame:
        if end_date is None:
            end_date = (pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None) + timedelta(days=370)).strftime("%Y-%m-%d")

        errors: list[str] = []
        for loader in (self._fetch_calendar_akshare, self._fetch_calendar_baostock):
            try:
                frame = loader(start_date, end_date)
                if frame is not None and not frame.empty:
                    self.store.save_trade_calendar(frame)
                    self.store.refresh_catalog()
                    return self.store.read_trade_calendar()
            except Exception as exc:
                errors.append(str(exc))

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

    def latest_trade_date(self, on_or_before: str | pd.Timestamp | None = None, max_age_hours: float = 18.0) -> pd.Timestamp | None:
        self.trade_calendar(max_age_hours=max_age_hours)
        return self.store.latest_trade_date(on_or_before=on_or_before)
