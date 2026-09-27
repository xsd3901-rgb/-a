from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import scanner
from config import SETTINGS


class FakeProvider:
    def latest_trade_date(self) -> pd.Timestamp:
        return pd.Timestamp("2026-09-25")

    def stock_list(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"code": "600001", "name": "中断一"},
                {"code": "600002", "name": "中断二"},
                {"code": "600003", "name": "中断三"},
            ]
        )


class FakeContext:
    def index_daily(self, *args, **kwargs) -> pd.DataFrame:
        return pd.DataFrame()


def _selected(code: str, name: str) -> dict:
    return {
        "代码": code,
        "名称": name,
        "交易日": "2026-09-25",
        "现价": 10.0,
        "评分": 80,
        "市场环境": "震荡",
        "环境分": 0.0,
        "相对沪深300_20日%": 1.0,
        "风险": "低",
        "风险过滤": "通过",
        "ATR波动%": 2.0,
        "止损参考": 9.0,
        "目标参考": 12.0,
        "信号原因": "offline",
    }


def main() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = replace(
            SETTINGS,
            project_root=root,
            data_store_dir=root / "data_store",
            cache_dir=root / "data_cache",
            report_dir=root / "reports",
            scan_resume=True,
            top_n=50,
        )
        settings.report_dir.mkdir(parents=True, exist_ok=True)

        runtime = SimpleNamespace(
            name="interrupt-test",
            batch_size=10,  # 故意让第1只尚未达到常规批次落盘点
            download_workers=1,
            compute_workers=1,
        )
        snapshot = SimpleNamespace(
            regime="震荡",
            score=0.0,
            index_count=0,
            details=pd.DataFrame(),
        )

        calls: dict[str, int] = {}
        interrupt_once = {"done": False}

        def fake_scan_one(code: str, name: str, **kwargs):
            calls[code] = calls.get(code, 0) + 1
            if code == "600002" and not interrupt_once["done"]:
                interrupt_once["done"] = True
                raise KeyboardInterrupt()
            return _selected(code, name), None

        with (
            patch.object(scanner, "SETTINGS", settings),
            patch.object(scanner, "ensure_directories", lambda: None),
            patch.object(scanner, "MarketDataService", FakeProvider),
            patch.object(scanner, "MarketContextService", FakeContext),
            patch.object(scanner, "current_profile", lambda: runtime),
            patch.object(
                scanner,
                "load_strategy_profile",
                lambda: {"score_threshold": 68},
            ),
            patch.object(
                scanner,
                "detect_market_regime",
                lambda *a, **k: snapshot,
            ),
            patch.object(scanner, "_scan_one", fake_scan_one),
        ):
            try:
                scanner.scan_market(limit=3)
                raise AssertionError("第一次应模拟 KeyboardInterrupt")
            except KeyboardInterrupt:
                pass

            checkpoint = settings.report_dir / "scan_checkpoint.json"
            assert checkpoint.exists()

            # 第1只虽未达到 batch_size，也必须在中断处理里被落盘。
            before = calls.copy()
            assert before["600001"] == 1
            assert before["600002"] == 1

            result = scanner.scan_market(limit=3)
            assert len(result) == 3

            # 已完成的 600001 不应重算；中断中的 600002 和未开始的
            # 600003 会继续执行。
            assert calls["600001"] == 1
            assert calls["600002"] == 2
            assert calls["600003"] == 1

    print("OFFLINE_INTERRUPT_RESUME_OK")


if __name__ == "__main__":
    main()
