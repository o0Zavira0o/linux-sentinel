"""CPU sampling and utilization calculations for Sentinel-X."""

from __future__ import annotations

import math
from typing import Final

from sentinel_x.observability.models import (
    CpuTimes,
    CpuUtilization,
    HostObservation,
    HostSnapshot,
)


MIN_SAMPLE_INTERVAL_SECONDS: Final[float] = 0.1
MAX_SAMPLE_INTERVAL_SECONDS: Final[float] = 60.0


class CpuSamplingError(RuntimeError):
    """Raised when two CPU samples cannot be compared safely."""


def validate_sample_interval(value: object) -> float:
    """Validate the requested one-shot CPU sampling interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CpuSamplingError("sample interval must be a number")

    normalized = float(value)

    if not math.isfinite(normalized):
        raise CpuSamplingError("sample interval must be finite")

    if not MIN_SAMPLE_INTERVAL_SECONDS <= normalized <= MAX_SAMPLE_INTERVAL_SECONDS:
        raise CpuSamplingError(
            "sample interval must be between "
            f"{MIN_SAMPLE_INTERVAL_SECONDS} and "
            f"{MAX_SAMPLE_INTERVAL_SECONDS} seconds"
        )

    return normalized


def calculate_cpu_utilization(
    previous: CpuTimes,
    current: CpuTimes,
) -> CpuUtilization:
    """Calculate CPU percentages from two aggregate /proc/stat samples."""

    user_delta = _strict_delta("user", previous.user, current.user)
    nice_delta = _strict_delta("nice", previous.nice, current.nice)
    system_delta = _strict_delta("system", previous.system, current.system)
    idle_delta = _strict_delta("idle", previous.idle, current.idle)
    irq_delta = _strict_delta("irq", previous.irq, current.irq)
    softirq_delta = _strict_delta("softirq", previous.softirq, current.softirq)
    steal_delta = _strict_delta("steal", previous.steal, current.steal)
    guest_delta_raw = _strict_delta("guest", previous.guest, current.guest)
    guest_nice_delta_raw = _strict_delta(
        "guest_nice",
        previous.guest_nice,
        current.guest_nice,
    )

    iowait_regressed = current.iowait < previous.iowait
    iowait_delta = max(current.iowait - previous.iowait, 0)

    guest_delta = min(guest_delta_raw, user_delta)
    guest_nice_delta = min(guest_nice_delta_raw, nice_delta)

    guest_accounting_adjusted = (
        guest_delta != guest_delta_raw or guest_nice_delta != guest_nice_delta_raw
    )

    host_user_delta = user_delta - guest_delta
    host_nice_delta = nice_delta - guest_nice_delta

    total_ticks = (
        user_delta
        + nice_delta
        + system_delta
        + idle_delta
        + iowait_delta
        + irq_delta
        + softirq_delta
        + steal_delta
    )

    if total_ticks == 0:
        raise CpuSamplingError("CPU counters did not advance between the two samples")

    busy_ticks = total_ticks - idle_delta - iowait_delta

    return CpuUtilization(
        total_ticks=total_ticks,
        busy_percent=_percent(busy_ticks, total_ticks),
        user_percent=_percent(host_user_delta, total_ticks),
        nice_percent=_percent(host_nice_delta, total_ticks),
        system_percent=_percent(system_delta, total_ticks),
        idle_percent=_percent(idle_delta, total_ticks),
        iowait_percent=_percent(iowait_delta, total_ticks),
        irq_percent=_percent(irq_delta, total_ticks),
        softirq_percent=_percent(softirq_delta, total_ticks),
        steal_percent=_percent(steal_delta, total_ticks),
        guest_percent=_percent(guest_delta, total_ticks),
        guest_nice_percent=_percent(guest_nice_delta, total_ticks),
        iowait_regressed=iowait_regressed,
        guest_accounting_adjusted=guest_accounting_adjusted,
    )


def build_host_observation(
    previous: HostSnapshot,
    current: HostSnapshot,
    *,
    sample_interval_seconds: object,
) -> HostObservation:
    """Build a comparable sampled host observation."""

    interval = validate_sample_interval(sample_interval_seconds)

    if previous.identity.hostname != current.identity.hostname:
        raise CpuSamplingError(
            "hostname changed between CPU samples; samples are not comparable"
        )

    if previous.identity.logical_cpu_count != current.identity.logical_cpu_count:
        raise CpuSamplingError(
            "logical CPU count changed between samples; samples are not comparable"
        )

    utilization = calculate_cpu_utilization(
        previous.cpu_times,
        current.cpu_times,
    )

    return HostObservation(
        sample_started_at=previous.captured_at,
        captured_at=current.captured_at,
        sample_interval_seconds=interval,
        identity=current.identity,
        cpu_times_start=previous.cpu_times,
        cpu_times_end=current.cpu_times,
        cpu_utilization=utilization,
        load_average=current.load_average,
    )


def _strict_delta(
    name: str,
    previous: int,
    current: int,
) -> int:
    """Return a monotonic counter delta or reject a regression."""

    if current < previous:
        raise CpuSamplingError(
            f"CPU counter {name!r} regressed from {previous} to {current}"
        )

    return current - previous


def _percent(value: int, total: int) -> float:
    """Convert an integer counter delta to a percentage."""

    return (float(value) / float(total)) * 100.0
