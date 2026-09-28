from __future__ import annotations

import csv
import json
import math
import os
import socket
import threading
import time
import urllib.request
import webbrowser
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

# 本地网页必须绕过系统/环境代理；外部行情请求仍保持使用用户现有代理。
_LOCAL_BYPASS = "localhost,127.0.0.1,::1"
for _key in ("NO_PROXY", "no_proxy"):
    _old = os.environ.get(_key, "").strip()
    if _old:
        _parts = [x.strip() for x in _old.split(",") if x.strip()]
        for _item in _LOCAL_BYPASS.split(","):
            if _item not in _parts:
                _parts.append(_item)
        os.environ[_key] = ",".join(_parts)
    else:
        os.environ[_key] = _LOCAL_BYPASS

try:
    import akshare as ak
except Exception as exc:  # 网页仍可启动，扫描时给出清晰错误
    ak = None
    AK_IMPORT_ERROR = str(exc)
else:
    AK_IMPORT_ERROR = ""

APP_TITLE = "MACD + 日线结构选股器"
ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
DAILY_CACHE = CACHE_DIR / "daily"
STOCK_LIST_CACHE = CACHE_DIR / "stock_list.csv"
CACHE_DIR.mkdir(exist_ok=True)
DAILY_CACHE.mkdir(exist_ok=True)

# ===== 选股参数：只使用“日线结构 + MACD(12,26,9)” =====
# 模板：A阶段低点 -> B第一段反弹高点 -> C回调不破A -> D再次突破B
# MACD同步：低位修复 -> 首次突破零轴 -> 回踩零轴附近 -> 二次向上突破
LOOKBACK_TRADING_DAYS = 240
PATTERN_WINDOW = 180
SWING_SPAN = 3
MIN_PREVIOUS_DECLINE = 0.12       # A点之前至少有一段约12%的下跌
MIN_REBOUND_FROM_A = 0.08         # A->B至少反弹约8%
MIN_PULLBACK_FROM_B = 0.04        # B->C至少回调约4%
C_LOW_TOLERANCE = 0.97            # C允许比A低最多约3%，避免机械误差
C_MAX_ABOVE_A = 1.35              # C不能离A过远，仍应属于同一底部结构
ZERO_BAND_RATIO = 0.0045          # DIF/股价在±0.45%内视为“零轴附近”
C_RECENT_DAYS = 22                # C回踩阶段只保留近期形态
D_RECENT_DAYS = 18                # D二次突破只保留近期形态
MAX_WORKERS = 5                   # 控制普通Windows电脑网络/内存压力
DAILY_CACHE_HOURS = 16
STOCK_LIST_CACHE_HOURS = 24 * 7
SCAN_LIMIT = 0                     # 0=全市场；调试可临时改为100
REQUEST_TIMEOUT = (5, 12)          # 连接/读取超时，避免代理下无限等待

app = FastAPI(title=APP_TITLE)
STATE_LOCK = threading.Lock()
SCAN_STATE: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "done": 0,
    "matched": 0,
    "failed": 0,
    "message": "尚未扫描",
    "stock_list_source": "",
    "results": [],
    "failures": [],
}


def safe_float(v: Any, digits: int = 4) -> float | None:
    try:
        x = float(v)
        if math.isfinite(x):
            return round(x, digits)
    except Exception:
        pass
    return None


def market_symbol(code: str) -> str:
    code = str(code).zfill(6)
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def sanitize_proxy_url(url: str | None) -> str:
    if not url:
        return ""
    # 不在网页中显示代理账号/密码。
    try:
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(url)
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        return urlunsplit((p.scheme, f"{host}{port}", "", "", ""))
    except Exception:
        return "已检测到"


def proxy_summary() -> dict[str, str]:
    proxies = urllib.request.getproxies()
    return {
        "http": sanitize_proxy_url(proxies.get("http")),
        "https": sanitize_proxy_url(proxies.get("https")),
        "no_proxy": os.environ.get("NO_PROXY", ""),
    }


def normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    aliases = {
        "date": ["date", "日期", "交易日期"],
        "open": ["open", "开盘"],
        "high": ["high", "最高"],
        "low": ["low", "最低"],
        "close": ["close", "收盘"],
        "volume": ["volume", "成交量"],
        "amount": ["amount", "成交额"],
    }
    rename: dict[str, str] = {}
    for std, candidates in aliases.items():
        for c in candidates:
            if c in df.columns:
                rename[c] = std
                break
    out = df.rename(columns=rename).copy()
    required = ["date", "open", "high", "low", "close"]
    if not all(c in out.columns for c in required):
        return pd.DataFrame()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=required).sort_values("date").drop_duplicates("date", keep="last")
    return out.reset_index(drop=True)


def _pick_code_name(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name"])
    code_cols = [c for c in df.columns if "代码" in str(c) or str(c).lower() in {"code", "symbol"}]
    name_cols = [c for c in df.columns if "简称" in str(c) or "名称" in str(c) or str(c).lower() == "name"]
    if not code_cols or not name_cols:
        return pd.DataFrame(columns=["code", "name"])
    out = df[[code_cols[0], name_cols[0]]].copy()
    out.columns = ["code", "name"]
    out["code"] = out["code"].astype(str).str.extract(r"(\d{6})", expand=False)
    out["name"] = out["name"].astype(str).str.strip()
    out = out.dropna(subset=["code"]).drop_duplicates("code")
    # 只保留沪深A股，不包含北交所/B股。
    out = out[out["code"].str.match(r"^(00|001|002|003|30|60|601|603|605|688)")]
    return out.reset_index(drop=True)


def _cache_fresh(path: Path, hours: int) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime <= hours * 3600)


def _read_stock_list_cache() -> pd.DataFrame:
    try:
        df = pd.read_csv(STOCK_LIST_CACHE, dtype={"code": str})
        if {"code", "name"}.issubset(df.columns) and len(df) > 1000:
            df["code"] = df["code"].astype(str).str.zfill(6)
            return _pick_code_name(df.rename(columns={"code": "代码", "name": "名称"}))
    except Exception:
        pass
    return pd.DataFrame(columns=["code", "name"])


def _save_stock_list_cache(df: pd.DataFrame) -> None:
    try:
        df[["code", "name"]].to_csv(STOCK_LIST_CACHE, index=False, encoding="utf-8-sig")
    except Exception:
        pass


def get_stock_list() -> tuple[pd.DataFrame, str]:
    # 新鲜缓存优先，减少每天反复请求股票列表。
    if _cache_fresh(STOCK_LIST_CACHE, STOCK_LIST_CACHE_HOURS):
        cached = _read_stock_list_cache()
        if not cached.empty:
            return cached, "本地股票列表缓存"

    if ak is None:
        cached = _read_stock_list_cache()
        if not cached.empty:
            return cached, "本地股票列表缓存(离线)"
        raise RuntimeError(f"AKShare 未安装或导入失败: {AK_IMPORT_ERROR}")

    parts: list[pd.DataFrame] = []
    used: list[str] = []
    # 优先交易所列表，不把东方财富作为主股票列表源。
    exchange_calls = [
        ("上交所主板", "stock_info_sh_name_code", {"symbol": "主板A股"}),
        ("上交所科创板", "stock_info_sh_name_code", {"symbol": "科创板"}),
        ("深交所A股", "stock_info_sz_name_code", {"symbol": "A股列表"}),
    ]
    for label, fn_name, kwargs in exchange_calls:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            got = _pick_code_name(fn(**kwargs))
            if not got.empty:
                parts.append(got)
                used.append(label)
        except Exception:
            continue

    if parts:
        stocks = pd.concat(parts, ignore_index=True).drop_duplicates("code").reset_index(drop=True)
        _save_stock_list_cache(stocks)
        return stocks, "+".join(used)

    # 备用：新浪A股快照（若当前AKShare版本提供）。
    fn = getattr(ak, "stock_zh_a_spot", None)
    if fn is not None:
        try:
            got = _pick_code_name(fn())
            if not got.empty:
                _save_stock_list_cache(got)
                return got, "新浪股票列表(备用)"
        except Exception:
            pass

    # 最后兜底：AKShare A股代码表，具体底层源随AKShare版本变化。
    fn = getattr(ak, "stock_info_a_code_name", None)
    if fn is not None:
        try:
            got = _pick_code_name(fn())
            if not got.empty:
                _save_stock_list_cache(got)
                return got, "AKShare A股代码表(备用)"
        except Exception:
            pass

    cached = _read_stock_list_cache()
    if not cached.empty:
        return cached, "本地股票列表缓存(网络失败)"
    raise RuntimeError("无法获取沪深A股股票列表；请查看代理/网络，或先保留一次成功生成的 cache/stock_list.csv")


def cache_path(code: str) -> Path:
    return DAILY_CACHE / f"{code}.csv"


def _save_daily_cache(path: Path, df: pd.DataFrame) -> None:
    try:
        df.tail(LOOKBACK_TRADING_DAYS + 60).to_csv(path, index=False, encoding="utf-8-sig")
    except Exception:
        pass


def fetch_tencent_daily(code: str) -> pd.DataFrame:
    """腾讯前复权日线，直接HTTP请求，显式超时，自动沿用用户代理。"""
    symbol = market_symbol(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,,,320,qfq"}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    if int(payload.get("code", -1)) != 0:
        raise RuntimeError(f"腾讯返回 code={payload.get('code')}")
    block = (payload.get("data") or {}).get(symbol) or {}
    rows = block.get("qfqday") or block.get("day") or []
    if not rows:
        raise RuntimeError("腾讯日线为空")
    parsed = []
    for x in rows:
        if not isinstance(x, list) or len(x) < 6:
            continue
        # 腾讯结构：[日期, 开盘, 收盘, 最高, 最低, 成交量, ...]
        parsed.append({
            "date": x[0], "open": x[1], "close": x[2],
            "high": x[3], "low": x[4], "volume": x[5],
        })
    return normalize_daily(pd.DataFrame(parsed))


def fetch_daily(code: str, force: bool = False) -> tuple[pd.DataFrame, str]:
    path = cache_path(code)
    if not force and _cache_fresh(path, DAILY_CACHE_HOURS):
        try:
            cached = normalize_daily(pd.read_csv(path))
            if len(cached) >= 80:
                return cached, "本地缓存"
        except Exception:
            pass

    errors: list[str] = []

    # 主源：腾讯直接HTTP。可控超时，对代理环境更稳。
    try:
        df = fetch_tencent_daily(code)
        if len(df) >= 80:
            _save_daily_cache(path, df)
            return df, "腾讯"
    except Exception as exc:
        errors.append(f"腾讯:{exc}")

    if ak is not None:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=520)).strftime("%Y%m%d")
        symbol = market_symbol(code)

        # 备用1：新浪
        fn = getattr(ak, "stock_zh_a_daily", None)
        if fn is not None:
            try:
                raw = fn(symbol=symbol, start_date=start, end_date=end, adjust="qfq")
                df = normalize_daily(raw)
                if len(df) >= 80:
                    _save_daily_cache(path, df)
                    return df, "新浪(备用)"
            except Exception as exc:
                errors.append(f"新浪:{exc}")

        # 备用2：AKShare腾讯接口（不同版本可能不存在）
        fn = getattr(ak, "stock_zh_a_hist_tx", None)
        if fn is not None:
            try:
                raw = fn(symbol=symbol, start_date=start, end_date=end, adjust="qfq")
                df = normalize_daily(raw)
                if len(df) >= 80:
                    _save_daily_cache(path, df)
                    return df, "腾讯AKShare(备用)"
            except Exception as exc:
                errors.append(f"腾讯AK:{exc}")

        # 最后备用：东方财富。不是主源。
        fn = getattr(ak, "stock_zh_a_hist", None)
        if fn is not None:
            try:
                raw = fn(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
                df = normalize_daily(raw)
                if len(df) >= 80:
                    _save_daily_cache(path, df)
                    return df, "东方财富(最后备用)"
            except Exception as exc:
                errors.append(f"东财:{exc}")

    # 即使缓存过期，所有网络源失败时仍允许使用旧缓存，保证代理波动时可看历史结果。
    if path.exists():
        try:
            cached = normalize_daily(pd.read_csv(path))
            if len(cached) >= 80:
                return cached, "旧缓存(网络失败)"
        except Exception:
            pass

    raise RuntimeError(" | ".join(errors[-4:]) or "无可用日线数据源")


def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["dif"] = ema12 - ema26
    out["dea"] = out["dif"].ewm(span=9, adjust=False).mean()
    out["macd"] = 2.0 * (out["dif"] - out["dea"])
    return out



def local_lows(series: pd.Series, span: int = SWING_SPAN) -> list[int]:
    vals = series.to_numpy(dtype=float)
    idxs: list[int] = []
    for i in range(span, len(vals) - span):
        v = vals[i]
        if v <= np.min(vals[i - span:i]) and v <= np.min(vals[i + 1:i + span + 1]):
            idxs.append(i)
    return idxs


def _first_zero_cross_up(dif: pd.Series, start: int, end: int) -> int | None:
    start = max(1, start)
    end = min(len(dif) - 1, end)
    for i in range(start, end + 1):
        if dif.iloc[i - 1] <= 0 < dif.iloc[i]:
            return i
    return None


def _first_price_break(high: pd.Series, close: pd.Series, level: float, start: int) -> int | None:
    for i in range(max(0, start), len(close)):
        if close.iloc[i] >= level or high.iloc[i] > level * 1.003:
            return i
    return None


def _point_payload(x: pd.DataFrame, idx: int, kind: str, price_col: str) -> dict[str, Any]:
    r = x.iloc[idx]
    return {
        "kind": kind,
        "index": int(idx),
        "date": pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
        "price": safe_float(r[price_col], 2),
    }


def detect_abcd_macd(df: pd.DataFrame) -> dict[str, Any] | None:
    """识别用户给定的日线A-B-C-D + MACD零轴二次启动模板。

    只使用OHLC价格和MACD，不使用均线、成交量、RSI、KDJ、资金等指标。
    返回的候选只保留两个阶段：
      - C零轴回踩：A/B/C结构已经成立，MACD回踩零轴附近并重新拐头，等待价格突破B。
      - D二次突破：在C之后MACD再次向上，价格也突破B。
    """
    if len(df) < 90:
        return None

    x = df.tail(PATTERN_WINDOW).reset_index(drop=True).copy()
    n = len(x)
    lows = local_lows(x["low"], span=SWING_SPAN)
    if len(lows) < 2:
        return None

    dif = x["dif"].astype(float)
    dea = x["dea"].astype(float)
    macd = x["macd"].astype(float)
    close = x["close"].astype(float)
    high = x["high"].astype(float)
    low = x["low"].astype(float)

    best: dict[str, Any] | None = None
    # 只检查较新的若干个C候选，保证全市场扫描速度。
    for c in reversed(lows[-14:]):
        if c < 35 or c >= n - 1:
            continue

        # C之前约15~110个交易日内寻找最低的阶段低点A。
        a_start = max(5, c - 110)
        a_end = c - 14
        if a_end <= a_start:
            continue
        a = int(low.iloc[a_start:a_end + 1].idxmin())
        a_price = float(low.iloc[a])
        c_price = float(low.iloc[c])

        # C必须属于同一个底部结构：不明显跌破A，也不能已经离A太远。
        if c_price < a_price * C_LOW_TOLERANCE or c_price > a_price * C_MAX_ABOVE_A:
            continue

        # A之前必须真正有过一段下跌。
        pre_start = max(0, a - 55)
        if a - pre_start < 12:
            continue
        pre_peak = float(high.iloc[pre_start:a].max())
        decline = 1.0 - a_price / pre_peak if pre_peak > 0 else 0.0
        if decline < MIN_PREVIOUS_DECLINE:
            continue

        # A之后、C之前必须出现MACD第一次从零轴下方突破到零轴上方。
        z1 = _first_zero_cross_up(dif, a + 2, c - 5)
        if z1 is None:
            continue

        # B定义为第一次过零轴后到C之前的反弹最高点。
        b_start = z1
        b_end = c - 3
        if b_end <= b_start:
            continue
        b = int(high.iloc[b_start:b_end + 1].idxmax())
        b_price = float(high.iloc[b])
        if not (a < z1 <= b < c):
            continue
        if b_price < a_price * (1.0 + MIN_REBOUND_FROM_A):
            continue
        if c_price > b_price * (1.0 - MIN_PULLBACK_FROM_B):
            continue

        # C附近MACD必须回到零轴附近，或轻微下穿零轴；这正是模板的“回踩”。
        c0, c1 = max(z1 + 1, c - 3), min(n - 1, c + 3)
        norm = (dif.iloc[c0:c1 + 1].abs() / close.iloc[c0:c1 + 1].clip(lower=0.01))
        near_zero = bool((norm <= ZERO_BAND_RATIO).any() or (dif.iloc[c0:c1 + 1] <= 0).any())
        if not near_zero:
            continue

        # 第一次上冲应有明显的正DIF，否则只是围绕零轴噪声。
        first_peak_ratio = float((dif.iloc[z1:c + 1] / close.iloc[z1:c + 1].clip(lower=0.01)).max())
        if first_peak_ratio < 0.0015:
            continue

        # C之后MACD重新抬头：优先识别“第二次过零轴”；若只轻触零轴，也允许零轴附近重新金叉向上。
        z2 = _first_zero_cross_up(dif, c + 1, n - 1)
        recent_up = bool(dif.iloc[-1] > dif.iloc[-2] and dif.iloc[-1] > dea.iloc[-1])
        c_band = ZERO_BAND_RATIO * max(float(close.iloc[c]), 0.01)
        soft_relaunch = bool(
            dif.iloc[c] <= c_band
            and dif.iloc[-1] > 0
            and recent_up
        )
        relaunch = z2 is not None or soft_relaunch

        # D是C之后第一次有效突破B的价格位置。
        d = _first_price_break(high, close, b_price, c + 1)
        c_age = n - 1 - c
        d_age = (n - 1 - d) if d is not None else 10**9

        signal = ""
        if d is not None and d_age <= D_RECENT_DAYS and relaunch:
            signal = "D二次突破"
        elif c_age <= C_RECENT_DAYS and d is None and (relaunch or recent_up):
            # C阶段必须还没真正突破B，才叫“回踩待突破”。
            signal = "C零轴回踩"
        else:
            continue

        macd_state = "二次过零轴" if z2 is not None else "零轴附近重新向上"
        if signal == "C零轴回踩":
            macd_state = "回踩零轴附近，重新拐头"

        points = [
            _point_payload(x, a, "A", "low"),
            _point_payload(x, b, "B", "high"),
            _point_payload(x, c, "C", "low"),
        ]
        if d is not None:
            points.append(_point_payload(x, d, "D", "close"))

        macd_marks = [
            {"kind": "Z1", "date": pd.Timestamp(x.iloc[z1]["date"]).strftime("%Y-%m-%d"), "label": "首次过零轴"},
            {"kind": "C", "date": pd.Timestamp(x.iloc[c]["date"]).strftime("%Y-%m-%d"), "label": "零轴回踩"},
        ]
        if z2 is not None:
            macd_marks.append({"kind": "Z2", "date": pd.Timestamp(x.iloc[z2]["date"]).strftime("%Y-%m-%d"), "label": "二次过零轴"})
        elif relaunch:
            macd_marks.append({"kind": "Z2", "date": pd.Timestamp(x.iloc[-1]["date"]).strftime("%Y-%m-%d"), "label": "重新向上"})

        candidate = {
            "signal": signal,
            "macd_state": macd_state,
            "decline": round(decline * 100, 1),
            "points": points,
            "macd_marks": macd_marks,
            "a_idx": a,
            "b_idx": b,
            "c_idx": c,
            "d_idx": d,
            "z1_idx": z1,
            "z2_idx": z2,
            "pattern_rows": x,
        }

        # 优先最新的C/D结构；同样新时优先D确认。
        rank = (0 if signal == "D二次突破" else 1, c_age, d_age)
        if best is None or rank < best["_rank"]:
            candidate["_rank"] = rank
            best = candidate

    if best is None:
        return None
    best.pop("_rank", None)
    return best


STAGE_ORDER = {"C零轴回踩": 0, "D二次突破": 1}


def analyze_one(code: str, name: str, force: bool = False) -> dict[str, Any] | None:
    df, source = fetch_daily(code, force=force)
    df = add_macd(df).tail(LOOKBACK_TRADING_DAYS).reset_index(drop=True)
    pattern = detect_abcd_macd(df)
    if pattern is None:
        return None

    x = pattern["pattern_rows"]
    last = x.iloc[-1]
    pts = {p["kind"]: p for p in pattern["points"]}
    return {
        "code": code,
        "name": name,
        "date": pd.Timestamp(last["date"]).strftime("%Y-%m-%d"),
        "close": safe_float(last["close"], 2),
        "dif": safe_float(last["dif"], 4),
        "dea": safe_float(last["dea"], 4),
        "macd": safe_float(last["macd"], 4),
        "signal": pattern["signal"],
        "macd_state": pattern["macd_state"],
        "a_date": pts.get("A", {}).get("date", ""),
        "a_price": pts.get("A", {}).get("price"),
        "b_date": pts.get("B", {}).get("date", ""),
        "b_price": pts.get("B", {}).get("price"),
        "c_date": pts.get("C", {}).get("date", ""),
        "c_price": pts.get("C", {}).get("price"),
        "d_date": pts.get("D", {}).get("date", ""),
        "d_price": pts.get("D", {}).get("price"),
        "source": source,
    }


def run_scan(force: bool = False, limit: int = 0) -> None:
    with STATE_LOCK:
        if SCAN_STATE["running"]:
            return
        SCAN_STATE.update({
            "running": True,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "total": 0,
            "done": 0,
            "matched": 0,
            "failed": 0,
            "message": "正在获取股票列表...",
            "stock_list_source": "",
            "results": [],
            "failures": [],
        })

    try:
        stocks, list_source = get_stock_list()
        actual_limit = limit or SCAN_LIMIT
        if actual_limit > 0:
            stocks = stocks.head(actual_limit)
        rows = list(stocks[["code", "name"]].itertuples(index=False, name=None))
        with STATE_LOCK:
            SCAN_STATE["total"] = len(rows)
            SCAN_STATE["stock_list_source"] = list_source
            SCAN_STATE["message"] = "正在扫描沪深A股..."

        results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(analyze_one, code, name, force): (code, name) for code, name in rows}
            for fut in as_completed(futs):
                code, name = futs[fut]
                try:
                    item = fut.result()
                    if item:
                        results.append(item)
                except Exception as exc:
                    failures.append({"code": code, "name": name, "error": str(exc)[:260]})
                with STATE_LOCK:
                    SCAN_STATE["done"] += 1
                    SCAN_STATE["matched"] = len(results)
                    SCAN_STATE["failed"] = len(failures)

        results.sort(key=lambda r: (STAGE_ORDER.get(r["signal"], 99), -pd.Timestamp(r["c_date"]).toordinal(), r["code"]))
        with STATE_LOCK:
            SCAN_STATE["results"] = results
            SCAN_STATE["failures"] = failures[:300]
            SCAN_STATE["message"] = f"扫描完成，发现 {len(results)} 只候选"
    except Exception as exc:
        with STATE_LOCK:
            SCAN_STATE["message"] = f"扫描失败: {exc}"
    finally:
        with STATE_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return INDEX_HTML


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    ps = proxy_summary()
    return {
        "ok": True,
        "app": APP_TITLE,
        "akshare": ak is not None,
        "proxy_detected": bool(ps["http"] or ps["https"]),
        "proxy": ps,
        "local_proxy_bypass": os.environ.get("NO_PROXY", ""),
    }


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    with STATE_LOCK:
        data = {k: v for k, v in SCAN_STATE.items() if k not in {"results", "failures"}}
    data["proxy_detected"] = bool(proxy_summary()["http"] or proxy_summary()["https"])
    return data


@app.get("/api/results")
def api_results(signal: str = Query("全部")) -> dict[str, Any]:
    with STATE_LOCK:
        rows = list(SCAN_STATE["results"])
    if signal != "全部":
        rows = [r for r in rows if r["signal"] == signal]
    return {"count": len(rows), "rows": rows}


@app.get("/api/failures")
def api_failures() -> dict[str, Any]:
    with STATE_LOCK:
        return {"rows": list(SCAN_STATE["failures"])}


@app.post("/api/scan/start")
def api_scan_start(force: bool = False, limit: int = 0) -> dict[str, Any]:
    with STATE_LOCK:
        if SCAN_STATE["running"]:
            return {"ok": False, "message": "扫描正在进行中"}
    threading.Thread(target=run_scan, args=(force, limit), daemon=True).start()
    return {"ok": True, "message": "已开始扫描"}


@app.get("/api/chart/{code}")
def api_chart(code: str, days: int = 180) -> dict[str, Any]:
    raw = "".join(ch for ch in code if ch.isdigit())[:6]
    if len(raw) != 6:
        raise HTTPException(400, "股票代码无效")
    try:
        full, source = fetch_daily(raw, force=False)
        full = add_macd(full).tail(LOOKBACK_TRADING_DAYS).reset_index(drop=True)
        pattern = detect_abcd_macd(full)
        df = full.tail(max(90, min(days, 250))).reset_index(drop=True)
    except Exception as exc:
        raise HTTPException(502, str(exc))

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "date": pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
            "open": safe_float(r["open"], 2),
            "high": safe_float(r["high"], 2),
            "low": safe_float(r["low"], 2),
            "close": safe_float(r["close"], 2),
            "dif": safe_float(r["dif"], 4),
            "dea": safe_float(r["dea"], 4),
            "macd": safe_float(r["macd"], 4),
        })

    points: list[dict[str, Any]] = []
    macd_marks: list[dict[str, Any]] = []
    signal = ""
    macd_state = ""
    if pattern:
        visible_dates = {r["date"] for r in rows}
        points = [p for p in pattern["points"] if p["date"] in visible_dates]
        macd_marks = [m for m in pattern["macd_marks"] if m["date"] in visible_dates]
        signal = pattern["signal"]
        macd_state = pattern["macd_state"]

    return {
        "code": raw,
        "source": source,
        "signal": signal,
        "macd_state": macd_state,
        "points": points,
        "macd_marks": macd_marks,
        "rows": rows,
    }


@app.get("/startup-check", response_class=PlainTextResponse)
def startup_check() -> str:
    return "OK - MACD Screener local web server is running."


INDEX_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MACD + 日线结构选股器</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0b0d10;color:#d9dee5;font-family:Arial,"Microsoft YaHei",sans-serif}.top{height:60px;display:flex;align-items:center;gap:12px;padding:0 16px;background:#12161b;border-bottom:1px solid #282e36}.brand{font-size:20px;font-weight:700;color:#ff8a2a}.sub{font-size:12px;color:#8f99a6}.spacer{flex:1}button{background:#20262d;color:#dce2e9;border:1px solid #38414c;border-radius:5px;padding:8px 11px;cursor:pointer}button:hover{border-color:#ff8a2a}.primary{background:#c65f18;border-color:#e17829;color:#fff}.wrap{height:calc(100vh - 60px);display:grid;grid-template-columns:43% 57%}.left{min-width:0;display:flex;flex-direction:column;border-right:1px solid #282e36}.filters{padding:10px;display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid #282e36}.filters .active{background:#914612;border-color:#ff8a2a}.status{padding:8px 12px;font-size:12px;color:#a4adb8;border-bottom:1px solid #282e36}.progress{height:4px;background:#20252b}.bar{height:100%;background:#f17822;width:0;transition:width .2s}.tablewrap{overflow:auto;flex:1}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px 6px;border-bottom:1px solid #20262c;white-space:nowrap;text-align:right}th{position:sticky;top:0;background:#151a20;color:#9da7b2;z-index:2}th:nth-child(1),th:nth-child(2),th:nth-child(4),th:nth-child(9),td:nth-child(1),td:nth-child(2),td:nth-child(4),td:nth-child(9){text-align:left}tbody tr:hover{background:#171c22;cursor:pointer}.sig{font-weight:700}.C零轴回踩{color:#ffd166}.D二次突破{color:#ff7777}.right{min-width:0;display:flex;flex-direction:column}.charthead{height:50px;display:flex;align-items:center;gap:9px;padding:0 12px;border-bottom:1px solid #282e36}.stocktitle{font-size:16px;font-weight:700}.muted{font-size:12px;color:#8994a0}.charts{position:relative;flex:1;min-height:0;padding:6px}.canvasbox{height:100%;width:100%;display:grid;grid-template-rows:68% 32%;gap:2px}.panel{position:relative;min-height:0}.panel canvas{position:absolute;inset:0;width:100%;height:100%}.legend{position:absolute;z-index:2;left:10px;top:7px;font-size:12px;color:#aeb6c0;background:rgba(11,13,16,.75);padding:3px 6px;border-radius:4px}.hint{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#6f7a86}.diag{font-size:11px;color:#7e8996}.rule{font-size:11px;color:#9ba5b0;padding:7px 12px;border-bottom:1px solid #282e36;line-height:1.5}.tag{display:inline-block;padding:2px 6px;border:1px solid #3a424b;border-radius:10px;margin-right:4px;color:#cbd2da}@media(max-width:1000px){.wrap{grid-template-columns:1fr;height:auto}.left{height:52vh;border-right:0}.right{height:65vh}}
</style>
</head>
<body>
<div class="top"><div><div class="brand">MACD + 日线结构选股器</div><div class="sub">只找：A低点 → B反弹 → C回调不破A → D突破；MACD首次过零轴 → 回踩 → 二次向上</div></div><div class="spacer"></div><span id="proxy" class="diag"></span><button onclick="startScan(false)">缓存扫描</button><button class="primary" onclick="startScan(true)">刷新全市场</button></div>
<div class="wrap">
<section class="left"><div class="filters" id="filters"></div><div class="rule"><span class="tag">C 零轴回踩</span>回调低点不破A，MACD回到零轴附近并重新拐头　 <span class="tag">D 二次突破</span>MACD再次向上，同时价格突破B</div><div class="status" id="status">正在连接本地服务...</div><div class="progress"><div id="bar" class="bar"></div></div><div class="tablewrap"><table><thead><tr><th>代码</th><th>名称</th><th>收盘</th><th>阶段</th><th>A低点</th><th>B高点</th><th>C次低</th><th>D突破</th><th>MACD状态</th><th>数据源</th></tr></thead><tbody id="tbody"></tbody></table></div></section>
<section class="right"><div class="charthead"><span id="stocktitle" class="stocktitle">点击左侧股票查看日线 + MACD</span><span id="chartsrc" class="muted"></span><div class="spacer"></div><button onclick="loadChart(currentCode,120)">120日</button><button onclick="loadChart(currentCode,180)">180日</button><button onclick="loadChart(currentCode,250)">250日</button></div><div class="charts"><div class="canvasbox"><div class="panel"><div class="legend">日线K线　A低点 / B反弹高点 / C回调次低 / D突破</div><canvas id="kcanvas"></canvas><div class="hint" id="hint">尚未选择股票</div></div><div class="panel"><div class="legend">MACD　Z1首次过零轴 / C零轴回踩 / Z2二次向上</div><canvas id="mcanvas"></canvas></div></div></div></section>
</div>
<script>
const stages=['全部','C零轴回踩','D二次突破'];let currentStage='全部',currentCode='',chartRows=[],chartPoints=[],macdMarks=[];
const filters=document.getElementById('filters');
stages.forEach(s=>{const b=document.createElement('button');b.textContent=s;b.className=s==='全部'?'active':'';b.onclick=()=>{currentStage=s;[...filters.children].forEach(x=>x.classList.remove('active'));b.classList.add('active');loadResults()};filters.appendChild(b)});
async function health(){try{const d=await(await fetch('/api/health')).json();document.getElementById('proxy').textContent=d.proxy_detected?'代理：已检测，本地已绕过':'代理：本地已绕过';}catch(e){document.getElementById('proxy').textContent='本地连接异常'}}
async function startScan(force){try{const d=await(await fetch('/api/scan/start?force='+force,{method:'POST'})).json();document.getElementById('status').textContent=d.message}catch(e){alert('无法连接本地服务')}}
async function poll(){try{const s=await(await fetch('/api/status')).json();document.getElementById('status').textContent=`${s.message} ｜ ${s.done}/${s.total} ｜ 候选 ${s.matched} ｜ 失败 ${s.failed} ｜ 股票列表 ${s.stock_list_source||'-'}`;document.getElementById('bar').style.width=s.total?Math.min(100,s.done/s.total*100)+'%':'0%';if(!s.running&&s.finished_at)loadResults()}catch(e){document.getElementById('status').textContent='本地网页服务连接失败'}setTimeout(poll,1300)}
function p(v){return v===null||v===undefined?'':v}
async function loadResults(){try{const d=await(await fetch('/api/results?signal='+encodeURIComponent(currentStage))).json();const tb=document.getElementById('tbody');tb.innerHTML='';d.rows.forEach(r=>{const tr=document.createElement('tr');tr.innerHTML=`<td>${r.code}</td><td>${r.name}</td><td>${p(r.close)}</td><td class="sig ${r.signal}">${r.signal}</td><td>${r.a_date.slice(5)} ${p(r.a_price)}</td><td>${r.b_date.slice(5)} ${p(r.b_price)}</td><td>${r.c_date.slice(5)} ${p(r.c_price)}</td><td>${r.d_date?r.d_date.slice(5)+' '+p(r.d_price):'-'}</td><td>${r.macd_state}</td><td>${r.source}</td>`;tr.onclick=()=>{document.getElementById('stocktitle').textContent=`${r.code} ${r.name} · ${r.signal}`;loadChart(r.code,180)};tb.appendChild(tr)})}catch(e){}}
async function loadChart(code,days){if(!code)return;currentCode=code;document.getElementById('hint').style.display='flex';document.getElementById('hint').textContent='加载中...';try{const res=await fetch(`/api/chart/${code}?days=${days}`);if(!res.ok)throw new Error(await res.text());const d=await res.json();chartRows=d.rows;chartPoints=d.points||[];macdMarks=d.macd_marks||[];document.getElementById('chartsrc').textContent=`${d.signal||''} ${d.macd_state||''} · 数据源: ${d.source}`;document.getElementById('hint').style.display='none';drawAll()}catch(e){document.getElementById('hint').style.display='flex';document.getElementById('hint').textContent='K线加载失败：'+String(e).slice(0,120)}}
function prep(c){const dpr=window.devicePixelRatio||1,rect=c.getBoundingClientRect();c.width=Math.max(10,Math.floor(rect.width*dpr));c.height=Math.max(10,Math.floor(rect.height*dpr));const x=c.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);return {x,w:rect.width,h:rect.height}}
function grid(x,w,h,L=48,R=12,T=24,B=24){x.clearRect(0,0,w,h);x.strokeStyle='#20272e';x.lineWidth=1;for(let i=0;i<=4;i++){const y=T+(h-T-B)*i/4;x.beginPath();x.moveTo(L,y);x.lineTo(w-R,y);x.stroke()}return {L,R,T,B,pw:w-L-R,ph:h-T-B}}
function markerStyle(kind){if(kind==='A')return ['#ffd166',-1];if(kind==='B')return ['#7ce3ff',1];if(kind==='C')return ['#ff9f43',-1];return ['#ff7272',1]}
function drawK(){if(!chartRows.length)return;const c=document.getElementById('kcanvas'),{x,w,h}=prep(c),g=grid(x,w,h);const hi=Math.max(...chartRows.map(r=>r.high)),lo=Math.min(...chartRows.map(r=>r.low)),range=(hi-lo)||1,n=chartRows.length,step=g.pw/n,bw=Math.max(1,Math.min(8,step*.62));const yy=v=>g.T+(hi-v)/range*g.ph;x.font='11px Arial';x.fillStyle='#7f8a96';x.textAlign='right';for(let i=0;i<=4;i++){const v=hi-range*i/4,y=g.T+g.ph*i/4;x.fillText(v.toFixed(2),g.L-5,y+4)}chartRows.forEach((r,i)=>{const cx=g.L+step*(i+.5),yo=yy(r.open),yc=yy(r.close),yh=yy(r.high),yl=yy(r.low),up=r.close>=r.open;x.strokeStyle=up?'#e65353':'#21b8c7';x.fillStyle=up?'#e65353':'#21b8c7';x.beginPath();x.moveTo(cx,yh);x.lineTo(cx,yl);x.stroke();const top=Math.min(yo,yc),bh=Math.max(1,Math.abs(yc-yo));if(up)x.fillRect(cx-bw/2,top,bw,bh);else x.strokeRect(cx-bw/2,top,bw,bh)});
const idxByDate=new Map(chartRows.map((r,i)=>[r.date,i]));chartPoints.forEach(m=>{const i=idxByDate.get(m.date);if(i===undefined)return;const cx=g.L+step*(i+.5),cy=yy(m.price),[color,dir]=markerStyle(m.kind);x.strokeStyle=color;x.fillStyle=color;x.lineWidth=1.4;x.beginPath();x.arc(cx,cy,4,0,Math.PI*2);x.fill();x.beginPath();x.moveTo(cx,cy);x.lineTo(cx,cy+dir*26);x.stroke();x.textAlign='center';x.font='bold 12px Arial';x.fillText(`${m.kind} ${m.price}`,cx,cy+dir*34)});
x.fillStyle='#73808d';x.font='11px Arial';x.textAlign='center';for(let j=0;j<5;j++){const i=Math.round((n-1)*j/4),cx=g.L+step*(i+.5);x.fillText(chartRows[i].date.slice(5),cx,h-5)}}
function drawM(){if(!chartRows.length)return;const c=document.getElementById('mcanvas'),{x,w,h}=prep(c),g=grid(x,w,h,48,12,22,24);const vals=chartRows.flatMap(r=>[r.dif,r.dea,r.macd]).filter(v=>Number.isFinite(v));let hi=Math.max(...vals,0),lo=Math.min(...vals,0);if(hi===lo){hi+=1;lo-=1}const range=hi-lo,n=chartRows.length,step=g.pw/n,yy=v=>g.T+(hi-v)/range*g.ph,zero=yy(0);x.strokeStyle='#59616a';x.beginPath();x.moveTo(g.L,zero);x.lineTo(w-g.R,zero);x.stroke();chartRows.forEach((r,i)=>{const cx=g.L+step*(i+.5),y=yy(r.macd);x.fillStyle=r.macd>=0?'#d84e4e':'#1eb4c3';const top=Math.min(zero,y),bh=Math.max(1,Math.abs(y-zero));x.fillRect(cx-Math.max(1,step*.28),top,Math.max(1,step*.56),bh)});function line(key,color){x.strokeStyle=color;x.lineWidth=1.3;x.beginPath();chartRows.forEach((r,i)=>{const cx=g.L+step*(i+.5),cy=yy(r[key]);i?x.lineTo(cx,cy):x.moveTo(cx,cy)});x.stroke()}line('dif','#e6d94f');line('dea','#e585d1');
const idxByDate=new Map(chartRows.map((r,i)=>[r.date,i]));macdMarks.forEach(m=>{const i=idxByDate.get(m.date);if(i===undefined)return;const cx=g.L+step*(i+.5);x.save();x.setLineDash([4,4]);x.strokeStyle=m.kind==='Z2'?'#ff7272':m.kind==='C'?'#ff9f43':'#7ce3ff';x.beginPath();x.moveTo(cx,g.T);x.lineTo(cx,h-g.B);x.stroke();x.restore();x.fillStyle=x.strokeStyle;x.textAlign='center';x.font='bold 10px Arial';x.fillText(m.kind,cx,g.T+11)});
x.font='11px Arial';x.fillStyle='#7f8a96';x.textAlign='right';for(let i=0;i<=4;i++){const v=hi-range*i/4,y=g.T+g.ph*i/4;x.fillText(v.toFixed(2),g.L-5,y+4)}x.textAlign='center';for(let j=0;j<5;j++){const i=Math.round((n-1)*j/4),cx=g.L+step*(i+.5);x.fillText(chartRows[i].date.slice(5),cx,h-5)}}
function drawAll(){drawK();drawM()}window.addEventListener('resize',()=>{if(chartRows.length)drawAll()});health();loadResults();poll();
</script>
</body></html>'''


def find_free_port(start_port: int = 8000) -> int:
    for port in range(start_port, start_port + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("8000附近没有可用端口")


def open_browser_when_ready(url: str) -> None:
    # 这个自检显式禁用代理，确保本地 127.0.0.1 不被代理劫持。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    check_url = url + "/startup-check"
    for _ in range(50):
        try:
            with opener.open(check_url, timeout=1.0) as r:
                if r.status == 200:
                    try:
                        (ROOT / "server_url.txt").write_text(url, encoding="utf-8")
                    except Exception:
                        pass
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.2)


if __name__ == "__main__":
    import uvicorn

    host = "127.0.0.1"
    port = find_free_port(int(os.environ.get("MACD_PORT", "8000")))
    url = f"http://{host}:{port}"
    print("=" * 68, flush=True)
    print("MACD + 日线结构选股器 - A/B/C/D模板版", flush=True)
    print(f"网页地址: {url}", flush=True)
    print("本地地址已加入 NO_PROXY；外部行情仍使用你原来的代理。", flush=True)
    print("网页只显示日线K线 + MACD，并自动标出A/B/C/D与零轴阶段。", flush=True)
    print("关闭这个窗口，网页服务也会停止。", flush=True)
    print("=" * 68, flush=True)
    threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except BaseException:
        try:
            with (ROOT / "startup_log.txt").open("a", encoding="utf-8") as f:
                f.write("\n\n===== main.py fatal error =====\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise