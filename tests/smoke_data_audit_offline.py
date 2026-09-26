from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from aquant.research.data_audit import audit_market_store


def _bars(code: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
    close = np.linspace(10.0, 12.0, len(dates))
    return pd.DataFrame(
        {
            "symbol": code,
            "trade_date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
            "pct_change": np.r_[0.0, np.diff(close) / close[:-1] * 100.0],
            "trade_status": 1,
            "is_st": 0,
        }
    )


def main() -> None:
    dates = pd.bdate_range("2025-01-02", periods=100)
    calendar = pd.DataFrame({"trade_date": dates, "is_open": True})
    lifecycle = pd.DataFrame(
        {
            "code": ["600001", "600002"],
            "name": ["完整样本", "缺口样本"],
            "listing_date": [pd.Timestamp("2020-01-01")] * 2,
            "delisting_date": [pd.NaT, pd.NaT],
        }
    )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        none_dir = root / "standard" / "daily" / "none"
        qfq_dir = root / "standard" / "daily" / "qfq"
        none_dir.mkdir(parents=True, exist_ok=True)
        qfq_dir.mkdir(parents=True, exist_ok=True)

        good = _bars("600001", dates)
        bad = _bars("600002", dates[:-12])
        good.to_parquet(none_dir / "600001.parquet", index=False)
        bad.to_parquet(none_dir / "600002.parquet", index=False)
        good.to_parquet(qfq_dir / "600001.parquet", index=False)
        bad.to_parquet(qfq_dir / "600002.parquet", index=False)

        details, summary = audit_market_store(
            root,
            calendar=calendar,
            lifecycle=lifecycle,
            min_coverage=0.90,
        )

        assert len(details) == 2
        good_row = details[details["代码"] == "600001"].iloc[0]
        bad_row = details[details["代码"] == "600002"].iloc[0]
        assert good_row["状态"] == "OK"
        assert bad_row["状态"] == "FAIL"
        assert int(bad_row["滞后交易日"]) == 12
        assert float(bad_row["覆盖率%"]) < 90.0
        assert summary["FAIL"] == 1
        assert summary["状态"] == "需修复"

    print("OFFLINE_DATA_AUDIT_OK")


if __name__ == "__main__":
    main()
