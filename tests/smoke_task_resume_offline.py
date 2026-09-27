from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import backtest
import scanner
from config import SETTINGS


class FakeScanProvider:
    def latest_trade_date(self) -> pd.Timestamp:
        return pd.Timestamp("2026-09-25")

    def stock_list(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"code": "600001", "name": "扫描一"},
                {"code": "600002", "name": "扫描二"},
                {"code": "600003", "name": "扫描三"},
                {"code": "600004", "name": "扫描四"},
            ]
        )


class FakeContext:
    def index_daily(self, *args, **kwargs) -> pd.DataFrame:
        return pd.DataFrame()


class FakeBacktestProvider:
    def latest_trade_date(self) -> pd.Timestamp:
        return pd.Timestamp("2026-09-25")


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
            backtest_resume=True,
            top_n=50,
        )
        settings.report_dir.mkdir(parents=True, exist_ok=True)

        runtime = SimpleNamespace(
            name="resume-test",
            batch_size=2,
            download_workers=1,
            compute_workers=1,
            backtest_workers=1,
        )

        # ---------- 扫描断点 ----------
        scan_calls: dict[str, int] = {}

        def fake_scan_one(code: str, name: str, **kwargs):
            scan_calls[code] = scan_calls.get(code, 0) + 1
            if code == "600003" and scan_calls[code] == 1:
                return None, {
                    "代码": code,
                    "名称": name,
                    "错误": "临时网络错误",
                }
            return (
                {
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
                },
                None,
            )

        snapshot = SimpleNamespace(
            regime="震荡",
            score=0.0,
            index_count=0,
            details=pd.DataFrame(),
        )

        with (
            patch.object(scanner, "SETTINGS", settings),
            patch.object(scanner, "ensure_directories", lambda: None),
            patch.object(scanner, "MarketDataService", FakeScanProvider),
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
            first = scanner.scan_market(limit=4)
            assert len(first) == 3
            assert sum(scan_calls.values()) == 4

            second = scanner.scan_market(limit=4)
            assert len(second) == 4
            # 第二次只重试第一次失败的 600003。
            assert sum(scan_calls.values()) == 5
            assert scan_calls["600003"] == 2

            third = scanner.scan_market(limit=4)
            assert len(third) == 4
            # 全部已完成后，同一交易日/同一参数再次运行不重复计算。
            assert sum(scan_calls.values()) == 5

            scan_checkpoint = settings.report_dir / "scan_checkpoint.json"
            assert scan_checkpoint.exists()

        # ---------- 回测断点 ----------
        universe = pd.DataFrame(
            [
                {
                    "code": "600001",
                    "name": "回测一",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "delisting_date": pd.NaT,
                },
                {
                    "code": "600002",
                    "name": "回测二",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "delisting_date": pd.NaT,
                },
                {
                    "code": "600003",
                    "name": "回测三",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "delisting_date": pd.NaT,
                },
                {
                    "code": "600004",
                    "name": "回测四",
                    "listing_date": pd.Timestamp("2020-01-01"),
                    "delisting_date": pd.NaT,
                },
            ]
        )

        backtest_calls: dict[str, int] = {}

        def fake_backtest_one(stock: dict, **kwargs):
            code = str(stock["code"])
            name = str(stock["name"])
            backtest_calls[code] = backtest_calls.get(code, 0) + 1
            if code == "600002" and backtest_calls[code] == 1:
                return [], {
                    "代码": code,
                    "名称": name,
                    "错误": "临时读取失败",
                }
            return [
                {
                    "代码": code,
                    "名称": name,
                    "信号日": "2026-01-05",
                    "买入日": "2026-01-06",
                    "卖出日": "2026-01-12",
                    "净收益%": 1.25,
                }
            ], None

        with (
            patch.object(backtest, "SETTINGS", settings),
            patch.object(backtest, "ensure_directories", lambda: None),
            patch.object(backtest, "MarketDataService", FakeBacktestProvider),
            patch.object(backtest, "MarketContextService", lambda: object()),
            patch.object(backtest, "current_profile", lambda: runtime),
            patch.object(
                backtest,
                "load_strategy_profile",
                lambda: {
                    "score_threshold": 68,
                    "stop_atr_multiple": 1.8,
                    "target_atr_multiple": 3.0,
                    "max_hold_days": 30,
                },
            ),
            patch.object(
                backtest,
                "_historical_universe",
                lambda *a, **k: universe.copy(),
            ),
            patch.object(
                backtest,
                "build_market_regime_history",
                lambda *a, **k: pd.DataFrame(),
            ),
            patch.object(backtest, "_backtest_one", fake_backtest_one),
        ):
            first_bt = backtest.run_backtest(limit=4, persist=True)
            assert len(first_bt) == 3
            assert sum(backtest_calls.values()) == 4

            second_bt = backtest.run_backtest(limit=4, persist=True)
            assert len(second_bt) == 4
            assert sum(backtest_calls.values()) == 5
            assert backtest_calls["600002"] == 2

            third_bt = backtest.run_backtest(limit=4, persist=True)
            assert len(third_bt) == 4
            assert sum(backtest_calls.values()) == 5

            backtest_checkpoint = (
                settings.report_dir / "backtest_checkpoint.json"
            )
            assert backtest_checkpoint.exists()

    print("OFFLINE_TASK_RESUME_OK")


if __name__ == "__main__":
    main()
