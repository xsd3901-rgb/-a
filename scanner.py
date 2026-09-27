from __future__ import annotations

import gc
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from aquant.research.market_regime import detect_market_regime
from aquant.runtime.checkpoint import JsonCheckpoint
from aquant.runtime.resources import current_profile
from aquant.version import __version__
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
            # 新股/历史不足属于正常“未入选”，不应在下次断点续跑时无限重试。
            return None, None

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
                "交易日": pd.to_datetime(
                    hist.iloc[-1]["date"]
                ).strftime("%Y-%m-%d"),
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


def scan_market(
    limit: int | None = None,
    refresh: bool = False,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """扫描沪深市场；同一交易日、同一策略口径支持断点续跑。"""
    ensure_directories()
    provider = MarketDataService()
    runtime = current_profile()
    profile = load_strategy_profile()
    score_threshold = int(profile["score_threshold"])
    market_date = provider.latest_trade_date()

    def announce(message: str) -> None:
        print(message)
        if progress is not None:
            progress(message)

    market_regime = "未知"
    market_score = 0.0
    benchmark = pd.DataFrame()
    try:
        context = MarketContextService()
        snapshot = detect_market_regime(context, market_date)
        market_regime = snapshot.regime
        market_score = snapshot.score
        snapshot.details.to_csv(
            SETTINGS.report_dir / "market_environment_latest.csv",
            index=False,
            encoding="utf-8-sig",
        )
        benchmark_start = (
            market_date - pd.Timedelta(days=180)
        ).strftime("%Y-%m-%d")
        benchmark = context.index_daily(
            "sh000300",
            benchmark_start,
            market_date.strftime("%Y-%m-%d"),
        )
        announce(
            f"沪深市场环境: {market_regime} | 环境分 {market_score:.2f} | "
            f"有效指数 {snapshot.index_count}"
        )
    except Exception as exc:
        announce(f"市场环境识别暂不可用，扫描继续使用原策略: {exc}")

    stocks = provider.stock_list()
    if limit and limit > 0:
        stocks = stocks.head(limit)
    stocks = stocks.reset_index(drop=True)
    stocks["code"] = stocks["code"].astype(str).str.zfill(6)

    signature = (
        f"scan-v2|{__version__}|{market_date:%Y-%m-%d}|"
        f"score={score_threshold}|adjust={SETTINGS.adjust}|"
        f"minbars={SETTINGS.min_bars}|st={int(bool(SETTINGS.exclude_st))}|"
        f"scope={SETTINGS.market_scope}|"
        f"regime={market_regime}:{market_score:.4f}"
    )
    checkpoint = JsonCheckpoint(
        SETTINGS.report_dir / "scan_checkpoint.json",
        signature,
    )
    resume_enabled = bool(SETTINGS.scan_resume) and not refresh
    if refresh or not bool(SETTINGS.scan_resume):
        checkpoint.clear()

    universe_codes = set(stocks["code"].astype(str))
    results: list[dict] = []
    resumed = 0
    pending_rows: list[dict] = []

    for _, row in stocks.iterrows():
        code = str(row["code"])
        payload = checkpoint.state.completed.get(code)
        if resume_enabled and payload is not None:
            resumed += 1
            if (
                payload.get("status") == "selected"
                and isinstance(payload.get("row"), dict)
            ):
                results.append(dict(payload["row"]))
        else:
            pending_rows.append(row.to_dict())

    # 只保留当前股票池的旧失败状态；失败股票本次仍会重新执行。
    previous_failed = {
        code: payload
        for code, payload in checkpoint.state.failed.items()
        if code in universe_codes
    }

    errors: list[dict] = []
    total = len(stocks)
    flush_every = max(1, int(runtime.batch_size))
    partial_path = SETTINGS.report_dir / "scan_partial.csv"
    workers = max(
        1,
        min(
            int(runtime.compute_workers),
            int(runtime.download_workers),
            max(1, len(pending_rows)),
        ),
    )
    started = time.monotonic()

    completed_batch: dict[str, dict] = {}
    failed_batch: dict[str, dict] = {}

    announce(
        f"资源档位: {runtime.name} | 批次 {runtime.batch_size} | "
        f"扫描并发 {workers} | 下载上限 {runtime.download_workers}"
    )
    announce(
        f"扫描股票 {total} | 断点跳过 {resumed} | "
        f"待处理 {len(pending_rows)} | 旧失败待重试 {len(previous_failed)}"
    )

    def flush_checkpoint() -> None:
        nonlocal completed_batch, failed_batch
        if completed_batch or failed_batch:
            checkpoint.update_batch(
                completed=completed_batch,
                failed=failed_batch,
            )
            completed_batch = {}
            failed_batch = {}

        if results:
            pd.DataFrame(results).to_csv(
                partial_path,
                index=False,
                encoding="utf-8-sig",
            )

    def handle(
        code: str,
        name: str,
        result: tuple[dict | None, dict | None],
        processed: int,
    ) -> None:
        selected, error = result
        if error is None:
            if selected is not None:
                results.append(selected)
                completed_batch[code] = {
                    "status": "selected",
                    "row": selected,
                }
            else:
                completed_batch[code] = {
                    "status": "not_selected",
                }
            failed_batch.pop(code, None)
        else:
            errors.append(error)
            prior_attempts = int(
                (previous_failed.get(code) or {}).get("attempts", 0)
            )
            failed_batch[code] = {
                "代码": code,
                "名称": name,
                "error": str(error.get("错误", ""))[:1000],
                "attempts": prior_attempts + 1,
            }
            completed_batch.pop(code, None)

        done = resumed + processed
        if (
            processed % flush_every == 0
            or done == total
        ):
            flush_checkpoint()
            elapsed = max(0.001, time.monotonic() - started)
            per_minute = processed / elapsed * 60.0
            remaining = max(0, len(pending_rows) - processed)
            eta = (
                remaining / per_minute
                if per_minute > 0
                else 0.0
            )
            announce(
                f"扫描进度 {done}/{total} | 当前入选 {len(results)} | "
                f"本次失败 {len(errors)} | 断点跳过 {resumed} | "
                f"{per_minute:.1f}只/分钟 | ETA {eta:.1f}分钟"
            )
            gc.collect()

    try:
        if workers == 1:
            for processed, row in enumerate(
                pending_rows,
                start=1,
            ):
                code = str(row["code"])
                name = str(row["name"])
                handle(
                    code,
                    name,
                    _scan_one(
                        code,
                        name,
                        refresh=refresh,
                        benchmark=benchmark,
                        score_threshold=score_threshold,
                        market_regime=market_regime,
                        market_score=market_score,
                    ),
                    processed,
                )
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="aquant-scan",
            ) as executor:
                future_map = {
                    executor.submit(
                        _scan_one,
                        str(row["code"]),
                        str(row["name"]),
                        refresh=refresh,
                        benchmark=benchmark,
                        score_threshold=score_threshold,
                        market_regime=market_regime,
                        market_score=market_score,
                    ): (
                        str(row["code"]),
                        str(row["name"]),
                    )
                    for row in pending_rows
                }
                for processed, future in enumerate(
                    as_completed(future_map),
                    start=1,
                ):
                    code, name = future_map[future]
                    handle(
                        code,
                        name,
                        future.result(),
                        processed,
                    )
    except BaseException:
        # Ctrl+C / 意外异常前先把已完成的当前批次安全落盘。
        flush_checkpoint()
        announce("扫描被中断，已保存当前断点；下次同口径运行会继续。")
        raise

    flush_checkpoint()

    if results:
        result_df = pd.DataFrame(results)
        result_df = result_df.drop_duplicates(
            subset=["代码"],
            keep="last",
        ).sort_values(
            ["评分", "ATR波动%"],
            ascending=[False, True],
        )
        result_df = (
            result_df.head(SETTINGS.top_n)
            .reset_index(drop=True)
        )
        result_df.insert(
            0,
            "排名",
            range(1, len(result_df) + 1),
        )
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
    announce(
        f"扫描完成：{datetime.now():%Y-%m-%d %H:%M:%S}，"
        f"入选 {len(result_df)} 只，活动阈值 {score_threshold}，"
        f"断点跳过 {resumed}，本次失败 {len(errors)}，"
        f"耗时 {elapsed:.1f} 秒"
    )
    return result_df
