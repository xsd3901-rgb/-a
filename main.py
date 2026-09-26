from __future__ import annotations

import argparse

from backtest import run_backtest
from eastmoney_formula import write_formula
from evaluator import evaluate_trades, metrics_frame
from scanner import scan_market
from config import SETTINGS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股短期波段量化选股器")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="扫描股票市场")
    scan.add_argument("--limit", type=int, default=None, help="仅扫描前N只；不填则扫描全部")
    scan.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")

    bt = sub.add_parser("backtest", help="执行历史回测")
    bt.add_argument("--limit", type=int, default=None, help="仅回测前N只；不填则回测全部")
    bt.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")

    sub.add_parser("formula", help="生成东方财富参考公式")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "scan":
        result = scan_market(limit=args.limit, refresh=args.refresh)
        if result.empty:
            print("本次没有达到评分阈值的股票。")
        else:
            print(result.to_string(index=False))
        return

    if args.command == "backtest":
        trades = run_backtest(limit=args.limit, refresh=args.refresh)
        metrics = evaluate_trades(trades)
        metrics_frame(metrics).to_csv(
            SETTINGS.report_dir / "backtest_metrics.csv",
            index=False,
            encoding="utf-8-sig",
        )
        print("\n回测统计：")
        for key, value in metrics.items():
            print(f"{key}: {value}")
        return

    if args.command == "formula":
        path = write_formula()
        print(f"东方财富公式参考稿已生成: {path}")


if __name__ == "__main__":
    main()
