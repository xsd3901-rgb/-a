from __future__ import annotations

import argparse

from backtest import run_backtest
from config import SETTINGS
from eastmoney_formula import write_formula
from evaluator import evaluate_trades, metrics_frame
from optimizer import optimize_parameters
from profile import load_strategy_profile
from scanner import scan_market


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股短期波段量化选股器")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="扫描股票市场")
    scan.add_argument("--limit", type=int, default=None, help="仅扫描前N只；不填则扫描全部")
    scan.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")

    bt = sub.add_parser("backtest", help="执行历史回测")
    bt.add_argument("--limit", type=int, default=None, help="仅回测前N只；不填则回测全部")
    bt.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")

    opt = sub.add_parser("optimize", help="训练/验证分段的受控参数优化")
    opt.add_argument("--limit", type=int, default=100, help="优化样本股票数，默认100")
    opt.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")

    sub.add_parser("profile", help="查看当前活动策略参数")
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

    if args.command == "optimize":
        result, best = optimize_parameters(limit=args.limit, refresh=args.refresh)
        print(result.to_string(index=False))
        if best:
            print("\n已通过样本外基础检查并更新活动参数：")
            for key, value in best.items():
                print(f"{key}: {value}")
        else:
            print("\n没有候选参数通过稳健性检查，活动参数保持不变。")
        return

    if args.command == "profile":
        print("当前活动策略参数：")
        for key, value in load_strategy_profile().items():
            print(f"{key}: {value}")
        return

    if args.command == "formula":
        path = write_formula()
        print(f"东方财富公式参考稿已生成: {path}")


if __name__ == "__main__":
    main()
