from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from aquant.data.universe_store import HistoricalUniverseStore
from aquant.risk.trading_rules import (
    board_of,
    is_one_price_limit_up,
    is_tradable_bar,
    limit_prices,
    price_limit_rule,
)


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "data_store"
        store = HistoricalUniverseStore(root)
        store.save(
            pd.DataFrame(
                [
                    {
                        "code": "600001",
                        "name": "主板样本",
                        "listing_date": "2010-01-01",
                        "delisting_date": None,
                        "status": "1",
                        "board": "sse_main",
                        "market": "SSE",
                        "provider": "test",
                    },
                    {
                        "code": "300001",
                        "name": "创业板样本",
                        "listing_date": "2009-10-30",
                        "delisting_date": None,
                        "status": "1",
                        "board": "chinext",
                        "market": "SZSE",
                        "provider": "test",
                    },
                    {
                        "code": "600999",
                        "name": "退市样本",
                        "listing_date": "2012-01-01",
                        "delisting_date": "2021-06-30",
                        "status": "0",
                        "board": "sse_main",
                        "market": "SSE",
                        "provider": "test",
                    },
                ]
            )
        )

        old_universe = store.universe_on("2020-01-02")
        assert set(old_universe["code"]) == {"600001", "300001", "600999"}

        new_universe = store.universe_on("2022-01-02")
        assert set(new_universe["code"]) == {"600001", "300001"}

        store.refresh_catalog()

        assert board_of("688001") == "star"
        assert board_of("300001") == "chinext"
        assert board_of("600001") == "sse_main"
        assert board_of("000001") == "szse_main"
        assert board_of("830001") == "bse"

        main_rule = price_limit_rule(
            "600001", "2026-09-25", is_st=False, trading_days_since_listing=2000
        )
        assert main_rule.up_limit_pct == 10.0
        assert limit_prices(10.0, main_rule) == (11.0, 9.0)

        st_rule = price_limit_rule(
            "600001", "2026-09-25", is_st=True, trading_days_since_listing=2000
        )
        assert st_rule.up_limit_pct == 5.0
        assert limit_prices(10.0, st_rule) == (10.5, 9.5)

        chinext_rule = price_limit_rule(
            "300001", "2026-09-25", is_st=False, trading_days_since_listing=2000
        )
        assert chinext_rule.up_limit_pct == 20.0

        star_ipo = price_limit_rule(
            "688001", "2026-09-25", trading_days_since_listing=3
        )
        assert star_ipo.no_price_limit is True
        assert limit_prices(10.0, star_ipo) == (None, None)

        bse_rule = price_limit_rule(
            "830001", "2026-09-25", trading_days_since_listing=10
        )
        assert bse_rule.up_limit_pct == 30.0

        tradable = pd.Series(
            {"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2, "volume": 1000, "trade_status": 1}
        )
        suspended = tradable.copy()
        suspended["trade_status"] = 0
        assert is_tradable_bar(tradable) is True
        assert is_tradable_bar(suspended) is False

        one_price = pd.Series(
            {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0}
        )
        assert is_one_price_limit_up(one_price, 11.0) is True

    print("OFFLINE_UNIVERSE_RULES_OK")


if __name__ == "__main__":
    main()
