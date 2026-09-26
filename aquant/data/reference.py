from __future__ import annotations

from pathlib import Path

import pandas as pd


class ReferenceStore:
    """股票基础库与交易日历的轻量本地存储。"""

    def __init__(self, root: str | Path = "data_store") -> None:
        self.root = Path(root) / "standard" / "reference"
        self.root.mkdir(parents=True, exist_ok=True)
        self.security_path = self.root / "security_master.parquet"
        self.calendar_path = self.root / "trade_calendar.parquet"

    @staticmethod
    def _write(path: Path, frame: pd.DataFrame) -> int:
        tmp = path.with_suffix(".tmp.parquet")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)
        return len(frame)

    def save_security_master(self, frame: pd.DataFrame) -> int:
        out = frame.copy()
        out["code"] = out["code"].astype(str).str.zfill(6)
        out["name"] = out["name"].astype(str).str.strip()
        out = out.drop_duplicates("code", keep="last").sort_values("code").reset_index(drop=True)
        return self._write(self.security_path, out)

    def read_security_master(self) -> pd.DataFrame:
        if not self.security_path.exists():
            return pd.DataFrame(columns=["code", "name"])
        return pd.read_parquet(self.security_path)

    def save_trade_calendar(self, frame: pd.DataFrame) -> int:
        out = frame.copy()
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
        out = out.dropna(subset=["trade_date"])
        if "is_open" not in out.columns:
            out["is_open"] = True
        out["is_open"] = out["is_open"].astype(bool)
        out = out.drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)
        return self._write(self.calendar_path, out)

    def read_trade_calendar(self) -> pd.DataFrame:
        if not self.calendar_path.exists():
            return pd.DataFrame(columns=["trade_date", "is_open"])
        out = pd.read_parquet(self.calendar_path)
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()
        return out

    def latest_trade_date(self, on_or_before: str | pd.Timestamp | None = None) -> pd.Timestamp | None:
        calendar = self.read_trade_calendar()
        if calendar.empty:
            return None
        open_days = calendar[calendar["is_open"]].copy()
        if on_or_before is not None:
            open_days = open_days[open_days["trade_date"] <= pd.Timestamp(on_or_before).normalize()]
        if open_days.empty:
            return None
        return pd.to_datetime(open_days["trade_date"]).max().normalize()

    def refresh_catalog(self, catalog_path: str | Path = "data_store/aquant.duckdb") -> None:
        import duckdb

        con = duckdb.connect(str(catalog_path))
        try:
            for name, path in (
                ("security_master", self.security_path),
                ("trade_calendar", self.calendar_path),
            ):
                if path.exists():
                    safe_path = str(path).replace("'", "''")
                    con.execute(
                        f"CREATE OR REPLACE VIEW {name} AS "
                        f"SELECT * FROM read_parquet('{safe_path}')"
                    )
        finally:
            con.close()
