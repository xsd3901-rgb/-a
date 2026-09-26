from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from aquant.data.providers.baostock_provider import BaoStockProvider
from aquant.data.providers.eastmoney_akshare import EastMoneyAKShareProvider
from aquant.data.quality import validate_bar_frame
from aquant.data.schema import FIELDS
from aquant.data.storage import MarketStore
from config import SETTINGS


class MarketDataService:
    """面向扫描/回测的统一数据服务。

    上层继续使用旧版 code/name/date/OHLCV 字段，底层改为：
    多数据源 -> 统一字段 -> 质量检查 -> Parquet/DuckDB 本地库。
    """

    def __init__(self, store_root: str = "data_store") -> None:
        self.primary = EastMoneyAKShareProvider()
        self.backup = BaoStockProvider()
        self.store = MarketStore(store_root)
        self.adjust = SETTINGS.adjust if SETTINGS.adjust in {"none", "qfq", "hfq"} else "qfq"
        self._attempted_today: set[tuple[str, str]] = set()

    def stock_list(self) -> pd.DataFrame:
        df = self.primary.fetch_stock_list()
        if df is None or df.empty:
            raise RuntimeError("全 A 股票列表获取失败")

        out = df.rename(columns={"symbol": "code"}).copy()
        out["code"] = out["code"].astype(str).str.zfill(6)
        out["name"] = out["name"].astype(str).str.strip()
        out = out.drop_duplicates("code")

        if SETTINGS.exclude_st:
            out = out[~out["name"].str.upper().str.contains("ST", na=False)]
        if SETTINGS.exclude_bj:
            out = out[~out["code"].str.startswith(("4", "8", "92"))]
        return out[["code", "name"]].reset_index(drop=True)

    @staticmethod
    def _legacy_frame(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        rename = {
            FIELDS.trade_date: "date",
            FIELDS.open: "open",
            FIELDS.high: "high",
            FIELDS.low: "low",
            FIELDS.close: "close",
            FIELDS.volume: "volume",
            FIELDS.amount: "amount",
            FIELDS.turnover: "turnover",
        }
        out = df.rename(columns=rename).copy()
        keep = [c for c in ["date", "open", "high", "low", "close", "volume", "amount", "turnover"] if c in out.columns]
        out = out[keep]
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        required = ["date", "open", "high", "low", "close", "volume"]
        out = out.dropna(subset=[c for c in required if c in out.columns])
        return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    def _fetch_with_fallback(self, symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
        errors: list[str] = []
        for provider in (self.primary, self.backup):
            try:
                frame = provider.fetch_daily(symbol, start_date, end_date, adjust=adjust)
                if frame is None or frame.empty:
                    errors.append(f"{provider.info.provider}: empty")
                    continue

                self.store.save_raw(frame, provider.info.provider, symbol, adjust)
                issues = validate_bar_frame(frame)
                fatal = [item for item in issues if item.severity == "error"]
                if fatal:
                    errors.append(
                        f"{provider.info.provider}: " + "; ".join(item.message for item in fatal)
                    )
                    continue

                clean = frame.copy()
                clean[FIELDS.quality_status] = "ok"
                self.store.save_standard(clean, symbol, adjust)
                return clean
            except Exception as exc:
                errors.append(f"{provider.info.provider}: {exc}")

        raise RuntimeError(f"{symbol} 行情获取失败: " + " | ".join(errors))

    def _archive_unadjusted(self, symbol: str, start_date: str, end_date: str) -> None:
        """尽力保存未复权底稿；失败不影响当前扫描。"""
        if self.adjust == "none":
            return
        try:
            latest = self.store.latest_date(symbol, "none")
            archive_start = start_date
            if latest is not None:
                next_date = latest + pd.Timedelta(days=1)
                archive_start = max(pd.Timestamp(start_date), next_date).strftime("%Y-%m-%d")
            if archive_start <= end_date:
                self._fetch_with_fallback(symbol, archive_start, end_date, "none")
        except Exception:
            pass

    def history(self, code: str, refresh: bool = False) -> pd.DataFrame:
        symbol = str(code).strip().zfill(6)[-6:]
        today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
        key = (symbol, self.adjust)

        latest = self.store.latest_date(symbol, self.adjust)
        local = self.store.read_daily(symbol, self.adjust) if latest is not None else pd.DataFrame()

        calendar_days = max(int(SETTINGS.history_days * 1.8), SETTINGS.history_days + 60)
        if refresh or latest is None:
            start = today - pd.Timedelta(days=calendar_days)
        else:
            start = latest + pd.Timedelta(days=1)

        should_fetch = refresh or latest is None or start <= today
        if key in self._attempted_today and not refresh:
            should_fetch = False

        if should_fetch:
            self._attempted_today.add(key)
            start_s = start.strftime("%Y-%m-%d")
            end_s = today.strftime("%Y-%m-%d")
            try:
                self._fetch_with_fallback(symbol, start_s, end_s, self.adjust)
                self._archive_unadjusted(symbol, start_s, end_s)
            except Exception:
                if local.empty:
                    raise

        result = self.store.read_daily(symbol, self.adjust)
        if result.empty:
            result = local
        legacy = self._legacy_frame(result)
        if legacy.empty:
            return legacy
        return legacy.tail(SETTINGS.history_days).reset_index(drop=True)
