from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from aquant.data.schema import FIELDS
from aquant.data.source_quality import SourceQualityLog
from aquant.data.storage import MarketStore
from aquant.research.source_quality import run_source_quality


def _bars(
    symbol: str,
    dates: pd.DatetimeIndex,
    *,
    provider: str,
    close_shift: float = 0.0,
    volume_scale: float = 1.0,
) -> pd.DataFrame:
    close = np.linspace(10.0, 11.0, len(dates)) + close_shift
    return pd.DataFrame(
        {
            FIELDS.symbol: symbol,
            FIELDS.trade_date: dates,
            FIELDS.open: close,
            FIELDS.high: close * 1.01,
            FIELDS.low: close * 0.99,
            FIELDS.close: close,
            FIELDS.preclose: np.r_[close[0], close[:-1]],
            FIELDS.volume: 1_000_000.0 * volume_scale,
            FIELDS.amount: 10_000_000.0,
            FIELDS.turnover: 1.0,
            FIELDS.pct_change: 0.1,
            FIELDS.trade_status: 1,
            FIELDS.is_st: 0,
            FIELDS.provider: provider,
            FIELDS.adapter: "offline",
            FIELDS.adjustment: "none",
            FIELDS.quality_status: "ok",
        }
    )


def main() -> None:
    dates = pd.bdate_range("2025-01-02", periods=20)

    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "data_store"
        reports = Path(tmp) / "reports"
        reports.mkdir(parents=True, exist_ok=True)

        store = MarketStore(root)
        standard = _bars(
            "600001",
            dates,
            provider="baostock",
        )
        store.save_standard(
            standard,
            "600001",
            "none",
        )

        qfq = standard.copy()
        qfq[FIELDS.provider] = "eastmoney"
        qfq[FIELDS.adjustment] = "qfq"
        store.save_standard(
            qfq,
            "600001",
            "qfq",
        )

        east = _bars(
            "600001",
            dates,
            provider="eastmoney",
            close_shift=0.03,
            volume_scale=1.08,
        )
        bao = _bars(
            "600001",
            dates,
            provider="baostock",
        )
        store.save_raw(
            east,
            "eastmoney",
            "600001",
            "none",
        )
        store.save_raw(
            bao,
            "baostock",
            "600001",
            "none",
        )

        log = SourceQualityLog(root)
        log.record(
            symbol="600001",
            provider="eastmoney",
            adapter="akshare",
            adjust="none",
            start_date="2025-01-01",
            end_date="2025-02-01",
            attempt_order=1,
            outcome="error",
            elapsed_ms=1200,
            detail="temporary",
        )
        log.record(
            symbol="600001",
            provider="baostock",
            adapter="baostock-python",
            adjust="none",
            start_date="2025-01-01",
            end_date="2025-02-01",
            attempt_order=2,
            outcome="success",
            rows=20,
            elapsed_ms=500,
        )
        log.record(
            symbol="600002",
            provider="eastmoney",
            adapter="akshare",
            adjust="qfq",
            start_date="2025-01-01",
            end_date="2025-02-01",
            attempt_order=1,
            outcome="success",
            rows=20,
            elapsed_ms=300,
        )

        providers, usage, crosscheck, summary = run_source_quality(
            data_root=root,
            report_dir=reports,
            crosscheck_limit=10,
        )

        assert not providers.empty
        assert not usage.empty
        assert len(crosscheck) == 1
        assert summary["抓取事件数"] == 3
        assert summary["成功抓取"] == 2
        assert summary["回退源成功"] == 1
        assert summary["多源交叉检查股票"] == 1
        assert (
            providers.loc[
                providers["数据源"].eq("baostock"),
                "回退成功",
            ].sum()
            == 1
        )
        assert set(usage["数据源"]) >= {
            "baostock",
            "eastmoney",
        }
        assert (
            reports / "data_source_quality.csv"
        ).exists()
        assert (
            reports / "data_source_usage.csv"
        ).exists()
        assert (
            reports / "data_source_crosscheck.csv"
        ).exists()
        assert (
            reports / "data_source_quality_summary.json"
        ).exists()

    print("OFFLINE_SOURCE_QUALITY_OK")


if __name__ == "__main__":
    main()
