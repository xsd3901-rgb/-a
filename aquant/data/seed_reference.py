from __future__ import annotations

from pathlib import Path

import pandas as pd


RESOURCE_DIR = Path(__file__).resolve().parents[1] / "resources"
SECURITY_SEED = RESOURCE_DIR / "security_master_seed.csv"
CALENDAR_SEED = RESOURCE_DIR / "trade_calendar_seed.csv"


def load_security_seed() -> pd.DataFrame:
    if not SECURITY_SEED.exists():
        return pd.DataFrame(columns=["code", "name"])
    frame = pd.read_csv(
        SECURITY_SEED,
        dtype={"code": str},
        encoding="utf-8",
    )
    if frame.empty:
        return pd.DataFrame(columns=["code", "name"])
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["name"] = frame["name"].astype(str).str.strip()
    if "listing_date" in frame.columns:
        frame["listing_date"] = pd.to_datetime(
            frame["listing_date"].astype(str),
            format="%Y%m%d",
            errors="coerce",
        ).dt.normalize()
    return frame.drop_duplicates("code", keep="last").reset_index(drop=True)


def load_trade_calendar_seed() -> pd.DataFrame:
    if not CALENDAR_SEED.exists():
        return pd.DataFrame(columns=["trade_date", "is_open", "provider"])
    frame = pd.read_csv(CALENDAR_SEED, encoding="utf-8")
    if frame.empty:
        return pd.DataFrame(columns=["trade_date", "is_open", "provider"])
    frame["trade_date"] = pd.to_datetime(
        frame["trade_date"],
        errors="coerce",
    ).dt.normalize()
    frame["is_open"] = (
        frame["is_open"]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"1", "true", "yes"})
    )
    return (
        frame.dropna(subset=["trade_date"])
        .drop_duplicates("trade_date", keep="last")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
