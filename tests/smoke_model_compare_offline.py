from __future__ import annotations

import numpy as np
import pandas as pd

from aquant.models.score_v2 import FeatureRule
from aquant.research.model_compare import _backtest_fold


def _frame() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=150)
    rows: list[dict] = []
    for code, feature_value, base in [
        ("600001", 2.0, 10.0),
        ("600002", -2.0, 20.0),
    ]:
        for i, date in enumerate(dates):
            signal_close = base * (1.0 + i * 0.001)
            raw_close = signal_close * 10.0
            rows.append(
                {
                    "code": code,
                    "name": f"样本{code[-1]}",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "date": date,
                    "open": signal_close,
                    "high": signal_close * 1.02,
                    "low": signal_close * 0.99,
                    "close": signal_close,
                    "preclose": signal_close / 1.001,
                    "volume": 1_000_000.0,
                    "amount": 100_000_000.0,
                    "turnover": 2.0,
                    "pct_change": 0.1,
                    "trade_status": 1,
                    "is_st": 0,
                    "atr14": signal_close * 0.004,
                    "atr_pct": 0.4,
                    "ma10": signal_close * 0.98,
                    "macd_dif": 0.2,
                    "macd_dea": 0.1,
                    "score": 80.0,
                    "exec_open": raw_close,
                    "exec_high": raw_close * 1.03,
                    "exec_low": raw_close * 0.99,
                    "exec_close": raw_close * 1.01,
                    "exec_preclose": raw_close / 1.001,
                    "exec_volume": 1_000_000.0,
                    "exec_amount": 100_000_000.0,
                    "exec_turnover": 2.0,
                    "exec_pct_change": 0.1,
                    "exec_trade_status": 1,
                    "exec_is_st": 0,
                    "close_ma20_gap_pct": feature_value,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    frame = _frame()
    rules = [
        FeatureRule(
            feature="close_ma20_gap_pct",
            kind="continuous",
            direction="high",
            weight=2.0,
            low_threshold=-0.5,
            high_threshold=0.5,
            quality=2.0,
            train_effect=0.5,
            train_ic=0.05,
        )
    ]
    validation_start = pd.Timestamp("2025-03-03")
    validation_end = pd.Timestamp("2025-06-30")

    v1, v2 = _backtest_fold(
        frame,
        rules,
        validation_start,
        validation_end,
        fold_no=1,
    )

    assert len(v1) > 0
    assert len(v2) > 0
    assert all(trade["模型"] == "V1" for trade in v1)
    assert all(trade["模型"] == "V2" for trade in v2)
    # V2 每日只选相对更强的股票，交易数不应超过 V1。
    assert len(v2) <= len(v1)
    assert all(float(trade["买入价"]) > 50 for trade in v2)
    print("OFFLINE_MODEL_COMPARE_OK")


if __name__ == "__main__":
    main()
