from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from aquant.data.schema import FIELDS


@dataclass(frozen=True)
class StorePaths:
    root: Path
    raw: Path
    standard: Path
    catalog: Path


class MarketStore:
    """A-Quant 本地行情仓库：Parquet 存储，DuckDB 查询。"""

    def __init__(self, root: str | Path = "data_store") -> None:
        base = Path(root)
        self.paths = StorePaths(
            root=base,
            raw=base / "raw",
            standard=base / "standard" / "daily",
            catalog=base / "aquant.duckdb",
        )
        self.paths.raw.mkdir(parents=True, exist_ok=True)
        self.paths.standard.mkdir(parents=True, exist_ok=True)

    def _standard_path(self, symbol: str, adjust: str) -> Path:
        folder = self.paths.standard / adjust
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{symbol}.parquet"

    def _raw_path(self, provider: str, symbol: str, adjust: str) -> Path:
        folder = self.paths.raw / provider / "daily" / adjust
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{symbol}.parquet"

    @staticmethod
    def _atomic_upsert(path: Path, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        incoming = frame.copy()
        incoming[FIELDS.trade_date] = pd.to_datetime(incoming[FIELDS.trade_date]).dt.normalize()

        if path.exists():
            old = pd.read_parquet(path)
            old[FIELDS.trade_date] = pd.to_datetime(old[FIELDS.trade_date]).dt.normalize()
            merged = pd.concat([old, incoming], ignore_index=True, sort=False)
        else:
            merged = incoming

        keys = [FIELDS.symbol, FIELDS.trade_date]
        merged = merged.sort_values(keys).drop_duplicates(keys, keep="last").reset_index(drop=True)

        tmp = path.with_suffix(".tmp.parquet")
        merged.to_parquet(tmp, index=False)
        tmp.replace(path)
        return len(merged)

    def save_raw(self, frame: pd.DataFrame, provider: str, symbol: str, adjust: str) -> int:
        return self._atomic_upsert(self._raw_path(provider, symbol, adjust), frame)

    def save_standard(self, frame: pd.DataFrame, symbol: str, adjust: str) -> int:
        return self._atomic_upsert(self._standard_path(symbol, adjust), frame)

    def read_daily(
        self,
        symbol: str,
        adjust: str = "none",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        path = self._standard_path(symbol, adjust)
        if not path.exists():
            return pd.DataFrame()

        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("缺少 duckdb 依赖，请先安装 requirements.txt") from exc

        where: list[str] = []
        params: list[str] = []
        if start_date:
            where.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("trade_date <= ?")
            params.append(end_date)

        sql = "SELECT * FROM read_parquet(?)"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY trade_date"

        # 不使用 duckdb.execute 的进程级默认连接。扫描/回测并发时，
        # 每个线程使用独立内存连接，避免默认连接被多个线程共享。
        con = duckdb.connect()
        try:
            return con.execute(
                sql,
                [str(path), *params],
            ).df()
        finally:
            con.close()

    def latest_date(self, symbol: str, adjust: str = "none") -> pd.Timestamp | None:
        path = self._standard_path(symbol, adjust)
        if not path.exists():
            return None

        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("缺少 duckdb 依赖，请先安装 requirements.txt") from exc

        con = duckdb.connect()
        try:
            value = con.execute(
                "SELECT MAX(trade_date) FROM read_parquet(?)",
                [str(path)],
            ).fetchone()[0]
        finally:
            con.close()

        if value is None:
            return None
        return pd.Timestamp(value).normalize()

    def light_stats(
        self,
        symbol: str,
        adjust: str = "none",
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """用 DuckDB 聚合读取轻量统计，不把整段行情装入 pandas。"""
        path = self._standard_path(symbol, adjust)
        empty = {
            "rows": 0,
            "start": None,
            "end": None,
            "providers": [],
            "tradable_rows": 0,
            "pct_change_coverage": 0.0,
        }
        if not path.exists():
            return empty

        try:
            import duckdb
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError(
                "需要 duckdb/pyarrow 才能读取本地 Parquet 统计"
            ) from exc

        schema_names = set(
            pq.ParquetFile(path).schema_arrow.names
        )
        if FIELDS.trade_date not in schema_names:
            return empty

        where: list[str] = []
        params: list[str] = [str(path)]
        if start_date:
            where.append(f"{FIELDS.trade_date} >= ?")
            params.append(str(start_date))
        if end_date:
            where.append(f"{FIELDS.trade_date} <= ?")
            params.append(str(end_date))
        where_sql = (
            " WHERE " + " AND ".join(where)
            if where
            else ""
        )

        has_status = FIELDS.trade_status in schema_names
        has_pct = FIELDS.pct_change in schema_names
        has_provider = FIELDS.provider in schema_names

        tradable_expr = (
            f"TRY_CAST({FIELDS.trade_status} AS DOUBLE) = 1"
            if has_status
            else "TRUE"
        )
        tradable_rows_expr = (
            f"SUM(CASE WHEN {tradable_expr} THEN 1 ELSE 0 END)"
        )
        if has_pct:
            pct_coverage_expr = (
                "CASE WHEN "
                f"{tradable_rows_expr} > 0 THEN "
                "SUM(CASE WHEN "
                f"{tradable_expr} "
                f"AND TRY_CAST({FIELDS.pct_change} AS DOUBLE) IS NOT NULL "
                "THEN 1 ELSE 0 END) * 1.0 / "
                f"{tradable_rows_expr} ELSE 0 END"
            )
        else:
            pct_coverage_expr = "0.0"

        sql = f"""
            SELECT
                COUNT(*) AS rows,
                MIN({FIELDS.trade_date}) AS start_date,
                MAX({FIELDS.trade_date}) AS end_date,
                {tradable_rows_expr} AS tradable_rows,
                {pct_coverage_expr} AS pct_coverage
            FROM read_parquet(?)
            {where_sql}
        """

        con = duckdb.connect()
        try:
            row = con.execute(sql, params).fetchone()
            if row is None or int(row[0] or 0) == 0:
                return empty

            providers: list[str] = []
            if has_provider:
                provider_rows = con.execute(
                    f"""
                    SELECT DISTINCT CAST({FIELDS.provider} AS VARCHAR)
                    FROM read_parquet(?)
                    {where_sql}
                    AND {FIELDS.provider} IS NOT NULL
                    ORDER BY 1
                    """
                    if where
                    else f"""
                    SELECT DISTINCT CAST({FIELDS.provider} AS VARCHAR)
                    FROM read_parquet(?)
                    WHERE {FIELDS.provider} IS NOT NULL
                    ORDER BY 1
                    """,
                    params,
                ).fetchall()
                providers = [
                    str(item[0]).strip()
                    for item in provider_rows
                    if item and str(item[0]).strip()
                ]
        finally:
            con.close()

        return {
            "rows": int(row[0] or 0),
            "start": (
                pd.Timestamp(row[1]).normalize()
                if row[1] is not None
                else None
            ),
            "end": (
                pd.Timestamp(row[2]).normalize()
                if row[2] is not None
                else None
            ),
            "providers": providers,
            "tradable_rows": int(row[3] or 0),
            "pct_change_coverage": float(row[4] or 0.0),
        }

    def refresh_catalog(self) -> None:
        """为 none/qfq/hfq 三种标准库建立 DuckDB 视图；无文件的目录自动跳过。"""
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("缺少 duckdb 依赖，请先安装 requirements.txt") from exc

        con = duckdb.connect(str(self.paths.catalog))
        try:
            for adjust in ("none", "qfq", "hfq"):
                folder = self.paths.standard / adjust
                files = list(folder.glob("*.parquet")) if folder.exists() else []
                if not files:
                    continue
                glob_path = str(folder / "*.parquet").replace("'", "''")
                con.execute(
                    f"CREATE OR REPLACE VIEW daily_{adjust} AS "
                    f"SELECT * FROM read_parquet('{glob_path}', union_by_name=true)"
                )
        finally:
            con.close()
