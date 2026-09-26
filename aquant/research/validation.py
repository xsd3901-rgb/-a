from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class TimeWindow:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    gap_days: int = 0


def validate_window(window: TimeWindow) -> None:
    if window.train_start > window.train_end:
        raise ValueError("训练区间起始日期晚于结束日期")
    if window.validation_start > window.validation_end:
        raise ValueError("验证区间起始日期晚于结束日期")
    if window.train_end >= window.validation_start:
        raise ValueError("训练区间与验证区间发生重叠")
    if window.gap_days < 0:
        raise ValueError("gap_days 不能为负数")


def walk_forward_windows(
    trade_dates: Iterable[str | date | pd.Timestamp],
    *,
    train_bars: int = 252,
    validation_bars: int = 63,
    gap_bars: int = 5,
    step_bars: int | None = None,
) -> list[TimeWindow]:
    """按实际交易日生成滚动训练/验证窗口。

    使用固定长度训练窗，验证窗始终位于训练窗之后，中间保留 gap，
    防止标签窗口跨越训练/验证边界。
    """
    dates = (
        pd.Series(pd.to_datetime(list(trade_dates), errors="coerce"))
        .dropna()
        .dt.normalize()
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )

    train_bars = int(train_bars)
    validation_bars = int(validation_bars)
    gap_bars = int(gap_bars)
    step_bars = int(step_bars or validation_bars)

    if train_bars < 20:
        raise ValueError("train_bars 过小")
    if validation_bars < 5:
        raise ValueError("validation_bars 过小")
    if gap_bars < 0:
        raise ValueError("gap_bars 不能为负数")
    if step_bars < 1:
        raise ValueError("step_bars 必须大于 0")

    required = train_bars + gap_bars + validation_bars
    if len(dates) < required:
        return []

    windows: list[TimeWindow] = []
    start_idx = 0

    while True:
        train_start_idx = start_idx
        train_end_idx = train_start_idx + train_bars - 1
        validation_start_idx = train_end_idx + gap_bars + 1
        validation_end_idx = validation_start_idx + validation_bars - 1

        if validation_end_idx >= len(dates):
            break

        train_end = dates.iloc[train_end_idx]
        validation_start = dates.iloc[validation_start_idx]

        window = TimeWindow(
            train_start=dates.iloc[train_start_idx].date(),
            train_end=train_end.date(),
            validation_start=validation_start.date(),
            validation_end=dates.iloc[validation_end_idx].date(),
            gap_days=max(0, (validation_start - train_end).days - 1),
        )
        validate_window(window)
        windows.append(window)
        start_idx += step_bars

    return windows
