from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

import aquant.research.data_audit as data_audit
from aquant.data.reference import ReferenceStore
from config import SETTINGS


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)

        data_audit.SETTINGS = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=reports,
        )
        (root / "data_store").mkdir(parents=True, exist_ok=True)

        reference = ReferenceStore(root / "data_store")
        dates = pd.bdate_range("2025-01-02", periods=80)
        reference.save_trade_calendar(
            pd.DataFrame({"trade_date": dates, "is_open": True})
        )
        reference.save_security_master(
            pd.DataFrame(
                {
                    "code": ["600001", "600002"],
                    "name": ["已有行情", "缺少整文件"],
                }
            )
        )

        none_dir = root / "data_store" / "standard" / "daily" / "none"
        qfq_dir = root / "data_store" / "standard" / "daily" / "qfq"
        none_dir.mkdir(parents=True, exist_ok=True)
        qfq_dir.mkdir(parents=True, exist_ok=True)

        close = np.linspace(10.0, 11.0, len(dates))
        bars = pd.DataFrame(
            {
                "symbol": "600001",
                "trade_date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000.0,
                "pct_change": np.r_[0.0, np.diff(close) / close[:-1] * 100.0],
            }
        )
        bars.to_parquet(none_dir / "600001.parquet", index=False)
        bars.to_parquet(qfq_dir / "600001.parquet", index=False)

        details, summary = data_audit.run_data_audit(
            lookback_calendar_days=120,
            min_coverage=0.90,
        )

        assert summary["股票池来源"] == "security_master_fallback"
        assert len(details) == 2
        missing = details[details["代码"] == "600002"].iloc[0]
        assert missing["状态"] == "FAIL"
        assert "缺少未复权行情文件" in str(missing["原因"])
        assert summary["FAIL"] == 1

    print("OFFLINE_DATA_AUDIT_FALLBACK_OK")


if __name__ == "__main__":
    main()
