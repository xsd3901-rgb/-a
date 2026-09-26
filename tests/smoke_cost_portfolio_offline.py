from __future__ import annotations

import numpy as np
import pandas as pd

from aquant.research.portfolio import simulate_portfolio
from aquant.risk.costs import AShareCostModel
from backtest import backtest_scored_stock


def _scored_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=150)
    close = np.linspace(10.0, 12.0, len(dates))
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
            "preclose": pd.Series(close).shift(1).bfill(),
            "trade_status": 1,
            "is_st": 0,
            "atr14": 0.20,
            "ma10": close * 0.98,
            "macd_dif": 0.20,
            "macd_dea": 0.10,
            "score": 0.0,
        }
    )

    raw_close = close * 10.0
    frame["exec_open"] = raw_close
    frame["exec_high"] = raw_close * 1.005
    frame["exec_low"] = raw_close * 0.995
    frame["exec_close"] = raw_close
    frame["exec_preclose"] = pd.Series(raw_close).shift(1).bfill()
    frame["exec_volume"] = 1_000_000.0
    frame["exec_trade_status"] = 1
    frame["exec_is_st"] = 0

    signal_idx = 120
    frame.loc[signal_idx, "score"] = 80.0
    entry_idx = signal_idx + 1
    target_idx = entry_idx + 1
    frame.loc[entry_idx, "exec_high"] = frame.loc[entry_idx, "exec_open"] * 1.01
    frame.loc[target_idx, "exec_high"] = frame.loc[target_idx, "exec_open"] * 1.08
    return frame


def main() -> None:
    costs = AShareCostModel(
        commission_bps=2.5,
        min_commission_cny=5.0,
        slippage_bps=3.0,
    )
    assert abs(costs.stamp_duty_rate("2023-08-27") - 0.0010) < 1e-12
    assert abs(costs.stamp_duty_rate("2023-08-28") - 0.0005) < 1e-12
    assert abs(costs.transfer_fee_rate("2022-04-28") - 0.00002) < 1e-12
    assert abs(costs.transfer_fee_rate("2022-04-29") - 0.00001) < 1e-12

    frame = _scored_frame()
    trades = backtest_scored_stock(
        code="600000",
        name="测试股份",
        scored=frame,
        score_threshold=68,
        stop_atr_multiple=1.8,
        target_atr_multiple=3.0,
        max_hold_days=10,
        signal_start_date=frame.loc[120, "date"],
        signal_end_date=frame.loc[120, "date"],
        model_name="V1_TEST",
        min_signal_index=0,
    )
    assert len(trades) == 1
    trade = trades[0]
    # 成交必须走 exec_ 未复权价格，而不是约 11 元的复权价格。
    assert trade["买入价"] > 100
    assert trade["毛收益%"] > trade["净收益%"]
    assert 0 < trade["估算交易成本%"] < 0.5
    assert trade["模型"] == "V1_TEST"

    trade_frame = pd.DataFrame(trades)
    raw_prices = pd.DataFrame(
        {
            "date": frame["date"],
            "close": frame["exec_close"],
        }
    )
    executed, equity, metrics = simulate_portfolio(
        trade_frame,
        initial_capital=100_000,
        max_positions=5,
        max_position_pct=0.20,
        price_frames={"600000": raw_prices},
    )
    assert len(executed) == 1
    assert not equity.empty
    assert executed.iloc[0]["实际股数"] % 100 == 0
    assert metrics["实际成交交易"] == 1
    assert metrics["期末资金"] > 0
    assert "盯市权益" in equity.columns
    assert metrics["盯市覆盖率%"] > 0
    assert metrics["最大盯市回撤%"] >= 0

    # 单独构造一个持仓中途明显回撤的样本，确认回撤不是只看买卖日。
    mtm_trade = pd.DataFrame(
        [
            {
                "代码": "600001",
                "名称": "盯市样本",
                "信号评分": 80.0,
                "买入日": "2025-01-02",
                "卖出日": "2025-01-07",
                "买入价": 100.0,
                "卖出价": 110.0,
            }
        ]
    )
    mtm_prices = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
            ),
            "close": [100.0, 78.0, 92.0, 110.0],
        }
    )
    _, mtm_equity, mtm_metrics = simulate_portfolio(
        mtm_trade,
        initial_capital=100_000,
        max_positions=5,
        max_position_pct=0.20,
        price_frames={"600001": mtm_prices},
    )
    assert len(mtm_equity) >= 4
    assert mtm_metrics["最大盯市回撤%"] > 2.0
    assert mtm_metrics["盯市覆盖率%"] == 100.0

    print("OFFLINE_COST_PORTFOLIO_OK")


if __name__ == "__main__":
    main()
