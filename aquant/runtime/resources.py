from __future__ import annotations

import os
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
    """先按总内存/当前可用内存选择基础资源档位。"""
    effective = min(total_gb, available_gb * 1.6)

    if available_gb < 3.0 or effective <= 8:
        return ResourceProfile(
            "conservative", 20, 2, 1, 1, 20
        )
    if available_gb < 6.0 or effective <= 16:
        return ResourceProfile(
            "standard", 50, 4, 2, 2, 60
        )
    if available_gb < 12.0 or effective <= 32:
        return ResourceProfile(
            "performance", 120, 6, 4, 4, 150
        )
    return ResourceProfile(
        "high_performance", 240, 8, 6, 6, 300
    )


def apply_cpu_cap(
    profile: ResourceProfile,
    logical_cpus: int | None,
) -> ResourceProfile:
    """在内存档位基础上再按 CPU 数量限流。

    目的不是把 CPU 跑满，而是给 Windows、浏览器和 PyCharm 留余量。
    网络线程可以略高于计算线程，但实际免费接口还会在数据层继续限流。
    """
    cpu = max(1, int(logical_cpus or 1))
    compute_cap = max(1, cpu - 1)
    io_cap = max(2, min(8, cpu * 2))

    return ResourceProfile(
        name=profile.name,
        batch_size=profile.batch_size,
        download_workers=max(
            1,
            min(profile.download_workers, io_cap),
        ),
        compute_workers=max(
            1,
            min(profile.compute_workers, compute_cap),
        ),
        backtest_workers=max(
            1,
            min(profile.backtest_workers, compute_cap),
        ),
        cache_symbols=profile.cache_symbols,
    )


def current_memory_gb() -> tuple[float, float]:
    """返回(总内存GB, 可用内存GB)。"""
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError(
            "需要 psutil 才能启用内存自适应调度"
        ) from exc

    vm = psutil.virtual_memory()
    gb = 1024 ** 3
    return vm.total / gb, vm.available / gb


def current_profile() -> ResourceProfile:
    total, available = current_memory_gb()
    base = choose_profile(total, available)
    return apply_cpu_cap(base, os.cpu_count())
