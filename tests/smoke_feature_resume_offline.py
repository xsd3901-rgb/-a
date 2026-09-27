from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

import aquant.research.feature_validation as feature_validation
from config import SETTINGS


def _history() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=180)
    factor = np.linspace(-1.0, 1.0, len(dates))
    close = 20.0 + factor * 3.0 + np.sin(np.arange(len(dates)) / 7.0) * 0.1
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "preclose": np.r_[close[0], close[:-1]],
            "volume": 1_000_000.0,
            "amount": 20_000_000.0,
            "turnover": 1.0,
            "pct_change": np.r_[0.0, np.diff(close) / close[:-1] * 100.0],
            "trade_status": 1,
            "is_st": 0,
        }
    )


class FakeProvider:
    fetch_count = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def latest_trade_date(self) -> pd.Timestamp:
        return pd.Timestamp("2024-09-06")

    def historical_securities(self, refresh: bool = False) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": f"60000{i}",
                    "name": f"样本{i}",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "delisting_date": pd.NaT,
                }
                for i in range(3)
            ]
        )

    def stock_list(self, refresh: bool = False) -> pd.DataFrame:
        return self.historical_securities()[["code", "name"]]

    def research_history_range(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        refresh: bool = False,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        type(self).fetch_count += 1
        frame = _history()
        return frame, frame


class FakeContext:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def index_daily(self, *args, **kwargs) -> pd.DataFrame:
        return pd.DataFrame()


def fake_score_history(
    hist: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    out = hist.copy()
    x = np.linspace(-2.0, 2.0, len(out))
    for name, kind in feature_validation.FEATURE_SPECS.items():
        if kind == "boolean":
            out[name] = np.arange(len(out)) % 2 == 0
        else:
            out[name] = x
    out["atr14"] = 0.5
    out["atr_pct"] = 2.0
    out["ma10"] = out["close"] * 0.99
    out["macd_dif"] = 0.2
    out["macd_dea"] = 0.1
    out["score"] = 80
    out["signal"] = True
    return out


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=root / "reports",
            feature_validation_calendar_days=240,
            feature_validation_warmup_calendar_days=30,
            feature_validation_resume=True,
        )

        def ensure_dirs() -> None:
            settings.data_store_dir.mkdir(parents=True, exist_ok=True)
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
            settings.report_dir.mkdir(parents=True, exist_ok=True)

        runtime = SimpleNamespace(batch_size=2)
        FakeProvider.fetch_count = 0

        patches = (
            patch.object(feature_validation, "SETTINGS", settings),
            patch.object(feature_validation, "ensure_directories", ensure_dirs),
            patch.object(feature_validation, "MarketDataService", FakeProvider),
            patch.object(feature_validation, "MarketContextService", FakeContext),
            patch.object(feature_validation, "current_profile", lambda: runtime),
            patch.object(feature_validation, "score_history", fake_score_history),
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            first = feature_validation.run_feature_validation(limit=3)
            assert FakeProvider.fetch_count == 3
            assert (settings.data_store_dir / "research" / "feature_validation_checkpoint.json").exists()
            assert list(
                (settings.data_store_dir / "research" / "feature_validation").glob(
                    "chunk_*.parquet"
                )
            )

            second = feature_validation.run_feature_validation(limit=3)
            assert FakeProvider.fetch_count == 3
            meta = pd.read_csv(settings.report_dir / "feature_validation_meta.csv")
            assert int(meta.iloc[0]["断点跳过股票"]) == 3
            assert int(meta.iloc[0]["本次处理股票"]) == 0
            assert len(second) == len(first)

    print("OFFLINE_FEATURE_RESUME_OK")


if __name__ == "__main__":
    main()
