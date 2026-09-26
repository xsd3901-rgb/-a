from __future__ import annotations

import pandas as pd

from aquant.data.providers.base import DailyBarProvider, ProviderInfo, normalize_symbol
from aquant.data.schema import FIELDS


class EastMoneyAKShareProvider(DailyBarProvider):
    """东方财富历史日线，使用 AKShare 作为适配器。标准成交量统一为“股”。"""

    info = ProviderInfo(provider="eastmoney", adapter="akshare", volume_unit="share")

    def fetch_stock_list(self) -> pd.DataFrame:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return pd.DataFrame(columns=["symbol", "name"])
        out = df.rename(columns={"代码": "symbol", "名称": "name"})[["symbol", "name"]].copy()
        out["symbol"] = out["symbol"].map(normalize_symbol)
        return out.drop_duplicates("symbol").reset_index(drop=True)

    def fetch_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "none",
    ) -> pd.DataFrame:
        import akshare as ak

        code = normalize_symbol(symbol)
        ak_adjust = {"none": "", "qfq": "qfq", "hfq": "hfq"}.get(adjust)
        if ak_adjust is None:
            raise ValueError(f"不支持的复权方式: {adjust}")

        raw = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust=ak_adjust,
        )
        if raw is None or raw.empty:
            return pd.DataFrame()

        rename = {
            "日期": FIELDS.trade_date,
            "股票代码": FIELDS.symbol,
            "开盘": FIELDS.open,
            "最高": FIELDS.high,
            "最低": FIELDS.low,
            "收盘": FIELDS.close,
            "成交量": FIELDS.volume,
            "成交额": FIELDS.amount,
            "换手率": FIELDS.turnover,
        }
        out = raw.rename(columns=rename).copy()
        if FIELDS.symbol not in out.columns:
            out[FIELDS.symbol] = code
        out[FIELDS.symbol] = out[FIELDS.symbol].map(normalize_symbol)
        out[FIELDS.trade_date] = pd.to_datetime(out[FIELDS.trade_date]).dt.normalize()

        numeric = [FIELDS.open, FIELDS.high, FIELDS.low, FIELDS.close, FIELDS.volume, FIELDS.amount, FIELDS.turnover]
        for col in numeric:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        # AKShare 官方文档：stock_zh_a_hist 的成交量单位是“手”；A-Quant 统一为“股”。
        out[FIELDS.volume] = out[FIELDS.volume] * 100.0
        out[FIELDS.provider] = self.info.provider
        out[FIELDS.adapter] = self.info.adapter
        out[FIELDS.adjustment] = adjust
        out[FIELDS.fetched_at] = pd.Timestamp.now(tz="Asia/Shanghai").isoformat()
        out[FIELDS.data_version] = "1"
        out[FIELDS.quality_status] = "raw"

        ordered = [
            FIELDS.symbol,
            FIELDS.trade_date,
            FIELDS.open,
            FIELDS.high,
            FIELDS.low,
            FIELDS.close,
            FIELDS.volume,
            FIELDS.amount,
            FIELDS.turnover,
            FIELDS.provider,
            FIELDS.adapter,
            FIELDS.adjustment,
            FIELDS.fetched_at,
            FIELDS.data_version,
            FIELDS.quality_status,
        ]
        return out[[c for c in ordered if c in out.columns]].sort_values(FIELDS.trade_date).reset_index(drop=True)
