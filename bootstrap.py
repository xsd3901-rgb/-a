from __future__ import annotations

import gc
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from aquant.runtime.checkpoint import JsonCheckpoint
from aquant.runtime.resources import current_profile
from config import SETTINGS, ensure_directories


def _bootstrap_universe(
    provider: MarketDataService,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    refresh: bool,
) -> pd.DataFrame:
    try:
        stocks = provider.historical_securities(refresh=refresh).copy()
        if stocks.empty:
            raise RuntimeError("历史生命周期股票池为空")

        listing_source = (
            stocks["listing_date"]
            if "listing_date" in stocks.columns
            else pd.Series(pd.NaT, index=stocks.index)
        )
        delisting_source = (
            stocks["delisting_date"]
            if "delisting_date" in stocks.columns
            else pd.Series(pd.NaT, index=stocks.index)
        )
        listing = pd.to_datetime(
            listing_source, errors="coerce"
        ).dt.normalize()
        delisting = pd.to_datetime(
            delisting_source, errors="coerce"
        ).dt.normalize()
        stocks["listing_date"] = listing
        stocks["delisting_date"] = delisting

        listed = listing.isna() | (listing <= end_date)
        alive = delisting.isna() | (delisting >= start_date)
        return (
            stocks[listed & alive]
            .drop_duplicates("code")
            .reset_index(drop=True)
        )
    except Exception:
        stocks = provider.stock_list().copy()
        stocks["listing_date"] = pd.NaT
        stocks["delisting_date"] = pd.NaT
        return stocks


def _stats_provider_label(stats: dict) -> str:
    providers = [
        str(value).strip()
        for value in (stats.get("providers") or [])
        if str(value).strip()
    ]
    if not providers:
        return "local/unknown"
    if len(providers) == 1:
        return providers[0]
    return "mixed:" + "+".join(sorted(set(providers)))


def _provider_label(frame: pd.DataFrame | None) -> str:
    if frame is None or frame.empty or "provider" not in frame.columns:
        return "local/unknown"
    providers = sorted(
        {
            str(value).strip()
            for value in frame["provider"].dropna().tolist()
            if str(value).strip()
        }
    )
    if not providers:
        return "local/unknown"
    if len(providers) == 1:
        return providers[0]
    return "mixed:" + "+".join(providers)


def _bootstrap_one(
    stock: dict,
    *,
    start_date: pd.Timestamp,
    market_end: pd.Timestamp,
    refresh: bool,
) -> tuple[dict, str | None, int]:
    code = str(stock["code"]).zfill(6)
    name = str(stock.get("name") or "")
    listing_date = stock.get("listing_date", pd.NaT)
    delisting_date = stock.get("delisting_date", pd.NaT)

    stock_start = start_date
    if pd.notna(listing_date):
        stock_start = max(
            stock_start,
            pd.Timestamp(listing_date).normalize(),
        )
    stock_end = market_end
    if pd.notna(delisting_date):
        stock_end = min(
            stock_end,
            pd.Timestamp(delisting_date).normalize(),
        )

    base = {
        "代码": code,
        "名称": name,
        "开始": stock_start.strftime("%Y-%m-%d"),
        "结束": stock_end.strftime("%Y-%m-%d"),
    }

    if stock_start >= stock_end:
        return (
            {
                **base,
                "Raw根数": 0,
                "连续研究价根数": 0,
                "QFQ根数": 0,
                "Raw来源": "n/a",
                "QFQ来源": "n/a",
                "信号价格模式": "n/a",
                "状态": "SKIPPED_RANGE",
            },
            None,
            0,
        )

    attempts = max(1, int(SETTINGS.bootstrap_retry_attempts))
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            # 每个线程独立数据服务；底层按数据源加闸门，BaoStock 串行，
            # EastMoney 最多双并发，避免免费接口被并发打爆。
            provider = MarketDataService()
            raw_stats = provider.sync_history_range(
                code,
                stock_start.strftime("%Y-%m-%d"),
                stock_end.strftime("%Y-%m-%d"),
                adjust="none",
                refresh=refresh,
                prefer_point_in_time=True,
            )
            qfq_stats = provider.sync_history_range(
                code,
                stock_start.strftime("%Y-%m-%d"),
                stock_end.strftime("%Y-%m-%d"),
                adjust="qfq",
                refresh=refresh,
                prefer_point_in_time=False,
            )

            pct_coverage = float(
                raw_stats.get("pct_change_coverage", 0.0) or 0.0
            )
            if pct_coverage >= 0.95:
                signal_mode = "point_in_time_continuous"
                signal_rows = int(raw_stats.get("tradable_rows", 0))
            else:
                signal_mode = "qfq_fallback"
                signal_rows = int(qfq_stats.get("rows", 0))

            return (
                {
                    **base,
                    "Raw根数": int(raw_stats.get("rows", 0)),
                    "连续研究价根数": signal_rows,
                    "QFQ根数": int(qfq_stats.get("rows", 0)),
                    "Raw来源": _stats_provider_label(raw_stats),
                    "QFQ来源": _stats_provider_label(qfq_stats),
                    "信号价格模式": signal_mode,
                    "Raw涨跌幅覆盖率%": round(pct_coverage * 100.0, 2),
                    "Raw本次下载区间": len(
                        raw_stats.get("fetched_segments") or []
                    ),
                    "QFQ本次下载区间": len(
                        qfq_stats.get("fetched_segments") or []
                    ),
                    "尝试次数": attempt,
                    "状态": "OK",
                },
                None,
                attempt,
            )
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(
                    float(SETTINGS.bootstrap_retry_backoff_seconds)
                    * attempt
                )

    error = str(last_error)[:500] if last_error is not None else "未知错误"
    return (
        {
            **base,
            "Raw根数": 0,
            "连续研究价根数": 0,
            "QFQ根数": 0,
            "Raw来源": "",
            "QFQ来源": "",
            "信号价格模式": "",
            "尝试次数": attempts,
            "状态": "FAILED",
        },
        error,
        attempts,
    )


def _write_source_quality(status: pd.DataFrame) -> None:
    """建库结束后刷新本地来源追踪报告，不额外访问远端。"""
    try:
        from aquant.research.source_quality import (
            run_source_quality,
        )

        run_source_quality(
            include_crosscheck=False,
        )
    except Exception as exc:
        print(f"数据源质量报告暂不可用，建库结果仍保留: {exc}")


def bootstrap_market(
    limit: int | None = None,
    refresh: bool = False,
    calendar_days: int | None = None,
    progress: Callable[[str], None] | None = None,
    codes: list[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """建立/增量补齐沪深 A 股本地研究库。

    优化原则：
    - history_range 只补本地缺失的首尾区间，不重复下载完整历史；
    - 同一市场日期的中断任务使用原子检查点续跑；
    - 免费接口失败自动有限重试；
    - 并发量由内存档位决定，再受免费接口安全上限约束；
    - BaoStock 底层串行，避免其全局 login/logout 状态被线程冲撞。
    """
    ensure_directories()
    provider = MarketDataService()
    runtime = current_profile()

    def announce(message: str) -> None:
        print(message)
        if progress is not None:
            progress(message)

    market_end = provider.latest_trade_date()
    days = int(calendar_days or SETTINGS.bootstrap_calendar_days)
    start_date = market_end - pd.Timedelta(days=days)

    stocks = _bootstrap_universe(
        provider,
        start_date,
        market_end,
        refresh=refresh,
    )
    if codes:
        wanted = {str(code).zfill(6) for code in codes}
        stocks = stocks[
            stocks["code"].astype(str).str.zfill(6).isin(wanted)
        ].copy()
    if limit and limit > 0:
        stocks = stocks.head(limit)
    stocks = stocks.reset_index(drop=True)

    signature = (
        f"bootstrap-v3|{SETTINGS.market_scope}|{SETTINGS.adjust}|"
        f"{start_date:%Y-%m-%d}|{market_end:%Y-%m-%d}"
    )
    checkpoint = JsonCheckpoint(
        SETTINGS.report_dir / "bootstrap_checkpoint.json",
        signature,
    )
    if (refresh and not codes) or not bool(SETTINGS.bootstrap_resume):
        checkpoint.clear()

    rows: list[dict] = []
    errors: list[dict] = []

    pending: list[dict] = []
    resumed = 0
    for _, stock_row in stocks.iterrows():
        stock = stock_row.to_dict()
        code = str(stock["code"]).zfill(6)
        checkpoint_ok = False
        if bool(SETTINGS.bootstrap_resume) and not refresh and checkpoint.is_completed(code):
            # 检查点只负责断点续跑，不能掩盖用户手工删除的数据文件。
            try:
                checkpoint_ok = (
                    provider.store.latest_date(code, "none") is not None
                    and provider.store.latest_date(code, "qfq") is not None
                )
            except Exception:
                checkpoint_ok = False

        if checkpoint_ok:
            payload = dict(checkpoint.state.completed.get(code) or {})
            payload.setdefault("代码", code)
            payload.setdefault("名称", str(stock.get("name") or ""))
            payload["状态"] = "SKIPPED_RESUME"
            rows.append(payload)
            resumed += 1
        else:
            pending.append(stock)

    workers = max(
        1,
        min(
            int(runtime.download_workers),
            int(SETTINGS.bootstrap_max_workers),
            max(1, len(pending)),
        ),
    )

    announce(
        f"本地建库股票池: {len(stocks)} | "
        f"{start_date:%Y-%m-%d} ~ {market_end:%Y-%m-%d}"
    )
    announce(
        f"资源档位: {runtime.name} | 建库并发 {workers} | "
        f"断点已完成 {resumed} | 待处理 {len(pending)}"
    )

    completed_now = 0
    run_started = time.monotonic()

    def handle_result(
        row: dict,
        error: str | None,
        attempts: int,
    ) -> None:
        nonlocal completed_now
        code = str(row.get("代码") or "")
        rows.append(row)
        if error is None:
            if row.get("状态") in {"OK", "SKIPPED_RANGE"}:
                checkpoint.mark_completed(code, row)
                completed_now += 1
        else:
            errors.append(
                {
                    "代码": code,
                    "名称": row.get("名称", ""),
                    "尝试次数": attempts,
                    "错误": error,
                }
            )
            checkpoint.mark_failed(
                code,
                error=error,
                attempts=attempts,
                payload=row,
            )

        done = resumed + completed_now + len(errors)
        if done == len(stocks) or done % max(1, runtime.batch_size) == 0:
            ok_count = sum(
                item.get("状态") in {"OK", "SKIPPED_RESUME", "SKIPPED_RANGE"}
                for item in rows
            )
            elapsed = max(0.001, time.monotonic() - run_started)
            actually_finished = completed_now + len(errors)
            per_minute = actually_finished / elapsed * 60.0
            remaining = max(0, len(pending) - actually_finished)
            eta_minutes = (
                remaining / per_minute
                if per_minute > 0
                else 0.0
            )
            announce(
                f"建库进度 {done}/{len(stocks)} | "
                f"成功/已完成 {ok_count} | 失败 {len(errors)} | "
                f"速度 {per_minute:.1f}只/分钟 | ETA {eta_minutes:.1f}分钟"
            )
            gc.collect()

    if workers == 1:
        for stock in pending:
            result = _bootstrap_one(
                stock,
                start_date=start_date,
                market_end=market_end,
                refresh=refresh,
            )
            handle_result(*result)
    else:
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="aquant-bootstrap",
        ) as executor:
            futures = [
                executor.submit(
                    _bootstrap_one,
                    stock,
                    start_date=start_date,
                    market_end=market_end,
                    refresh=refresh,
                )
                for stock in pending
            ]
            for future in as_completed(futures):
                handle_result(*future.result())

    context_summary: dict[str, object] = {}
    try:
        context = MarketContextService()
        index_counts = context.refresh_core_indices(
            start_date.strftime("%Y-%m-%d"),
            market_end.strftime("%Y-%m-%d"),
        )
        context_summary["指数"] = index_counts
        industry = context.industry_map(force=refresh)
        context_summary["行业映射"] = len(industry)
    except Exception as exc:
        context_summary["错误"] = str(exc)

    try:
        provider.refresh_catalog()
    except Exception as exc:
        context_summary["目录刷新错误"] = str(exc)

    status = pd.DataFrame(rows)
    if not status.empty and "代码" in status.columns:
        status = status.sort_values("代码").reset_index(drop=True)

    successful_states = {"OK", "SKIPPED_RESUME", "SKIPPED_RANGE"}
    ok_count = (
        int(status["状态"].isin(successful_states).sum())
        if not status.empty and "状态" in status.columns
        else 0
    )
    source_quality = (
        status["Raw来源"].fillna("").astype(str).value_counts().to_dict()
        if not status.empty and "Raw来源" in status.columns
        else {}
    )

    elapsed_seconds = round(time.monotonic() - run_started, 2)
    processed_count = max(0, len(pending))
    summary = {
        "市场": "沪深A股",
        "开始日期": start_date.strftime("%Y-%m-%d"),
        "结束日期": market_end.strftime("%Y-%m-%d"),
        "股票池": int(len(stocks)),
        "定向修复": bool(codes),
        "成功股票": ok_count,
        "本次实际处理": int(len(pending)),
        "断点跳过": int(resumed),
        "失败股票": int(len(errors)),
        "成功率%": round(ok_count / len(stocks) * 100.0, 2)
        if len(stocks)
        else 0.0,
        "资源档位": runtime.name,
        "建库并发": workers,
        "耗时秒": elapsed_seconds,
        "平均实际处理秒/只": round(
            elapsed_seconds / processed_count, 3
        ) if processed_count else 0.0,
        "检查点": str(checkpoint.path),
        "Raw来源分布": source_quality,
        "数据目录": str(SETTINGS.data_store_dir),
        "上下文": context_summary,
    }

    status.to_csv(
        SETTINGS.report_dir / "bootstrap_status.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame([summary]).to_json(
        SETTINGS.report_dir / "bootstrap_summary.json",
        orient="records",
        force_ascii=False,
        indent=2,
    )
    if errors:
        pd.DataFrame(errors).to_csv(
            SETTINGS.report_dir / "bootstrap_errors.csv",
            index=False,
            encoding="utf-8-sig",
        )
    else:
        error_path = SETTINGS.report_dir / "bootstrap_errors.csv"
        if error_path.exists():
            error_path.unlink()

    _write_source_quality(status)

    announce(
        f"建库完成：成功/已完成 {ok_count}/{len(stocks)}，"
        f"失败 {len(errors)}，断点跳过 {resumed}。"
    )
    return status, summary
