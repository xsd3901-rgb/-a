from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd


def make_history() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=220)
    trend = np.linspace(10.0, 20.0, len(dates))
    wave = np.sin(np.linspace(0, 8 * np.pi, len(dates))) * 0.15
    close = trend + wave
    open_ = close * 0.998
    high = np.maximum(open_, close) * 1.012
    low = np.minimum(open_, close) * 0.988
    volume = np.full(len(dates), 1_000_000.0)
    volume[-10:] = np.linspace(1_050_000, 1_450_000, 10)
    amount = volume * close

    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
            "turnover": np.full(len(dates), 2.0),
        }
    )


class FakeMarketContextService:
    def index_daily(self, index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        dates = pd.bdate_range(pd.Timestamp(start_date), pd.Timestamp(end_date))
        close = np.linspace(3000.0, 3300.0, len(dates))
        return pd.DataFrame(
            {
                "trade_date": dates,
                "open": close * 0.998,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": np.full(len(dates), 1_000_000.0),
                "amount": np.full(len(dates), 1_000_000_000.0),
            }
        )


class FakeMarketDataService:
    def __init__(self, *args, **kwargs) -> None:
        self._history = make_history()

    def latest_trade_date(self) -> pd.Timestamp:
        return pd.Timestamp("2025-11-07")

    def stock_list(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"code": "600000", "name": "样本甲"},
                {"code": "000001", "name": "样本乙"},
            ]
        )

    def history(self, code: str, refresh: bool = False) -> pd.DataFrame:
        frame = self._history.copy()
        if str(code) == "000001":
            frame["close"] = frame["close"] * 1.01
            frame["open"] = frame["open"] * 1.01
            frame["high"] = frame["high"] * 1.01
            frame["low"] = frame["low"] * 1.01
            frame["amount"] = frame["volume"] * frame["close"]
        return frame


def main() -> None:
    import scanner
    import backtest
    import optimizer

    old_cwd = Path.cwd()
    with TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            fake_snapshot = SimpleNamespace(
                regime="偏强",
                score=35.0,
                index_count=5,
                details=pd.DataFrame(
                    [{"index_code": "sh000001", "index_name": "上证指数", "score": 2.0}]
                ),
            )
            with (
                patch.object(scanner, "MarketDataService", FakeMarketDataService),
                patch.object(scanner, "MarketContextService", FakeMarketContextService),
                patch.object(scanner, "detect_market_regime", lambda *args, **kwargs: fake_snapshot),
            ):
                selected = scanner.scan_market(limit=2, refresh=False)
                assert isinstance(selected, pd.DataFrame)
                assert (scanner.SETTINGS.report_dir / "scan_latest.csv").exists()
                assert (scanner.SETTINGS.report_dir / "market_environment_latest.csv").exists()
                if not selected.empty:
                    assert "市场环境" in selected.columns
                    assert set(selected["市场环境"]) == {"偏强"}

            with (
                patch.object(backtest, "MarketDataService", FakeMarketDataService),
                patch.object(backtest, "MarketContextService", FakeMarketContextService),
            ):
                trades = backtest.run_backtest(limit=2, refresh=False, persist=False)
                assert isinstance(trades, pd.DataFrame)
                if not trades.empty:
                    assert "市场环境" in trades.columns
                    assert "环境分" in trades.columns

            with patch.object(optimizer, "MarketDataService", FakeMarketDataService):
                result, best = optimizer.optimize_parameters(limit=2, refresh=False)
                assert isinstance(result, pd.DataFrame)
                assert len(result) == 9
                assert (optimizer.SETTINGS.report_dir / "optimizer_results.csv").exists()
        finally:
            os.chdir(old_cwd)

    print("OFFLINE_PIPELINE_SMOKE_OK")


if __name__ == "__main__":
    main()
