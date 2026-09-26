from __future__ import annotations

import numpy as np
import pandas as pd

from aquant.features.relative_strength import add_relative_strength
from aquant.features.technical import TechnicalFeatureBuilder
from aquant.research.market_regime import build_market_regime_history, classify_market_regime


def _bars(multiplier: float = 1.0) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=140)
    close = np.linspace(10.0, 16.0, len(dates)) * multiplier
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.linspace(1_000_000, 1_500_000, len(dates)),
            "amount": np.linspace(12_000_000, 20_000_000, len(dates)),
        }
    )


class FakeContext:
    def index_daily(self, index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        dates = pd.bdate_range(pd.Timestamp(start_date), pd.Timestamp(end_date))
        base = {
            "sh000001": 3000.0,
            "sz399001": 10000.0,
            "sz399006": 2000.0,
            "sh000300": 4000.0,
            "sh000852": 6000.0,
        }.get(index_code, 1000.0)
        close = np.linspace(base, base * 1.12, len(dates))
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


def main() -> None:
    stock = _bars(1.0)
    feat = TechnicalFeatureBuilder().transform(stock)
    required = {
        "ma20",
        "ma60",
        "ret_20d",
        "breakout_20_pct",
        "vol_ratio20",
        "volatility_20",
        "trend_up",
        "overheated",
    }
    assert required.issubset(set(feat.columns))
    assert len(feat) == len(stock)

    benchmark = _bars(0.9).rename(columns={"date": "trade_date"})
    rs = add_relative_strength(feat, benchmark)
    assert {"rs_5d", "rs_20d", "rs_60d"}.issubset(set(rs.columns))
    assert rs["rs_20d"].notna().sum() > 0

    strong = pd.DataFrame(
        {"score": [3.0, 2.5, 2.0, 2.5, 3.0]}
    )
    regime, score, strong_count, weak_count = classify_market_regime(strong)
    assert regime == "强势上行"
    assert score > 0
    assert strong_count == 5
    assert weak_count == 0

    mixed = pd.DataFrame({"score": [1.0, 0.0, -0.5, 0.5, -0.5]})
    regime, _, _, _ = classify_market_regime(mixed)
    assert regime == "震荡"

    history = build_market_regime_history(
        FakeContext(),
        "2025-06-01",
        "2025-08-31",
    )
    assert not history.empty
    assert {
        "trade_date",
        "market_regime",
        "market_score",
        "strong_count",
        "weak_count",
        "index_count",
    }.issubset(set(history.columns))
    assert history["index_count"].max() == 5

    print("OFFLINE_FEATURES_REGIME_OK")


if __name__ == "__main__":
    main()
