from __future__ import annotations

import numpy as np
import pandas as pd

from aquant.research.feature_validation import evaluate_feature_samples


def main() -> None:
    dates = pd.bdate_range("2024-01-02", periods=420)
    x = np.sin(np.linspace(0, 20 * np.pi, len(dates))) * 2.5
    trend = x > 0
    noise = np.cos(np.linspace(0, 13 * np.pi, len(dates))) * 0.05

    frame = pd.DataFrame(
        {
            "date": dates,
            "close_ma20_gap_pct": x,
            "trend_up": trend,
            "fwd_ret_5d": x * 0.30 + noise,
            "fwd_ret_10d": x * 0.45 + noise,
            "fwd_ret_20d": x * 0.60 + noise,
        }
    )

    result, split_date = evaluate_feature_samples(frame, train_ratio=0.70)
    assert split_date is not None
    assert not result.empty

    continuous = result[result["特征"] == "close_ma20_gap_pct"]
    assert len(continuous) == 3
    assert set(continuous["稳定性"]) == {"方向一致"}
    assert (pd.to_numeric(continuous["验证SpearmanIC"], errors="coerce") > 0.8).all()

    boolean = result[result["特征"] == "trend_up"]
    assert len(boolean) == 3
    assert set(boolean["稳定性"]) == {"方向一致"}
    assert (pd.to_numeric(boolean["验证高低组收益差%"], errors="coerce") > 0).all()

    print("OFFLINE_FEATURE_VALIDATION_OK")


if __name__ == "__main__":
    main()
