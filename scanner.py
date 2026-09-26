from __future__ import annotations

import gc
from datetime import datetime

import pandas as pd

from config import SETTINGS, ensure_directories
from data import FreeAStockData
from profile import load_strategy_profile
from strategy import evaluate_latest


def scan_market(limit: int | None = None, refresh: bool = False) -> pd.DataFrame:
    ensure_directories()
    provider = FreeAStockData()
    profile = load_strategy_profile()
    score_threshold = int(profile["score_threshold"])
    stocks = provider.stock_list()
    if limit and limit > 0:
        stocks = stocks.head(limit)

    results: list[dict] = []
    errors: list[dict] = []
    total = len(stocks)
    partial_path = SETTINGS.report_dir / "scan_partial.csv"

    for i, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        try:
            hist = provider.history(code, refresh=refresh)
            if len(hist) < SETTINGS.min_bars:
                raise ValueError(f"K线不足: {len(hist)}")
            signal = evaluate_latest(hist)
            if signal.score >= score_threshold:
                results.append(
                    {
                        "代码": code,
                        "名称": name,
                        "交易日": pd.to_datetime(hist.iloc[-1]["date"]).strftime("%Y-%m-%d"),
                        "现价": signal.close,
                        "评分": signal.score,
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
            if (i + 1) % SETTINGS.flush_every == 0:
                if results:
                    pd.DataFrame(results).to_csv(partial_path, index=False, encoding="utf-8-sig")
                print(f"扫描进度 {i + 1}/{total}，当前入选 {len(results)}，失败 {len(errors)}")
                gc.collect()

    if results:
        result_df = pd.DataFrame(results).sort_values(["评分", "ATR波动%"], ascending=[False, True])
        result_df = result_df.head(SETTINGS.top_n).reset_index(drop=True)
        result_df.insert(0, "排名", range(1, len(result_df) + 1))
    else:
        result_df = pd.DataFrame(columns=["排名", "代码", "名称", "交易日", "现价", "评分", "风险", "ATR波动%", "止损参考", "目标参考", "信号原因"])

    result_df.to_csv(SETTINGS.report_dir / "scan_latest.csv", index=False, encoding="utf-8-sig")
    if errors:
        pd.DataFrame(errors).to_csv(SETTINGS.report_dir / "scan_errors.csv", index=False, encoding="utf-8-sig")

    print(f"扫描完成：{datetime.now():%Y-%m-%d %H:%M:%S}，入选 {len(result_df)} 只，活动阈值 {score_threshold}")
    return result_df
