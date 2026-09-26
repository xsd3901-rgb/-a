from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import pandas as pd

from aquant.data.context_store import MarketContextStore
from aquant.data.reference import ReferenceStore
from aquant.data.schema import FIELDS
from aquant.data.storage import MarketStore
from aquant.runtime.resources import current_profile


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "data_store"

        reference = ReferenceStore(root)
        reference.save_security_master(
            pd.DataFrame(
                [
                    {"code": "600000", "name": "浦发银行"},
                    {"code": "000001", "name": "平安银行"},
                ]
            )
        )
        securities = reference.read_security_master()
        assert len(securities) == 2
        assert set(securities["code"]) == {"600000", "000001"}

        reference.save_trade_calendar(
            pd.DataFrame(
                {
                    "trade_date": ["2026-09-23", "2026-09-24", "2026-09-25"],
                    "is_open": [True, True, False],
                    "provider": ["test", "test", "test"],
                }
            )
        )
        latest_trade = reference.latest_trade_date("2026-09-25")
        assert latest_trade == pd.Timestamp("2026-09-24")
        reference.refresh_catalog(root / "aquant.duckdb")

        market = MarketStore(root)
        bars = pd.DataFrame(
            {
                FIELDS.symbol: ["600000", "600000", "600000"],
                FIELDS.trade_date: pd.to_datetime(
                    ["2026-09-22", "2026-09-23", "2026-09-24"]
                ),
                FIELDS.open: [10.0, 10.1, 10.2],
                FIELDS.high: [10.3, 10.4, 10.5],
                FIELDS.low: [9.9, 10.0, 10.1],
                FIELDS.close: [10.1, 10.2, 10.4],
                FIELDS.volume: [1_000_000, 1_100_000, 1_200_000],
                FIELDS.amount: [10_000_000, 11_000_000, 12_000_000],
                FIELDS.turnover: [1.1, 1.2, 1.3],
                FIELDS.provider: ["test", "test", "test"],
                FIELDS.adapter: ["offline", "offline", "offline"],
                FIELDS.adjustment: ["qfq", "qfq", "qfq"],
                FIELDS.quality_status: ["ok", "ok", "ok"],
            }
        )
        market.save_standard(bars, "600000", "qfq")
        loaded = market.read_daily("600000", "qfq")
        assert len(loaded) == 3
        assert market.latest_date("600000", "qfq") == pd.Timestamp("2026-09-24")
        market.refresh_catalog()

        context = MarketContextStore(root)
        context.save_industry_map(
            pd.DataFrame(
                [
                    {
                        "code": "600000",
                        "name": "浦发银行",
                        "industry": "银行",
                        "classification": "申万",
                        "provider": "test",
                    },
                    {
                        "code": "000001",
                        "name": "平安银行",
                        "industry": "银行",
                        "classification": "申万",
                        "provider": "test",
                    },
                ]
            )
        )
        context.save_index_daily(
            "sh000001",
            "上证指数",
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(
                        ["2026-09-22", "2026-09-23", "2026-09-24"]
                    ),
                    "open": [3800.0, 3810.0, 3820.0],
                    "high": [3820.0, 3830.0, 3840.0],
                    "low": [3780.0, 3790.0, 3800.0],
                    "close": [3810.0, 3820.0, 3830.0],
                    "volume": [1.0, 1.1, 1.2],
                    "amount": [100.0, 110.0, 120.0],
                    "provider": ["test", "test", "test"],
                }
            ),
        )
        assert len(context.read_industry_map()) == 2
        assert len(context.read_index_daily("sh000001")) == 3
        context.refresh_catalog()

        con = duckdb.connect(str(root / "aquant.duckdb"), read_only=True)
        try:
            assert con.execute("SELECT COUNT(*) FROM security_master").fetchone()[0] == 2
            assert con.execute("SELECT COUNT(*) FROM trade_calendar").fetchone()[0] == 3
            assert con.execute("SELECT COUNT(*) FROM daily_qfq").fetchone()[0] == 3
            assert con.execute("SELECT COUNT(*) FROM industry_map").fetchone()[0] == 2
            assert con.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0] == 3
        finally:
            con.close()

        profile = current_profile()
        assert profile.batch_size >= 1
        assert profile.download_workers >= 1

    print("OFFLINE_DATA_SMOKE_OK")


if __name__ == "__main__":
    main()
