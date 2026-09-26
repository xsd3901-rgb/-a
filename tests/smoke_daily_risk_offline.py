from __future__ import annotations

import pandas as pd

from aquant.risk.daily import DailySwingRiskFilter


def main() -> None:
    gate = DailySwingRiskFilter()

    healthy = pd.Series(
        {
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 1_000_000,
            "trade_status": 1,
            "is_st": 0,
            "atr_pct": 3.5,
            "amount_ma5": 80_000_000,
            "overheated": False,
        }
    )
    result = gate.evaluate(healthy, fallback_name="正常股份")
    assert result.allowed is True
    assert result.level == "低"

    illiquid = healthy.copy()
    illiquid["amount_ma5"] = 5_000_000
    result = gate.evaluate(illiquid, fallback_name="正常股份")
    assert result.allowed is False
    assert any("成交额" in reason for reason in result.reasons)

    extreme = healthy.copy()
    extreme["atr_pct"] = 15.0
    result = gate.evaluate(extreme, fallback_name="正常股份")
    assert result.allowed is False
    assert any("ATR" in reason for reason in result.reasons)

    suspended = healthy.copy()
    suspended["trade_status"] = 0
    result = gate.evaluate(suspended, fallback_name="正常股份")
    assert result.allowed is False

    st = healthy.copy()
    st["is_st"] = 1
    result = gate.evaluate(st, fallback_name="ST样本")
    assert result.allowed is False

    print("OFFLINE_DAILY_RISK_OK")


if __name__ == "__main__":
    main()
