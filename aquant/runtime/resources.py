from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceProfile:
    name: str
    batch_size: int
    download_workers: int
    compute_workers: int
    backtest_workers: int
    cache_symbols: int


def choose_profile(total_gb: float, available_gb: float) -> ResourceProfile:
    """根据总内存和当前可用内存选择资源档位；运行中可重复调用进行降级/升级。"""
    effective = min(total_gb, available_gb * 1.6)

    if available_gb < 3.0 or effective <= 8:
        return ResourceProfile("conservative", 20, 2, 1, 1, 20)
    if available_gb < 6.0 or effective <= 16:
        return ResourceProfile("standard", 50, 4, 2, 2, 60)
    if available_gb < 12.0 or effective <= 32:
        return ResourceProfile("performance", 120, 6, 4, 4, 150)
    return ResourceProfile("high_performance", 240, 8, 6, 6, 300)


def current_memory_gb() -> tuple[float, float]:
    """返回(总内存GB, 可用内存GB)。psutil不可用时抛出清晰错误。"""
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("需要 psutil 才能启用内存自适应调度") from exc

    vm = psutil.virtual_memory()
    gb = 1024 ** 3
    return vm.total / gb, vm.available / gb


def current_profile() -> ResourceProfile:
    total, available = current_memory_gb()
    return choose_profile(total, available)
