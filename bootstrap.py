from __future__ import annotations

import gc

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
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

        listing = pd.to_datetime(
            stocks.get("listing_date"), errors="coerce"
        ).dt.normalize()
        delisting = pd.to_datetime(
            stocks.get("delisting_date"), errors="coerce"
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


def bootstrap_market(
    limit: int | None = None,
    refresh: bool = False,
    calendar_days: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """建立/增量补齐沪深 A 股本地研究库。

    Raw 未复权层是回测成交底稿；qfq 只服务当前扫描兼容。
    历史研究正式使用 research_history_range 生成点时连续信号价格。
    """
    ensure_directories()
    provider = MarketDataService()
    runtime = current_profile()

    market_end = provider.latest_trade_date()
    days = int(calendar_days or SETTINGS.bootstrap_calendar_days)
    start_date = market_end - pd.Timedelta(days=days)

    stocks = _bootstrap_universe(
        provider,
        start_date,
        market_end,
        refresh=refresh,
    )
    if limit and limit > 0:
        stocks = stocks.head(limit)

    rows: list[dict] = []
    errors: list[dict] = []
    flush_every = max(1, runtime.batch_size)

    print(
        f"本地建库股票池: {len(stocks)} | "
        f"{start_date:%Y-%m-%d} ~ {market_end:%Y-%m-%d}"
    )
    print(
        f"资源档位: {runtime.name} | 批次 {runtime.batch_size} | "
        f"数据目录 {SETTINGS.data_store_dir}"
    )

    for pos, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        listing_date = row.get("listing_date", pd.NaT)
        delisting_date = row.get("delisting_date", pd.NaT)

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
        if stock_start >= stock_end:
            continue

        try:
            signal, raw = provider.research_history_range(
                code,
                stock_start.strftime("%Y-%m-%d"),
                stock_end.strftime("%Y-%m-%d"),
                refresh=refresh,
            )
            qfq = provider.history_range(
                code,
                stock_start.strftime("%Y-%m-%d"),
                stock_end.strftime("%Y-%m-%d"),
                adjust="qfq",
                refresh=refresh,
                prefer_point_in_time=False,
            )
            rows.append(
                {
                    "代码": code,
                    "名称": name,
                    "开始": stock_start.strftime("%Y-%m-%d"),
                    "结束": stock_end.strftime("%Y-%m-%d"),
                    "Raw根数": len(raw),
                    "连续研究价根数": len(signal),
                    "QFQ根数": len(qfq),
                    "状态": "OK",
                }
            )
        except Exception as exc:
            errors.append(
                {"代码": code, "名称": name, "错误": str(exc)[:500]}
            )
            rows.append(
                {
                    "代码": code,
                    "名称": name,
                    "开始": stock_start.strftime("%Y-%m-%d"),
                    "结束": stock_end.strftime("%Y-%m-%d"),
                    "Raw根数": 0,
                    "连续研究价根数": 0,
                    "QFQ根数": 0,
                    "状态": "FAILED",
                }
            )
        finally:
            if (pos + 1) % flush_every == 0:
                ok_count = sum(item["状态"] == "OK" for item in rows)
                print(
                    f"建库进度 {pos + 1}/{len(stocks)} | "
                    f"成功 {ok_count} | 失败 {len(errors)}"
                )
                gc.collect()

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
    ok_count = int((status["状态"] == "OK").sum()) if not status.empty else 0
    summary = {
        "市场": "沪深A股",
        "开始日期": start_date.strftime("%Y-%m-%d"),
        "结束日期": market_end.strftime("%Y-%m-%d"),
        "股票池": int(len(stocks)),
        "成功股票": ok_count,
        "失败股票": int(len(errors)),
        "成功率%": round(ok_count / len(stocks) * 100.0, 2)
        if len(stocks)
        else 0.0,
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

    print(
        f"建库完成：成功 {ok_count}/{len(stocks)}，"
        f"失败 {len(errors)}。"
    )
    return status, summary
