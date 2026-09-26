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
        return duckdb.execute(sql, [str(path), *params]).df()

    def latest_date(self, symbol: str, adjust: str = "none") -> pd.Timestamp | None:
        path = self._standard_path(symbol, adjust)
        if not path.exists():
            return None
        dates = pd.read_parquet(path, columns=[FIELDS.trade_date])
        if dates.empty:
            return None
        return pd.to_datetime(dates[FIELDS.trade_date]).max().normalize()

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
