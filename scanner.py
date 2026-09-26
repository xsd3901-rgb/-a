from __future__ import annotations

import gc
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from aquant.research.market_regime import detect_market_regime
from aquant.runtime.resources import current_profile
from config import SETTINGS, ensure_directories
from profile import load_strategy_profile
from strategy import evaluate_latest


def _scan_one(
    code: str,
    name: str,
    *,
    refresh: bool,
    benchmark: pd.DataFrame,
    score_threshold: int,
    market_regime: str,
    market_score: float,
) -> tuple[dict | None, dict | None]:
    try:
        provider = MarketDataService()
        hist = provider.history(code, refresh=refresh)
        if len(hist) < SETTINGS.min_bars:
            raise ValueError(f"K线不足: {len(hist)}")

        signal = evaluate_latest(
            hist,
            benchmark_bars=benchmark,
            fallback_name=name,
        )
        if signal.score < score_threshold or not signal.allowed:
            return None, None

        return (
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
                "风险过滤": signal.risk_reasons or "通过",
                "ATR波动%": signal.atr_pct,
                "止损参考": signal.stop,
                "目标参考": signal.target,
                "信号原因": signal.reasons,
            },
            None,
        )
    except Exception as exc:
        return None, {
            "代码": code,
            "名称": name,
            "错误": str(exc)[:300],
        }


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
    stocks = stocks.reset_index(drop=True)

    results: list[dict] = []
    errors: list[dict] = []
    total = len(stocks)
    flush_every = max(1, runtime.batch_size)
    partial_path = SETTINGS.report_dir / "scan_partial.csv"

    workers = max(
        1,
        min(
            int(runtime.compute_workers),
            int(runtime.download_workers),
            max(1, total),
        ),
    )
    started = time.monotonic()

    print(
        f"资源档位: {runtime.name} | 批次 {runtime.batch_size} | "
        f"扫描并发 {workers} | 下载上限 {runtime.download_workers}"
    )

    def handle(result: tuple[dict | None, dict | None], done: int) -> None:
        selected, error = result
        if selected is not None:
            results.append(selected)
        if error is not None:
            errors.append(error)

        if done == total or done % flush_every == 0:
            if results:
                pd.DataFrame(results).to_csv(
                    partial_path,
                    index=False,
                    encoding="utf-8-sig",
                )
            elapsed = max(0.001, time.monotonic() - started)
            per_minute = done / elapsed * 60.0
            remaining = max(0, total - done)
            eta = remaining / per_minute if per_minute > 0 else 0.0
            print(
                f"扫描进度 {done}/{total} | 当前入选 {len(results)} | "
                f"失败 {len(errors)} | {per_minute:.1f}只/分钟 | "
                f"ETA {eta:.1f}分钟"
            )
            gc.collect()

    if workers == 1:
        for done, (_, row) in enumerate(stocks.iterrows(), start=1):
            handle(
                _scan_one(
                    str(row["code"]),
                    str(row["name"]),
                    refresh=refresh,
                    benchmark=benchmark,
                    score_threshold=score_threshold,
                    market_regime=market_regime,
                    market_score=market_score,
                ),
                done,
            )
    else:
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="aquant-scan",
        ) as executor:
            futures = [
                executor.submit(
                    _scan_one,
                    str(row["code"]),
                    str(row["name"]),
                    refresh=refresh,
                    benchmark=benchmark,
                    score_threshold=score_threshold,
                    market_regime=market_regime,
                    market_score=market_score,
                )
                for _, row in stocks.iterrows()
            ]
            for done, future in enumerate(as_completed(futures), start=1):
                handle(future.result(), done)

    if results:
        result_df = pd.DataFrame(results).sort_values(
            ["评分", "ATR波动%"],
            ascending=[False, True],
        )
        result_df = result_df.head(SETTINGS.top_n).reset_index(drop=True)
        result_df.insert(0, "排名", range(1, len(result_df) + 1))
    else:
        result_df = pd.DataFrame(
            columns=[
                "排名",
                "代码",
                "名称",
                "交易日",
                "现价",
                "评分",
                "市场环境",
                "环境分",
                "相对沪深300_20日%",
                "风险",
                "风险过滤",
                "ATR波动%",
                "止损参考",
                "目标参考",
                "信号原因",
            ]
        )

    result_df.to_csv(
        SETTINGS.report_dir / "scan_latest.csv",
        index=False,
        encoding="utf-8-sig",
    )
    if errors:
        pd.DataFrame(errors).to_csv(
            SETTINGS.report_dir / "scan_errors.csv",
            index=False,
            encoding="utf-8-sig",
        )
    else:
        error_path = SETTINGS.report_dir / "scan_errors.csv"
        if error_path.exists():
            error_path.unlink()

    elapsed = time.monotonic() - started
    print(
        f"扫描完成：{datetime.now():%Y-%m-%d %H:%M:%S}，"
        f"入选 {len(result_df)} 只，活动阈值 {score_threshold}，"
        f"耗时 {elapsed:.1f} 秒"
    )
    return result_df
