from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

import backtest
import scanner
from config import SETTINGS


def _hist() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=160)
    close = np.linspace(10.0, 12.0, len(dates))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "preclose": np.r_[close[0], close[:-1]],
            "volume": 1_000_000.0,
            "amount": 10_000_000.0,
            "turnover": 1.0,
            "pct_change": 0.1,
            "trade_status": 1,
            "is_st": 0,
        }
    )


class FakeService:
    scan_threads: set[str] = set()
    backtest_threads: set[str] = set()

    def __init__(self, *args, **kwargs) -> None:
        pass

    def latest_trade_date(self) -> pd.Timestamp:
        return pd.Timestamp("2025-08-15")

    def stock_list(self, refresh: bool = False) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"code": f"60000{i}", "name": f"样本{i}"}
                for i in range(6)
            ]
        )

    def historical_securities(self, refresh: bool = False) -> pd.DataFrame:
        frame = self.stock_list()
        frame["listing_date"] = pd.Timestamp("2020-01-01")
        frame["delisting_date"] = pd.NaT
        return frame

    def history(self, code: str, refresh: bool = False) -> pd.DataFrame:
        type(self).scan_threads.add(threading.current_thread().name)
        time.sleep(0.03)
        return _hist()

    def research_history_range(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        refresh: bool = False,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        type(self).backtest_threads.add(threading.current_thread().name)
        time.sleep(0.03)
        frame = _hist()
        return frame, frame


class FakeContext:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def index_daily(self, *args, **kwargs) -> pd.DataFrame:
        return pd.DataFrame()


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=root / "reports",
            top_n=50,
        )
        settings.report_dir.mkdir(parents=True, exist_ok=True)

        runtime = SimpleNamespace(
            name="parallel-test",
            batch_size=2,
            download_workers=4,
            compute_workers=3,
            backtest_workers=3,
        )
        signal = SimpleNamespace(
            score=80,
            allowed=True,
            close=12.0,
            rs20=2.0,
            risk="低",
            risk_reasons="",
            atr_pct=2.0,
            stop=11.0,
            target=14.0,
            reasons="offline",
        )
        regime = SimpleNamespace(
            regime="震荡",
            score=0.0,
            index_count=0,
            details=pd.DataFrame(),
        )

        FakeService.scan_threads = set()
        with (
            patch.object(scanner, "SETTINGS", settings),
            patch.object(scanner, "MarketDataService", FakeService),
            patch.object(scanner, "MarketContextService", FakeContext),
            patch.object(scanner, "current_profile", lambda: runtime),
            patch.object(scanner, "evaluate_latest", lambda *a, **k: signal),
            patch.object(scanner, "detect_market_regime", lambda *a, **k: regime),
            patch.object(
                scanner,
                "load_strategy_profile",
                lambda: {"score_threshold": 68},
            ),
            patch.object(scanner, "ensure_directories", lambda: None),
        ):
            selected = scanner.scan_market(limit=6)
            assert len(selected) == 6
            assert len(FakeService.scan_threads) >= 2

        FakeService.backtest_threads = set()

        def fake_backtest_stock(code, name, hist, **kwargs):
            return [
                {
                    "代码": code,
                    "名称": name,
                    "买入日": "2025-07-01",
                    "卖出日": "2025-07-08",
                    "净收益%": 1.0,
                }
            ]

        with (
            patch.object(backtest, "SETTINGS", settings),
            patch.object(backtest, "MarketDataService", FakeService),
            patch.object(backtest, "MarketContextService", FakeContext),
            patch.object(backtest, "current_profile", lambda: runtime),
            patch.object(backtest, "backtest_stock", fake_backtest_stock),
            patch.object(
                backtest,
                "build_market_regime_history",
                lambda *a, **k: pd.DataFrame(),
            ),
            patch.object(backtest, "ensure_directories", lambda: None),
        ):
            trades = backtest.run_backtest(limit=6, persist=False)
            assert len(trades) == 6
            assert len(FakeService.backtest_threads) >= 2

    print("OFFLINE_PARALLEL_ENGINE_OK")


if __name__ == "__main__":
    main()
