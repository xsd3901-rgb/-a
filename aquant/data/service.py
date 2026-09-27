from __future__ import annotations

import threading
import time

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
from aquant.data.source_quality import SourceQualityLog
from aquant.data.universe_service import HistoricalUniverseService
from config import SETTINGS


_BAOSTOCK_GATE = threading.BoundedSemaphore(1)
_EASTMONEY_GATE = threading.BoundedSemaphore(2)


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
        self.source_quality = SourceQualityLog(self.store.paths.root)
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
                "provider",
                "adapter",
                "quality_status",
                "fetched_at",
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
        for attempt_order, provider in enumerate(providers, start=1):
            started = time.monotonic()
            try:
                gate = (
                    _BAOSTOCK_GATE
                    if provider.info.provider == "baostock"
                    else _EASTMONEY_GATE
                )
                with gate:
                    frame = provider.fetch_daily(
                        symbol,
                        start_date,
                        end_date,
                        adjust=adjust,
                    )

                elapsed_ms = (time.monotonic() - started) * 1000.0
                if frame is None or frame.empty:
                    detail = "empty"
                    errors.append(
                        f"{provider.info.provider}: {detail}"
                    )
                    self.source_quality.record(
                        symbol=symbol,
                        provider=provider.info.provider,
                        adapter=provider.info.adapter,
                        adjust=adjust,
                        start_date=start_date,
                        end_date=end_date,
                        attempt_order=attempt_order,
                        outcome="empty",
                        rows=0,
                        elapsed_ms=elapsed_ms,
                        prefer_point_in_time=prefer_point_in_time,
                        detail=detail,
                    )
                    continue

                self.store.save_raw(
                    frame,
                    provider.info.provider,
                    symbol,
                    adjust,
                )
                issues = validate_bar_frame(frame)
                fatal = [
                    item
                    for item in issues
                    if item.severity == "error"
                ]
                if fatal:
                    detail = "; ".join(
                        item.message for item in fatal
                    )
                    errors.append(
                        f"{provider.info.provider}: {detail}"
                    )
                    self.source_quality.record(
                        symbol=symbol,
                        provider=provider.info.provider,
                        adapter=provider.info.adapter,
                        adjust=adjust,
                        start_date=start_date,
                        end_date=end_date,
                        attempt_order=attempt_order,
                        outcome="quality_fail",
                        rows=len(frame),
                        elapsed_ms=elapsed_ms,
                        prefer_point_in_time=prefer_point_in_time,
                        detail=detail,
                    )
                    continue

                clean = frame.copy()
                clean[FIELDS.quality_status] = "ok"
                self.store.save_standard(
                    clean,
                    symbol,
                    adjust,
                )
                self.source_quality.record(
                    symbol=symbol,
                    provider=provider.info.provider,
                    adapter=provider.info.adapter,
                    adjust=adjust,
                    start_date=start_date,
                    end_date=end_date,
                    attempt_order=attempt_order,
                    outcome="success",
                    rows=len(clean),
                    elapsed_ms=elapsed_ms,
                    prefer_point_in_time=prefer_point_in_time,
                )
                return clean
            except Exception as exc:
                elapsed_ms = (
                    time.monotonic() - started
                ) * 1000.0
                detail = str(exc)[:1000]
                errors.append(
                    f"{provider.info.provider}: {detail}"
                )
                self.source_quality.record(
                    symbol=symbol,
                    provider=provider.info.provider,
                    adapter=provider.info.adapter,
                    adjust=adjust,
                    start_date=start_date,
                    end_date=end_date,
                    attempt_order=attempt_order,
                    outcome="error",
                    rows=0,
                    elapsed_ms=elapsed_ms,
                    prefer_point_in_time=prefer_point_in_time,
                    detail=detail,
                )

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

    def sync_history_range(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        adjust: str | None = None,
        refresh: bool = False,
        prefer_point_in_time: bool = False,
    ) -> dict:
        """仅同步本地文件并返回轻量统计，不加载完整 OHLCV 到内存。"""
        symbol = str(code).strip().zfill(6)[-6:]
        mode = adjust or self.adjust
        if mode not in {"none", "qfq", "hfq"}:
            raise ValueError(f"不支持的复权方式: {mode}")

        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize()
        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        start_s = start.strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")
        local_stats = self.store.light_stats(
            symbol,
            mode,
            start_date=start_s,
            end_date=end_s,
        )

        segments: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        if refresh or int(local_stats.get("rows", 0)) == 0:
            segments.append((start, end))
        else:
            local_min = local_stats.get("start")
            local_max = local_stats.get("end")
            if local_min is None or local_max is None:
                segments.append((start, end))
            else:
                local_min = pd.Timestamp(local_min).normalize()
                local_max = pd.Timestamp(local_max).normalize()
                if local_min > start:
                    head_end = local_min - pd.Timedelta(days=1)
                    if start <= head_end:
                        segments.append((start, head_end))
                if local_max < end:
                    tail_start = local_max + pd.Timedelta(days=1)
                    if tail_start <= end:
                        segments.append((tail_start, end))

        fetch_errors: list[Exception] = []
        for seg_start, seg_end in segments:
            try:
                self._fetch_with_fallback(
                    symbol,
                    seg_start.strftime("%Y-%m-%d"),
                    seg_end.strftime("%Y-%m-%d"),
                    mode,
                    prefer_point_in_time=prefer_point_in_time,
                )
            except Exception as exc:
                fetch_errors.append(exc)

        if mode != "none" and segments:
            try:
                self._archive_unadjusted(symbol, start_s, end_s)
            except Exception:
                pass

        stats = self.store.light_stats(
            symbol,
            mode,
            start_date=start_s,
            end_date=end_s,
        )
        if int(stats.get("rows", 0)) == 0 and fetch_errors:
            raise fetch_errors[-1]
        stats["fetched_segments"] = [
            (
                seg_start.strftime("%Y-%m-%d"),
                seg_end.strftime("%Y-%m-%d"),
            )
            for seg_start, seg_end in segments
        ]
        stats["adjust"] = mode
        stats["symbol"] = symbol
        return stats

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
        """同步后读取完整指定历史区间。"""
        symbol = str(code).strip().zfill(6)[-6:]
        mode = adjust or self.adjust
        self.sync_history_range(
            symbol,
            start_date,
            end_date,
            adjust=mode,
            refresh=refresh,
            prefer_point_in_time=prefer_point_in_time,
        )
        result = self.store.read_daily(
            symbol,
            mode,
            start_date=pd.Timestamp(start_date).strftime("%Y-%m-%d"),
            end_date=pd.Timestamp(end_date).strftime("%Y-%m-%d"),
        )
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
            pct = pd.to_numeric(execution["pct_change"], errors="coerce")
            if "trade_status" in execution.columns:
                status = pd.to_numeric(
                    execution["trade_status"], errors="coerce"
                )
                sample = pct[status.eq(1)]
            else:
                sample = pct
            coverage = float(sample.notna().mean()) if len(sample) else 0.0

        if coverage >= 0.95:
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

        # 日常扫描只需要最近 history_days 根 K 线。用自然日缓冲窗口
        # 读取局部 Parquet，避免每次扫描都把 3~4 年历史整表载入内存。
        calendar_days = max(
            int(SETTINGS.history_days * 1.8),
            SETTINGS.history_days + 60,
        )
        read_start = market_date - pd.Timedelta(days=calendar_days)
        read_start_s = read_start.strftime("%Y-%m-%d")
        market_date_s = market_date.strftime("%Y-%m-%d")

        latest = self.store.latest_date(symbol, self.adjust)
        local = (
            self.store.read_daily(
                symbol,
                self.adjust,
                start_date=read_start_s,
                end_date=market_date_s,
            )
            if latest is not None
            else pd.DataFrame()
        )

        if refresh or latest is None:
            fetch_start = read_start
        else:
            fetch_start = latest + pd.Timedelta(days=1)

        should_fetch = (
            refresh
            or latest is None
            or latest < market_date
        )
        if key in self._attempted_today and not refresh:
            should_fetch = False

        if should_fetch and fetch_start <= market_date:
            self._attempted_today.add(key)
            start_s = fetch_start.strftime("%Y-%m-%d")
            try:
                self._fetch_with_fallback(
                    symbol,
                    start_s,
                    market_date_s,
                    self.adjust,
                )
                self._archive_unadjusted(
                    symbol,
                    start_s,
                    market_date_s,
                )
            except Exception:
                if local.empty:
                    raise

        result = self.store.read_daily(
            symbol,
            self.adjust,
            start_date=read_start_s,
            end_date=market_date_s,
        )
        if result.empty:
            result = local

        legacy = self._legacy_frame(result)
        if legacy.empty:
            return legacy
        return (
            legacy.tail(SETTINGS.history_days)
            .reset_index(drop=True)
        )

    def refresh_catalog(self) -> None:
        self.store.refresh_catalog()
        self.reference.store.refresh_catalog(self.store.paths.catalog)
