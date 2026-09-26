from __future__ import annotations

import multiprocessing as mp
import queue
from typing import Any

import pandas as pd


def _stock_list_worker(source: str, out_queue) -> None:
    try:
        if source == "eastmoney":
            from aquant.data.providers.eastmoney_akshare import EastMoneyAKShareProvider

            frame = EastMoneyAKShareProvider().fetch_stock_list()
        elif source == "baostock":
            from aquant.data.providers.baostock_provider import BaoStockProvider

            frame = BaoStockProvider().fetch_stock_list()
        else:
            raise ValueError(f"未知股票列表数据源: {source}")
        out_queue.put(("ok", frame))
    except Exception as exc:
        out_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def fetch_stock_list_with_timeout(source: str, timeout_seconds: float = 15.0) -> pd.DataFrame:
    """在独立进程中抓取股票列表，避免免费接口卡死主程序。"""
    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_stock_list_worker, args=(source, out_queue), daemon=True)
    proc.start()
    proc.join(timeout_seconds)

    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        raise TimeoutError(f"{source} 股票列表请求超过 {timeout_seconds:.0f} 秒")

    try:
        status, payload = out_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(f"{source} 股票列表子进程未返回结果，退出码 {proc.exitcode}") from exc

    if status != "ok":
        raise RuntimeError(str(payload))
    if payload is None:
        return pd.DataFrame()
    return payload


def _calendar_worker(source: str, start_date: str, end_date: str, out_queue) -> None:
    try:
        if source == "akshare":
            import akshare as ak

            raw = ak.tool_trade_date_hist_sina()
            if raw is None or raw.empty:
                frame = pd.DataFrame()
            else:
                date_col = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
                frame = pd.DataFrame({"trade_date": pd.to_datetime(raw[date_col], errors="coerce")})
                frame = frame.dropna(subset=["trade_date"])
                start = pd.Timestamp(start_date)
                end = pd.Timestamp(end_date)
                frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)].copy()
                frame["is_open"] = True
                frame["provider"] = "sina"
                frame = frame.reset_index(drop=True)
        elif source == "baostock":
            import baostock as bs

            login = bs.login()
            if getattr(login, "error_code", "-1") != "0":
                raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
            try:
                rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
                if rs.error_code != "0":
                    raise RuntimeError(f"BaoStock 交易日历失败: {rs.error_code} {rs.error_msg}")
                rows: list[list[str]] = []
                while rs.next():
                    rows.append(rs.get_row_data())
                raw = pd.DataFrame(rows, columns=rs.fields)
            finally:
                bs.logout()

            if raw.empty:
                frame = pd.DataFrame()
            else:
                frame = raw.rename(
                    columns={"calendar_date": "trade_date", "is_trading_day": "is_open"}
                ).copy()
                frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
                frame["is_open"] = frame["is_open"].astype(str).eq("1")
                frame["provider"] = "baostock"
                frame = frame[["trade_date", "is_open", "provider"]].dropna(
                    subset=["trade_date"]
                ).reset_index(drop=True)
        else:
            raise ValueError(f"未知交易日历数据源: {source}")

        out_queue.put(("ok", frame))
    except Exception as exc:
        out_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def fetch_trade_calendar_with_timeout(
    source: str,
    start_date: str,
    end_date: str,
    timeout_seconds: float = 15.0,
) -> pd.DataFrame:
    """在独立进程中抓取交易日历，避免网络接口无限等待。"""
    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_calendar_worker,
        args=(source, start_date, end_date, out_queue),
        daemon=True,
    )
    proc.start()
    proc.join(timeout_seconds)

    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        raise TimeoutError(f"{source} 交易日历请求超过 {timeout_seconds:.0f} 秒")

    try:
        status, payload = out_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(f"{source} 交易日历子进程未返回结果，退出码 {proc.exitcode}") from exc

    if status != "ok":
        raise RuntimeError(str(payload))
    if payload is None:
        return pd.DataFrame()
    return payload
