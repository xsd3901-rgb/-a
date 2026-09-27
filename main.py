from __future__ import annotations

import argparse

import pandas as pd

from backtest import run_backtest
from bootstrap import bootstrap_market
from config import SETTINGS
from evaluator import evaluate_by_market_regime, evaluate_trades, metrics_frame
from eastmoney_formula import write_formula
from aquant.data.overview import data_overview
from aquant.data.reference_refresh import refresh_reference_data
from aquant.models.registry import model_status
from aquant.research.feature_validation import run_feature_validation
from aquant.research.data_audit import run_data_audit
from aquant.research.data_repair import repair_failed_market_data
from aquant.research.feature_selection import build_v2_candidate
from aquant.research.walk_forward import run_walk_forward
from aquant.research.model_compare import run_model_comparison
from aquant.research.local_prepare import run_local_prepare
from aquant.research.readiness import release_readiness
from aquant.research.system_validation import run_system_validation
from aquant.research.stock_detail import stock_detail
from aquant.research.source_quality import run_source_quality
from aquant.research.strategy_ablation import run_v1_ablation
from aquant.research.portfolio import simulate_portfolio
from optimizer import optimize_parameters
from profile import load_strategy_profile
from scanner import scan_market


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股短期波段量化选股器")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare-local", help="首次本地完整准备：建库、审计、研究验收、就绪检查")
    prepare.add_argument("--bootstrap-limit", type=int, default=0, help="建库股票数；0=全沪深市场")
    prepare.add_argument("--validation-limit", type=int, default=200, help="研究验收股票数，默认200")
    prepare.add_argument("--horizon", type=int, choices=[5, 10, 20], default=10, help="V2预测窗口")
    prepare.add_argument("--refresh", action="store_true", help="强制刷新远端数据")

    sub.add_parser("readiness", help="检查代码、依赖、本地数据和研究验收是否就绪")
    sub.add_parser("data-status", help="查看本地数据库存和数据源健康状态")
    sub.add_parser("refresh-reference", help="单独更新股票基础表、交易日历和历史生命周期")

    audit = sub.add_parser("audit-data", help="审计本地行情完整性，不访问网络")
    audit.add_argument("--limit", type=int, default=None, help="仅检查前N个本地股票文件")

    source_quality = sub.add_parser(
        "source-quality",
        help="查看数据源成功率、回退情况和本地多源差异",
    )
    source_quality.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最多交叉检查N只同时具有双源Raw数据的股票",
    )

    repair = sub.add_parser("repair-data", help="只修复数据审计中的 FAIL 股票")
    repair.add_argument("--limit", type=int, default=None, help="最多修复N只失败股票")

    bootstrap = sub.add_parser("bootstrap", help="建立/增量更新沪深A股本地数据库")
    bootstrap.add_argument("--limit", type=int, default=None, help="仅建库前N只；不填则全部")
    bootstrap.add_argument("--refresh", action="store_true", help="强制刷新远端数据")
    bootstrap.add_argument("--days", type=int, default=None, help="向前建库自然日数，默认使用配置")

    stock = sub.add_parser("stock", help="查看单只股票当前量化详情")
    stock.add_argument("code", help="6位股票代码")
    stock.add_argument("--refresh", action="store_true", help="强制刷新行情")

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
    sub.add_parser("ablation", help="研究 V1 逐条评分规则的边际贡献，不自动改权重")

    wf = sub.add_parser("walk-forward", help="对 V2 候选模型执行滚动样本外验证")
    wf.add_argument("--horizon", type=int, choices=[5, 10, 20], default=10, help="预测窗口，默认10日")

    compare = sub.add_parser("compare-models", help="同一滚动样本外区间比较 V1 与 V2 真实成交")
    compare.add_argument("--horizon", type=int, choices=[5, 10, 20], default=10, help="V2预测窗口，默认10日")

    validate = sub.add_parser("validate-system", help="一键运行特征、Walk-Forward、模型对比和正式回测验收")
    validate.add_argument("--limit", type=int, default=200, help="特征与正式回测股票数，默认200")
    validate.add_argument("--horizon", type=int, choices=[5, 10, 20], default=10, help="V2预测窗口，默认10日")
    validate.add_argument("--refresh", action="store_true", help="刷新研究数据")

    opt = sub.add_parser("optimize", help="训练/验证分段的受控参数优化")
    opt.add_argument("--limit", type=int, default=100, help="优化样本股票数，默认100")
    opt.add_argument("--refresh", action="store_true", help="强制刷新本地缓存")

    sub.add_parser("formula", help="导出东方财富 V1 参考选股公式")
    sub.add_parser("models", help="查看 V1 正式模型和 V2 研究候选状态")
    sub.add_parser("profile", help="查看当前活动策略参数")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "prepare-local":
        bootstrap_limit = (
            args.bootstrap_limit if args.bootstrap_limit and args.bootstrap_limit > 0 else None
        )
        validation_limit = (
            args.validation_limit if args.validation_limit and args.validation_limit > 0 else None
        )
        steps, summary = run_local_prepare(
            bootstrap_limit=bootstrap_limit,
            validation_limit=validation_limit,
            horizon=args.horizon,
            refresh=args.refresh,
        )
        print("\n首次本地准备步骤：")
        print(steps.to_string(index=False))
        print("\n汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if summary.get("总体状态") != "首次准备完成":
            raise SystemExit(2)
        return

    if args.command == "readiness":
        report, summary = release_readiness()
        print("\n发布/本地运行就绪检查：")
        print(report.to_string(index=False))
        print("\n汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        return

    if args.command == "data-status":
        overview = data_overview()
        print("\n数据中心概览：")
        for key, value in overview.get("summary", {}).items():
            print(f"{key}: {value}")
        health = overview.get("source_health") or []
        if health:
            print("\n数据源健康：")
            print(pd.DataFrame(health).to_string(index=False))
        return

    if args.command == "refresh-reference":
        frame, summary, health = refresh_reference_data(
            progress=print,
        )
        print("\n基础资料更新汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if not frame.empty:
            print("\n更新明细：")
            print(frame.to_string(index=False))
        if health:
            print("\n数据源健康：")
            print(pd.DataFrame(health).to_string(index=False))
        return

    if args.command == "audit-data":
        details, summary = run_data_audit(limit=args.limit)
        print("\n本地数据审计汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if not details.empty:
            problems = details[details["状态"] != "OK"]
            if not problems.empty:
                print("\n需要关注的数据：")
                print(problems.head(50).to_string(index=False))
        return

    if args.command == "source-quality":
        providers, usage, crosscheck, summary = run_source_quality(
            crosscheck_limit=args.limit,
            include_crosscheck=True,
        )
        print("\n数据源质量追踪汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if not providers.empty:
            print("\n数据源抓取可靠性：")
            print(providers.to_string(index=False))
        if not usage.empty:
            print("\n当前本地数据源使用：")
            print(usage.to_string(index=False))
        if not crosscheck.empty:
            problems = crosscheck[
                crosscheck["状态"].astype(str) != "一致"
            ]
            print("\n多源交叉检查：")
            print(
                (problems if not problems.empty else crosscheck.head(20))
                .head(50)
                .to_string(index=False)
            )
        return

    if args.command == "repair-data":
        frame, summary = repair_failed_market_data(max_symbols=args.limit)
        print("\n数据定向修复汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if not frame.empty:
            print("\n修复明细：")
            print(frame.to_string(index=False))
        return

    if args.command == "bootstrap":
        status, summary = bootstrap_market(
            limit=args.limit,
            refresh=args.refresh,
            calendar_days=args.days,
        )
        print("\n本地数据库建库汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if not status.empty:
            print("\n最后20条建库状态：")
            print(status.tail(20).to_string(index=False))
        return

    if args.command == "stock":
        summary, recent = stock_detail(args.code, refresh=args.refresh)
        print("\n单股量化摘要：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        print("\n最近20个交易日：")
        print(recent.tail(20).to_string(index=False))
        return

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
        _, equity, portfolio_metrics = simulate_portfolio(trades)
        pd.DataFrame([portfolio_metrics]).to_csv(
            SETTINGS.report_dir / "backtest_portfolio_metrics.csv",
            index=False,
            encoding="utf-8-sig",
        )
        if not equity.empty:
            equity.to_csv(
                SETTINGS.report_dir / "backtest_portfolio_equity.csv",
                index=False,
                encoding="utf-8-sig",
            )
        print("\n回测统计：")
        for key, value in metrics.items():
            print(f"{key}: {value}")
        if not by_regime.empty:
            print("\n按沪深市场环境拆分：")
            print(by_regime.to_string(index=False))
        print("\n有限资金组合模拟：")
        for key, value in portfolio_metrics.items():
            print(f"{key}: {value}")
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

    if args.command == "ablation":
        detail, summary_table, summary = run_v1_ablation()
        print("\nV1规则消融汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if not summary_table.empty:
            print("\n规则研究结论：")
            print(summary_table.to_string(index=False))
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

    if args.command == "compare-models":
        comparison, summary = run_model_comparison(horizon=args.horizon)
        print("\nV1 / V2 样本外真实成交对比：")
        print(comparison.to_string(index=False))
        print("\n模型对比结论：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        return

    if args.command == "validate-system":
        comparison, summary = run_system_validation(
            feature_limit=args.limit if args.limit > 0 else None,
            backtest_limit=args.limit if args.limit > 0 else None,
            horizon=args.horizon,
            refresh=args.refresh,
        )
        print("\n系统验收汇总：")
        for key, value in summary.items():
            print(f"{key}: {value}")
        if not comparison.empty:
            print("\nV1/V2 对比：")
            print(comparison.to_string(index=False))
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

    if args.command == "formula":
        path = write_formula()
        print(f"东方财富 V1 参考公式已导出：{path}")
        print("提示：ST/停牌/历史涨跌停等点时风控仍以 A-Quant 本地系统为准。")
        return

    if args.command == "models":
        print("模型状态：")
        for key, value in model_status().items():
            print(f"{key}: {value}")
        return

    if args.command == "profile":
        print("当前活动策略参数：")
        for key, value in load_strategy_profile().items():
            print(f"{key}: {value}")
        return


if __name__ == "__main__":
    main()
