from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import bootstrap
from config import SETTINGS


def _history(code: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2025-01-02", periods=8)
    base = pd.DataFrame(
        {
            "date": dates,
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "preclose": 10.0,
            "volume": 1_000_000.0,
            "amount": 10_000_000.0,
            "turnover": 1.0,
            "pct_change": 0.1,
            "trade_status": 1,
            "is_st": 0,
            "provider": "baostock",
            "adapter": "offline",
            "quality_status": "ok",
        }
    )
    signal = base.copy()
    signal["signal_price_mode"] = "point_in_time_continuous"
    return signal, base


class FakeMarketDataService:
    fetch_count = 0

    def __init__(self, *args, **kwargs) -> None:
        self.store = SimpleNamespace(
            latest_date=lambda code, adjust: pd.Timestamp("2025-06-30")
        )

    def latest_trade_date(self) -> pd.Timestamp:
        return pd.Timestamp("2025-06-30")

    def historical_securities(self, refresh: bool = False) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": "600001",
                    "name": "样本一",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "delisting_date": pd.NaT,
                },
                {
                    "code": "000001",
                    "name": "样本二",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "delisting_date": pd.NaT,
                },
            ]
        )

    def stock_list(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"code": "600001", "name": "样本一"},
                {"code": "000001", "name": "样本二"},
            ]
        )

    def research_history_range(
        self,
        code: str,
        start_date: str,
        end_date: str,
        refresh: bool = False,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        type(self).fetch_count += 1
        return _history(code)

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
        signal, raw = _history(code)
        qfq = raw.copy()
        qfq["provider"] = "eastmoney"
        return qfq

    def refresh_catalog(self) -> None:
        return None


class FakeContext:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def refresh_core_indices(self, start_date: str, end_date: str) -> dict:
        return {"sh000001": 10}

    def industry_map(self, force: bool = False) -> pd.DataFrame:
        return pd.DataFrame([{"code": "600001", "industry": "测试"}])


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=root / "reports",
            bootstrap_max_workers=2,
            bootstrap_retry_attempts=2,
            bootstrap_resume=True,
        )

        def ensure_dirs() -> None:
            settings.data_store_dir.mkdir(parents=True, exist_ok=True)
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
            settings.report_dir.mkdir(parents=True, exist_ok=True)

        FakeMarketDataService.fetch_count = 0
        with (
            patch.object(bootstrap, "SETTINGS", settings),
            patch.object(bootstrap, "ensure_directories", ensure_dirs),
            patch.object(bootstrap, "MarketDataService", FakeMarketDataService),
            patch.object(bootstrap, "MarketContextService", FakeContext),
            patch.object(
                bootstrap,
                "current_profile",
                lambda: SimpleNamespace(
                    name="test",
                    batch_size=1,
                    download_workers=4,
                ),
            ),
        ):
            first, first_summary = bootstrap.bootstrap_market(limit=2)
            assert first_summary["成功股票"] == 2
            assert first_summary["本次实际处理"] == 2
            assert first_summary["断点跳过"] == 0
            assert FakeMarketDataService.fetch_count == 2
            assert set(first["Raw来源"]) == {"baostock"}
            assert set(first["QFQ来源"]) == {"eastmoney"}
            assert (settings.report_dir / "bootstrap_checkpoint.json").exists()
            assert (settings.report_dir / "data_source_quality.csv").exists()

            second, second_summary = bootstrap.bootstrap_market(limit=2)
            assert second_summary["成功股票"] == 2
            assert second_summary["本次实际处理"] == 0
            assert second_summary["断点跳过"] == 2
            assert FakeMarketDataService.fetch_count == 2
            assert set(second["状态"]) == {"SKIPPED_RESUME"}

    print("OFFLINE_BOOTSTRAP_RESUME_OK")


if __name__ == "__main__":
    main()
