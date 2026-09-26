from __future__ import annotations

import gc
from datetime import datetime

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from aquant.research.market_regime import detect_market_regime
from aquant.runtime.resources import current_profile
from config import SETTINGS, ensure_directories
from profile import load_strategy_profile
from strategy import evaluate_latest


def scan_market(limit: int | None = None, refresh: bool = False) -> pd.DataFrame:
    ensure_directories()
    provider = MarketDataService()
    runtime = current_profile()
    profile = load_strategy_profile()
    score_threshold = int(profile["score_threshold"])

    market_regime = "未知"
    market_score = 0.0
    benchmark = pd.DataFrame()
    try:
        context = MarketContextService()
        market_date = provider.latest_trade_date()
        snapshot = detect_market_regime(context, market_date)
        market_regime = snapshot.regime
        market_score = snapshot.score
        snapshot.details.to_csv(
            SETTINGS.report_dir / "market_environment_latest.csv",
            index=False,
            encoding="utf-8-sig",
        )
        benchmark_start = (market_date - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
        benchmark = context.index_daily(
            "sh000300",
            benchmark_start,
            market_date.strftime("%Y-%m-%d"),
        )
        print(
            f"沪深市场环境: {market_regime} | 环境分 {market_score:.2f} | "
            f"有效指数 {snapshot.index_count}"
        )
    except Exception as exc:
        print(f"市场环境识别暂不可用，扫描继续使用原策略: {exc}")

    stocks = provider.stock_list()
    if limit and limit > 0:
        stocks = stocks.head(limit)

    results: list[dict] = []
    errors: list[dict] = []
    total = len(stocks)
    flush_every = max(1, runtime.batch_size)
    partial_path = SETTINGS.report_dir / "scan_partial.csv"

    print(
        f"资源档位: {runtime.name} | 批次 {runtime.batch_size} | "
        f"下载并发上限 {runtime.download_workers} | 计算并发上限 {runtime.compute_workers}"
    )

    for i, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        try:
            hist = provider.history(code, refresh=refresh)
            if len(hist) < SETTINGS.min_bars:
                raise ValueError(f"K线不足: {len(hist)}")
            signal = evaluate_latest(hist, benchmark_bars=benchmark)
            if signal.score >= score_threshold:
                results.append(
                    {
                        "代码": code,
                        "名称": name,
                        "交易日": pd.to_datetime(hist.iloc[-1]["date"]).strftime("%Y-%m-%d"),
                        "现价": signal.close,
                        "评分": signal.score,
                        "市场环境": market_regime,
                        "环境分": market_score,
                        "相对沪深300_20日%": signal.rs20,
                        "风险": signal.risk,
                        "ATR波动%": signal.atr_pct,
                        "止损参考": signal.stop,
                        "目标参考": signal.target,
                        "信号原因": signal.reasons,
                    }
                )
        except Exception as exc:
            errors.append({"代码": code, "名称": name, "错误": str(exc)[:300]})
        finally:
            if (i + 1) % flush_every == 0:
                if results:
                    pd.DataFrame(results).to_csv(partial_path, index=False, encoding="utf-8-sig")
                print(f"扫描进度 {i + 1}/{total}，当前入选 {len(results)}，失败 {len(errors)}")
                gc.collect()

    if results:
        result_df = pd.DataFrame(results).sort_values(["评分", "ATR波动%"], ascending=[False, True])
        result_df = result_df.head(SETTINGS.top_n).reset_index(drop=True)
        result_df.insert(0, "排名", range(1, len(result_df) + 1))
    else:
        result_df = pd.DataFrame(columns=["排名", "代码", "名称", "交易日", "现价", "评分", "市场环境", "环境分", "相对沪深300_20日%", "风险", "ATR波动%", "止损参考", "目标参考", "信号原因"])

    result_df.to_csv(SETTINGS.report_dir / "scan_latest.csv", index=False, encoding="utf-8-sig")
    if errors:
        pd.DataFrame(errors).to_csv(SETTINGS.report_dir / "scan_errors.csv", index=False, encoding="utf-8-sig")

    print(f"扫描完成：{datetime.now():%Y-%m-%d %H:%M:%S}，入选 {len(result_df)} 只，活动阈值 {score_threshold}")
    return result_df
