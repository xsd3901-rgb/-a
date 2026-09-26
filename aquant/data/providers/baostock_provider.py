from __future__ import annotations

import pandas as pd

from aquant.data.providers.base import DailyBarProvider, ProviderInfo, normalize_symbol, to_baostock_code
from aquant.data.schema import FIELDS


class BaoStockProvider(DailyBarProvider):
    """BaoStock 历史日线适配器，主要用于沪深 A 股历史数据和交叉校验。"""

    info = ProviderInfo(provider="baostock", adapter="baostock-python", volume_unit="share")

    def fetch_stock_list(self) -> pd.DataFrame:
        """BaoStock 股票基础列表备用源。

        BaoStock 的证券基础资料包含指数、股票、基金等，这里只保留正常上市股票。
        北京证券交易所覆盖能力不足，因此它主要作为沪深市场的独立降级源。
        """
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError("缺少 baostock 依赖，请先安装 requirements.txt") from exc

        login = bs.login()
        if getattr(login, "error_code", "-1") != "0":
            raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")

        try:
            rs = bs.query_stock_basic()
            if rs.error_code != "0":
                raise RuntimeError(f"BaoStock 股票基础资料获取失败: {rs.error_code} {rs.error_msg}")

            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            raw = pd.DataFrame(rows, columns=rs.fields)
        finally:
            bs.logout()

        if raw.empty:
            return pd.DataFrame(columns=["symbol", "name"])

        code_col = "code"
        name_col = "code_name"
        if code_col not in raw.columns or name_col not in raw.columns:
            raise RuntimeError(f"BaoStock 股票基础资料字段异常: {list(raw.columns)}")

        if "type" in raw.columns:
            raw = raw[raw["type"].astype(str).eq("1")]
        if "status" in raw.columns:
            raw = raw[raw["status"].astype(str).eq("1")]

        out = pd.DataFrame(
            {
                "symbol": raw[code_col].map(normalize_symbol),
                "name": raw[name_col].astype(str).str.strip(),
            }
        )
        out = out[out["symbol"].str.match(r"^(0|2|3|6|9)\d{5}$", na=False)]
        return out.drop_duplicates("symbol").reset_index(drop=True)

    def fetch_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "none",
    ) -> pd.DataFrame:
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError("缺少 baostock 依赖，请先安装 requirements.txt") from exc

        adjust_flag = {"none": "3", "qfq": "2", "hfq": "1"}.get(adjust)
        if adjust_flag is None:
            raise ValueError(f"不支持的复权方式: {adjust}")

        code = to_baostock_code(symbol)
        login = bs.login()
        if getattr(login, "error_code", "-1") != "0":
            raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")

        fields = "date,code,open,high,low,close,volume,amount,turn,tradestatus"
        try:
            rs = bs.query_history_k_data_plus(
                code,
                fields,
                start_date=start_date.replace("/", "-")[:10],
                end_date=end_date.replace("/", "-")[:10],
                frequency="d",
                adjustflag=adjust_flag,
            )
            if rs.error_code != "0":
                raise RuntimeError(f"BaoStock 日线获取失败: {rs.error_code} {rs.error_msg}")

            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            raw = pd.DataFrame(rows, columns=rs.fields)
        finally:
            bs.logout()

        if raw.empty:
            return pd.DataFrame()

        rename = {
            "date": FIELDS.trade_date,
            "code": FIELDS.symbol,
            "open": FIELDS.open,
            "high": FIELDS.high,
            "low": FIELDS.low,
            "close": FIELDS.close,
            "volume": FIELDS.volume,
            "amount": FIELDS.amount,
            "turn": FIELDS.turnover,
            "tradestatus": FIELDS.trade_status,
        }
        out = raw.rename(columns=rename).copy()
        out[FIELDS.symbol] = out[FIELDS.symbol].map(normalize_symbol)
        out[FIELDS.trade_date] = pd.to_datetime(out[FIELDS.trade_date]).dt.normalize()

        numeric = [FIELDS.open, FIELDS.high, FIELDS.low, FIELDS.close, FIELDS.volume, FIELDS.amount, FIELDS.turnover, FIELDS.trade_status]
        for col in numeric:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        # BaoStock 日线 volume 原生单位就是“股”，无需换算。
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
            FIELDS.trade_status,
            FIELDS.provider,
            FIELDS.adapter,
            FIELDS.adjustment,
            FIELDS.fetched_at,
            FIELDS.data_version,
            FIELDS.quality_status,
        ]
        return out[[c for c in ordered if c in out.columns]].sort_values(FIELDS.trade_date).reset_index(drop=True)
