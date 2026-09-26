from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import aquant.research.data_repair as data_repair
from config import SETTINGS


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=root / "reports",
        )
        settings.report_dir.mkdir(parents=True, exist_ok=True)

        before = pd.DataFrame(
            [
                {
                    "代码": "600001",
                    "名称": "缺口一",
                    "状态": "FAIL",
                    "原因": "覆盖率不足",
                },
                {
                    "代码": "600002",
                    "名称": "正常二",
                    "状态": "OK",
                    "原因": "通过",
                },
            ]
        )
        after = pd.DataFrame(
            [
                {
                    "代码": "600001",
                    "名称": "缺口一",
                    "状态": "OK",
                    "原因": "通过",
                },
                {
                    "代码": "600002",
                    "名称": "正常二",
                    "状态": "OK",
                    "原因": "通过",
                },
            ]
        )
        audits = [
            (before, {"FAIL": 1, "状态": "需修复"}),
            (after, {"FAIL": 0, "状态": "通过"}),
        ]
        calls: list[dict] = []

        def fake_audit():
            return audits.pop(0)

        def fake_bootstrap(**kwargs):
            calls.append(kwargs)
            return (
                pd.DataFrame([{"代码": "600001", "状态": "OK"}]),
                {"失败股票": 0},
            )

        with (
            patch.object(data_repair, "SETTINGS", settings),
            patch.object(data_repair, "run_data_audit", fake_audit),
            patch.object(data_repair, "bootstrap_market", fake_bootstrap),
            patch.object(data_repair, "ensure_directories", lambda: None),
        ):
            frame, summary = data_repair.repair_failed_market_data()
            assert summary["状态"] == "修复完成"
            assert summary["目标股票"] == 1
            assert summary["本次已修复"] == 1
            assert calls[0]["refresh"] is True
            assert calls[0]["codes"] == ["600001"]
            assert frame.iloc[0]["修复结果"] == "已通过"
            assert (settings.report_dir / "data_repair.csv").exists()
            assert (settings.report_dir / "data_repair_summary.json").exists()

    print("OFFLINE_DATA_REPAIR_OK")


if __name__ == "__main__":
    main()
