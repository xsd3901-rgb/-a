from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from aquant.data.reference import ReferenceStore
from aquant.data.schema import FIELDS
from aquant.data.universe_store import HistoricalUniverseStore
from config import SETTINGS, ensure_directories


def _lifecycle_map(lifecycle: pd.DataFrame | None) -> dict[str, dict]:
    if lifecycle is None or lifecycle.empty or "code" not in lifecycle.columns:
        return {}

    frame = lifecycle.copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    for col in ("listing_date", "delisting_date"):
        if col in frame.columns:
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.normalize()

    result: dict[str, dict] = {}
    for _, row in frame.drop_duplicates("code", keep="last").iterrows():
        result[str(row["code"])] = row.to_dict()
    return result


def _read_daily_file(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()

    date_col = FIELDS.trade_date if FIELDS.trade_date in frame.columns else "date"
    if date_col not in frame.columns:
        return pd.DataFrame()

    out = frame.copy()
    out["_date"] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
    return out.dropna(subset=["_date"]).sort_values("_date").reset_index(drop=True)


def audit_market_store(
    root: str | Path,
    *,
    calendar: pd.DataFrame,
    lifecycle: pd.DataFrame | None = None,
    limit: int | None = None,
    lookback_calendar_days: int = (
        SETTINGS.feature_validation_calendar_days
        + SETTINGS.feature_validation_warmup_calendar_days
    ),
    min_coverage: float = 0.90,
) -> tuple[pd.DataFrame, dict]:
    """只检查本地行情文件，不触发任何网络请求。"""
    base = Path(root)
    none_dir = base / "standard" / "daily" / "none"
    qfq_dir = base / "standard" / "daily" / "qfq"

    all_files = sorted(none_dir.glob("*.parquet")) if none_dir.exists() else []
    file_map = {path.stem.zfill(6): path for path in all_files}

    cal = calendar.copy() if calendar is not None else pd.DataFrame()
    if not cal.empty and "trade_date" in cal.columns:
        cal["trade_date"] = pd.to_datetime(
            cal["trade_date"], errors="coerce"
        ).dt.normalize()
        cal = cal.dropna(subset=["trade_date"])
        if "is_open" in cal.columns:
            cal = cal[cal["is_open"].astype(bool)]
        cal = cal.drop_duplicates("trade_date").sort_values("trade_date")
    else:
        cal = pd.DataFrame(columns=["trade_date"])

    latest_market_date = (
        pd.Timestamp(cal["trade_date"].max()).normalize()
        if not cal.empty
        else None
    )
    earliest_allowed = (
        latest_market_date - pd.Timedelta(days=int(lookback_calendar_days))
        if latest_market_date is not None
        else None
    )
    life_map = _lifecycle_map(lifecycle)

    expected_codes: set[str] = set()
    if lifecycle is not None and not lifecycle.empty and "code" in lifecycle.columns:
        life_frame = lifecycle.copy()
        life_frame["code"] = life_frame["code"].astype(str).str.zfill(6)
        listing = pd.to_datetime(
            life_frame.get("listing_date", pd.Series(pd.NaT, index=life_frame.index)),
            errors="coerce",
        ).dt.normalize()
        delisting = pd.to_datetime(
            life_frame.get("delisting_date", pd.Series(pd.NaT, index=life_frame.index)),
            errors="coerce",
        ).dt.normalize()
        if latest_market_date is not None:
            listed = listing.isna() | (listing <= latest_market_date)
            if earliest_allowed is not None:
                overlaps = delisting.isna() | (delisting >= earliest_allowed)
            else:
                overlaps = pd.Series(True, index=life_frame.index)
            expected_codes = set(
                life_frame.loc[listed & overlaps, "code"].dropna().astype(str)
            )

    codes = sorted(expected_codes | set(file_map))
    if limit and limit > 0:
        codes = codes[: int(limit)]

    rows: list[dict] = []

    for code in codes:
        path = file_map.get(code)
        if path is None:
            life = life_map.get(code, {})
            rows.append(
                {
                    "代码": code,
                    "名称": str(life.get("name") or ""),
                    "状态": "FAIL",
                    "原因": "缺少未复权行情文件",
                    "记录数": 0,
                    "起始日": "",
                    "最新日": "",
                    "覆盖率%": 0.0,
                    "缺失交易日": 0,
                    "滞后交易日": 0,
                    "重复日期": 0,
                    "涨跌幅字段覆盖率%": 0.0,
                    "QFQ存在": (qfq_dir / f"{code}.parquet").exists(),
                }
            )
            continue

        frame = _read_daily_file(path)
        if frame.empty:
            rows.append(
                {
                    "代码": code,
                    "状态": "FAIL",
                    "原因": "未复权文件为空或日期字段损坏",
                    "记录数": 0,
                    "起始日": "",
                    "最新日": "",
                    "覆盖率%": 0.0,
                    "缺失交易日": 0,
                    "滞后交易日": 0,
                    "重复日期": 0,
                    "涨跌幅字段覆盖率%": 0.0,
                    "QFQ存在": (qfq_dir / f"{code}.parquet").exists(),
                }
            )
            continue

        dates = frame["_date"]
        first_date = pd.Timestamp(dates.min()).normalize()
        last_date = pd.Timestamp(dates.max()).normalize()
        duplicate_dates = int(dates.duplicated().sum())

        life = life_map.get(code, {})
        listing_date = life.get("listing_date")
        delisting_date = life.get("delisting_date")
        name = str(life.get("name") or "")

        # 不能用文件自身 first_date 作为期望起点，否则“只剩最近30天”的
        # 残缺文件也会被误判为 100% 覆盖。期望起点应由研究窗口/上市日决定。
        expected_start = earliest_allowed if earliest_allowed is not None else first_date
        if pd.notna(listing_date):
            expected_start = max(
                expected_start,
                pd.Timestamp(listing_date).normalize(),
            )

        expected_end = latest_market_date or last_date
        if pd.notna(delisting_date):
            expected_end = min(
                expected_end,
                pd.Timestamp(delisting_date).normalize(),
            )

        observed_window = dates[
            (dates >= expected_start) & (dates <= expected_end)
        ].drop_duplicates()

        if not cal.empty and expected_start <= expected_end:
            expected_dates = cal[
                (cal["trade_date"] >= expected_start)
                & (cal["trade_date"] <= expected_end)
            ]["trade_date"].drop_duplicates()
            expected_count = int(len(expected_dates))
            observed_set = set(observed_window.tolist())
            missing_count = int(
                sum(date not in observed_set for date in expected_dates.tolist())
            )
            coverage = (
                len(observed_set) / expected_count
                if expected_count > 0
                else 1.0
            )
            stale_open_days = int(
                (
                    (cal["trade_date"] > last_date)
                    & (cal["trade_date"] <= expected_end)
                ).sum()
            )
        else:
            expected_count = int(len(observed_window))
            missing_count = 0
            coverage = 1.0 if expected_count > 0 else 0.0
            stale_open_days = 0

        pct_col = (
            FIELDS.pct_change
            if FIELDS.pct_change in frame.columns
            else "pct_change"
        )
        if pct_col in frame.columns and len(observed_window):
            window_mask = frame["_date"].isin(observed_window)
            pct_coverage = float(
                pd.to_numeric(
                    frame.loc[window_mask, pct_col],
                    errors="coerce",
                ).notna().mean()
            )
        else:
            pct_coverage = 0.0

        reasons: list[str] = []
        hard_fail = False

        if duplicate_dates:
            reasons.append(f"重复日期{duplicate_dates}")
            hard_fail = True
        if coverage < float(min_coverage):
            reasons.append(f"覆盖率{coverage * 100:.1f}%")
            hard_fail = True
        if stale_open_days > 0:
            reasons.append(f"滞后{stale_open_days}个交易日")
            if stale_open_days > 2:
                hard_fail = True
        if pct_coverage < 0.70:
            reasons.append(f"pct_change覆盖{pct_coverage * 100:.1f}%")
        qfq_exists = (qfq_dir / f"{code}.parquet").exists()
        if not qfq_exists:
            reasons.append("缺少QFQ扫描缓存")

        if hard_fail:
            status = "FAIL"
        elif reasons:
            status = "WARN"
        else:
            status = "OK"

        rows.append(
            {
                "代码": code,
                "名称": name,
                "状态": status,
                "原因": "、".join(reasons) if reasons else "通过",
                "记录数": int(len(frame)),
                "起始日": first_date.strftime("%Y-%m-%d"),
                "最新日": last_date.strftime("%Y-%m-%d"),
                "覆盖率%": round(coverage * 100.0, 2),
                "缺失交易日": missing_count,
                "滞后交易日": stale_open_days,
                "重复日期": duplicate_dates,
                "涨跌幅字段覆盖率%": round(pct_coverage * 100.0, 2),
                "QFQ存在": bool(qfq_exists),
            }
        )

    details = pd.DataFrame(rows)
    qfq_count = (
        len(list(qfq_dir.glob("*.parquet")))
        if qfq_dir.exists()
        else 0
    )

    if details.empty:
        summary = {
            "状态": "未建库",
            "检查股票数": 0,
            "OK": 0,
            "WARN": 0,
            "FAIL": 0,
            "未复权文件数": len(all_files),
            "QFQ文件数": qfq_count,
            "交易日历状态": "缺失" if cal.empty else "可用",
            "市场最新交易日": (
                latest_market_date.strftime("%Y-%m-%d")
                if latest_market_date is not None
                else ""
            ),
            "覆盖率中位数%": 0.0,
            "说明": "本地未复权行情尚未建立，请先运行 bootstrap。",
        }
        return details, summary

    counts = details["状态"].value_counts().to_dict()
    fail_count = int(counts.get("FAIL", 0))
    warn_count = int(counts.get("WARN", 0))
    calendar_ok = not cal.empty

    if not calendar_ok:
        overall = "需补交易日历"
    elif fail_count > 0:
        overall = "需修复"
    elif warn_count > 0:
        overall = "可用但有警告"
    else:
        overall = "通过"

    summary = {
        "状态": overall,
        "检查股票数": int(len(details)),
        "OK": int(counts.get("OK", 0)),
        "WARN": warn_count,
        "FAIL": fail_count,
        "未复权文件数": int(len(all_files)),
        "QFQ文件数": int(qfq_count),
        "交易日历状态": "可用" if calendar_ok else "缺失",
        "市场最新交易日": (
            latest_market_date.strftime("%Y-%m-%d")
            if latest_market_date is not None
            else ""
        ),
        "覆盖率中位数%": round(
            float(
                pd.to_numeric(
                    details["覆盖率%"],
                    errors="coerce",
                ).median()
            ),
            2,
        ),
        "说明": (
            "审计只读取本地文件，不会访问网络；FAIL 应先修复后再做正式回测。"
        ),
    }
    return details, summary


def run_data_audit(
    limit: int | None = None,
    *,
    lookback_calendar_days: int = (
        SETTINGS.feature_validation_calendar_days
        + SETTINGS.feature_validation_warmup_calendar_days
    ),
    min_coverage: float = 0.90,
) -> tuple[pd.DataFrame, dict]:
    ensure_directories()
    reference = ReferenceStore(SETTINGS.data_store_dir)
    lifecycle_store = HistoricalUniverseStore(SETTINGS.data_store_dir)

    details, summary = audit_market_store(
        SETTINGS.data_store_dir,
        calendar=reference.read_trade_calendar(),
        lifecycle=lifecycle_store.read(),
        limit=limit,
        lookback_calendar_days=lookback_calendar_days,
        min_coverage=min_coverage,
    )

    details.to_csv(
        SETTINGS.report_dir / "data_audit.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame([summary]).to_csv(
        SETTINGS.report_dir / "data_audit_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    return details, summary
