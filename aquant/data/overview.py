from __future__ import annotations

from pathlib import Path

import pandas as pd

from aquant.data.reference_service import ReferenceDataService
from aquant.data.source_health import SourceHealthRegistry
from config import SETTINGS


def data_overview(
    data_root: str | Path | None = None,
) -> dict:
    root = (
        Path(SETTINGS.data_store_dir)
        if data_root is None
        else Path(data_root)
    )
    reference_service = ReferenceDataService(str(root))
    reference = reference_service.store
    securities = reference.read_security_master()
    calendar = reference.read_trade_calendar()

    daily_root = root / "standard" / "daily"
    none_dir = daily_root / "none"
    qfq_dir = daily_root / "qfq"

    none_files = (
        sum(1 for _ in none_dir.glob("*.parquet"))
        if none_dir.exists()
        else 0
    )
    qfq_files = (
        sum(1 for _ in qfq_dir.glob("*.parquet"))
        if qfq_dir.exists()
        else 0
    )

    latest = reference.latest_trade_date()
    security_source = "local"
    if (
        not securities.empty
        and "provider" in securities.columns
    ):
        values = (
            securities["provider"]
            .dropna()
            .astype(str)
            .value_counts()
        )
        if not values.empty:
            security_source = str(values.index[0])

    calendar_source = "local"
    if not calendar.empty and "provider" in calendar.columns:
        values = (
            calendar["provider"]
            .dropna()
            .astype(str)
            .value_counts()
        )
        if not values.empty:
            calendar_source = str(values.index[0])

    security_snapshot = ""
    if not securities.empty and "snapshot_date" in securities.columns:
        values = securities["snapshot_date"].dropna().astype(str)
        if not values.empty:
            security_snapshot = str(values.iloc[0])

    calendar_snapshot = ""
    if not calendar.empty and "snapshot_date" in calendar.columns:
        values = calendar["snapshot_date"].dropna().astype(str)
        if not values.empty:
            calendar_snapshot = str(values.iloc[0])

    health = SourceHealthRegistry(root).snapshot()
    healthy = sum(row["status"] == "healthy" for row in health)
    cooling = sum(row["status"] == "cooldown" for row in health)

    return {
        "summary": {
            "股票基础库": int(len(securities)),
            "股票池来源": security_source,
            "股票池快照日期": security_snapshot,
            "交易日历记录": int(len(calendar)),
            "交易日历来源": calendar_source,
            "交易日历快照日期": calendar_snapshot,
            "最新交易日": (
                pd.Timestamp(latest).strftime("%Y-%m-%d")
                if latest is not None
                else ""
            ),
            "未复权已建库": int(none_files),
            "QFQ已建库": int(qfq_files),
            "健康数据源": int(healthy),
            "冷却数据源": int(cooling),
        },
        "source_health": health,
    }
