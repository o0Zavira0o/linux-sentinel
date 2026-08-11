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

        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")

    def to_attributes(self) -> dict[str, object]:
        """Return structured attributes suitable for a SentinelEvent."""

        return {
            "captured_at": self.captured_at.isoformat(),
            "host": self.identity.to_dict(),
            "cpu_times": self.cpu_times.to_dict(),
            "load_average": self.load_average.to_dict(),
        }
