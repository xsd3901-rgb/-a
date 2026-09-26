from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
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


class FakeMarketDataService:
    def __init__(self, *args, **kwargs) -> None:
        self._history = make_history()

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
            with patch.object(scanner, "MarketDataService", FakeMarketDataService):
                selected = scanner.scan_market(limit=2, refresh=False)
                assert isinstance(selected, pd.DataFrame)
                assert (scanner.SETTINGS.report_dir / "scan_latest.csv").exists()

            with patch.object(backtest, "MarketDataService", FakeMarketDataService):
                trades = backtest.run_backtest(limit=2, refresh=False, persist=False)
                assert isinstance(trades, pd.DataFrame)

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
