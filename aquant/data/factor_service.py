from __future__ import annotations

import pandas as pd

from aquant.data.factor_store import AdjustmentFactorStore
from aquant.data.safe_fetch import fetch_adjust_factors_with_timeout


class AdjustmentFactorService:
    """复权因子服务。

    当前只维护因子事件表，不直接替换正式回测价格。这样可以先把未复权底稿和
    复权事件完整保存，后续再统一决定回测使用哪一种点时复权口径。
    """

    def __init__(self, store_root: str = "data_store") -> None:
        self.store = AdjustmentFactorStore(store_root)

    def factors(
        self,
        symbol: str,
        start_date: str = "1990-12-19",
        end_date: str | None = None,
        refresh: bool = False,
    ) -> pd.DataFrame:
        code = str(symbol).zfill(6)[-6:]
        if end_date is None:
            end_date = (
                pd.Timestamp.now(tz="Asia/Shanghai")
                .tz_localize(None)
                .normalize()
                .strftime("%Y-%m-%d")
            )

        local = self.store.read(code, start_date, end_date)
        if not refresh and not local.empty:
            latest = self.store.latest_event_date(code)
            if latest is not None and latest >= pd.Timestamp(end_date) - pd.Timedelta(days=370):
                return local

        try:
            remote = fetch_adjust_factors_with_timeout(
                symbol=code,
                start_date=start_date,
                end_date=end_date,
                timeout_seconds=35.0,
            )
            if remote is not None and not remote.empty:
                self.store.save(code, remote)
                self.store.refresh_catalog()
                return self.store.read(code, start_date, end_date)
        except Exception:
            if not local.empty:
                return local
            raise

        return local

    @staticmethod
    def attach_fore_adjust_factor(
        raw_daily: pd.DataFrame,
        factors: pd.DataFrame,
    ) -> pd.DataFrame:
        """把事件因子按最近已发生事件映射到未复权日线，仅用于研究/校验。"""
        if raw_daily is None or raw_daily.empty:
            return pd.DataFrame()
        out = raw_daily.copy()
        date_col = "trade_date" if "trade_date" in out.columns else "date"
        out[date_col] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()

        if factors is None or factors.empty:
            out["adj_factor"] = 1.0
            return out

        event = factors[["event_date", "fore_adjust_factor"]].copy()
        event["event_date"] = pd.to_datetime(
            event["event_date"], errors="coerce"
        ).dt.normalize()
        event = event.dropna(subset=["event_date", "fore_adjust_factor"]).sort_values(
            "event_date"
        )
        out = out.sort_values(date_col)
        merged = pd.merge_asof(
            out,
            event,
            left_on=date_col,
            right_on="event_date",
            direction="backward",
        )
        merged["adj_factor"] = merged["fore_adjust_factor"].fillna(1.0)
        return merged.drop(columns=["event_date", "fore_adjust_factor"], errors="ignore")
