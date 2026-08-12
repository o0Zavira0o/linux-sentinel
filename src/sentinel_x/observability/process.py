"""Linux process discovery and raw process telemetry for Sentinel-X."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final
from uuid import UUID


_PROCESS_STAT_MAX_BYTES: Final[int] = 65_536
_PROCESS_STATUS_MAX_BYTES: Final[int] = 262_144
_PROCESS_IO_MAX_BYTES: Final[int] = 65_536
_BOOT_ID_MAX_BYTES: Final[int] = 128


class ProcessObservationError(RuntimeError):
    """Base error for Linux process observability failures."""


class ProcessDiscoveryError(ProcessObservationError):
    """Raised when the process set or host process identity cannot be discovered."""


class ProcessReadError(ProcessObservationError):
    """Raised when one process procfs file cannot be read safely."""


class ProcessParseError(ProcessObservationError):
    """Raised when one process procfs file contains malformed data."""


class ProcessProbeStage(str, Enum):
    """Stage at which one process probe failed."""

    STAT_INITIAL = "stat_initial"
    STATUS = "status"
    IO = "io"
    STAT_VERIFY = "stat_verify"
    IDENTITY_VERIFY = "identity_verify"


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Strong per-boot Linux process identity."""

    boot_id: str
    pid: int
    start_time_ticks: int
    comm: str

    def __post_init__(self) -> None:
        """Validate process identity fields."""

        _require_uuid("boot_id", self.boot_id)
        _require_positive_int("pid", self.pid)
        _require_nonnegative_int("start_time_ticks", self.start_time_ticks)
        _require_process_comm(self.comm)

    @property
    def identity_key(self) -> str:
        """Return a boot-scoped PID/start-time identity key."""

        return f"{self.boot_id}:{self.pid}:{self.start_time_ticks}"

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly process identity."""

        return {
            "boot_id": self.boot_id,
            "pid": self.pid,
            "start_time_ticks": self.start_time_ticks,
            "comm": self.comm,
            "identity_key": self.identity_key,
        }


@dataclass(frozen=True, slots=True)
class ProcessStat:
    """Selected raw fields from /proc/<pid>/stat."""

    pid: int
    comm: str
    state: str
    ppid: int
    process_group_id: int
    session_id: int
    flags: int
    minor_faults: int
    child_minor_faults: int
    major_faults: int
    child_major_faults: int
    user_time_ticks: int
    system_time_ticks: int
    child_user_time_ticks: int
    child_system_time_ticks: int
    priority: int
    nice: int
    thread_count: int
    start_time_ticks: int
    virtual_memory_bytes: int
    resident_pages: int

    def __post_init__(self) -> None:
        """Validate raw stat fields."""

        _require_positive_int("pid", self.pid)
        _require_process_comm(self.comm)
        _require_process_state(self.state)
        _require_nonnegative_int("ppid", self.ppid)
        _require_nonnegative_int("process_group_id", self.process_group_id)
        _require_nonnegative_int("session_id", self.session_id)
        _require_nonnegative_int("flags", self.flags)
        _require_nonnegative_int("minor_faults", self.minor_faults)
        _require_nonnegative_int("child_minor_faults", self.child_minor_faults)
        _require_nonnegative_int("major_faults", self.major_faults)
        _require_nonnegative_int("child_major_faults", self.child_major_faults)
        _require_nonnegative_int("user_time_ticks", self.user_time_ticks)
        _require_nonnegative_int("system_time_ticks", self.system_time_ticks)
        _require_int("child_user_time_ticks", self.child_user_time_ticks)
        _require_int("child_system_time_ticks", self.child_system_time_ticks)
        _require_int("priority", self.priority)
        _require_int("nice", self.nice)
        _require_nonnegative_int("thread_count", self.thread_count)
        _require_nonnegative_int("start_time_ticks", self.start_time_ticks)
        _require_nonnegative_int("virtual_memory_bytes", self.virtual_memory_bytes)
        _require_int("resident_pages", self.resident_pages)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly raw stat evidence."""

        return {
            "pid": self.pid,
            "comm": self.comm,
            "state": self.state,
            "ppid": self.ppid,
            "process_group_id": self.process_group_id,
            "session_id": self.session_id,
            "flags": self.flags,
            "minor_faults": self.minor_faults,
            "child_minor_faults": self.child_minor_faults,
            "major_faults": self.major_faults,
            "child_major_faults": self.child_major_faults,
            "user_time_ticks": self.user_time_ticks,
            "system_time_ticks": self.system_time_ticks,
            "child_user_time_ticks": self.child_user_time_ticks,
            "child_system_time_ticks": self.child_system_time_ticks,
            "priority": self.priority,
            "nice": self.nice,
            "thread_count": self.thread_count,
            "start_time_ticks": self.start_time_ticks,
            "virtual_memory_bytes": self.virtual_memory_bytes,
            "resident_pages": self.resident_pages,
        }


@dataclass(frozen=True, slots=True)
class ProcessStatus:
    """Selected raw fields from /proc/<pid>/status."""

    name: str
    state: str
    tgid: int
    pid: int
    ppid: int
    real_uid: int
    effective_uid: int
    saved_uid: int
    filesystem_uid: int
    real_gid: int
    effective_gid: int
    saved_gid: int
    filesystem_gid: int
    thread_count: int
    namespace_pids: tuple[int, ...] | None
    kernel_thread: bool | None
    vm_size_kb: int | None
    vm_rss_kb: int | None
    rss_anon_kb: int | None
    rss_file_kb: int | None
    rss_shmem_kb: int | None
    vm_swap_kb: int | None
    voluntary_context_switches: int | None
    nonvoluntary_context_switches: int | None

    def __post_init__(self) -> None:
        """Validate selected status fields."""

        _require_process_comm(self.name)
        _require_process_state(self.state)
        _require_positive_int("tgid", self.tgid)
        _require_positive_int("pid", self.pid)
        _require_nonnegative_int("ppid", self.ppid)

        for field_name, field_value in (
            ("real_uid", self.real_uid),
            ("effective_uid", self.effective_uid),
            ("saved_uid", self.saved_uid),
            ("filesystem_uid", self.filesystem_uid),
            ("real_gid", self.real_gid),
            ("effective_gid", self.effective_gid),
            ("saved_gid", self.saved_gid),
            ("filesystem_gid", self.filesystem_gid),
            ("thread_count", self.thread_count),
        ):
            _require_nonnegative_int(field_name, field_value)

        if self.namespace_pids is not None:
            if not self.namespace_pids:
                raise ValueError("namespace_pids must not be empty when present")

            for namespace_pid in self.namespace_pids:
                _require_positive_int("namespace pid", namespace_pid)

        if self.kernel_thread is not None and not isinstance(self.kernel_thread, bool):
            raise TypeError("kernel_thread must be a bool or None")

        for optional_name, optional_value in (
            ("vm_size_kb", self.vm_size_kb),
            ("vm_rss_kb", self.vm_rss_kb),
            ("rss_anon_kb", self.rss_anon_kb),
            ("rss_file_kb", self.rss_file_kb),
            ("rss_shmem_kb", self.rss_shmem_kb),
            ("vm_swap_kb", self.vm_swap_kb),
            ("voluntary_context_switches", self.voluntary_context_switches),
            ("nonvoluntary_context_switches", self.nonvoluntary_context_switches),
        ):
            if optional_value is not None:
                _require_nonnegative_int(optional_name, optional_value)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly status evidence."""

        return {
            "name": self.name,
            "state": self.state,
            "tgid": self.tgid,
            "pid": self.pid,
            "ppid": self.ppid,
            "uids": {
                "real": self.real_uid,
                "effective": self.effective_uid,
                "saved": self.saved_uid,
                "filesystem": self.filesystem_uid,
            },
            "gids": {
                "real": self.real_gid,
                "effective": self.effective_gid,
                "saved": self.saved_gid,
                "filesystem": self.filesystem_gid,
            },
            "thread_count": self.thread_count,
            "namespace_pids": (
                None if self.namespace_pids is None else list(self.namespace_pids)
            ),
            "kernel_thread": self.kernel_thread,
            "memory_kb": {
                "vm_size": self.vm_size_kb,
                "vm_rss": self.vm_rss_kb,
                "rss_anon": self.rss_anon_kb,
                "rss_file": self.rss_file_kb,
                "rss_shmem": self.rss_shmem_kb,
                "vm_swap": self.vm_swap_kb,
            },
            "context_switches": {
                "voluntary": self.voluntary_context_switches,
                "nonvoluntary": self.nonvoluntary_context_switches,
            },
        }


@dataclass(frozen=True, slots=True)
class ProcessIo:
    """Raw I/O accounting fields from /proc/<pid>/io."""

    rchar: int
    wchar: int
    syscr: int
    syscw: int
    read_bytes: int
    write_bytes: int
    cancelled_write_bytes: int

    def __post_init__(self) -> None:
        """Validate process I/O counters."""

        for field_name, field_value in (
            ("rchar", self.rchar),
            ("wchar", self.wchar),
            ("syscr", self.syscr),
            ("syscw", self.syscw),
            ("read_bytes", self.read_bytes),
            ("write_bytes", self.write_bytes),
            ("cancelled_write_bytes", self.cancelled_write_bytes),
        ):
            _require_nonnegative_int(field_name, field_value)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly process I/O evidence."""

        return {
            "rchar": self.rchar,
            "wchar": self.wchar,
            "syscr": self.syscr,
            "syscw": self.syscw,
            "read_bytes": self.read_bytes,
            "write_bytes": self.write_bytes,
            "cancelled_write_bytes": self.cancelled_write_bytes,
        }


@dataclass(frozen=True, slots=True)
class ProcessProbeFailure:
    """One isolated process-probe failure."""

    pid: int
    stage: ProcessProbeStage
    error_type: str
    error_message: str

    def __post_init__(self) -> None:
        """Validate probe failure metadata."""

        _require_positive_int("pid", self.pid)

        if not isinstance(self.stage, ProcessProbeStage):
            raise TypeError("stage must be a ProcessProbeStage")

        _require_nonempty_text("error_type", self.error_type)
        _require_nonempty_text("error_message", self.error_message)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly probe failure evidence."""

        return {
            "pid": self.pid,
            "stage": self.stage.value,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    """One identity-verified process record."""

    identity: ProcessIdentity
    stat: ProcessStat
    status: ProcessStatus | None
    io: ProcessIo | None
    partial_failures: tuple[ProcessProbeFailure, ...]

    def __post_init__(self) -> None:
        """Validate process record consistency."""

        if not isinstance(self.identity, ProcessIdentity):
            raise TypeError("identity must be a ProcessIdentity")

        if not isinstance(self.stat, ProcessStat):
            raise TypeError("stat must be a ProcessStat")

        if self.status is not None and not isinstance(self.status, ProcessStatus):
            raise TypeError("status must be a ProcessStatus or None")

        if self.io is not None and not isinstance(self.io, ProcessIo):
            raise TypeError("io must be a ProcessIo or None")

        if self.identity.pid != self.stat.pid:
            raise ValueError("identity and stat PID must match")

        if self.identity.start_time_ticks != self.stat.start_time_ticks:
            raise ValueError("identity and stat start time must match")

        if self.status is not None and self.status.pid != self.identity.pid:
            raise ValueError("status PID must match process identity")

        for failure in self.partial_failures:
            if not isinstance(failure, ProcessProbeFailure):
                raise TypeError(
                    "partial_failures must contain ProcessProbeFailure objects"
                )

            if failure.pid != self.identity.pid:
                raise ValueError("partial failure PID must match process identity")

            if failure.stage not in (ProcessProbeStage.STATUS, ProcessProbeStage.IO):
                raise ValueError("partial failures may only describe status or io")

    @property
    def status_available(self) -> bool:
        """Return whether /proc/<pid>/status was collected successfully."""

        return self.status is not None

    @property
    def io_available(self) -> bool:
        """Return whether /proc/<pid>/io was collected successfully."""

        return self.io is not None

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly process evidence."""

        return {
            "identity": self.identity.to_dict(),
            "stat": self.stat.to_dict(),
            "status": None if self.status is None else self.status.to_dict(),
            "io": None if self.io is None else self.io.to_dict(),
            "partial_failures": [
                failure.to_dict() for failure in self.partial_failures
            ],
        }


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    """One timestamped raw process snapshot."""

    captured_at: datetime
    boot_id: str
    clock_ticks_per_second: int
    page_size_bytes: int
    discovered_pid_count: int
    processes: tuple[ProcessRecord, ...]
    dropped_failures: tuple[ProcessProbeFailure, ...]

    def __post_init__(self) -> None:
        """Validate snapshot metadata and process uniqueness."""

        _require_aware_datetime("captured_at", self.captured_at)
        _require_uuid("boot_id", self.boot_id)
        _require_positive_int("clock_ticks_per_second", self.clock_ticks_per_second)
        _require_positive_int("page_size_bytes", self.page_size_bytes)
        _require_nonnegative_int("discovered_pid_count", self.discovered_pid_count)

        pids: list[int] = []
        identity_keys: list[str] = []

        for process in self.processes:
            if not isinstance(process, ProcessRecord):
                raise TypeError("processes must contain ProcessRecord objects")

            if process.identity.boot_id != self.boot_id:
                raise ValueError("process identity boot_id must match snapshot boot_id")

            pids.append(process.identity.pid)
            identity_keys.append(process.identity.identity_key)

        if len(set(pids)) != len(pids):
            raise ValueError("process snapshot contains duplicate PIDs")

        if len(set(identity_keys)) != len(identity_keys):
            raise ValueError("process snapshot contains duplicate process identities")

        for failure in self.dropped_failures:
            if not isinstance(failure, ProcessProbeFailure):
                raise TypeError(
                    "dropped_failures must contain ProcessProbeFailure objects"
                )

            if failure.stage in (ProcessProbeStage.STATUS, ProcessProbeStage.IO):
                raise ValueError(
                    "dropped failures must describe identity-critical stages"
                )

        if self.discovered_pid_count < len(self.processes):
            raise ValueError(
                "discovered_pid_count cannot be smaller than process_count"
            )

        if len(self.dropped_failures) != self.dropped_process_count:
            raise ValueError("dropped_failures must account for every dropped process")

        dropped_pids = [failure.pid for failure in self.dropped_failures]

        if len(set(dropped_pids)) != len(dropped_pids):
            raise ValueError("dropped_failures must not repeat a PID")

        if set(dropped_pids).intersection(pids):
            raise ValueError("a PID cannot be both retained and dropped")

    @property
    def process_count(self) -> int:
        """Return the number of identity-verified process records."""

        return len(self.processes)

    @property
    def dropped_process_count(self) -> int:
        """Return discovered PIDs that could not produce a verified record."""

        return self.discovered_pid_count - self.process_count

    @property
    def partial_failure_count(self) -> int:
        """Return the number of nonfatal per-process probe failures."""

        return sum(len(process.partial_failures) for process in self.processes)

    @property
    def status_unavailable_count(self) -> int:
        """Return records without status evidence."""

        return sum(not process.status_available for process in self.processes)

    @property
    def io_unavailable_count(self) -> int:
        """Return records without I/O evidence."""

        return sum(not process.io_available for process in self.processes)

    @property
    def zombie_count(self) -> int:
        """Return processes observed in zombie state."""

        return sum(process.stat.state == "Z" for process in self.processes)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly raw process snapshot evidence."""

        return {
            "captured_at": self.captured_at.isoformat(),
            "boot_id": self.boot_id,
            "clock_ticks_per_second": self.clock_ticks_per_second,
            "page_size_bytes": self.page_size_bytes,
            "summary": {
                "discovered_pid_count": self.discovered_pid_count,
                "process_count": self.process_count,
                "dropped_process_count": self.dropped_process_count,
                "partial_failure_count": self.partial_failure_count,
                "status_unavailable_count": self.status_unavailable_count,
                "io_unavailable_count": self.io_unavailable_count,
                "zombie_count": self.zombie_count,
            },
            "processes": [process.to_dict() for process in self.processes],
            "dropped_failures": [
                failure.to_dict() for failure in self.dropped_failures
            ],
        }


class LinuxProcessReader:
    """Discover Linux processes and collect identity-safe raw procfs evidence."""

    def __init__(
        self,
        *,
        proc_root: str | Path = "/proc",
        boot_id_path: str | Path = "/proc/sys/kernel/random/boot_id",
        clock_ticks_per_second: int | None = None,
        page_size_bytes: int | None = None,
    ) -> None:
        self._proc_root = Path(proc_root)
        self._boot_id_path = Path(boot_id_path)
        self._clock_ticks_per_second = _resolve_positive_sysconf(
            "SC_CLK_TCK",
            override=clock_ticks_per_second,
        )
        self._page_size_bytes = _resolve_positive_sysconf(
            "SC_PAGE_SIZE",
            override=page_size_bytes,
        )

    def read_snapshot(self) -> ProcessSnapshot:
        """Read one identity-verified process snapshot."""

        boot_id = _read_boot_id(self._boot_id_path)
        pids = _discover_pids(self._proc_root)

        processes: list[ProcessRecord] = []
        dropped_failures: list[ProcessProbeFailure] = []

        for pid in pids:
            record, failure = self._read_process(pid, boot_id=boot_id)

            if record is not None:
                processes.append(record)

            if failure is not None:
                dropped_failures.append(failure)

        try:
            return ProcessSnapshot(
                captured_at=datetime.now(timezone.utc),
                boot_id=boot_id,
                clock_ticks_per_second=self._clock_ticks_per_second,
                page_size_bytes=self._page_size_bytes,
                discovered_pid_count=len(pids),
                processes=tuple(processes),
                dropped_failures=tuple(dropped_failures),
            )

        except (TypeError, ValueError) as exc:
            raise ProcessParseError(f"invalid process snapshot: {exc}") from exc

    def _read_process(
        self,
        pid: int,
        *,
        boot_id: str,
    ) -> tuple[ProcessRecord | None, ProcessProbeFailure | None]:
        """Read one process while guarding against PID reuse during the probe."""

        process_root = self._proc_root / str(pid)

        try:
            initial_stat = _read_process_stat(process_root / "stat", expected_pid=pid)

        except (ProcessReadError, ProcessParseError) as exc:
            return None, _failure_from_exception(
                pid,
                ProcessProbeStage.STAT_INITIAL,
                exc,
            )

        partial_failures: list[ProcessProbeFailure] = []
        status: ProcessStatus | None = None
        io: ProcessIo | None = None

        try:
            status = _read_process_status(process_root / "status", expected_pid=pid)

        except (ProcessReadError, ProcessParseError) as exc:
            partial_failures.append(
                _failure_from_exception(pid, ProcessProbeStage.STATUS, exc)
            )

        try:
            io = _read_process_io(process_root / "io")

        except (ProcessReadError, ProcessParseError) as exc:
            partial_failures.append(
                _failure_from_exception(pid, ProcessProbeStage.IO, exc)
            )

        try:
            verified_stat = _read_process_stat(process_root / "stat", expected_pid=pid)

        except (ProcessReadError, ProcessParseError) as exc:
            return None, _failure_from_exception(
                pid,
                ProcessProbeStage.STAT_VERIFY,
                exc,
            )

        if verified_stat.start_time_ticks != initial_stat.start_time_ticks:
            return None, ProcessProbeFailure(
                pid=pid,
                stage=ProcessProbeStage.IDENTITY_VERIFY,
                error_type="ProcessIdentityChanged",
                error_message=(
                    "process start_time_ticks changed during the procfs probe; "
                    "the PID may have been reused"
                ),
            )

        identity = ProcessIdentity(
            boot_id=boot_id,
            pid=initial_stat.pid,
            start_time_ticks=initial_stat.start_time_ticks,
            comm=initial_stat.comm,
        )

        try:
            return (
                ProcessRecord(
                    identity=identity,
                    stat=initial_stat,
                    status=status,
                    io=io,
                    partial_failures=tuple(partial_failures),
                ),
                None,
            )

        except (TypeError, ValueError) as exc:
            return None, ProcessProbeFailure(
                pid=pid,
                stage=ProcessProbeStage.IDENTITY_VERIFY,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )


def _discover_pids(proc_root: Path) -> tuple[int, ...]:
    """Return visible numeric process IDs from procfs."""

    try:
        entries = tuple(proc_root.iterdir())

    except OSError as exc:
        raise ProcessDiscoveryError(f"could not enumerate {proc_root}: {exc}") from exc

    pids: list[int] = []

    for entry in entries:
        name = entry.name

        if not name.isascii() or not name.isdecimal():
            continue

        pid = int(name, 10)

        if pid > 0:
            pids.append(pid)

    pids.sort()

    return tuple(pids)


def _read_boot_id(path: Path) -> str:
    """Read the kernel boot UUID used to scope process identities."""

    try:
        raw_value = _read_bounded_text(path, max_bytes=_BOOT_ID_MAX_BYTES).strip()

    except ProcessReadError as exc:
        raise ProcessDiscoveryError(f"could not read boot_id: {exc}") from exc

    try:
        parsed = UUID(raw_value)

    except (ValueError, AttributeError) as exc:
        raise ProcessDiscoveryError(f"invalid boot_id value: {raw_value!r}") from exc

    return str(parsed)


def _read_process_stat(path: Path, *, expected_pid: int) -> ProcessStat:
    """Read and parse one /proc/<pid>/stat file."""

    text = _read_bounded_text(path, max_bytes=_PROCESS_STAT_MAX_BYTES)

    try:
        stat = _parse_process_stat(text)

    except ProcessParseError:
        raise

    if stat.pid != expected_pid:
        raise ProcessParseError(
            f"stat PID {stat.pid} does not match procfs directory PID {expected_pid}"
        )

    return stat


def _parse_process_stat(text: str) -> ProcessStat:
    """Parse selected stable fields from /proc/<pid>/stat."""

    raw = text.strip()

    if not raw:
        raise ProcessParseError("process stat is empty")

    open_paren = raw.find("(")
    close_paren = raw.rfind(")")

    if open_paren <= 0 or close_paren <= open_paren:
        raise ProcessParseError("process stat does not contain a valid comm field")

    pid_text = raw[:open_paren].strip()
    comm = raw[open_paren + 1 : close_paren]
    tail = raw[close_paren + 1 :].strip().split()

    if len(tail) < 22:
        raise ProcessParseError("process stat has fewer than 24 fields")

    pid = _parse_positive_int(pid_text, field_name="pid")
    state = tail[0]

    try:
        _require_process_comm(comm)
        _require_process_state(state)

    except (TypeError, ValueError) as exc:
        raise ProcessParseError(f"invalid process stat identity field: {exc}") from exc

    return ProcessStat(
        pid=pid,
        comm=comm,
        state=state,
        ppid=_parse_nonnegative_int(tail[1], field_name="ppid"),
        process_group_id=_parse_nonnegative_int(
            tail[2],
            field_name="process group id",
        ),
        session_id=_parse_nonnegative_int(tail[3], field_name="session id"),
        flags=_parse_nonnegative_int(tail[6], field_name="flags"),
        minor_faults=_parse_nonnegative_int(tail[7], field_name="minor faults"),
        child_minor_faults=_parse_nonnegative_int(
            tail[8],
            field_name="child minor faults",
        ),
        major_faults=_parse_nonnegative_int(tail[9], field_name="major faults"),
        child_major_faults=_parse_nonnegative_int(
            tail[10],
            field_name="child major faults",
        ),
        user_time_ticks=_parse_nonnegative_int(
            tail[11],
            field_name="user time ticks",
        ),
        system_time_ticks=_parse_nonnegative_int(
            tail[12],
            field_name="system time ticks",
        ),
        child_user_time_ticks=_parse_int(tail[13], field_name="child user time ticks"),
        child_system_time_ticks=_parse_int(
            tail[14],
            field_name="child system time ticks",
        ),
        priority=_parse_int(tail[15], field_name="priority"),
        nice=_parse_int(tail[16], field_name="nice"),
        thread_count=_parse_nonnegative_int(tail[17], field_name="thread count"),
        start_time_ticks=_parse_nonnegative_int(
            tail[19],
            field_name="start time ticks",
        ),
        virtual_memory_bytes=_parse_nonnegative_int(
            tail[20],
            field_name="virtual memory bytes",
        ),
        resident_pages=_parse_int(tail[21], field_name="resident pages"),
    )


def _read_process_status(path: Path, *, expected_pid: int) -> ProcessStatus:
    """Read and parse selected /proc/<pid>/status fields."""

    status = _parse_process_status(
        _read_bounded_text(path, max_bytes=_PROCESS_STATUS_MAX_BYTES)
    )

    if status.pid != expected_pid:
        raise ProcessParseError(
            f"status PID {status.pid} does not match "
            f"procfs directory PID {expected_pid}"
        )

    if status.tgid != expected_pid:
        raise ProcessParseError(
            f"status Tgid {status.tgid} does not match "
            f"procfs directory PID {expected_pid}"
        )

    return status


def _parse_process_status(text: str) -> ProcessStatus:
    """Parse selected identity, memory, and scheduling status fields."""

    selected_names = {
        "Name",
        "State",
        "Tgid",
        "Pid",
        "PPid",
        "Uid",
        "Gid",
        "NSpid",
        "Kthread",
        "Threads",
        "VmSize",
        "VmRSS",
        "RssAnon",
        "RssFile",
        "RssShmem",
        "VmSwap",
        "voluntary_ctxt_switches",
        "nonvoluntary_ctxt_switches",
    }
    values: dict[str, str] = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        field_name, separator, raw_value = raw_line.partition(":")

        if not separator or field_name not in selected_names:
            continue

        if field_name in values:
            raise ProcessParseError(
                f"duplicate {field_name} field on process status line {line_number}"
            )

        values[field_name] = raw_value.strip()

    required_names = (
        "Name",
        "State",
        "Tgid",
        "Pid",
        "PPid",
        "Uid",
        "Gid",
        "Threads",
    )
    missing = [field_name for field_name in required_names if field_name not in values]

    if missing:
        raise ProcessParseError(
            "process status is missing required fields: " + ", ".join(missing)
        )

    name = values["Name"]
    state_parts = values["State"].split(maxsplit=1)

    if not state_parts:
        raise ProcessParseError("process status State field is empty")

    state = state_parts[0]
    uid_values = _parse_fixed_nonnegative_ints(values["Uid"], field_name="Uid", count=4)
    gid_values = _parse_fixed_nonnegative_ints(values["Gid"], field_name="Gid", count=4)

    namespace_pids = (
        None
        if "NSpid" not in values
        else _parse_nonempty_positive_ints(values["NSpid"], field_name="NSpid")
    )
    kernel_thread = (
        None
        if "Kthread" not in values
        else _parse_zero_one_bool(values["Kthread"], field_name="Kthread")
    )

    try:
        return ProcessStatus(
            name=name,
            state=state,
            tgid=_parse_positive_int(values["Tgid"], field_name="Tgid"),
            pid=_parse_positive_int(values["Pid"], field_name="Pid"),
            ppid=_parse_nonnegative_int(values["PPid"], field_name="PPid"),
            real_uid=uid_values[0],
            effective_uid=uid_values[1],
            saved_uid=uid_values[2],
            filesystem_uid=uid_values[3],
            real_gid=gid_values[0],
            effective_gid=gid_values[1],
            saved_gid=gid_values[2],
            filesystem_gid=gid_values[3],
            thread_count=_parse_nonnegative_int(
                values["Threads"],
                field_name="Threads",
            ),
            namespace_pids=namespace_pids,
            kernel_thread=kernel_thread,
            vm_size_kb=_parse_optional_kb_field(values, "VmSize"),
            vm_rss_kb=_parse_optional_kb_field(values, "VmRSS"),
            rss_anon_kb=_parse_optional_kb_field(values, "RssAnon"),
            rss_file_kb=_parse_optional_kb_field(values, "RssFile"),
            rss_shmem_kb=_parse_optional_kb_field(values, "RssShmem"),
            vm_swap_kb=_parse_optional_kb_field(values, "VmSwap"),
            voluntary_context_switches=_parse_optional_nonnegative_field(
                values,
                "voluntary_ctxt_switches",
            ),
            nonvoluntary_context_switches=_parse_optional_nonnegative_field(
                values,
                "nonvoluntary_ctxt_switches",
            ),
        )

    except (TypeError, ValueError) as exc:
        raise ProcessParseError(f"invalid process status: {exc}") from exc


def _read_process_io(path: Path) -> ProcessIo:
    """Read and parse /proc/<pid>/io accounting fields."""

    return _parse_process_io(_read_bounded_text(path, max_bytes=_PROCESS_IO_MAX_BYTES))


def _parse_process_io(text: str) -> ProcessIo:
    """Parse the seven standard /proc/<pid>/io counters."""

    expected_fields = {
        "rchar",
        "wchar",
        "syscr",
        "syscw",
        "read_bytes",
        "write_bytes",
        "cancelled_write_bytes",
    }
    values: dict[str, int] = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        field_name, separator, raw_value = raw_line.partition(":")

        if not separator:
            if raw_line.strip():
                raise ProcessParseError(f"process io line {line_number} is missing ':'")
            continue

        if field_name not in expected_fields:
            continue

        if field_name in values:
            raise ProcessParseError(
                f"duplicate {field_name} field on process io line {line_number}"
            )

        values[field_name] = _parse_nonnegative_int(
            raw_value.strip(),
            field_name=field_name,
        )

    missing = sorted(expected_fields.difference(values))

    if missing:
        raise ProcessParseError(
            "process io is missing required fields: " + ", ".join(missing)
        )

    return ProcessIo(
        rchar=values["rchar"],
        wchar=values["wchar"],
        syscr=values["syscr"],
        syscw=values["syscw"],
        read_bytes=values["read_bytes"],
        write_bytes=values["write_bytes"],
        cancelled_write_bytes=values["cancelled_write_bytes"],
    )


def _parse_optional_kb_field(values: dict[str, str], field_name: str) -> int | None:
    """Parse one optional status memory field using the documented kB unit."""

    raw_value = values.get(field_name)

    if raw_value is None:
        return None

    fields = raw_value.split()

    if len(fields) != 2 or fields[1] != "kB":
        raise ProcessParseError(f"{field_name} must contain an integer followed by kB")

    return _parse_nonnegative_int(fields[0], field_name=field_name)


def _parse_optional_nonnegative_field(
    values: dict[str, str],
    field_name: str,
) -> int | None:
    """Parse one optional non-negative scalar status field."""

    raw_value = values.get(field_name)

    if raw_value is None:
        return None

    return _parse_nonnegative_int(raw_value, field_name=field_name)


def _parse_fixed_nonnegative_ints(
    raw_value: str,
    *,
    field_name: str,
    count: int,
) -> tuple[int, ...]:
    """Parse a fixed-size list of non-negative integers."""

    parts = raw_value.split()

    if len(parts) != count:
        raise ProcessParseError(f"{field_name} must contain exactly {count} integers")

    return tuple(_parse_nonnegative_int(part, field_name=field_name) for part in parts)


def _parse_nonempty_positive_ints(
    raw_value: str,
    *,
    field_name: str,
) -> tuple[int, ...]:
    """Parse a non-empty list of positive integers."""

    parts = raw_value.split()

    if not parts:
        raise ProcessParseError(f"{field_name} must contain at least one integer")

    return tuple(_parse_positive_int(part, field_name=field_name) for part in parts)


def _parse_zero_one_bool(raw_value: str, *, field_name: str) -> bool:
    """Parse one kernel 0/1 boolean field."""

    if raw_value == "0":
        return False

    if raw_value == "1":
        return True

    raise ProcessParseError(f"{field_name} must be 0 or 1")


def _read_bounded_text(path: Path, *, max_bytes: int) -> str:
    """Read one procfs text file with a strict upper bound."""

    try:
        with path.open("rb") as file_handle:
            data = file_handle.read(max_bytes + 1)

    except (OSError, ValueError) as exc:
        raise ProcessReadError(f"could not read {path}: {exc}") from exc

    if len(data) > max_bytes:
        raise ProcessReadError(f"procfs file exceeds {max_bytes} bytes: {path}")

    try:
        return data.decode("utf-8")

    except UnicodeDecodeError as exc:
        raise ProcessReadError(f"procfs file is not valid UTF-8: {path}") from exc


def _resolve_positive_sysconf(name: str, *, override: int | None) -> int:
    """Resolve one positive sysconf value with deterministic test override support."""

    if override is not None:
        try:
            _require_positive_int(name, override)

        except (TypeError, ValueError) as exc:
            raise ProcessDiscoveryError(f"invalid {name} override: {exc}") from exc

        return override

    try:
        value = os.sysconf(name)

    except (OSError, ValueError) as exc:
        raise ProcessDiscoveryError(f"could not resolve {name}: {exc}") from exc

    try:
        _require_positive_int(name, value)

    except (TypeError, ValueError) as exc:
        raise ProcessDiscoveryError(f"invalid {name} value: {exc}") from exc

    return value


def _failure_from_exception(
    pid: int,
    stage: ProcessProbeStage,
    exc: Exception,
) -> ProcessProbeFailure:
    """Build structured process probe failure evidence."""

    return ProcessProbeFailure(
        pid=pid,
        stage=stage,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )


def _parse_positive_int(raw_value: str, *, field_name: str) -> int:
    """Parse one strictly positive integer."""

    value = _parse_int(raw_value, field_name=field_name)

    if value <= 0:
        raise ProcessParseError(f"{field_name} must be greater than zero")

    return value


def _parse_nonnegative_int(raw_value: str, *, field_name: str) -> int:
    """Parse one non-negative integer."""

    value = _parse_int(raw_value, field_name=field_name)

    if value < 0:
        raise ProcessParseError(f"{field_name} must not be negative")

    return value


def _parse_int(raw_value: str, *, field_name: str) -> int:
    """Parse one base-10 integer."""

    try:
        return int(raw_value, 10)

    except ValueError as exc:
        raise ProcessParseError(f"invalid {field_name}: {raw_value!r}") from exc


def _require_process_comm(value: str) -> None:
    """Require a NUL-free process comm string; an empty comm is valid."""

    if not isinstance(value, str):
        raise TypeError("process comm must be a string")

    if "\x00" in value:
        raise ValueError("process comm must not contain NUL")


def _require_process_state(value: str) -> None:
    """Require one non-whitespace process state character."""

    if not isinstance(value, str):
        raise TypeError("process state must be a string")

    if len(value) != 1 or value.isspace():
        raise ValueError("process state must be one non-whitespace character")


def _require_uuid(name: str, value: str) -> None:
    """Require a canonical UUID string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    try:
        parsed = UUID(value)

    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc

    if str(parsed) != value.lower():
        raise ValueError(f"{name} must use canonical UUID form")


def _require_nonempty_text(name: str, value: str) -> None:
    """Require a non-empty string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_positive_int(name: str, value: int) -> None:
    """Require a strictly positive integer."""

    _require_int(name, value)

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer."""

    _require_int(name, value)

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _require_int(name: str, value: int) -> None:
    """Require an integer while rejecting bool."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


def _require_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware datetime."""

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
