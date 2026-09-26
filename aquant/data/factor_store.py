from __future__ import annotations

from pathlib import Path

import pandas as pd


class AdjustmentFactorStore:
    """复权因子事件表存储。原始未复权行情仍是价格底稿。"""

    def __init__(self, root: str | Path = "data_store") -> None:
        base = Path(root)
        self.root = base / "standard" / "adjust_factor"
        self.catalog = base / "aquant.duckdb"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.root / f"{str(symbol).zfill(6)[-6:]}.parquet"

    @staticmethod
    def _atomic_write(path: Path, frame: pd.DataFrame) -> int:
        tmp = path.with_suffix(".tmp.parquet")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)
        return len(frame)

    def save(self, symbol: str, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        incoming = frame.copy()
        incoming["symbol"] = incoming["symbol"].astype(str).str.zfill(6)
        incoming["event_date"] = pd.to_datetime(
            incoming["event_date"], errors="coerce"
        ).dt.normalize()
        incoming = incoming.dropna(subset=["event_date"])

        path = self._path(symbol)
        if path.exists():
            old = pd.read_parquet(path)
            old["event_date"] = pd.to_datetime(
                old["event_date"], errors="coerce"
            ).dt.normalize()
            merged = pd.concat([old, incoming], ignore_index=True, sort=False)
        else:
            merged = incoming

        merged = (
            merged.sort_values("event_date")
            .drop_duplicates(["symbol", "event_date"], keep="last")
            .reset_index(drop=True)
        )
        return self._atomic_write(path, merged)

    def read(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        path = self._path(symbol)
        if not path.exists():
            return pd.DataFrame()
        out = pd.read_parquet(path)
        out["event_date"] = pd.to_datetime(
            out["event_date"], errors="coerce"
        ).dt.normalize()
        if start_date:
            out = out[out["event_date"] >= pd.Timestamp(start_date)]
        if end_date:
            out = out[out["event_date"] <= pd.Timestamp(end_date)]
        return out.sort_values("event_date").reset_index(drop=True)

    def latest_event_date(self, symbol: str) -> pd.Timestamp | None:
        frame = self.read(symbol)
        if frame.empty:
            return None
        return pd.to_datetime(frame["event_date"]).max().normalize()

    def refresh_catalog(self) -> None:
        import duckdb

        files = list(self.root.glob("*.parquet"))
        if not files:
            return

        glob_path = str(self.root / "*.parquet").replace("'", "''")
        con = duckdb.connect(str(self.catalog))
        try:
            con.execute(
                f"CREATE OR REPLACE VIEW adjust_factor AS "
                f"SELECT * FROM read_parquet('{glob_path}', union_by_name=true)"
            )
        finally:
            con.close()
