from __future__ import annotations

import pandas as pd

from aquant.data.continuous import build_point_in_time_continuous


def main() -> None:
    raw = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=6),
            "open": [10.0, 10.2, 8.2, 8.3, 8.4, 8.5],
            "high": [10.1, 10.3, 8.3, 8.4, 8.5, 8.6],
            "low": [9.9, 10.1, 8.1, 8.2, 8.3, 8.4],
            "close": [10.0, 10.2, 8.2, 8.3, 8.4, 8.5],
            # 第3天模拟除权造成原始价格大幅下降，但官方涨跌幅为0。
            "pct_change": [0.0, 2.0, 0.0, 1.219512, 1.204819, 1.190476],
            "volume": [1000] * 6,
            "trade_status": [1] * 6,
            "is_st": [0] * 6,
        }
    )

    continuous = build_point_in_time_continuous(raw)
    assert len(continuous) == len(raw)
    # 除权日不会因为原始价格从10.2掉到8.2而制造约-20%的假跌幅。
    day2 = float(continuous.loc[1, "close"])
    day3 = float(continuous.loc[2, "close"])
    assert abs(day3 / day2 - 1.0) < 1e-10

    # 修改未来最后一天的数据，不应改变此前任何连续价格。
    changed = raw.copy()
    changed.loc[5, "pct_change"] = 9.9
    changed.loc[5, "close"] = 9.3
    continuous2 = build_point_in_time_continuous(changed)
    pd.testing.assert_series_equal(
        continuous.loc[:4, "close"],
        continuous2.loc[:4, "close"],
        check_names=False,
    )

    # 停牌日不参与技术指标K线窗口；执行层会单独保留该日期。
    suspended = raw.copy()
    suspended.loc[2, "trade_status"] = 0
    suspended.loc[2, ["open", "high", "low", "close"]] = pd.NA
    suspended_continuous = build_point_in_time_continuous(suspended)
    assert len(suspended_continuous) == len(raw) - 1
    assert pd.Timestamp(raw.loc[2, "date"]) not in set(
        pd.to_datetime(suspended_continuous["date"])
    )

    assert set(continuous["signal_price_mode"]) == {"point_in_time_continuous"}
    print("OFFLINE_CONTINUOUS_PRICE_OK")


if __name__ == "__main__":
    main()
