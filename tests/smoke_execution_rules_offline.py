from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import backtest_scored_stock


def base_scored() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=130)
    close = np.full(len(dates), 10.0)
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": close.copy(),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close.copy(),
            "volume": np.full(len(dates), 1_000_000.0),
            "amount": np.full(len(dates), 10_000_000.0),
            "turnover": np.full(len(dates), 2.0),
            "preclose": np.r_[np.nan, close[:-1]],
            "pct_change": np.zeros(len(dates)),
            "trade_status": np.ones(len(dates)),
            "is_st": np.zeros(len(dates)),
            "score": np.zeros(len(dates), dtype=int),
            "atr14": np.full(len(dates), 1.0),
            "ma10": np.full(len(dates), 9.5),
            "macd_dif": np.full(len(dates), 0.2),
            "macd_dea": np.full(len(dates), 0.1),
        }
    )
    frame.loc[119, "score"] = 100
    return frame


def run_case(frame: pd.DataFrame, max_hold_days: int = 2) -> list[dict]:
    return backtest_scored_stock(
        code="600001",
        name="样本主板",
        scored=frame,
        score_threshold=68,
        stop_atr_multiple=1.8,
        target_atr_multiple=3.0,
        max_hold_days=max_hold_days,
        listing_date="2010-01-01",
        signal_start_date="2025-01-01",
    )


def main() -> None:
    # 历史 ST 信号不允许新开仓。
    st_case = base_scored()
    st_case.loc[119, "is_st"] = 1
    assert run_case(st_case) == []

    # 次日开盘直接涨停，不假设能够在涨停价买到。
    limit_up = base_scored()
    limit_up.loc[120, ["open", "high", "low", "close"]] = 11.0
    limit_up.loc[120, "preclose"] = 10.0
    limit_up.loc[120, "pct_change"] = 10.0
    assert run_case(limit_up) == []

    # 观察窗口结束日若一字跌停不能卖，顺延到下一可交易日。
    delayed = base_scored()
    delayed.loc[121, ["open", "high", "low", "close"]] = 9.0
    delayed.loc[121, "preclose"] = 10.0
    delayed.loc[121, "pct_change"] = -10.0
    delayed.loc[122, ["open", "high", "low", "close"]] = [9.2, 9.3, 9.1, 9.2]
    delayed.loc[122, "preclose"] = 9.0
    delayed.loc[122, "pct_change"] = (9.2 / 9.0 - 1.0) * 100

    trades = run_case(delayed)
    assert len(trades) == 1
    assert trades[0]["退出原因"] == "观察窗口结束_延迟成交"
    assert trades[0]["延迟卖出交易日"] == 1
    assert trades[0]["卖出日"] == pd.Timestamp(delayed.loc[122, "date"]).strftime("%Y-%m-%d")

    # T+1：买入当日即使触发目标价，也不能当天卖出。
    t1 = base_scored()
    t1.loc[120, "high"] = 14.0
    t1.loc[121, ["open", "high", "low", "close"]] = [10.0, 10.1, 9.9, 10.0]
    trades = run_case(t1, max_hold_days=2)
    assert len(trades) == 1
    assert trades[0]["买入日"] != trades[0]["卖出日"]
    assert trades[0]["卖出日"] == pd.Timestamp(t1.loc[121, "date"]).strftime("%Y-%m-%d")

    # 停牌/不可交易的入场日同样不能开仓。
    suspended = base_scored()
    suspended.loc[120, "trade_status"] = 0
    assert run_case(suspended) == []

    print("OFFLINE_EXECUTION_RULES_OK")


if __name__ == "__main__":
    main()
