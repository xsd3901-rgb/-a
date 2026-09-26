from __future__ import annotations

import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from config import SETTINGS
from profile import load_strategy_profile
from strategy import evaluate_latest, score_history


def stock_detail(
    code: str,
    *,
    refresh: bool = False,
) -> tuple[dict, pd.DataFrame]:
    """返回单只沪深 A 股当前量化摘要与最近特征。"""
    symbol = str(code).strip().zfill(6)[-6:]
    provider = MarketDataService()

    stocks = provider.stock_list()
    matched = stocks[stocks["code"].astype(str).str.zfill(6) == symbol]
    name = (
        str(matched.iloc[0]["name"])
        if not matched.empty
        else symbol
    )

    hist = provider.history(symbol, refresh=refresh)
    if len(hist) < SETTINGS.min_bars:
        raise ValueError(
            f"{symbol} 有效K线只有 {len(hist)} 根，少于 {SETTINGS.min_bars} 根"
        )

    benchmark = pd.DataFrame()
    try:
        market_date = pd.to_datetime(hist.iloc[-1]["date"]).normalize()
        benchmark = MarketContextService().index_daily(
            "sh000300",
            (market_date - pd.Timedelta(days=220)).strftime("%Y-%m-%d"),
            market_date.strftime("%Y-%m-%d"),
        )
    except Exception:
        benchmark = pd.DataFrame()

    scored = score_history(hist, benchmark_bars=benchmark)
    signal = evaluate_latest(
        hist,
        benchmark_bars=benchmark,
        fallback_name=name,
    )
    latest = scored.iloc[-1]

    active_threshold = int(load_strategy_profile()["score_threshold"])

    summary = {
        "代码": symbol,
        "名称": name,
        "交易日": pd.to_datetime(latest["date"]).strftime("%Y-%m-%d"),
        "现价": signal.close,
        "V1评分": signal.score,
        "达到当前阈值": bool(signal.score >= active_threshold),
        "当前阈值": active_threshold,
        "风险闸门": "通过" if signal.allowed else "阻断",
        "风险等级": signal.risk,
        "风险原因": signal.risk_reasons or "",
        "信号原因": signal.reasons,
        "ATR波动%": signal.atr_pct,
        "相对沪深300_20日%": signal.rs20,
        "止损参考": signal.stop,
        "目标参考": signal.target,
    }

    keep = [
        col
        for col in [
            "date",
            "close",
            "ma5",
            "ma10",
            "ma20",
            "ma60",
            "rsi6",
            "atr_pct",
            "vol_ratio5",
            "ret_5d",
            "ret_20d",
            "rs_20d",
            "score",
        ]
        if col in scored.columns
    ]
    recent = scored[keep].tail(60).copy()
    return summary, recent
