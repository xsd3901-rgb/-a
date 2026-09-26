from __future__ import annotations

import argparse

from backtest import run_backtest
from config import SETTINGS
from evaluator import evaluate_by_market_regime, evaluate_trades, metrics_frame
from aquant.research.feature_validation import run_feature_validation
from aquant.research.feature_selection import build_v2_candidate
from aquant.research.walk_forward import run_walk_forward
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

    features = sub.add_parser("features", help="验证特征有效性，不自动修改评分权重")
    features.add_argument("--limit", type=int, default=200, help="验证股票数，默认200；0或不限制时可扩大")
    features.add_argument("--refresh", action="store_true", help="强制刷新研究数据")

    sub.add_parser("select-features", help="从特征验证结果生成 V2 候选特征，不启用")

    wf = sub.add_parser("walk-forward", help="对 V2 候选模型执行滚动样本外验证")
    wf.add_argument("--horizon", type=int, choices=[5, 10, 20], default=10, help="预测窗口，默认10日")

    opt = sub.add_parser("optimize", help="训练/验证分段的受控参数优化")
    opt.add_argument("--limit", type=int, default=100, help="优化样本股票数，默认100")
    opt.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")

    sub.add_parser("profile", help="查看当前活动策略参数")
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
        by_regime = evaluate_by_market_regime(trades)
        if not by_regime.empty:
            by_regime.to_csv(
                SETTINGS.report_dir / "backtest_by_market_regime.csv",
                index=False,
                encoding="utf-8-sig",
            )
        print("\n回测统计：")
        for key, value in metrics.items():
            print(f"{key}: {value}")
        if not by_regime.empty:
            print("\n按沪深市场环境拆分：")
            print(by_regime.to_string(index=False))
        return

    if args.command == "features":
        limit = args.limit if args.limit and args.limit > 0 else None
        result = run_feature_validation(limit=limit, refresh=args.refresh)
        if result.empty:
            print("特征验证没有生成有效结果。")
        else:
            print("\n特征验证结果（仅研究，不自动改评分）：")
            print(result.head(40).to_string(index=False))
            print(
                f"\n完整结果已保存：{SETTINGS.report_dir / 'feature_validation.csv'}"
            )
        return

    if args.command == "select-features":
        selected, candidate = build_v2_candidate()
        if selected.empty:
            print("当前没有特征通过 V2 候选筛选，正式评分保持 V1。")
        else:
            print("\nV2 候选特征（研究状态，不自动启用）：")
            print(selected.to_string(index=False))
            print(
                f"\n候选模型规则数: {len(candidate.get('rules', []))}；"
                "下一步请运行 walk-forward。"
            )
        return

    if args.command == "walk-forward":
        folds, summary = run_walk_forward(horizon=args.horizon)
        if folds.empty:
            print("没有足够历史样本生成 Walk-Forward 窗口。")
        else:
            print("\nWalk-Forward 分折结果：")
            print(folds.to_string(index=False))
        print("\nWalk-Forward 汇总：")
        for key, value in summary.items():
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


if __name__ == "__main__":
    main()
