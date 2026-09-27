from __future__ import annotations

import pandas as pd

from aquant.data.providers.base import (
    DailyBarProvider,
    ProviderInfo,
    is_shsz_a_share,
    normalize_symbol,
    to_baostock_code,
)
from aquant.data.providers.baostock_session import BAOSTOCK_SESSION
from aquant.data.schema import FIELDS


class BaoStockProvider(DailyBarProvider):
    """BaoStock 历史日线适配器。

    使用进程内复用会话，避免全市场下载时每只股票都 login/logout。
    """

    info = ProviderInfo(
        provider="baostock",
        adapter="baostock-python",
        volume_unit="share",
    )

    def fetch_stock_list(self) -> pd.DataFrame:
        def query(bs):
            rs = bs.query_stock_basic()
            if rs.error_code != "0":
                raise RuntimeError(
                    f"BaoStock 股票基础资料获取失败: "
                    f"{rs.error_code} {rs.error_msg}"
                )
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            return pd.DataFrame(rows, columns=rs.fields)

        raw = BAOSTOCK_SESSION.run(query)
        if raw.empty:
            return pd.DataFrame(columns=["symbol", "name"])

        if "code" not in raw.columns or "code_name" not in raw.columns:
            raise RuntimeError(
                f"BaoStock 股票基础资料字段异常: {list(raw.columns)}"
            )

        if "type" in raw.columns:
            raw = raw[raw["type"].astype(str).eq("1")]
        if "status" in raw.columns:
            raw = raw[raw["status"].astype(str).eq("1")]

        out = pd.DataFrame(
            {
                "symbol": raw["code"].map(normalize_symbol),
                "name": raw["code_name"].astype(str).str.strip(),
            }
        )
        out = out[out["symbol"].map(is_shsz_a_share)]
        return (
            out.drop_duplicates("symbol")
            .sort_values("symbol")
            .reset_index(drop=True)
        )

    def fetch_daily(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "none",
    ) -> pd.DataFrame:
        adjust_flag = {
            "none": "3",
            "qfq": "2",
            "hfq": "1",
        }.get(adjust)
        if adjust_flag is None:
            raise ValueError(f"不支持的复权方式: {adjust}")

        code = to_baostock_code(symbol)
        fields = (
            "date,code,open,high,low,close,preclose,volume,"
            "amount,turn,tradestatus,pctChg,isST"
        )

        def query(bs):
            rs = bs.query_history_k_data_plus(
                code,
                fields,
                start_date=start_date.replace("/", "-")[:10],
                end_date=end_date.replace("/", "-")[:10],
                frequency="d",
                adjustflag=adjust_flag,
            )
            if rs.error_code != "0":
                raise RuntimeError(
                    f"BaoStock 日线获取失败: "
                    f"{rs.error_code} {rs.error_msg}"
                )
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            return pd.DataFrame(rows, columns=rs.fields)

        raw = BAOSTOCK_SESSION.run(query)
        if raw.empty:
            return pd.DataFrame()

        rename = {
            "date": FIELDS.trade_date,
            "code": FIELDS.symbol,
            "open": FIELDS.open,
            "high": FIELDS.high,
            "low": FIELDS.low,
            "close": FIELDS.close,
            "preclose": FIELDS.preclose,
            "volume": FIELDS.volume,
            "amount": FIELDS.amount,
            "turn": FIELDS.turnover,
            "tradestatus": FIELDS.trade_status,
            "pctChg": FIELDS.pct_change,
            "isST": FIELDS.is_st,
        }
        out = raw.rename(columns=rename).copy()
        out[FIELDS.symbol] = out[FIELDS.symbol].map(
            normalize_symbol
        )
        out[FIELDS.trade_date] = pd.to_datetime(
            out[FIELDS.trade_date],
            errors="coerce",
        ).dt.normalize()

        numeric = [
            FIELDS.open,
            FIELDS.high,
            FIELDS.low,
            FIELDS.close,
            FIELDS.preclose,
            FIELDS.volume,
            FIELDS.amount,
            FIELDS.turnover,
            FIELDS.pct_change,
            FIELDS.trade_status,
            FIELDS.is_st,
        ]
        for col in numeric:
            if col in out.columns:
                out[col] = pd.to_numeric(
                    out[col],
                    errors="coerce",
                )

        out[FIELDS.provider] = self.info.provider
        out[FIELDS.adapter] = self.info.adapter
        out[FIELDS.adjustment] = adjust
        out[FIELDS.fetched_at] = pd.Timestamp.now(
            tz="Asia/Shanghai"
        ).isoformat()
        out[FIELDS.data_version] = "1"
        out[FIELDS.quality_status] = "raw"

        ordered = [
            FIELDS.symbol,
            FIELDS.trade_date,
            FIELDS.open,
            FIELDS.high,
            FIELDS.low,
            FIELDS.close,
            FIELDS.preclose,
            FIELDS.volume,
            FIELDS.amount,
            FIELDS.turnover,
            FIELDS.pct_change,
            FIELDS.trade_status,
            FIELDS.is_st,
            FIELDS.provider,
            FIELDS.adapter,
            FIELDS.adjustment,
            FIELDS.fetched_at,
            FIELDS.data_version,
            FIELDS.quality_status,
        ]
        return (
            out[[c for c in ordered if c in out.columns]]
            .sort_values(FIELDS.trade_date)
            .reset_index(drop=True)
        )
