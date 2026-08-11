"""Linux-native host observability readers for Sentinel-X."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from sentinel_x.observability.models import (
    CpuTimes,
    HostIdentity,
    HostSnapshot,
    LoadAverage,
    MemoryStats,
)


_PROC_STAT_MAX_BYTES: Final[int] = 1_048_576
_PROC_MEMINFO_MAX_BYTES: Final[int] = 131_072
_LOADAVG_MAX_BYTES: Final[int] = 4_096
_OS_RELEASE_MAX_BYTES: Final[int] = 65_536


class LinuxObservationError(RuntimeError):
    """Base error for Linux host observation failures."""


class LinuxObservationReadError(LinuxObservationError):
    """Raised when a Linux observation source cannot be read safely."""


class LinuxObservationParseError(LinuxObservationError):
    """Raised when a Linux observation source contains invalid data."""


@dataclass(frozen=True, slots=True)
class _ProcStatSnapshot:
    """Parsed subset of /proc/stat used by Phase 1.1."""

    cpu_times: CpuTimes
    logical_cpu_count: int


class LinuxHostReader:
    """Read one typed host snapshot from Linux-native interfaces."""

    def __init__(
        self,
        *,
        proc_root: str | Path = "/proc",
        os_release_path: str | Path = "/etc/os-release",
    ) -> None:
        self._proc_root = Path(proc_root)
        self._os_release_path = Path(os_release_path)

    def read_snapshot(self) -> HostSnapshot:
        """Read host identity, aggregate CPU counters, and load averages."""

        proc_stat = _parse_proc_stat(
            _read_bounded_text(
                self._proc_root / "stat",
                max_bytes=_PROC_STAT_MAX_BYTES,
            )
        )

        load_average = _parse_loadavg(
            _read_bounded_text(
                self._proc_root / "loadavg",
                max_bytes=_LOADAVG_MAX_BYTES,
            )
        )

        os_release = _parse_os_release(
            _read_bounded_text(
                self._os_release_path,
                max_bytes=_OS_RELEASE_MAX_BYTES,
            )
        )

        try:
            uname = os.uname()
        except OSError as exc:
            raise LinuxObservationReadError(
                f"could not read uname information: {exc}"
            ) from exc

        identity = HostIdentity(
            hostname=uname.nodename,
            kernel_name=uname.sysname,
            kernel_release=uname.release,
            kernel_version=uname.version,
            machine=uname.machine,
            logical_cpu_count=proc_stat.logical_cpu_count,
            os_id=os_release.get("ID"),
            os_version_id=os_release.get("VERSION_ID"),
            os_pretty_name=os_release.get("PRETTY_NAME") or os_release.get("NAME"),
        )

        return HostSnapshot(
            captured_at=datetime.now(timezone.utc),
            identity=identity,
            cpu_times=proc_stat.cpu_times,
            load_average=load_average,
        )

    def read_memory_stats(self) -> MemoryStats:
        """Read selected raw memory counters from /proc/meminfo."""

        return _parse_meminfo(
            _read_bounded_text(
                self._proc_root / "meminfo",
                max_bytes=_PROC_MEMINFO_MAX_BYTES,
            )
        )


def _read_bounded_text(path: Path, *, max_bytes: int) -> str:
    """Read a small kernel or OS metadata file with a strict size limit."""

    try:
        with path.open("rb") as file_handle:
            data = file_handle.read(max_bytes + 1)
    except (OSError, ValueError) as exc:
        raise LinuxObservationReadError(f"could not read {path}: {exc}") from exc

    if len(data) > max_bytes:
        raise LinuxObservationReadError(
            f"observation source exceeds {max_bytes} bytes: {path}"
        )

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LinuxObservationReadError(
            f"observation source is not valid UTF-8: {path}"
        ) from exc


def _parse_proc_stat(text: str) -> _ProcStatSnapshot:
    """Parse aggregate CPU counters and logical CPU count from /proc/stat."""

    aggregate_fields: list[str] | None = None
    logical_cpu_count = 0

    for raw_line in text.splitlines():
        fields = raw_line.split()

        if not fields:
            continue

        label = fields[0]

        if label == "cpu":
            aggregate_fields = fields[1:]
            continue

        if label.startswith("cpu") and label[3:].isdigit():
            logical_cpu_count += 1

    if aggregate_fields is None:
        raise LinuxObservationParseError(
            "/proc/stat does not contain an aggregate cpu line"
        )

    if logical_cpu_count == 0:
        raise LinuxObservationParseError(
            "/proc/stat does not contain any logical cpu lines"
        )

    if len(aggregate_fields) < 4:
        raise LinuxObservationParseError(
            "/proc/stat aggregate cpu line has fewer than four counters"
        )

    parsed_values: list[int] = []

    for raw_value in aggregate_fields[:10]:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise LinuxObservationParseError(
                f"invalid /proc/stat cpu counter: {raw_value!r}"
            ) from exc

        if value < 0:
            raise LinuxObservationParseError(
                f"negative /proc/stat cpu counter: {raw_value!r}"
            )

        parsed_values.append(value)

    parsed_values.extend([0] * (10 - len(parsed_values)))

    return _ProcStatSnapshot(
        cpu_times=CpuTimes(
            user=parsed_values[0],
            nice=parsed_values[1],
            system=parsed_values[2],
            idle=parsed_values[3],
            iowait=parsed_values[4],
            irq=parsed_values[5],
            softirq=parsed_values[6],
            steal=parsed_values[7],
            guest=parsed_values[8],
            guest_nice=parsed_values[9],
        ),
        logical_cpu_count=logical_cpu_count,
    )


def _parse_loadavg(text: str) -> LoadAverage:
    """Parse /proc/loadavg into a typed snapshot."""

    fields = text.split()

    if len(fields) < 5:
        raise LinuxObservationParseError(
            "/proc/loadavg must contain at least five fields"
        )

    try:
        one_minute = float(fields[0])
        five_minutes = float(fields[1])
        fifteen_minutes = float(fields[2])
    except ValueError as exc:
        raise LinuxObservationParseError(
            "invalid floating-point load average in /proc/loadavg"
        ) from exc

    task_fields = fields[3].split("/", maxsplit=1)

    if len(task_fields) != 2:
        raise LinuxObservationParseError(
            "invalid runnable/total task field in /proc/loadavg"
        )

    try:
        runnable_tasks = int(task_fields[0])
        total_tasks = int(task_fields[1])
        last_pid = int(fields[4])
    except ValueError as exc:
        raise LinuxObservationParseError(
            "invalid integer task metadata in /proc/loadavg"
        ) from exc

    try:
        return LoadAverage(
            one_minute=one_minute,
            five_minutes=five_minutes,
            fifteen_minutes=fifteen_minutes,
            runnable_tasks=runnable_tasks,
            total_tasks=total_tasks,
            last_pid=last_pid,
        )
    except (TypeError, ValueError) as exc:
        raise LinuxObservationParseError(
            f"invalid /proc/loadavg values: {exc}"
        ) from exc


_MEMINFO_REQUIRED_FIELDS: Final[dict[str, str]] = {
    "MemTotal": "mem_total_kb",
    "MemAvailable": "mem_available_kb",
    "MemFree": "mem_free_kb",
    "Buffers": "buffers_kb",
    "Cached": "cached_kb",
    "SwapTotal": "swap_total_kb",
    "SwapFree": "swap_free_kb",
}

_MEMINFO_OPTIONAL_FIELDS: Final[dict[str, str]] = {
    "SReclaimable": "sreclaimable_kb",
    "Shmem": "shmem_kb",
    "SwapCached": "swap_cached_kb",
}


def _parse_meminfo(text: str) -> MemoryStats:
    """Parse selected kB counters from /proc/meminfo."""

    selected_fields = _MEMINFO_REQUIRED_FIELDS | _MEMINFO_OPTIONAL_FIELDS
    parsed: dict[str, int] = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if ":" not in raw_line:
            continue

        raw_key, raw_value = raw_line.split(":", maxsplit=1)
        key = raw_key.strip()

        if key not in selected_fields:
            continue

        attribute_name = selected_fields[key]

        if attribute_name in parsed:
            raise LinuxObservationParseError(
                f"duplicate /proc/meminfo field on line {line_number}: {key}"
            )

        value_fields = raw_value.split()

        if len(value_fields) != 2 or value_fields[1] != "kB":
            raise LinuxObservationParseError(
                f"invalid /proc/meminfo value for {key}: expected '<integer> kB'"
            )

        try:
            value = int(value_fields[0])
        except ValueError as exc:
            raise LinuxObservationParseError(
                f"invalid /proc/meminfo integer for {key}: {value_fields[0]!r}"
            ) from exc

        if value < 0:
            raise LinuxObservationParseError(
                f"negative /proc/meminfo value for {key}: {value}"
            )

        parsed[attribute_name] = value

    missing = [
        kernel_name
        for kernel_name, attribute_name in _MEMINFO_REQUIRED_FIELDS.items()
        if attribute_name not in parsed
    ]

    if missing:
        names = ", ".join(missing)
        raise LinuxObservationParseError(
            f"/proc/meminfo is missing required field(s): {names}"
        )

    try:
        return MemoryStats(
            mem_total_kb=parsed["mem_total_kb"],
            mem_available_kb=parsed["mem_available_kb"],
            mem_free_kb=parsed["mem_free_kb"],
            buffers_kb=parsed["buffers_kb"],
            cached_kb=parsed["cached_kb"],
            swap_total_kb=parsed["swap_total_kb"],
            swap_free_kb=parsed["swap_free_kb"],
            sreclaimable_kb=parsed.get("sreclaimable_kb"),
            shmem_kb=parsed.get("shmem_kb"),
            swap_cached_kb=parsed.get("swap_cached_kb"),
        )
    except (TypeError, ValueError) as exc:
        raise LinuxObservationParseError(
            f"invalid /proc/meminfo values: {exc}"
        ) from exc


def _parse_os_release(text: str) -> dict[str, str]:
    """Parse shell-style KEY=VALUE assignments from os-release."""

    values: dict[str, str] = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise LinuxObservationParseError(
                f"invalid os-release line {line_number}: missing '='"
            )

        key, raw_value = line.split("=", maxsplit=1)
        key = key.strip()

        if not key or not key.isidentifier():
            raise LinuxObservationParseError(
                f"invalid os-release key on line {line_number}: {key!r}"
            )

        try:
            tokens = shlex.split(
                raw_value,
                comments=False,
                posix=True,
            )
        except ValueError as exc:
            raise LinuxObservationParseError(
                f"invalid os-release value on line {line_number}: {exc}"
            ) from exc

        if len(tokens) > 1:
            raise LinuxObservationParseError(
                f"invalid os-release value on line {line_number}"
            )

        values[key] = tokens[0] if tokens else ""

    return values
