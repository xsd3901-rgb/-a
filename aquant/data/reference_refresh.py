from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from aquant.data.service import MarketDataService


def refresh_reference_data(
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[pd.DataFrame, dict, list[dict]]:
    """独立刷新股票基础表、交易日历和历史生命周期资料。

    任一远端元数据源失败时保留本地/随包种子，返回 WARN 而不是删除
    已有资料。正式历史生命周期失败仍会在后续 audit/readiness 中保持
    严格未通过，不会被 bootstrap seed 冒充。
    """
    service = MarketDataService()
    summary: dict[str, object] = {}
    rows: list[dict] = []

    def announce(message: str) -> None:
        if progress is not None:
            progress(message)

    announce("正在更新股票基础库")
    try:
        stocks = service.stock_list(refresh=True)
        summary["股票基础库"] = len(stocks)
        rows.append(
            {
                "项目": "股票基础库",
                "状态": "OK",
                "数量": len(stocks),
                "说明": "远端更新成功，已写入本地基础库",
            }
        )
    except Exception as exc:
        local = service.reference.store.read_security_master()
        summary["股票基础库"] = len(local)
        rows.append(
            {
                "项目": "股票基础库",
                "状态": "WARN",
                "数量": len(local),
                "说明": (
                    "远端更新失败，继续使用本地/内置快照: "
                    f"{exc}"
                ),
            }
        )

    announce("正在更新交易日历")
    try:
        calendar = service.trade_calendar(refresh=True)
        summary["交易日历"] = len(calendar)
        rows.append(
            {
                "项目": "交易日历",
                "状态": "OK",
                "数量": len(calendar),
                "说明": "交易日历已更新",
            }
        )
    except Exception as exc:
        local = service.reference.store.read_trade_calendar()
        summary["交易日历"] = len(local)
        rows.append(
            {
                "项目": "交易日历",
                "状态": "WARN",
                "数量": len(local),
                "说明": (
                    "远端更新失败，继续使用本地/内置日历: "
                    f"{exc}"
                ),
            }
        )

    announce("正在更新历史股票生命周期")
    try:
        lifecycle = service.historical_securities(refresh=True)
        summary["历史生命周期"] = len(lifecycle)
        rows.append(
            {
                "项目": "历史生命周期",
                "状态": "OK",
                "数量": len(lifecycle),
                "说明": "正式历史股票生命周期资料已更新",
            }
        )
    except Exception as exc:
        local = service.universe.store.read()
        summary["历史生命周期"] = len(local)
        rows.append(
            {
                "项目": "历史生命周期",
                "状态": "WARN",
                "数量": len(local),
                "说明": (
                    "暂未更新成功；正式回测验收仍保持严格检查: "
                    f"{exc}"
                ),
            }
        )

    frame = pd.DataFrame(rows)
    summary["总体状态"] = (
        "基础资料已更新"
        if not frame.empty and frame["状态"].eq("OK").all()
        else "基础资料可用但存在远端警告"
    )
    announce(str(summary["总体状态"]))
    return frame, summary, service.router.snapshot()
