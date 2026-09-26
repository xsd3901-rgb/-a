from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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


def walk_forward_windows() -> list[TimeWindow]:
    """占位接口：后续由项目配置生成滚动训练/验证窗口。"""
    return []
