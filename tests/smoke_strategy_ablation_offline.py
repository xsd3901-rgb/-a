from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

import aquant.research.strategy_ablation as strategy_ablation
from config import SETTINGS
from strategy import (
    V1_COMPONENT_COLUMNS,
    V1_COMPONENT_SPECS,
    score_history,
)


def _bars() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=180)
    close = np.linspace(10.0, 16.0, len(dates))
    volume = np.linspace(1_000_000.0, 2_000_000.0, len(dates))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.998,
            "high": close * 1.012,
            "low": close * 0.988,
            "close": close,
            "preclose": np.r_[close[0], close[:-1]],
            "volume": volume,
            "amount": close * volume,
            "turnover": 1.0,
            "pct_change": np.r_[0.0, np.diff(close) / close[:-1] * 100.0],
            "trade_status": 1,
            "is_st": 0,
        }
    )


def _manual_score(frame: pd.DataFrame) -> pd.Series:
    score = pd.Series(0, index=frame.index, dtype="int16")
    score += np.where(frame["close"] > frame["ma20"], 12, 0)
    score += np.where(
        (frame["ma5"] > frame["ma10"])
        & (frame["ma10"] > frame["ma20"]),
        14,
        0,
    )
    score += np.where(frame["ma20"] > frame["ma60"], 8, 0)
    score += np.where(frame["macd_dif"] > frame["macd_dea"], 12, 0)
    score += np.where(
        (frame["rsi6"] >= 45) & (frame["rsi6"] <= 78),
        10,
        0,
    )
    score += np.where(
        (frame["kdj_k"] > frame["kdj_d"])
        & (frame["kdj_j"] < 100),
        8,
        0,
    )
    score += np.where(
        (frame["vol_ratio5"] >= 1.05)
        & (frame["vol_ratio5"] <= 3.5),
        12,
        0,
    )
    score += np.where(frame["position_20"] >= 0.94, 10, 0)
    score += np.where(
        (frame["ret_5d"] >= 0)
        & (frame["ret_5d"] <= 18),
        7,
        0,
    )
    score += np.where(
        (frame["ret_20d"] >= -5)
        & (frame["ret_20d"] <= 35),
        7,
        0,
    )
    score -= np.where(frame["rsi6"] > 85, 12, 0)
    score -= np.where(frame["atr_pct"] > 9, 10, 0)
    score -= np.where(frame["ret_5d"] > 25, 10, 0)
    return score.clip(0, 100).round().astype("Int64")


def _ablation_samples() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=500)
    rows: list[dict] = []
    for code_idx, code in enumerate(("600001", "000001")):
        for idx, date in enumerate(dates):
            active = (idx + code_idx) % 2 == 0
            row = {
                "code": code,
                "name": f"样本{code_idx}",
                "date": date,
                "fwd_ret_5d": 2.0 if active else -1.0,
                "fwd_ret_10d": 2.5 if active else -1.2,
                "fwd_ret_20d": 3.0 if active else -1.5,
            }
            for col in V1_COMPONENT_COLUMNS:
                row[col] = 0

            # 固定 64 分，再让 ret5 健康规则提供 7 分跨过 68 阈值。
            row["v1c_close_above_ma20"] = 12
            row["v1c_short_ma_bull"] = 14
            row["v1c_ma20_above_ma60"] = 8
            row["v1c_macd_bull"] = 12
            row["v1c_rsi_healthy"] = 10
            row["v1c_kdj_bull"] = 8
            row["v1c_ret5_healthy"] = 7 if active else 0
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    scored = score_history(_bars())
    manual = _manual_score(scored)
    assert scored["score"].equals(manual)

    raw_sum = (
        scored[list(V1_COMPONENT_COLUMNS)]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
        .sum(axis=1)
        .clip(0, 100)
        .round()
        .astype("Int64")
    )
    assert scored["score"].equals(raw_sum)
    assert set(V1_COMPONENT_SPECS) == set(V1_COMPONENT_COLUMNS)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "data_store" / "research" / "feature_validation"
        reports = root / "reports"
        work.mkdir(parents=True, exist_ok=True)
        reports.mkdir(parents=True, exist_ok=True)
        _ablation_samples().to_parquet(
            work / "chunk_00001.parquet",
            index=False,
        )

        settings = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=reports,
            feature_validation_train_ratio=0.70,
        )

        with (
            patch.object(strategy_ablation, "SETTINGS", settings),
            patch.object(
                strategy_ablation,
                "ensure_directories",
                lambda: None,
            ),
            patch.object(
                strategy_ablation,
                "load_strategy_profile",
                lambda: {"score_threshold": 68},
            ),
        ):
            detail, summary_table, summary = (
                strategy_ablation.run_v1_ablation(
                    work_dir=work
                )
            )

        assert not detail.empty
        assert not summary_table.empty
        assert summary["自动修改权重"] is False
        ret5 = summary_table[
            summary_table["规则列"].eq("v1c_ret5_healthy")
        ].iloc[0]
        assert ret5["研究结论"] == "规则贡献稳定"
        assert float(ret5["边际帮助中位数%"]) > 0
        assert (
            reports / "strategy_ablation_v1.csv"
        ).exists()
        assert (
            reports / "strategy_ablation_v1_summary.json"
        ).exists()

    print("OFFLINE_STRATEGY_ABLATION_OK")


if __name__ == "__main__":
    main()
