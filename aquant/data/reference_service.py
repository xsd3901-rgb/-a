from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd

from config import SETTINGS

from aquant.data.reference import ReferenceStore
from aquant.data.routing.policy import CALENDAR_SOURCES
from aquant.data.routing.router import DataSourceRouter
from aquant.data.safe_fetch import fetch_trade_calendar_with_timeout
from aquant.data.seed_reference import (
    load_security_seed,
    load_trade_calendar_seed,
)


class ReferenceDataService:
    """维护股票基础库和交易日历；网络失败时优先退回本地快照。"""

    def __init__(self, store_root: str | None = None) -> None:
        root = str(SETTINGS.data_store_dir) if store_root is None else store_root
        self.store = ReferenceStore(root)
        self.router = DataSourceRouter(root)
        self._ensure_bundled_reference()

    def _ensure_bundled_reference(self) -> None:
        """首次部署没有本地快照时，用随项目分发的只读种子启动。

        种子只负责让系统能够开始工作；后续远端刷新仍会覆盖本地基础库。
        历史生命周期资料不由这里伪造，因此正式验收仍会检查
        security_lifecycle 是否真实可用。
        """
        changed = False

        local_security = self.store.read_security_master()
        if local_security.empty:
            security_seed = load_security_seed()
            if not security_seed.empty:
                self.store.save_security_master(security_seed)
                changed = True

        local_calendar = self.store.read_trade_calendar()
        if local_calendar.empty:
            calendar_seed = load_trade_calendar_seed()
            if not calendar_seed.empty:
                self.store.save_trade_calendar(calendar_seed)
                changed = True

        if changed:
            try:
                self.store.refresh_catalog()
            except Exception:
                # 种子落盘成功比 DuckDB 目录视图更重要；目录后续仍可重建。
                pass

    @staticmethod
    def _fresh(path, max_age_hours: float) -> bool:
        if not path.exists():
            return False
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        return age_hours <= max_age_hours

    def stock_list(
        self,
        fetcher,
        max_age_hours: float = 18.0,
        force: bool = False,
        fallback_local: bool = True,
        provider_name: str | None = None,
    ) -> pd.DataFrame:
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
                if provider_name:
                    out["provider"] = str(provider_name)
                    out["snapshot_date"] = (
                        pd.Timestamp.now(tz="Asia/Shanghai")
                        .strftime("%Y-%m-%d")
                    )
                self.store.save_security_master(out)
                self.store.refresh_catalog()
                return out
        except Exception:
            if fallback_local and not local.empty:
                return local[["code", "name"]].copy()
            raise

        if fallback_local and not local.empty:
            return local[["code", "name"]].copy()
        raise RuntimeError("股票基础库远程数据为空")

    def refresh_trade_calendar(
        self,
        start_date: str = "2000-01-01",
        end_date: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> pd.DataFrame:
        if end_date is None:
            end_date = (
                pd.Timestamp.now(tz="Asia/Shanghai")
                .tz_localize(None)
                + timedelta(days=370)
            ).strftime("%Y-%m-%d")

        errors: list[str] = []
        source_timeout = {
            spec.name: spec.timeout_seconds
            for spec in CALENDAR_SOURCES
        }
        sources = self.router.names(
            list(source_timeout),
            capability="calendar",
        )
        for source in sources:
            started = time.monotonic()
            try:
                frame = fetch_trade_calendar_with_timeout(
                    source=source,
                    start_date=start_date,
                    end_date=end_date,
                    timeout_seconds=max(
                        timeout_seconds,
                        source_timeout[source],
                    ),
                )
                elapsed_ms = (
                    time.monotonic() - started
                ) * 1000.0
                if frame is not None and not frame.empty:
                    self.store.save_trade_calendar(frame)
                    self.store.refresh_catalog()
                    self.router.success(
                        source,
                        capability="calendar",
                        elapsed_ms=elapsed_ms,
                    )
                    return self.store.read_trade_calendar()
                errors.append(f"{source}: empty")
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                self.router.failure(
                    source,
                    capability="calendar",
                    error=str(exc),
                )

        local = self.store.read_trade_calendar()
        if not local.empty:
            return local
        raise RuntimeError(
            "交易日历获取失败: "
            + (" | ".join(errors) if errors else "所有远端源均在冷却期")
        )

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

    def derive_trade_calendar_from_local_market(self) -> pd.DataFrame:
        """从已落盘的未复权真实日线反推开市日，不访问网络。

        这只在远端交易日历不可用时作为正式本地日历来源。全市场日线中
        实际出现过的日期即为真实开市日；不会用“周一到周五”猜测节假日。
        """
        daily_dir = self.store.root.parent / "daily" / "none"
        files = list(daily_dir.glob("*.parquet")) if daily_dir.exists() else []
        if not files:
            return pd.DataFrame(columns=["trade_date", "is_open", "provider"])

        import duckdb

        glob_path = str(daily_dir / "*.parquet").replace("'", "''")
        con = duckdb.connect()
        try:
            frame = con.execute(
                f"""
                SELECT DISTINCT CAST(trade_date AS DATE) AS trade_date
                FROM read_parquet('{glob_path}', union_by_name=true)
                WHERE trade_date IS NOT NULL
                ORDER BY trade_date
                """
            ).df()
        finally:
            con.close()

        if frame.empty:
            return pd.DataFrame(columns=["trade_date", "is_open", "provider"])

        frame["trade_date"] = pd.to_datetime(
            frame["trade_date"], errors="coerce"
        ).dt.normalize()
        frame = frame.dropna(subset=["trade_date"]).drop_duplicates("trade_date")
        frame["is_open"] = True
        frame["provider"] = "derived_local_market"

        existing = self.store.read_trade_calendar()
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, frame], ignore_index=True, sort=False)
            combined = (
                combined.sort_values("trade_date")
                .drop_duplicates("trade_date", keep="last")
                .reset_index(drop=True)
            )
        else:
            combined = frame

        self.store.save_trade_calendar(combined)
        self.store.refresh_catalog()
        return self.store.read_trade_calendar()

    def latest_trade_date(
        self,
        on_or_before: str | pd.Timestamp | None = None,
        max_age_hours: float = 18.0,
    ) -> pd.Timestamp | None:
        try:
            self.trade_calendar(max_age_hours=max_age_hours)
        except Exception:
            # 首次部署时两个免费交易日历接口可能同时超时。这里返回 None，
            # 让上层仅把“今天”作为行情下载上界；正式交易日历会在真实日线
            # 落盘后由 derive_trade_calendar_from_local_market() 重建。
            local = self.store.read_trade_calendar()
            if local.empty:
                return None
        return self.store.latest_trade_date(on_or_before=on_or_before)
