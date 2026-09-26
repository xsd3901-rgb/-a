from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from config import SETTINGS, ensure_directories


class DataSourceError(RuntimeError):
    pass


def _import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError as exc:
        raise DataSourceError("未安装 akshare，请先运行: python -m pip install -r requirements.txt") from exc


def _clean_code(value: object) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def _symbol_with_market(code: str) -> str:
    code = _clean_code(code)
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    aliases = {
        "日期": "date", "date": "date",
        "开盘": "open", "open": "open",
        "最高": "high", "high": "high",
        "最低": "low", "low": "low",
        "收盘": "close", "close": "close",
        "成交量": "volume", "volume": "volume",
        "成交额": "amount", "amount": "amount",
        "换手率": "turnover", "turnover": "turnover",
    }
    out = df.rename(columns={c: aliases.get(str(c), str(c)) for c in df.columns}).copy()
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise DataSourceError(f"历史行情缺少必要字段: {missing}; 实际字段: {list(df.columns)}")

    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "amount", "turnover"] if c in out.columns]
    out = out[keep]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=required).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return out


class FreeAStockData:
    """免费数据适配层。主源为东方财富接口，失败时尝试 AKShare 提供的备用接口。"""

    def __init__(self) -> None:
        ensure_directories()
        self.ak = _import_akshare()

    def stock_list(self) -> pd.DataFrame:
        errors: list[str] = []
        try:
            df = self.ak.stock_zh_a_spot_em()
            result = df[["代码", "名称"]].rename(columns={"代码": "code", "名称": "name"})
        except Exception as exc:
            errors.append(f"stock_zh_a_spot_em: {exc}")
            try:
                df = self.ak.stock_info_a_code_name()
                code_col = "code" if "code" in df.columns else "代码"
                name_col = "name" if "name" in df.columns else "名称"
                result = df[[code_col, name_col]].rename(columns={code_col: "code", name_col: "name"})
            except Exception as exc2:
                errors.append(f"stock_info_a_code_name: {exc2}")
                raise DataSourceError("股票列表接口全部失败: " + " | ".join(errors)) from exc2

        result["code"] = result["code"].map(_clean_code)
        result["name"] = result["name"].astype(str).str.strip()
        result = result.drop_duplicates("code")
        if SETTINGS.exclude_st:
            result = result[~result["name"].str.upper().str.contains("ST", na=False)]
        if SETTINGS.exclude_bj:
            result = result[~result["code"].str.startswith(("4", "8", "92"))]
        return result.reset_index(drop=True)

    def _cache_file(self, code: str) -> Path:
        return SETTINGS.cache_dir / f"{_clean_code(code)}.csv.gz"

    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        return age_hours <= SETTINGS.cache_hours

    def history(self, code: str, refresh: bool = False) -> pd.DataFrame:
        code = _clean_code(code)
        cache = self._cache_file(code)
        if not refresh and self._cache_is_fresh(cache):
            try:
                return _normalize_hist(pd.read_csv(cache, compression="gzip"))
            except Exception:
                pass

        calendar_days = int(SETTINGS.history_days * 1.8)
        end = datetime.now()
        start = end - timedelta(days=calendar_days)
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        errors: list[str] = []

        try:
            raw = self.ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_s,
                end_date=end_s,
                adjust=SETTINGS.adjust,
            )
            hist = _normalize_hist(raw)
        except Exception as exc:
            errors.append(f"stock_zh_a_hist: {exc}")
            hist = pd.DataFrame()

        if hist.empty:
            try:
                raw = self.ak.stock_zh_a_daily(
                    symbol=_symbol_with_market(code),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust=SETTINGS.adjust,
                )
                hist = _normalize_hist(raw.reset_index() if raw.index.name else raw)
            except Exception as exc:
                errors.append(f"stock_zh_a_daily: {exc}")

        if hist.empty:
            raise DataSourceError(f"{code} 历史行情获取失败: " + " | ".join(errors))

        hist.tail(SETTINGS.history_days).to_csv(cache, index=False, compression="gzip", encoding="utf-8-sig")
        time.sleep(SETTINGS.request_pause_seconds)
        return hist.tail(SETTINGS.history_days).reset_index(drop=True)
