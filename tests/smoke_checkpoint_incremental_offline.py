from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from aquant.data.schema import FIELDS
from aquant.data.service import MarketDataService
from aquant.runtime.checkpoint import JsonCheckpoint


def _bars(symbol: str, dates: pd.DatetimeIndex, provider: str = "test") -> pd.DataFrame:
    close = pd.Series(range(len(dates)), dtype=float) + 10.0
    return pd.DataFrame(
        {
            FIELDS.symbol: symbol,
            FIELDS.trade_date: dates,
            FIELDS.open: close,
            FIELDS.high: close + 0.2,
            FIELDS.low: close - 0.2,
            FIELDS.close: close,
            FIELDS.preclose: close.shift(1).fillna(close.iloc[0]),
            FIELDS.volume: 1_000_000.0,
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
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 原子检查点：同 signature 恢复，signature 变化自动清空旧状态。
        cp_path = root / "checkpoint.json"
        cp = JsonCheckpoint(cp_path, "job-a")
        cp.mark_completed("600001", {"状态": "OK"})
        cp.mark_failed("600002", error="temporary", attempts=2)
        cp2 = JsonCheckpoint(cp_path, "job-a")
        assert cp2.is_completed("600001")
        assert cp2.failed_count == 1
        cp3 = JsonCheckpoint(cp_path, "job-b")
        assert cp3.completed_count == 0
        assert cp3.failed_count == 0

        # history_range 已有中段数据时，只抓首尾缺口，不重下整个窗口。
        store_root = root / "data_store"
        service = MarketDataService(str(store_root))
        symbol = "600001"
        local_dates = pd.date_range("2025-01-03", "2025-01-05", freq="D")
        service.store.save_standard(
            _bars(symbol, local_dates),
            symbol,
            "none",
        )

        calls: list[tuple[str, str]] = []

        def fake_fetch(
            symbol_arg: str,
            start_date: str,
            end_date: str,
            adjust: str,
            *,
            prefer_point_in_time: bool = False,
        ) -> pd.DataFrame:
            calls.append((start_date, end_date))
            dates = pd.date_range(start_date, end_date, freq="D")
            frame = _bars(symbol_arg, dates, provider="mock")
            service.store.save_standard(frame, symbol_arg, adjust)
            return frame

        service._fetch_with_fallback = fake_fetch  # type: ignore[method-assign]
        out = service.history_range(
            symbol,
            "2025-01-01",
            "2025-01-07",
            adjust="none",
        )

        assert calls == [
            ("2025-01-01", "2025-01-02"),
            ("2025-01-06", "2025-01-07"),
        ]
        assert len(out) == 7
        assert "provider" in out.columns
        assert set(out["provider"].dropna()) == {"test", "mock"}

    print("OFFLINE_CHECKPOINT_INCREMENTAL_OK")


if __name__ == "__main__":
    main()
