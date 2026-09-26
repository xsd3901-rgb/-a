from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from aquant.models.score_v2 import CandidateScoreV2, fit_candidate_model
from aquant.research.feature_selection import select_candidate_features
from aquant.research.feature_validation import FEATURE_SPECS
from aquant.research.validation import walk_forward_windows
from aquant.research.walk_forward import evaluate_walk_forward_store


def _validation_report() -> pd.DataFrame:
    rows: list[dict] = []
    for horizon in (5, 10, 20):
        rows.append(
            {
                "特征": "close_ma20_gap_pct",
                "类型": "continuous",
                "预测窗口": f"{horizon}日",
                "切分日": "2025-01-01",
                "训练样本": 1000,
                "验证样本": 500,
                "训练SpearmanIC": 0.06,
                "验证SpearmanIC": 0.05,
                "训练高低组收益差%": 0.45,
                "验证高低组收益差%": 0.40,
                "验证t值": 2.4,
                "训练方向": "高值更优",
                "稳定性": "方向一致",
                "训练30%分位": -0.5,
                "训练70%分位": 0.5,
            }
        )
        rows.append(
            {
                "特征": "trend_up",
                "类型": "boolean",
                "预测窗口": f"{horizon}日",
                "切分日": "2025-01-01",
                "训练样本": 1000,
                "验证样本": 500,
                "训练SpearmanIC": np.nan,
                "验证SpearmanIC": np.nan,
                "训练高低组收益差%": 0.30,
                "验证高低组收益差%": 0.25,
                "验证t值": 1.8,
                "训练方向": "True更优",
                "稳定性": "方向一致",
                "训练30%分位": np.nan,
                "训练70%分位": np.nan,
            }
        )
    return pd.DataFrame(rows)


def _research_samples() -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-03", periods=520)
    codes = [f"60{i:04d}" for i in range(20)]
    rows: list[dict] = []

    for dpos, date in enumerate(dates):
        time_wave = np.sin(dpos / 17.0)
        for spos, code in enumerate(codes):
            cross = (spos - 9.5) / 5.0
            factor = cross + time_wave * 0.35
            trend_up = factor > 0
            noise = np.sin((dpos + 1) * (spos + 2) * 0.013) * 0.08
            future_10 = factor * 0.55 + (0.22 if trend_up else -0.10) + noise
            rows.append(
                {
                    "code": code,
                    "name": f"样本{spos:02d}",
                    "date": date,
                    "close_ma20_gap_pct": factor,
                    "trend_up": trend_up,
                    "fwd_ret_10d": future_10,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    selected = select_candidate_features(_validation_report())
    assert not selected.empty
    assert "close_ma20_gap_pct" in set(selected["特征"])
    assert "trend_up" in set(selected["特征"])

    samples = _research_samples()
    model = fit_candidate_model(
        samples,
        {
            "close_ma20_gap_pct": FEATURE_SPECS["close_ma20_gap_pct"],
            "trend_up": FEATURE_SPECS["trend_up"],
        },
        target="fwd_ret_10d",
        max_features=2,
    )
    assert len(model.rules) == 2

    scored = CandidateScoreV2(model.rules).predict(samples.head(1000))
    assert "v2_score" in scored.columns
    assert scored["v2_score"].notna().sum() > 0

    dates = samples["date"].drop_duplicates().sort_values()
    windows = walk_forward_windows(
        dates,
        train_bars=252,
        validation_bars=63,
        gap_bars=10,
        step_bars=63,
    )
    assert len(windows) >= 3
    assert all(w.train_end < w.validation_start for w in windows)

    with TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        half = len(samples) // 2
        samples.iloc[:half].to_parquet(
            work_dir / "chunk_00001.parquet",
            index=False,
        )
        samples.iloc[half:].to_parquet(
            work_dir / "chunk_00002.parquet",
            index=False,
        )

        folds, rules, summary = evaluate_walk_forward_store(
            work_dir,
            horizon=10,
            train_bars=252,
            validation_bars=63,
            gap_bars=10,
            step_bars=63,
            top_quantile=0.20,
        )
        assert len(folds) >= 3
        assert not rules.empty
        assert (pd.to_numeric(folds["超额收益%"], errors="coerce") > 0).mean() >= 0.6
        assert summary["超额收益均值%"] > 0

    print("OFFLINE_V2_WALK_FORWARD_OK")


if __name__ == "__main__":
    main()
