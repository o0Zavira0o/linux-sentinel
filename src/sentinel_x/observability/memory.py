"""Memory and swap metric derivation for Sentinel-X."""

from __future__ import annotations

from datetime import datetime

from sentinel_x.observability.models import (
    MemoryObservation,
    MemoryStats,
    MemoryUtilization,
)


def calculate_memory_utilization(
    stats: MemoryStats,
) -> MemoryUtilization:
    """Derive memory and swap utilization from raw /proc/meminfo counters."""

    if not isinstance(stats, MemoryStats):
        raise TypeError("calculate_memory_utilization() requires MemoryStats")

    used_estimate_kb = stats.mem_total_kb - stats.mem_available_kb
    available_percent = _percent(
        stats.mem_available_kb,
        stats.mem_total_kb,
    )
    used_estimate_percent = _percent(
        used_estimate_kb,
        stats.mem_total_kb,
    )

    swap_used_kb = stats.swap_total_kb - stats.swap_free_kb

    if stats.swap_total_kb == 0:
        swap_used_percent: float | None = None
        swap_configured = False

    else:
        swap_used_percent = _percent(
            swap_used_kb,
            stats.swap_total_kb,
        )
        swap_configured = True

    return MemoryUtilization(
        used_estimate_kb=used_estimate_kb,
        available_percent=available_percent,
        used_estimate_percent=used_estimate_percent,
        swap_used_kb=swap_used_kb,
        swap_used_percent=swap_used_percent,
        swap_configured=swap_configured,
    )


def build_memory_observation(
    stats: MemoryStats,
    *,
    captured_at: datetime,
) -> MemoryObservation:
    """Build one timestamped memory observation from raw kernel evidence."""

    if not isinstance(stats, MemoryStats):
        raise TypeError("build_memory_observation() requires MemoryStats")

    return MemoryObservation(
        captured_at=captured_at,
        stats=stats,
        utilization=calculate_memory_utilization(stats),
    )


def _percent(value: int, total: int) -> float:
    """Convert a non-negative memory quantity to a percentage."""

    return (float(value) / float(total)) * 100.0
