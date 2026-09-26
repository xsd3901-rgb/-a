from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import aquant.research.local_prepare as local_prepare
from config import SETTINGS


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        local_prepare.SETTINGS = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=root / "reports",
        )
        local_prepare.ensure_directories = lambda: (
            (root / "data_store").mkdir(parents=True, exist_ok=True),
            (root / "data_cache").mkdir(parents=True, exist_ok=True),
            (root / "reports").mkdir(parents=True, exist_ok=True),
        )

        progress_messages: list[str] = []

        local_prepare.bootstrap_market = lambda **kwargs: (
            pd.DataFrame([{"状态": "OK"}]),
            {
                "股票池": 100,
                "成功股票": 100,
                "失败股票": 0,
            },
        )
        local_prepare.run_data_audit = lambda **kwargs: (
            pd.DataFrame([{"状态": "OK"}]),
            {
                "状态": "通过",
                "检查股票数": 100,
                "FAIL": 0,
                "覆盖率中位数%": 99.8,
            },
        )
        local_prepare.run_system_validation = lambda **kwargs: (
            pd.DataFrame([{"模型": "V1"}]),
            {
                "总体状态": "系统链路通过，继续使用V1",
            },
        )
        local_prepare.release_readiness = lambda **kwargs: (
            pd.DataFrame([{"检查项": "活动模型", "状态": "OK"}]),
            {
                "总体状态": "本地运行链路已就绪",
                "活动模型": "V1",
                "V2状态": "继续观察",
            },
        )

        steps, summary = local_prepare.run_local_prepare(
            bootstrap_limit=None,
            validation_limit=200,
            horizon=10,
            progress=progress_messages.append,
        )

        assert len(steps) == 4
        assert (steps["状态"] == "OK").all()
        assert summary["总体状态"] == "首次准备完成"
        assert summary["活动模型"] == "V1"
        assert summary["自动晋级"] is False
        assert len(progress_messages) == 4
        assert (root / "reports" / "local_prepare_steps.csv").exists()
        assert (root / "reports" / "local_prepare_summary.json").exists()

    print("OFFLINE_LOCAL_PREPARE_OK")


if __name__ == "__main__":
    main()
