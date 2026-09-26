from __future__ import annotations

import pandas as pd

from aquant.data.continuous import build_point_in_time_continuous
from aquant.data.providers.baostock_provider import BaoStockProvider
from aquant.data.providers.base import is_shsz_a_share
from aquant.data.providers.eastmoney_akshare import EastMoneyAKShareProvider
from aquant.data.quality import validate_bar_frame
from aquant.data.reference_service import ReferenceDataService
from aquant.data.schema import FIELDS
from aquant.data.safe_fetch import fetch_stock_list_with_timeout
from aquant.data.storage import MarketStore
from aquant.data.universe_service import HistoricalUniverseService
from config import SETTINGS


class MarketDataService:
    """面向扫描/回测的统一数据服务。

    上层继续使用旧版 code/name/date/OHLCV 字段，底层为：
    多数据源 -> 统一字段 -> 质量检查 -> Parquet/DuckDB 本地库。
    """

    def __init__(self, store_root: str | None = None) -> None:
        root = str(SETTINGS.data_store_dir) if store_root is None else store_root
        self.primary = EastMoneyAKShareProvider()
        self.backup = BaoStockProvider()
        self.store = MarketStore(root)
        self.reference = ReferenceDataService(root)
        self.universe = HistoricalUniverseService(root)
        self.adjust = SETTINGS.adjust if SETTINGS.adjust in {"none", "qfq", "hfq"} else "qfq"
        self._attempted_today: set[tuple[str, str]] = set()
        self._cached_market_date: pd.Timestamp | None = None

    def stock_list(self) -> pd.DataFrame:
        errors: list[str] = []
        df = pd.DataFrame()
        sources = (
            ("eastmoney", 18.0, False),
            ("exchange", 30.0, True),
            ("baostock", 45.0, True),
        )

        for source, timeout_seconds, force in sources:
            try:
                df = self.reference.stock_list(
                    fetcher=lambda s=source, t=timeout_seconds: fetch_stock_list_with_timeout(s, t),
                    max_age_hours=float(SETTINGS.cache_hours),
                    force=force,
                )
                if df is not None and not df.empty:
                    break
            except Exception as exc:
                errors.append(f"{source}: {exc}")

        if df is None or df.empty:
            local = self.reference.store.read_security_master()
            if not local.empty:
                df = local
            else:
                raise RuntimeError(
                    "股票基础库主源、交易所备用源、BaoStock 和本地快照全部不可用: "
                    + " | ".join(errors)
                )

        out = df.copy()
        out["code"] = out["code"].astype(str).str.zfill(6)
        out["name"] = out["name"].astype(str).str.strip()
        out = out.drop_duplicates("code")

        if SETTINGS.exclude_st:
            out = out[~out["name"].str.upper().str.contains("ST", na=False)]
        if SETTINGS.market_scope == "shsz":
            out = out[out["code"].map(is_shsz_a_share)]
        elif SETTINGS.exclude_bj:
            out = out[~out["code"].str.startswith(("4", "8", "92"))]
        return out[["code", "name"]].reset_index(drop=True)

    def trade_calendar(self, refresh: bool = False) -> pd.DataFrame:
        return self.reference.trade_calendar(
            max_age_hours=float(SETTINGS.cache_hours),
            force=refresh,
        )

    def historical_securities(self, refresh: bool = False) -> pd.DataFrame:
        """返回带上市/退市日期的沪深历史证券资料。"""
        out = self.universe.all_securities(refresh=refresh)
        if SETTINGS.market_scope == "shsz" and not out.empty:
            out = out[out["code"].map(is_shsz_a_share)].reset_index(drop=True)
        return out

    def historical_universe(
        self,
        as_of_date: str | pd.Timestamp,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """返回指定历史日期当时已上市且尚未退市的股票池。

        当前生命周期主资料来自 BaoStock，北交所历史覆盖后续再补独立来源。
        """
        out = self.universe.universe_on(as_of_date, refresh=refresh)
        if SETTINGS.market_scope == "shsz" and not out.empty:
            out = out[out["code"].map(is_shsz_a_share)].reset_index(drop=True)
        return out

    def latest_trade_date(self) -> pd.Timestamp:
        if self._cached_market_date is not None:
            return self._cached_market_date
        today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
        latest = self.reference.latest_trade_date(
            on_or_before=today,
            max_age_hours=float(SETTINGS.cache_hours),
        )
        self._cached_market_date = latest if latest is not None else today
        return self._cached_market_date

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
        keep = [
            c
            for c in [
                "date",
                "open",
                "high",
                "low",
                "close",
                "preclose",
                "volume",
                "amount",
                "turnover",
                "pct_change",
                "trade_status",
                "is_st",
            ]
            if c in out.columns
        ]
        out = out[keep]
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out = out.dropna(subset=["date"])

        # 停牌日必须保留在执行时间轴里。可交易行仍要求 OHLCV 完整；
        # trade_status=0 的行即使价格为空，也用于阻断买卖和保留真实日期。
        if "trade_status" in out.columns:
            status = pd.to_numeric(out["trade_status"], errors="coerce")
            suspended = status.eq(0)
        else:
            suspended = pd.Series(False, index=out.index)

        core = [c for c in ["open", "high", "low", "close", "volume"] if c in out.columns]
        if core:
            complete_core = out[core].notna().all(axis=1)
            out = out[suspended | complete_core].copy()

        return (
            out.sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )

    def _fetch_with_fallback(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str,
        *,
        prefer_point_in_time: bool = False,
    ) -> pd.DataFrame:
        errors: list[str] = []
        providers = (
            (self.backup, self.primary)
            if prefer_point_in_time
            else (self.primary, self.backup)
        )
        for provider in providers:
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

    def history_range(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        adjust: str | None = None,
        refresh: bool = False,
        prefer_point_in_time: bool = False,
    ) -> pd.DataFrame:
        """读取/补齐指定历史区间，供历史回测和退市股票研究使用。"""
        symbol = str(code).strip().zfill(6)[-6:]
        mode = adjust or self.adjust
        if mode not in {"none", "qfq", "hfq"}:
            raise ValueError(f"不支持的复权方式: {mode}")

        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        local = self.store.read_daily(
            symbol,
            mode,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
        )

        need_fetch = refresh or local.empty
        if not local.empty:
            local_dates = pd.to_datetime(local[FIELDS.trade_date], errors="coerce")
            if local_dates.min() > start or local_dates.max() < end:
                need_fetch = True

        if need_fetch:
            try:
                self._fetch_with_fallback(
                    symbol,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                    mode,
                    prefer_point_in_time=prefer_point_in_time,
                )
                if mode != "none":
                    try:
                        self._fetch_with_fallback(
                            symbol,
                            start.strftime("%Y-%m-%d"),
                            end.strftime("%Y-%m-%d"),
                            "none",
                            prefer_point_in_time=prefer_point_in_time,
                        )
                    except Exception:
                        pass
            except Exception:
                if local.empty:
                    raise

        result = self.store.read_daily(
            symbol,
            mode,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
        )
        if result.empty:
            result = local
        return self._legacy_frame(result)

    def research_history_range(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        refresh: bool = False,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """返回(研究信号价格, 未复权执行价格)。

        历史研究优先从未复权点时字段构造连续信号价格，避免直接使用
        查询时点的前复权历史造成潜在的未来公司行动信息渗透。
        若 pct_change 覆盖不足，则退回 qfq 仅作为兼容后备。
        """
        execution = self.history_range(
            code,
            start_date,
            end_date,
            adjust="none",
            refresh=refresh,
            prefer_point_in_time=True,
        )
        if execution.empty:
            return pd.DataFrame(), pd.DataFrame()

        coverage = 0.0
        if "pct_change" in execution.columns:
            coverage = float(
                pd.to_numeric(
                    execution["pct_change"], errors="coerce"
                ).notna().mean()
            )

        if coverage >= 0.70:
            signal = build_point_in_time_continuous(execution)
            return signal, execution

        signal = self.history_range(
            code,
            start_date,
            end_date,
            adjust=self.adjust,
            refresh=refresh,
            prefer_point_in_time=True,
        )
        if not signal.empty:
            signal = signal.copy()
            signal["signal_price_mode"] = "qfq_fallback"
        return signal, execution

    def history(self, code: str, refresh: bool = False) -> pd.DataFrame:
        symbol = str(code).strip().zfill(6)[-6:]
        market_date = self.latest_trade_date()
        key = (symbol, self.adjust)

        latest = self.store.latest_date(symbol, self.adjust)
        local = self.store.read_daily(symbol, self.adjust) if latest is not None else pd.DataFrame()

        calendar_days = max(int(SETTINGS.history_days * 1.8), SETTINGS.history_days + 60)
        if refresh or latest is None:
            start = market_date - pd.Timedelta(days=calendar_days)
        else:
            start = latest + pd.Timedelta(days=1)

        should_fetch = refresh or latest is None or latest < market_date
        if key in self._attempted_today and not refresh:
            should_fetch = False

        if should_fetch and start <= market_date:
            self._attempted_today.add(key)
            start_s = start.strftime("%Y-%m-%d")
            end_s = market_date.strftime("%Y-%m-%d")
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

    def refresh_catalog(self) -> None:
        self.store.refresh_catalog()
        self.reference.store.refresh_catalog(self.store.paths.catalog)
