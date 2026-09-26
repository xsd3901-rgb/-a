from __future__ import annotations

from pathlib import Path

import pandas as pd


class MarketContextStore:
    """指数与行业等市场环境数据的本地 Parquet/DuckDB 存储。"""

    def __init__(self, root: str | Path = "data_store") -> None:
        base = Path(root) / "standard" / "context"
        self.root = base
        self.index_dir = base / "index_daily"
        self.industry_path = base / "industry_map.parquet"
        self.catalog = Path(root) / "aquant.duckdb"
        self.index_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        tmp = path.with_suffix(".tmp.parquet")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)
        return len(frame)

    def save_industry_map(self, frame: pd.DataFrame) -> int:
        out = frame.copy()
        if "code" not in out.columns:
            raise ValueError("industry_map 必须包含 code")
        out["code"] = out["code"].astype(str).str.zfill(6)
        out = out.drop_duplicates("code", keep="last").sort_values("code").reset_index(drop=True)
        return self._atomic_write(self.industry_path, out)

    def read_industry_map(self) -> pd.DataFrame:
        if not self.industry_path.exists():
            return pd.DataFrame()
        return pd.read_parquet(self.industry_path)

    def _index_path(self, index_code: str) -> Path:
        return self.index_dir / f"{index_code}.parquet"

    def save_index_daily(self, index_code: str, index_name: str, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        incoming = frame.copy()
        incoming["index_code"] = index_code
        incoming["index_name"] = index_name
        incoming["trade_date"] = pd.to_datetime(incoming["trade_date"], errors="coerce").dt.normalize()
        incoming = incoming.dropna(subset=["trade_date", "open", "high", "low", "close"])

        path = self._index_path(index_code)
        if path.exists():
            old = pd.read_parquet(path)
            old["trade_date"] = pd.to_datetime(old["trade_date"], errors="coerce").dt.normalize()
            merged = pd.concat([old, incoming], ignore_index=True, sort=False)
        else:
            merged = incoming

        merged = (
            merged.sort_values("trade_date")
            .drop_duplicates(["index_code", "trade_date"], keep="last")
            .reset_index(drop=True)
        )
        return self._atomic_write(path, merged)

    def read_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        path = self._index_path(index_code)
        if not path.exists():
            return pd.DataFrame()
        out = pd.read_parquet(path)
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
        if start_date:
            out = out[out["trade_date"] >= pd.Timestamp(start_date)]
        if end_date:
            out = out[out["trade_date"] <= pd.Timestamp(end_date)]
        return out.sort_values("trade_date").reset_index(drop=True)

    def latest_index_date(self, index_code: str) -> pd.Timestamp | None:
        frame = self.read_index_daily(index_code)
        if frame.empty:
            return None
        return pd.to_datetime(frame["trade_date"]).max().normalize()

    def refresh_catalog(self) -> None:
        import duckdb

        con = duckdb.connect(str(self.catalog))
        try:
            if self.industry_path.exists():
                p = str(self.industry_path).replace("'", "''")
                con.execute(
                    f"CREATE OR REPLACE VIEW industry_map AS "
                    f"SELECT * FROM read_parquet('{p}')"
                )
            files = list(self.index_dir.glob("*.parquet"))
            if files:
                glob_path = str(self.index_dir / "*.parquet").replace("'", "''")
                con.execute(
                    f"CREATE OR REPLACE VIEW index_daily AS "
                    f"SELECT * FROM read_parquet('{glob_path}', union_by_name=true)"
                )
        finally:
            con.close()
