"""Typed observation models for Sentinel-X."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


def _normalize_required_text(name: str, value: str) -> str:
    """Normalize a required non-empty text field."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{name} must not be empty")

    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    """Normalize an optional text field."""

    if value is None:
        return None

    if not isinstance(value, str):
        raise TypeError("optional text value must be a string or None")

    normalized = value.strip()

    return normalized or None


def _validate_nonnegative_int(name: str, value: int) -> None:
    """Validate a non-negative integer field."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _validate_nonnegative_float(name: str, value: float) -> None:
    """Validate a finite, non-negative floating-point field."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _validate_percentage(name: str, value: float) -> None:
    """Validate a finite percentage in the inclusive 0-100 range."""

    _validate_nonnegative_float(name, value)

    if value > 100.0:
        raise ValueError(f"{name} must not exceed 100")


def _validate_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class HostIdentity:
    """Stable host and operating-system identity fields."""

    hostname: str
    kernel_name: str
    kernel_release: str
    kernel_version: str
    machine: str
    logical_cpu_count: int
    os_id: str | None = None
    os_version_id: str | None = None
    os_pretty_name: str | None = None

    def __post_init__(self) -> None:
        """Normalize and validate host identity values."""

        object.__setattr__(
            self,
            "hostname",
            _normalize_required_text("hostname", self.hostname),
        )
        object.__setattr__(
            self,
            "kernel_name",
            _normalize_required_text("kernel_name", self.kernel_name),
        )
        object.__setattr__(
            self,
            "kernel_release",
            _normalize_required_text("kernel_release", self.kernel_release),
        )
        object.__setattr__(
            self,
            "kernel_version",
            _normalize_required_text("kernel_version", self.kernel_version),
        )
        object.__setattr__(
            self,
            "machine",
            _normalize_required_text("machine", self.machine),
        )

        _validate_nonnegative_int("logical_cpu_count", self.logical_cpu_count)

        if self.logical_cpu_count == 0:
            raise ValueError("logical_cpu_count must be greater than zero")

        object.__setattr__(self, "os_id", _normalize_optional_text(self.os_id))
        object.__setattr__(
            self,
            "os_version_id",
            _normalize_optional_text(self.os_version_id),
        )
        object.__setattr__(
            self,
            "os_pretty_name",
            _normalize_optional_text(self.os_pretty_name),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly host identity mapping."""

        return {
            "hostname": self.hostname,
            "kernel_name": self.kernel_name,
            "kernel_release": self.kernel_release,
            "kernel_version": self.kernel_version,
            "machine": self.machine,
            "logical_cpu_count": self.logical_cpu_count,
            "os_id": self.os_id,
            "os_version_id": self.os_version_id,
            "os_pretty_name": self.os_pretty_name,
        }


@dataclass(frozen=True, slots=True)
class CpuTimes:
    """Aggregate Linux CPU time counters from the first /proc/stat CPU line."""

    user: int
    nice: int
    system: int
    idle: int
    iowait: int = 0
    irq: int = 0
    softirq: int = 0
    steal: int = 0
    guest: int = 0
    guest_nice: int = 0

    def __post_init__(self) -> None:
        """Validate all CPU counters."""

        counters = (
            ("user", self.user),
            ("nice", self.nice),
            ("system", self.system),
            ("idle", self.idle),
            ("iowait", self.iowait),
            ("irq", self.irq),
            ("softirq", self.softirq),
            ("steal", self.steal),
            ("guest", self.guest),
            ("guest_nice", self.guest_nice),
        )

        for name, value in counters:
            _validate_nonnegative_int(name, value)

    def to_dict(self) -> dict[str, int]:
        """Return the raw CPU counter mapping."""

        return {
            "user": self.user,
            "nice": self.nice,
            "system": self.system,
            "idle": self.idle,
            "iowait": self.iowait,
            "irq": self.irq,
            "softirq": self.softirq,
            "steal": self.steal,
            "guest": self.guest,
            "guest_nice": self.guest_nice,
        }


@dataclass(frozen=True, slots=True)
class CpuUtilization:
    """CPU utilization calculated from two aggregate /proc/stat samples.

    user_percent and nice_percent exclude guest and guest_nice
    respectively. Guest execution is reported separately.
    """

    total_ticks: int
    busy_percent: float
    user_percent: float
    nice_percent: float
    system_percent: float
    idle_percent: float
    iowait_percent: float
    irq_percent: float
    softirq_percent: float
    steal_percent: float
    guest_percent: float
    guest_nice_percent: float
    iowait_regressed: bool = False
    guest_accounting_adjusted: bool = False

    def __post_init__(self) -> None:
        """Validate utilization percentages and sampling metadata."""

        _validate_nonnegative_int("total_ticks", self.total_ticks)

        if self.total_ticks == 0:
            raise ValueError("total_ticks must be greater than zero")

        percentages = (
            ("busy_percent", self.busy_percent),
            ("user_percent", self.user_percent),
            ("nice_percent", self.nice_percent),
            ("system_percent", self.system_percent),
            ("idle_percent", self.idle_percent),
            ("iowait_percent", self.iowait_percent),
            ("irq_percent", self.irq_percent),
            ("softirq_percent", self.softirq_percent),
            ("steal_percent", self.steal_percent),
            ("guest_percent", self.guest_percent),
            ("guest_nice_percent", self.guest_nice_percent),
        )

        for name, value in percentages:
            _validate_percentage(name, value)

        category_total = (
            self.user_percent
            + self.nice_percent
            + self.system_percent
            + self.idle_percent
            + self.iowait_percent
            + self.irq_percent
            + self.softirq_percent
            + self.steal_percent
            + self.guest_percent
            + self.guest_nice_percent
        )

        if not math.isclose(category_total, 100.0, abs_tol=1e-6):
            raise ValueError("CPU utilization categories must sum to 100 percent")

        expected_busy = 100.0 - self.idle_percent - self.iowait_percent

        if not math.isclose(self.busy_percent, expected_busy, abs_tol=1e-6):
            raise ValueError(
                "busy_percent must equal 100 - idle_percent - iowait_percent"
            )

        if type(self.iowait_regressed) is not bool:
            raise TypeError("iowait_regressed must be a boolean")

        if type(self.guest_accounting_adjusted) is not bool:
            raise TypeError("guest_accounting_adjusted must be a boolean")

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly utilization mapping."""

        return {
            "total_ticks": self.total_ticks,
            "busy_percent": self.busy_percent,
            "user_percent": self.user_percent,
            "nice_percent": self.nice_percent,
            "system_percent": self.system_percent,
            "idle_percent": self.idle_percent,
            "iowait_percent": self.iowait_percent,
            "irq_percent": self.irq_percent,
            "softirq_percent": self.softirq_percent,
            "steal_percent": self.steal_percent,
            "guest_percent": self.guest_percent,
            "guest_nice_percent": self.guest_nice_percent,
            "iowait_regressed": self.iowait_regressed,
            "guest_accounting_adjusted": self.guest_accounting_adjusted,
        }


@dataclass(frozen=True, slots=True)
class LoadAverage:
    """Linux load-average and runnable-task snapshot."""

    one_minute: float
    five_minutes: float
    fifteen_minutes: float
    runnable_tasks: int
    total_tasks: int
    last_pid: int

    def __post_init__(self) -> None:
        """Validate load-average values."""

        loads = (
            ("one_minute", self.one_minute),
            ("five_minutes", self.five_minutes),
            ("fifteen_minutes", self.fifteen_minutes),
        )

        for name, value in loads:
            _validate_nonnegative_float(name, value)

        _validate_nonnegative_int("runnable_tasks", self.runnable_tasks)
        _validate_nonnegative_int("total_tasks", self.total_tasks)
        _validate_nonnegative_int("last_pid", self.last_pid)

        if self.total_tasks == 0:
            raise ValueError("total_tasks must be greater than zero")

        if self.runnable_tasks > self.total_tasks:
            raise ValueError("runnable_tasks must not exceed total_tasks")

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly load-average mapping."""

        return {
            "one_minute": self.one_minute,
            "five_minutes": self.five_minutes,
            "fifteen_minutes": self.fifteen_minutes,
            "runnable_tasks": self.runnable_tasks,
            "total_tasks": self.total_tasks,
            "last_pid": self.last_pid,
        }


@dataclass(frozen=True, slots=True)
class HostSnapshot:
    """One typed Linux host observation captured at a point in time."""

    captured_at: datetime
    identity: HostIdentity
    cpu_times: CpuTimes
    load_average: LoadAverage

    def __post_init__(self) -> None:
        """Require a timezone-aware capture timestamp."""

        _validate_aware_datetime("captured_at", self.captured_at)

    def to_attributes(self) -> dict[str, object]:
        """Return structured attributes suitable for a SentinelEvent."""

        return {
            "captured_at": self.captured_at.isoformat(),
            "host": self.identity.to_dict(),
            "cpu_times": self.cpu_times.to_dict(),
            "load_average": self.load_average.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class HostObservation:
    """Sampled Linux host observation derived from two raw snapshots."""

    sample_started_at: datetime
    captured_at: datetime
    sample_interval_seconds: float
    identity: HostIdentity
    cpu_times_start: CpuTimes
    cpu_times_end: CpuTimes
    cpu_utilization: CpuUtilization
    load_average: LoadAverage

    def __post_init__(self) -> None:
        """Validate sampled observation metadata."""

        _validate_aware_datetime("sample_started_at", self.sample_started_at)
        _validate_aware_datetime("captured_at", self.captured_at)
        _validate_nonnegative_float(
            "sample_interval_seconds",
            self.sample_interval_seconds,
        )

        if self.sample_interval_seconds == 0:
            raise ValueError("sample_interval_seconds must be greater than zero")

    def to_attributes(self) -> dict[str, object]:
        """Return structured event attributes for the sampled observation."""

        return {
            "sample_started_at": self.sample_started_at.isoformat(),
            "captured_at": self.captured_at.isoformat(),
            "sample_interval_seconds": self.sample_interval_seconds,
            "host": self.identity.to_dict(),
            "cpu_times_start": self.cpu_times_start.to_dict(),
            "cpu_times_end": self.cpu_times_end.to_dict(),
            "cpu_utilization": self.cpu_utilization.to_dict(),
            "load_average": self.load_average.to_dict(),
        }
