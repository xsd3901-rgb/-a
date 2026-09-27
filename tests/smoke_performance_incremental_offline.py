from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from aquant.data.schema import FIELDS
from aquant.data.service import MarketDataService
from aquant.data.storage import MarketStore
from aquant.runtime.resources import (
    ResourceProfile,
    apply_cpu_cap,
)


def _bars(symbol: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
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
            FIELDS.provider: "offline",
            FIELDS.adapter: "offline",
            FIELDS.adjustment: "none",
            FIELDS.quality_status: "ok",
        }
    )


def main() -> None:
    # CPU 限流：高内存档位在 2 核机器上不能仍开 6 个计算/回测线程。
    base = ResourceProfile(
        "high_performance",
        batch_size=240,
        download_workers=8,
        compute_workers=6,
        backtest_workers=6,
        cache_symbols=300,
    )
    capped = apply_cpu_cap(base, 2)
    assert capped.compute_workers == 1
    assert capped.backtest_workers == 1
    assert capped.download_workers <= 4

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = MarketStore(root / "data_store")
        dates = pd.date_range("2025-01-01", periods=60, freq="D")
        store.save_standard(
            _bars("600001", dates),
            "600001",
            "none",
        )

        # 并发本地读取使用独立 DuckDB 连接，不共享进程级默认连接。
        def read_once(_: int) -> tuple[int, pd.Timestamp | None]:
            frame = store.read_daily(
                "600001",
                "none",
                start_date="2025-01-10",
                end_date="2025-02-10",
            )
            return len(frame), store.latest_date("600001", "none")

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(read_once, range(24)))

        assert all(rows == 32 for rows, _ in results)
        assert all(
            latest == pd.Timestamp("2025-03-01")
            for _, latest in results
        )

        stats = store.light_stats(
            "600001",
            "none",
            start_date="2025-01-10",
            end_date="2025-02-10",
        )
        assert stats["rows"] == 32
        assert stats["tradable_rows"] == 32
        assert stats["pct_change_coverage"] == 1.0

        # 当前区间已经完整时，增量同步必须是纯本地 no-op。
        service = MarketDataService(str(root / "data_store"))
        calls: list[tuple[str, str]] = []

        def fail_if_fetch(
            symbol: str,
            start_date: str,
            end_date: str,
            adjust: str,
            *,
            prefer_point_in_time: bool = False,
        ) -> pd.DataFrame:
            calls.append((start_date, end_date))
            raise AssertionError("完整本地数据不应再次访问网络")

        service._fetch_with_fallback = fail_if_fetch  # type: ignore[method-assign]
        synced = service.sync_history_range(
            "600001",
            "2025-01-01",
            "2025-03-01",
            adjust="none",
        )
        assert calls == []
        assert synced["rows"] == 60
        assert synced["fetched_segments"] == []

    print("OFFLINE_PERFORMANCE_INCREMENTAL_OK")


if __name__ == "__main__":
    main()
