from __future__ import annotations

import json
from typing import Callable

import pandas as pd

from aquant.research.data_audit import run_data_audit
from bootstrap import bootstrap_market
from config import SETTINGS, ensure_directories


def repair_failed_market_data(
    *,
    max_symbols: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """只修复数据审计中的 FAIL 股票，不重跑整个全市场。

    先本地审计 -> 提取 FAIL 代码 -> 定向 refresh -> 再次本地审计。
    """
    ensure_directories()

    def announce(message: str) -> None:
        print(message)
        if progress is not None:
            progress(message)

    announce("正在读取本地数据审计结果...")
    before, before_summary = run_data_audit()
    if before.empty:
        summary = {
            "状态": "没有可修复数据",
            "修复前FAIL": 0,
            "目标股票": 0,
            "修复后FAIL": 0,
            "说明": "本地行情库尚未建立，请先运行首次完整准备/建库。",
        }
        _persist(pd.DataFrame(), summary)
        return pd.DataFrame(), summary

    failed = before[before["状态"].astype(str).eq("FAIL")].copy()
    if failed.empty:
        summary = {
            "状态": "无需修复",
            "修复前FAIL": 0,
            "目标股票": 0,
            "修复后FAIL": 0,
            "说明": "本地数据审计没有 FAIL 股票。",
        }
        _persist(pd.DataFrame(), summary)
        return pd.DataFrame(), summary

    if max_symbols and max_symbols > 0:
        failed = failed.head(int(max_symbols)).copy()

    codes = failed["代码"].astype(str).str.zfill(6).tolist()
    announce(f"发现 {len(codes)} 只需要修复，正在定向重新抓取...")

    status, bootstrap_summary = bootstrap_market(
        refresh=True,
        codes=codes,
        progress=progress,
    )

    announce("定向抓取结束，正在重新执行本地数据审计...")
    after, after_summary = run_data_audit()
    remaining = set(
        after.loc[
            after["状态"].astype(str).eq("FAIL"),
            "代码",
        ].astype(str).str.zfill(6)
    )
    repaired = [code for code in codes if code not in remaining]
    still_failed = [code for code in codes if code in remaining]

    result = failed[["代码", "名称", "原因"]].copy()
    result["修复结果"] = result["代码"].astype(str).str.zfill(6).map(
        lambda code: "已通过" if code in repaired else "仍失败"
    )

    summary = {
        "状态": "修复完成" if not still_failed else "部分修复",
        "修复前FAIL": int(before_summary.get("FAIL", len(failed))),
        "目标股票": int(len(codes)),
        "本次已修复": int(len(repaired)),
        "本次仍失败": int(len(still_failed)),
        "修复后全库FAIL": int(after_summary.get("FAIL", 0)),
        "抓取失败股票": int(bootstrap_summary.get("失败股票", 0)),
        "说明": (
            "只对审计 FAIL 股票做强制刷新；仍失败的股票保留在报告中，"
            "不会被正式验收忽略。"
        ),
    }
    _persist(result, summary)
    return result, summary


def _persist(frame: pd.DataFrame, summary: dict) -> None:
    frame.to_csv(
        SETTINGS.report_dir / "data_repair.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (SETTINGS.report_dir / "data_repair_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
