from __future__ import annotations

import json

import pandas as pd


def records(frame: pd.DataFrame | None, limit: int = 100) -> list[dict]:
    if frame is None or frame.empty:
        return []
    return json.loads(
        frame.head(limit).to_json(
            orient="records",
            force_ascii=False,
            date_format="iso",
        )
    )


def safe_dict(value: dict) -> dict:
    return json.loads(
        json.dumps(value, ensure_ascii=False, default=str)
    )
