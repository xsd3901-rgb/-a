from __future__ import annotations

from pathlib import Path

import pandas as pd


class HistoricalUniverseStore:
    """历史股票生命周期资料的本地存储。"""

    def __init__(self, root: str | Path = "data_store") -> None:
        base = Path(root)
        self.root = base / "standard" / "reference"
        self.path = self.root / "security_lifecycle.parquet"
        self.catalog = base / "aquant.duckdb"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, frame: pd.DataFrame) -> int:
        tmp = path.with_suffix(".tmp.parquet")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)
        return len(frame)

    def save(self, frame: pd.DataFrame) -> int:
        if frame is None or frame.empty:
            return 0
        out = frame.copy()
        out["code"] = out["code"].astype(str).str.zfill(6)
        out["name"] = out["name"].astype(str).str.strip()
        for col in ("listing_date", "delisting_date"):
            if col in out.columns:
                out[col] = pd.to_datetime(out[col], errors="coerce").dt.normalize()
        out = out.drop_duplicates("code", keep="last").sort_values("code").reset_index(drop=True)
        return self._atomic_write(self.path, out)

    def read(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        out = pd.read_parquet(self.path)
        for col in ("listing_date", "delisting_date"):
            if col in out.columns:
                out[col] = pd.to_datetime(out[col], errors="coerce").dt.normalize()
        return out

    def universe_on(self, as_of_date: str | pd.Timestamp) -> pd.DataFrame:
        frame = self.read()
        if frame.empty:
            return frame
        as_of = pd.Timestamp(as_of_date).normalize()
        listed = frame["listing_date"].isna() | (frame["listing_date"] <= as_of)
        if "delisting_date" in frame.columns:
            alive = frame["delisting_date"].isna() | (frame["delisting_date"] >= as_of)
        else:
            alive = pd.Series(True, index=frame.index)
        return frame[listed & alive].copy().reset_index(drop=True)

    def refresh_catalog(self) -> None:
        if not self.path.exists():
            return
        import duckdb

        safe_path = str(self.path).replace("'", "''")
        con = duckdb.connect(str(self.catalog))
        try:
            con.execute(
                f"CREATE OR REPLACE VIEW security_lifecycle AS "
                f"SELECT * FROM read_parquet('{safe_path}')"
            )
        finally:
            con.close()
