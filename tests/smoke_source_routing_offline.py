from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from aquant.data import service as service_module
from aquant.data.providers.baostock_session import BaoStockSession
from aquant.data.reference_service import ReferenceDataService
from aquant.data.routing.router import DataSourceRouter
from aquant.data.source_health import SourceHealthRegistry


class _LoginResult:
    error_code = "0"
    error_msg = ""


class _FakeBaoStock:
    def __init__(self) -> None:
        self.logins = 0
        self.logouts = 0

    def login(self):
        self.logins += 1
        return _LoginResult()

    def logout(self):
        self.logouts += 1


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "data_store"

        # First-run seed must make reference data usable without network.
        reference = ReferenceDataService(str(root))
        securities = reference.store.read_security_master()
        calendar = reference.store.read_trade_calendar()
        assert len(securities) > 5000
        assert len(calendar) > 6000
        assert "provider" in securities.columns
        assert set(securities["provider"].astype(str)) == {"bundled_seed"}
        assert "provider" in calendar.columns
        assert "bundled_seed" in set(calendar["provider"].astype(str))

        # Normal stock-list reads must stay local and never touch remote APIs.
        original_fetch = service_module.fetch_stock_list_with_timeout

        def should_not_run(*args, **kwargs):
            raise AssertionError("local-first stock list unexpectedly touched network")

        service_module.fetch_stock_list_with_timeout = should_not_run
        try:
            service = service_module.MarketDataService(str(root))
            stocks = service.stock_list()
        finally:
            service_module.fetch_stock_list_with_timeout = original_fetch

        assert len(stocks) > 5000

        # Circuit breaker is capability-scoped: a failing stock-list endpoint
        # must not disable the same provider's daily-bars capability.
        health = SourceHealthRegistry(root)
        router = DataSourceRouter(root)
        health.mark_failure(
            "stock_list:eastmoney",
            "offline test",
        )
        selected = router.names(
            ["eastmoney", "baostock"],
            capability="stock_list",
        )
        assert "eastmoney" not in selected
        assert "baostock" in selected

        daily_selected = router.names(
            ["eastmoney", "baostock"],
            capability="daily",
        )
        assert "eastmoney" in daily_selected
        assert "baostock" in daily_selected

        snapshot = health.snapshot()
        row = next(
            item
            for item in snapshot
            if item["source"] == "stock_list:eastmoney"
        )
        assert row["status"] == "cooldown"
        assert row["score"] < 100

        # BaoStock session is reused instead of login/logout per stock.
        fake = _FakeBaoStock()
        session = BaoStockSession(max_calls=2)
        session._module = lambda: fake  # type: ignore[method-assign]
        assert session.run(lambda bs: "one") == "one"
        assert session.run(lambda bs: "two") == "two"
        assert fake.logins == 1
        assert fake.logouts == 1

    print("OFFLINE_SOURCE_ROUTING_OK")


if __name__ == "__main__":
    main()
