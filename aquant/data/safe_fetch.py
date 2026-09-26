from __future__ import annotations

import multiprocessing as mp
import queue
import pandas as pd


def _stock_list_worker(source: str, out_queue) -> None:
    try:
        if source == "eastmoney":
            import akshare as ak
            from aquant.data.providers.base import normalize_symbol

            raw = ak.stock_zh_a_spot_em()
            if raw is None or raw.empty:
                frame = pd.DataFrame()
            else:
                frame = raw.rename(columns={"代码": "symbol", "名称": "name"})[
                    ["symbol", "name"]
                ].copy()
                frame["symbol"] = frame["symbol"].map(normalize_symbol)
                frame = frame.drop_duplicates("symbol").reset_index(drop=True)

        elif source == "exchange":
            import akshare as ak
            from aquant.data.providers.base import normalize_symbol

            parts: list[pd.DataFrame] = []

            sh_main = ak.stock_info_sh_name_code(symbol="主板A股")
            if sh_main is not None and not sh_main.empty:
                temp = sh_main[["证券代码", "证券简称"]].rename(
                    columns={"证券代码": "symbol", "证券简称": "name"}
                )
                parts.append(temp)

            sh_star = ak.stock_info_sh_name_code(symbol="科创板")
            if sh_star is not None and not sh_star.empty:
                temp = sh_star[["证券代码", "证券简称"]].rename(
                    columns={"证券代码": "symbol", "证券简称": "name"}
                )
                parts.append(temp)

            sz = ak.stock_info_sz_name_code(symbol="A股列表")
            if sz is not None and not sz.empty:
                temp = sz[["A股代码", "A股简称"]].rename(
                    columns={"A股代码": "symbol", "A股简称": "name"}
                )
                parts.append(temp)

            bj = ak.stock_info_bj_name_code()
            if bj is not None and not bj.empty:
                temp = bj[["证券代码", "证券简称"]].rename(
                    columns={"证券代码": "symbol", "证券简称": "name"}
                )
                parts.append(temp)

            if not parts:
                frame = pd.DataFrame()
            else:
                frame = pd.concat(parts, ignore_index=True)
                frame["symbol"] = frame["symbol"].map(normalize_symbol)
                frame["name"] = frame["name"].astype(str).str.strip()
                frame = frame.drop_duplicates("symbol").reset_index(drop=True)

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


def _industry_worker(source: str, out_queue) -> None:
    try:
        if source != "baostock":
            raise ValueError(f"未知行业数据源: {source}")
        import baostock as bs

        login = bs.login()
        if getattr(login, "error_code", "-1") != "0":
            raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
        try:
            rs = bs.query_stock_industry()
            if rs.error_code != "0":
                raise RuntimeError(f"BaoStock 行业分类失败: {rs.error_code} {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            raw = pd.DataFrame(rows, columns=rs.fields)
        finally:
            bs.logout()

        if raw.empty:
            frame = pd.DataFrame()
        else:
            rename = {
                "code": "raw_code",
                "code_name": "name",
                "industry": "industry",
                "industryClassification": "classification",
                "updateDate": "update_date",
            }
            frame = raw.rename(columns=rename).copy()
            if "raw_code" not in frame.columns:
                raise RuntimeError(f"BaoStock 行业分类字段异常: {list(raw.columns)}")
            from aquant.data.providers.base import normalize_symbol

            frame["code"] = frame["raw_code"].map(normalize_symbol)
            frame["provider"] = "baostock"
            keep = [
                col
                for col in ["code", "name", "industry", "classification", "update_date", "provider"]
                if col in frame.columns
            ]
            frame = frame[keep].drop_duplicates("code", keep="last").reset_index(drop=True)

        out_queue.put(("ok", frame))
    except Exception as exc:
        out_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def fetch_industry_map_with_timeout(
    source: str = "baostock",
    timeout_seconds: float = 20.0,
) -> pd.DataFrame:
    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_industry_worker, args=(source, out_queue), daemon=True)
    proc.start()
    proc.join(timeout_seconds)

    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        raise TimeoutError(f"{source} 行业分类请求超过 {timeout_seconds:.0f} 秒")

    try:
        status, payload = out_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(f"{source} 行业分类子进程未返回结果，退出码 {proc.exitcode}") from exc

    if status != "ok":
        raise RuntimeError(str(payload))
    return payload if payload is not None else pd.DataFrame()


def _index_daily_worker(
    source: str,
    index_symbol: str,
    start_date: str,
    end_date: str,
    out_queue,
) -> None:
    try:
        if source == "akshare":
            import akshare as ak

            raw = ak.stock_zh_index_daily_em(symbol=index_symbol)
            if raw is None or raw.empty:
                frame = pd.DataFrame()
            else:
                rename = {
                    "date": "trade_date",
                    "日期": "trade_date",
                    "open": "open",
                    "开盘": "open",
                    "high": "high",
                    "最高": "high",
                    "low": "low",
                    "最低": "low",
                    "close": "close",
                    "收盘": "close",
                    "volume": "volume",
                    "成交量": "volume",
                    "amount": "amount",
                    "成交额": "amount",
                }
                frame = raw.rename(columns=rename).copy()
                frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
                start = pd.Timestamp(start_date)
                end = pd.Timestamp(end_date)
                frame = frame[
                    (frame["trade_date"] >= start) & (frame["trade_date"] <= end)
                ].copy()
                for col in ("open", "high", "low", "close", "volume", "amount"):
                    if col in frame.columns:
                        frame[col] = pd.to_numeric(frame[col], errors="coerce")
                frame["provider"] = "eastmoney"
        elif source == "baostock":
            import baostock as bs

            if len(index_symbol) != 8 or index_symbol[:2] not in {"sh", "sz"}:
                raise ValueError(f"BaoStock 指数代码格式不支持: {index_symbol}")
            bs_code = f"{index_symbol[:2]}.{index_symbol[2:]}"
            login = bs.login()
            if getattr(login, "error_code", "-1") != "0":
                raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
            try:
                fields = "date,code,open,high,low,close,volume,amount"
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    fields,
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3",
                )
                if rs.error_code != "0":
                    raise RuntimeError(f"BaoStock 指数日线失败: {rs.error_code} {rs.error_msg}")
                rows: list[list[str]] = []
                while rs.next():
                    rows.append(rs.get_row_data())
                raw = pd.DataFrame(rows, columns=rs.fields)
            finally:
                bs.logout()

            if raw.empty:
                frame = pd.DataFrame()
            else:
                frame = raw.rename(columns={"date": "trade_date"}).copy()
                frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
                for col in ("open", "high", "low", "close", "volume", "amount"):
                    if col in frame.columns:
                        frame[col] = pd.to_numeric(frame[col], errors="coerce")
                frame["provider"] = "baostock"
        else:
            raise ValueError(f"未知指数数据源: {source}")

        if frame is not None and not frame.empty:
            frame = frame.dropna(subset=["trade_date", "open", "high", "low", "close"])
            frame = frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
            frame = frame.reset_index(drop=True)
        out_queue.put(("ok", frame))
    except Exception as exc:
        out_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def fetch_index_daily_with_timeout(
    source: str,
    index_symbol: str,
    start_date: str,
    end_date: str,
    timeout_seconds: float = 15.0,
) -> pd.DataFrame:
    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_index_daily_worker,
        args=(source, index_symbol, start_date, end_date, out_queue),
        daemon=True,
    )
    proc.start()
    proc.join(timeout_seconds)

    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        raise TimeoutError(f"{source} 指数日线请求超过 {timeout_seconds:.0f} 秒: {index_symbol}")

    try:
        status, payload = out_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(
            f"{source} 指数日线子进程未返回结果，退出码 {proc.exitcode}: {index_symbol}"
        ) from exc

    if status != "ok":
        raise RuntimeError(str(payload))
    return payload if payload is not None else pd.DataFrame()
