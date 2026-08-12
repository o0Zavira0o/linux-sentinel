"""Sampled Linux process resource metrics for Sentinel-X."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sentinel_x.observability.process import (
    ProcessIdentity,
    ProcessIo,
    ProcessObservationError,
    ProcessProbeFailure,
    ProcessRecord,
    ProcessSnapshot,
    ProcessStatus,
)


class ProcessSamplingError(ProcessObservationError):
    """Raised when process snapshots cannot be compared safely."""


class ProcessSampleStatus(str, Enum):
    """Comparison status for one PID across two process snapshots."""

    SAMPLED = "sampled"
    STARTED = "started"
    EXITED = "exited"
    PID_REUSED = "pid_reused"
    COUNTER_RESET = "counter_reset"
    START_UNVERIFIED = "start_unverified"
    EXIT_UNVERIFIED = "exit_unverified"


@dataclass(frozen=True, slots=True)
class ProcessCpuMetrics:
    """Derived process CPU metrics for one sampling interval."""

    user_time_ticks_delta: int
    system_time_ticks_delta: int
    total_time_ticks_delta: int
    user_time_seconds: float
    system_time_seconds: float
    total_time_seconds: float
    single_core_equivalent_percent: float
    host_capacity_percent: float

    def __post_init__(self) -> None:
        """Validate process CPU metrics."""

        for integer_name, integer_value in (
            ("user_time_ticks_delta", self.user_time_ticks_delta),
            ("system_time_ticks_delta", self.system_time_ticks_delta),
            ("total_time_ticks_delta", self.total_time_ticks_delta),
        ):
            _require_nonnegative_int(integer_name, integer_value)

        for float_name, float_value in (
            ("user_time_seconds", self.user_time_seconds),
            ("system_time_seconds", self.system_time_seconds),
            ("total_time_seconds", self.total_time_seconds),
            (
                "single_core_equivalent_percent",
                self.single_core_equivalent_percent,
            ),
            ("host_capacity_percent", self.host_capacity_percent),
        ):
            _require_nonnegative_finite_float(float_name, float_value)

        if self.total_time_ticks_delta != (
            self.user_time_ticks_delta + self.system_time_ticks_delta
        ):
            raise ValueError("total_time_ticks_delta must equal user + system ticks")

        if not math.isclose(
            self.total_time_seconds,
            self.user_time_seconds + self.system_time_seconds,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("total_time_seconds must equal user + system seconds")

        if self.host_capacity_percent > self.single_core_equivalent_percent:
            raise ValueError(
                "host_capacity_percent cannot exceed single_core_equivalent_percent"
            )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly CPU metrics."""

        return {
            "user_time_ticks_delta": self.user_time_ticks_delta,
            "system_time_ticks_delta": self.system_time_ticks_delta,
            "total_time_ticks_delta": self.total_time_ticks_delta,
            "user_time_seconds": self.user_time_seconds,
            "system_time_seconds": self.system_time_seconds,
            "total_time_seconds": self.total_time_seconds,
            "single_core_equivalent_percent": self.single_core_equivalent_percent,
            "host_capacity_percent": self.host_capacity_percent,
        }


@dataclass(frozen=True, slots=True)
class ProcessFaultMetrics:
    """Derived process page-fault metrics for one sampling interval."""

    minor_faults_delta: int
    major_faults_delta: int
    minor_faults_per_second: float
    major_faults_per_second: float

    def __post_init__(self) -> None:
        """Validate process page-fault metrics."""

        _require_nonnegative_int("minor_faults_delta", self.minor_faults_delta)
        _require_nonnegative_int("major_faults_delta", self.major_faults_delta)
        _require_nonnegative_finite_float(
            "minor_faults_per_second",
            self.minor_faults_per_second,
        )
        _require_nonnegative_finite_float(
            "major_faults_per_second",
            self.major_faults_per_second,
        )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly page-fault metrics."""

        return {
            "minor_faults_delta": self.minor_faults_delta,
            "major_faults_delta": self.major_faults_delta,
            "minor_faults_per_second": self.minor_faults_per_second,
            "major_faults_per_second": self.major_faults_per_second,
        }


@dataclass(frozen=True, slots=True)
class ProcessMemoryMetrics:
    """Instantaneous end-of-window process memory evidence."""

    virtual_memory_bytes: int
    resident_pages: int
    resident_memory_bytes: int | None
    vm_size_kb: int | None
    vm_rss_kb: int | None
    rss_anon_kb: int | None
    rss_file_kb: int | None
    rss_shmem_kb: int | None
    vm_swap_kb: int | None
    thread_count: int

    def __post_init__(self) -> None:
        """Validate process memory metrics."""

        _require_nonnegative_int("virtual_memory_bytes", self.virtual_memory_bytes)
        _require_int("resident_pages", self.resident_pages)
        _require_nonnegative_int("thread_count", self.thread_count)

        for name, value in (
            ("resident_memory_bytes", self.resident_memory_bytes),
            ("vm_size_kb", self.vm_size_kb),
            ("vm_rss_kb", self.vm_rss_kb),
            ("rss_anon_kb", self.rss_anon_kb),
            ("rss_file_kb", self.rss_file_kb),
            ("rss_shmem_kb", self.rss_shmem_kb),
            ("vm_swap_kb", self.vm_swap_kb),
        ):
            if value is not None:
                _require_nonnegative_int(name, value)

        if self.resident_pages < 0 and self.resident_memory_bytes is not None:
            raise ValueError(
                "negative resident_pages requires resident_memory_bytes to be None"
            )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly process memory metrics."""

        return {
            "virtual_memory_bytes": self.virtual_memory_bytes,
            "resident_pages": self.resident_pages,
            "resident_memory_bytes": self.resident_memory_bytes,
            "status_memory_kb": {
                "vm_size": self.vm_size_kb,
                "vm_rss": self.vm_rss_kb,
                "rss_anon": self.rss_anon_kb,
                "rss_file": self.rss_file_kb,
                "rss_shmem": self.rss_shmem_kb,
                "vm_swap": self.vm_swap_kb,
            },
            "thread_count": self.thread_count,
        }


@dataclass(frozen=True, slots=True)
class ProcessContextSwitchMetrics:
    """Derived process context-switch metrics."""

    voluntary_delta: int
    nonvoluntary_delta: int
    total_delta: int
    voluntary_per_second: float
    nonvoluntary_per_second: float
    total_per_second: float

    def __post_init__(self) -> None:
        """Validate context-switch metrics."""

        for integer_name, integer_value in (
            ("voluntary_delta", self.voluntary_delta),
            ("nonvoluntary_delta", self.nonvoluntary_delta),
            ("total_delta", self.total_delta),
        ):
            _require_nonnegative_int(integer_name, integer_value)

        for float_name, float_value in (
            ("voluntary_per_second", self.voluntary_per_second),
            ("nonvoluntary_per_second", self.nonvoluntary_per_second),
            ("total_per_second", self.total_per_second),
        ):
            _require_nonnegative_finite_float(float_name, float_value)

        if self.total_delta != self.voluntary_delta + self.nonvoluntary_delta:
            raise ValueError("total_delta must equal voluntary + nonvoluntary")

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly context-switch metrics."""

        return {
            "voluntary_delta": self.voluntary_delta,
            "nonvoluntary_delta": self.nonvoluntary_delta,
            "total_delta": self.total_delta,
            "voluntary_per_second": self.voluntary_per_second,
            "nonvoluntary_per_second": self.nonvoluntary_per_second,
            "total_per_second": self.total_per_second,
        }


@dataclass(frozen=True, slots=True)
class ProcessIoMetrics:
    """Derived process I/O metrics when both raw endpoints are available."""

    rchar_delta: int
    wchar_delta: int
    syscr_delta: int
    syscw_delta: int
    read_bytes_delta: int
    write_bytes_delta: int
    cancelled_write_bytes_delta: int
    rchar_per_second: float
    wchar_per_second: float
    read_syscalls_per_second: float
    write_syscalls_per_second: float
    read_bytes_per_second: float
    write_bytes_per_second: float
    cancelled_write_bytes_per_second: float

    def __post_init__(self) -> None:
        """Validate sampled process I/O metrics."""

        for integer_name, integer_value in (
            ("rchar_delta", self.rchar_delta),
            ("wchar_delta", self.wchar_delta),
            ("syscr_delta", self.syscr_delta),
            ("syscw_delta", self.syscw_delta),
            ("read_bytes_delta", self.read_bytes_delta),
            ("write_bytes_delta", self.write_bytes_delta),
            ("cancelled_write_bytes_delta", self.cancelled_write_bytes_delta),
        ):
            _require_nonnegative_int(integer_name, integer_value)

        for float_name, float_value in (
            ("rchar_per_second", self.rchar_per_second),
            ("wchar_per_second", self.wchar_per_second),
            ("read_syscalls_per_second", self.read_syscalls_per_second),
            ("write_syscalls_per_second", self.write_syscalls_per_second),
            ("read_bytes_per_second", self.read_bytes_per_second),
            ("write_bytes_per_second", self.write_bytes_per_second),
            (
                "cancelled_write_bytes_per_second",
                self.cancelled_write_bytes_per_second,
            ),
        ):
            _require_nonnegative_finite_float(float_name, float_value)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly process I/O metrics."""

        return {
            "rchar_delta": self.rchar_delta,
            "wchar_delta": self.wchar_delta,
            "syscr_delta": self.syscr_delta,
            "syscw_delta": self.syscw_delta,
            "read_bytes_delta": self.read_bytes_delta,
            "write_bytes_delta": self.write_bytes_delta,
            "cancelled_write_bytes_delta": self.cancelled_write_bytes_delta,
            "rchar_per_second": self.rchar_per_second,
            "wchar_per_second": self.wchar_per_second,
            "read_syscalls_per_second": self.read_syscalls_per_second,
            "write_syscalls_per_second": self.write_syscalls_per_second,
            "read_bytes_per_second": self.read_bytes_per_second,
            "write_bytes_per_second": self.write_bytes_per_second,
            "cancelled_write_bytes_per_second": (self.cancelled_write_bytes_per_second),
        }


@dataclass(frozen=True, slots=True)
class ProcessMetrics:
    """Derived resource metrics for one identity-verified process."""

    cpu: ProcessCpuMetrics
    faults: ProcessFaultMetrics
    memory: ProcessMemoryMetrics
    context_switches: ProcessContextSwitchMetrics | None
    io: ProcessIoMetrics | None

    def __post_init__(self) -> None:
        """Validate nested process metric components."""

        if not isinstance(self.cpu, ProcessCpuMetrics):
            raise TypeError("cpu must be ProcessCpuMetrics")

        if not isinstance(self.faults, ProcessFaultMetrics):
            raise TypeError("faults must be ProcessFaultMetrics")

        if not isinstance(self.memory, ProcessMemoryMetrics):
            raise TypeError("memory must be ProcessMemoryMetrics")

        if self.context_switches is not None and not isinstance(
            self.context_switches,
            ProcessContextSwitchMetrics,
        ):
            raise TypeError(
                "context_switches must be ProcessContextSwitchMetrics or None"
            )

        if self.io is not None and not isinstance(self.io, ProcessIoMetrics):
            raise TypeError("io must be ProcessIoMetrics or None")

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly process resource metrics."""

        return {
            "cpu": self.cpu.to_dict(),
            "faults": self.faults.to_dict(),
            "memory": self.memory.to_dict(),
            "context_switches": (
                None
                if self.context_switches is None
                else self.context_switches.to_dict()
            ),
            "io": None if self.io is None else self.io.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProcessSample:
    """Comparison result for one PID across two process snapshots."""

    start_record: ProcessRecord | None
    end_record: ProcessRecord | None
    status: ProcessSampleStatus
    metrics: ProcessMetrics | None
    core_regressed_fields: tuple[str, ...] = ()
    context_regressed_fields: tuple[str, ...] = ()
    io_regressed_fields: tuple[str, ...] = ()
    missing_endpoint_failure: ProcessProbeFailure | None = None

    def __post_init__(self) -> None:
        """Validate status-specific process sample invariants."""

        if not isinstance(self.status, ProcessSampleStatus):
            raise TypeError("status must be a ProcessSampleStatus")

        if self.start_record is None and self.end_record is None:
            raise ValueError("at least one process endpoint must be present")

        for collection_name, field_names in (
            ("core_regressed_fields", self.core_regressed_fields),
            ("context_regressed_fields", self.context_regressed_fields),
            ("io_regressed_fields", self.io_regressed_fields),
        ):
            _validate_unique_field_names(collection_name, field_names)

        if self.missing_endpoint_failure is not None and not isinstance(
            self.missing_endpoint_failure,
            ProcessProbeFailure,
        ):
            raise TypeError(
                "missing_endpoint_failure must be ProcessProbeFailure or None"
            )

        if self.status is ProcessSampleStatus.SAMPLED:
            self._validate_sampled()
        elif self.status is ProcessSampleStatus.COUNTER_RESET:
            self._validate_counter_reset()
        elif self.status is ProcessSampleStatus.PID_REUSED:
            self._validate_pid_reused()
        elif self.status is ProcessSampleStatus.STARTED:
            self._validate_started()
        elif self.status is ProcessSampleStatus.EXITED:
            self._validate_exited()
        elif self.status is ProcessSampleStatus.START_UNVERIFIED:
            self._validate_start_unverified()
        elif self.status is ProcessSampleStatus.EXIT_UNVERIFIED:
            self._validate_exit_unverified()

    def _validate_sampled(self) -> None:
        """Validate a successfully sampled process."""

        self._require_same_identity()

        if self.metrics is None:
            raise ValueError("sampled process requires derived metrics")

        if self.core_regressed_fields:
            raise ValueError("sampled process must not contain core regressions")

        if self.missing_endpoint_failure is not None:
            raise ValueError("sampled process cannot have a missing endpoint failure")

        if self.context_regressed_fields and self.metrics.context_switches is not None:
            raise ValueError(
                "context regressions require context_switches metrics to be None"
            )

        if self.io_regressed_fields and self.metrics.io is not None:
            raise ValueError("I/O regressions require io metrics to be None")

    def _validate_counter_reset(self) -> None:
        """Validate a core cumulative-counter reset result."""

        self._require_same_identity()

        if self.metrics is not None:
            raise ValueError("counter reset must not contain derived metrics")

        if not self.core_regressed_fields:
            raise ValueError("counter reset must identify core regressed fields")

        if self.context_regressed_fields or self.io_regressed_fields:
            raise ValueError(
                "counter reset must not mix optional-domain regression evidence"
            )

        if self.missing_endpoint_failure is not None:
            raise ValueError("counter reset cannot have a missing endpoint failure")

    def _validate_pid_reused(self) -> None:
        """Validate strong PID reuse evidence."""

        self._require_both_endpoints()

        assert self.start_record is not None
        assert self.end_record is not None

        if self.start_record.identity.pid != self.end_record.identity.pid:
            raise ValueError("PID reuse requires the same numeric PID")

        if self.start_record.identity.boot_id != self.end_record.identity.boot_id:
            raise ValueError("PID reuse requires endpoints from the same boot")

        if (
            self.start_record.identity.identity_key
            == self.end_record.identity.identity_key
        ):
            raise ValueError("PID reuse requires different process identity keys")

        self._require_no_metrics_or_regressions()

        if self.missing_endpoint_failure is not None:
            raise ValueError("PID reuse cannot have a missing endpoint failure")

    def _validate_started(self) -> None:
        """Validate a verified process start."""

        if self.start_record is not None or self.end_record is None:
            raise ValueError("started process requires only end_record")

        self._require_no_metrics_or_regressions()

        if self.missing_endpoint_failure is not None:
            raise ValueError("verified start cannot have a missing endpoint failure")

    def _validate_exited(self) -> None:
        """Validate a verified process exit."""

        if self.start_record is None or self.end_record is not None:
            raise ValueError("exited process requires only start_record")

        self._require_no_metrics_or_regressions()

        if self.missing_endpoint_failure is not None:
            raise ValueError("verified exit cannot have a missing endpoint failure")

    def _validate_start_unverified(self) -> None:
        """Validate an apparent start obscured by a dropped start endpoint."""

        if self.start_record is not None or self.end_record is None:
            raise ValueError("start_unverified requires only end_record")

        self._require_missing_endpoint_failure(self.end_record.identity.pid)
        self._require_no_metrics_or_regressions()

    def _validate_exit_unverified(self) -> None:
        """Validate an apparent exit obscured by a dropped end endpoint."""

        if self.start_record is None or self.end_record is not None:
            raise ValueError("exit_unverified requires only start_record")

        self._require_missing_endpoint_failure(self.start_record.identity.pid)
        self._require_no_metrics_or_regressions()

    def _require_both_endpoints(self) -> None:
        """Require both raw process endpoints."""

        if self.start_record is None or self.end_record is None:
            raise ValueError("comparison status requires both process endpoints")

    def _require_same_identity(self) -> None:
        """Require identical strong process identity across both snapshots."""

        self._require_both_endpoints()

        assert self.start_record is not None
        assert self.end_record is not None

        if (
            self.start_record.identity.identity_key
            != self.end_record.identity.identity_key
        ):
            raise ValueError("sampled endpoints must share the same identity key")

    def _require_no_metrics_or_regressions(self) -> None:
        """Require lifecycle-only evidence without derived metrics."""

        if self.metrics is not None:
            raise ValueError("lifecycle status must not contain derived metrics")

        if (
            self.core_regressed_fields
            or self.context_regressed_fields
            or self.io_regressed_fields
        ):
            raise ValueError("lifecycle status must not contain counter regressions")

    def _require_missing_endpoint_failure(self, expected_pid: int) -> None:
        """Require a dropped-failure record for the hidden endpoint."""

        if self.missing_endpoint_failure is None:
            raise ValueError("unverified lifecycle status requires dropped failure")

        if self.missing_endpoint_failure.pid != expected_pid:
            raise ValueError("missing endpoint failure PID must match process PID")

    @property
    def identity(self) -> ProcessIdentity:
        """Return the most recent available strong process identity."""

        if self.end_record is not None:
            return self.end_record.identity

        assert self.start_record is not None

        return self.start_record.identity

    @property
    def comm_changed(self) -> bool:
        """Return whether comm changed for a same-identity process."""

        if not self._has_same_identity_endpoints():
            return False

        assert self.start_record is not None
        assert self.end_record is not None

        return self.start_record.identity.comm != self.end_record.identity.comm

    @property
    def state_changed(self) -> bool:
        """Return whether the raw process state changed."""

        if not self._has_same_identity_endpoints():
            return False

        assert self.start_record is not None
        assert self.end_record is not None

        return self.start_record.stat.state != self.end_record.stat.state

    @property
    def ppid_changed(self) -> bool:
        """Return whether the parent PID changed."""

        if not self._has_same_identity_endpoints():
            return False

        assert self.start_record is not None
        assert self.end_record is not None

        return self.start_record.stat.ppid != self.end_record.stat.ppid

    @property
    def effective_uid_changed(self) -> bool:
        """Return whether effective UID changed when status exists at both ends."""

        statuses = self._status_pair()

        if statuses is None:
            return False

        start_status, end_status = statuses

        return start_status.effective_uid != end_status.effective_uid

    @property
    def effective_gid_changed(self) -> bool:
        """Return whether effective GID changed when status exists at both ends."""

        statuses = self._status_pair()

        if statuses is None:
            return False

        start_status, end_status = statuses

        return start_status.effective_gid != end_status.effective_gid

    def _has_same_identity_endpoints(self) -> bool:
        """Return whether both records represent the same strong identity."""

        return (
            self.start_record is not None
            and self.end_record is not None
            and self.start_record.identity.identity_key
            == self.end_record.identity.identity_key
        )

    def _status_pair(self) -> tuple[ProcessStatus, ProcessStatus] | None:
        """Return both status records when present for the same process identity."""

        if not self._has_same_identity_endpoints():
            return None

        assert self.start_record is not None
        assert self.end_record is not None

        if self.start_record.status is None or self.end_record.status is None:
            return None

        return self.start_record.status, self.end_record.status

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly process sampling evidence."""

        return {
            "identity": self.identity.to_dict(),
            "status": self.status.value,
            "regressed_fields": {
                "core": list(self.core_regressed_fields),
                "context": list(self.context_regressed_fields),
                "io": list(self.io_regressed_fields),
            },
            "changes": {
                "comm_changed": self.comm_changed,
                "state_changed": self.state_changed,
                "ppid_changed": self.ppid_changed,
                "effective_uid_changed": self.effective_uid_changed,
                "effective_gid_changed": self.effective_gid_changed,
            },
            "missing_endpoint_failure": (
                None
                if self.missing_endpoint_failure is None
                else self.missing_endpoint_failure.to_dict()
            ),
            "start_record": (
                None if self.start_record is None else self.start_record.to_dict()
            ),
            "end_record": (
                None if self.end_record is None else self.end_record.to_dict()
            ),
            "metrics": None if self.metrics is None else self.metrics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    """One sampled observation across the visible Linux process set."""

    sample_started_at: datetime
    captured_at: datetime
    sample_interval_seconds: float
    logical_cpu_count: int
    clock_ticks_per_second: int
    page_size_bytes: int
    start_discovered_pid_count: int
    end_discovered_pid_count: int
    samples: tuple[ProcessSample, ...]
    start_dropped_failures: tuple[ProcessProbeFailure, ...]
    end_dropped_failures: tuple[ProcessProbeFailure, ...]

    def __post_init__(self) -> None:
        """Validate sampled process observation metadata."""

        _require_aware_datetime("sample_started_at", self.sample_started_at)
        _require_aware_datetime("captured_at", self.captured_at)
        _require_positive_finite_float(
            "sample_interval_seconds",
            self.sample_interval_seconds,
        )
        _require_positive_int("logical_cpu_count", self.logical_cpu_count)
        _require_positive_int("clock_ticks_per_second", self.clock_ticks_per_second)
        _require_positive_int("page_size_bytes", self.page_size_bytes)
        _require_nonnegative_int(
            "start_discovered_pid_count",
            self.start_discovered_pid_count,
        )
        _require_nonnegative_int(
            "end_discovered_pid_count",
            self.end_discovered_pid_count,
        )

        start_pids: list[int] = []
        end_pids: list[int] = []

        for sample in self.samples:
            if not isinstance(sample, ProcessSample):
                raise TypeError("samples must contain ProcessSample objects")

            if sample.start_record is not None:
                start_pids.append(sample.start_record.identity.pid)

            if sample.end_record is not None:
                end_pids.append(sample.end_record.identity.pid)

        if len(set(start_pids)) != len(start_pids):
            raise ValueError("process observation reuses a start process record")

        if len(set(end_pids)) != len(end_pids):
            raise ValueError("process observation reuses an end process record")

        _validate_failure_collection(
            "start_dropped_failures",
            self.start_dropped_failures,
        )
        _validate_failure_collection(
            "end_dropped_failures",
            self.end_dropped_failures,
        )

        if self.start_discovered_pid_count != (
            len(start_pids) + len(self.start_dropped_failures)
        ):
            raise ValueError(
                "start_discovered_pid_count must account for retained and dropped PIDs"
            )

        if self.end_discovered_pid_count != (
            len(end_pids) + len(self.end_dropped_failures)
        ):
            raise ValueError(
                "end_discovered_pid_count must account for retained and dropped PIDs"
            )

        start_failures = {
            failure.pid: failure for failure in self.start_dropped_failures
        }
        end_failures = {failure.pid: failure for failure in self.end_dropped_failures}

        for sample in self.samples:
            if sample.status is ProcessSampleStatus.START_UNVERIFIED:
                failure = sample.missing_endpoint_failure
                assert failure is not None

                if start_failures.get(failure.pid) != failure:
                    raise ValueError(
                        "start_unverified sample must reference a start dropped failure"
                    )

            if sample.status is ProcessSampleStatus.EXIT_UNVERIFIED:
                failure = sample.missing_endpoint_failure
                assert failure is not None

                if end_failures.get(failure.pid) != failure:
                    raise ValueError(
                        "exit_unverified sample must reference an end dropped failure"
                    )

    @property
    def sampled_count(self) -> int:
        """Return successfully sampled process identities."""

        return sum(
            sample.status is ProcessSampleStatus.SAMPLED for sample in self.samples
        )

    @property
    def started_count(self) -> int:
        """Return verified process starts."""

        return sum(
            sample.status is ProcessSampleStatus.STARTED for sample in self.samples
        )

    @property
    def exited_count(self) -> int:
        """Return verified process exits."""

        return sum(
            sample.status is ProcessSampleStatus.EXITED for sample in self.samples
        )

    @property
    def pid_reused_count(self) -> int:
        """Return numeric PIDs reused by a new strong identity."""

        return sum(
            sample.status is ProcessSampleStatus.PID_REUSED for sample in self.samples
        )

    @property
    def counter_reset_count(self) -> int:
        """Return same-identity processes with core counter regression."""

        return sum(
            sample.status is ProcessSampleStatus.COUNTER_RESET
            for sample in self.samples
        )

    @property
    def start_unverified_count(self) -> int:
        """Return apparent starts hidden by a dropped start endpoint."""

        return sum(
            sample.status is ProcessSampleStatus.START_UNVERIFIED
            for sample in self.samples
        )

    @property
    def exit_unverified_count(self) -> int:
        """Return apparent exits hidden by a dropped end endpoint."""

        return sum(
            sample.status is ProcessSampleStatus.EXIT_UNVERIFIED
            for sample in self.samples
        )

    @property
    def context_metrics_unavailable_count(self) -> int:
        """Return sampled processes without context-switch metrics."""

        return sum(
            sample.status is ProcessSampleStatus.SAMPLED
            and sample.metrics is not None
            and sample.metrics.context_switches is None
            for sample in self.samples
        )

    @property
    def io_metrics_unavailable_count(self) -> int:
        """Return sampled processes without process I/O metrics."""

        return sum(
            sample.status is ProcessSampleStatus.SAMPLED
            and sample.metrics is not None
            and sample.metrics.io is None
            for sample in self.samples
        )

    @property
    def comm_changed_count(self) -> int:
        """Return same-identity samples whose comm changed."""

        return sum(sample.comm_changed for sample in self.samples)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly sampled process evidence."""

        return {
            "sample_started_at": self.sample_started_at.isoformat(),
            "captured_at": self.captured_at.isoformat(),
            "sample_interval_seconds": self.sample_interval_seconds,
            "logical_cpu_count": self.logical_cpu_count,
            "clock_ticks_per_second": self.clock_ticks_per_second,
            "page_size_bytes": self.page_size_bytes,
            "summary": {
                "sample_count": len(self.samples),
                "sampled_count": self.sampled_count,
                "started_count": self.started_count,
                "exited_count": self.exited_count,
                "pid_reused_count": self.pid_reused_count,
                "counter_reset_count": self.counter_reset_count,
                "start_unverified_count": self.start_unverified_count,
                "exit_unverified_count": self.exit_unverified_count,
                "context_metrics_unavailable_count": (
                    self.context_metrics_unavailable_count
                ),
                "io_metrics_unavailable_count": self.io_metrics_unavailable_count,
                "comm_changed_count": self.comm_changed_count,
                "start_discovered_pid_count": self.start_discovered_pid_count,
                "end_discovered_pid_count": self.end_discovered_pid_count,
                "start_dropped_process_count": len(self.start_dropped_failures),
                "end_dropped_process_count": len(self.end_dropped_failures),
            },
            "samples": [sample.to_dict() for sample in self.samples],
            "start_dropped_failures": [
                failure.to_dict() for failure in self.start_dropped_failures
            ],
            "end_dropped_failures": [
                failure.to_dict() for failure in self.end_dropped_failures
            ],
        }


def build_process_observation(
    previous: ProcessSnapshot,
    current: ProcessSnapshot,
    *,
    sample_interval_seconds: object,
    logical_cpu_count: object,
) -> ProcessObservation:
    """Compare two raw process snapshots using strong process identity."""

    if not isinstance(previous, ProcessSnapshot):
        raise ProcessSamplingError("previous must be a ProcessSnapshot")

    if not isinstance(current, ProcessSnapshot):
        raise ProcessSamplingError("current must be a ProcessSnapshot")

    interval = _normalize_sample_interval(sample_interval_seconds)
    cpu_count = _normalize_logical_cpu_count(logical_cpu_count)

    if previous.boot_id != current.boot_id:
        raise ProcessSamplingError("process snapshots come from different boot IDs")

    if previous.clock_ticks_per_second != current.clock_ticks_per_second:
        raise ProcessSamplingError(
            "process snapshots use different clock tick frequencies"
        )

    if previous.page_size_bytes != current.page_size_bytes:
        raise ProcessSamplingError("process snapshots use different page sizes")

    previous_by_pid = {process.identity.pid: process for process in previous.processes}
    current_by_pid = {process.identity.pid: process for process in current.processes}
    previous_dropped = {failure.pid: failure for failure in previous.dropped_failures}
    current_dropped = {failure.pid: failure for failure in current.dropped_failures}

    samples: list[ProcessSample] = []

    for current_record in current.processes:
        pid = current_record.identity.pid
        previous_record = previous_by_pid.get(pid)

        if previous_record is None:
            previous_failure = previous_dropped.get(pid)

            if previous_failure is None:
                samples.append(
                    ProcessSample(
                        start_record=None,
                        end_record=current_record,
                        status=ProcessSampleStatus.STARTED,
                        metrics=None,
                    )
                )
            else:
                samples.append(
                    ProcessSample(
                        start_record=None,
                        end_record=current_record,
                        status=ProcessSampleStatus.START_UNVERIFIED,
                        metrics=None,
                        missing_endpoint_failure=previous_failure,
                    )
                )

            continue

        if (
            previous_record.identity.identity_key
            != current_record.identity.identity_key
        ):
            samples.append(
                ProcessSample(
                    start_record=previous_record,
                    end_record=current_record,
                    status=ProcessSampleStatus.PID_REUSED,
                    metrics=None,
                )
            )
            continue

        core_regressions = _find_core_regressions(previous_record, current_record)

        if core_regressions:
            samples.append(
                ProcessSample(
                    start_record=previous_record,
                    end_record=current_record,
                    status=ProcessSampleStatus.COUNTER_RESET,
                    metrics=None,
                    core_regressed_fields=core_regressions,
                )
            )
            continue

        context_metrics, context_regressions = _calculate_context_switch_metrics(
            previous_record.status,
            current_record.status,
            sample_interval_seconds=interval,
        )
        io_metrics, io_regressions = _calculate_io_metrics(
            previous_record.io,
            current_record.io,
            sample_interval_seconds=interval,
        )

        samples.append(
            ProcessSample(
                start_record=previous_record,
                end_record=current_record,
                status=ProcessSampleStatus.SAMPLED,
                metrics=ProcessMetrics(
                    cpu=_calculate_cpu_metrics(
                        previous_record,
                        current_record,
                        clock_ticks_per_second=current.clock_ticks_per_second,
                        sample_interval_seconds=interval,
                        logical_cpu_count=cpu_count,
                    ),
                    faults=_calculate_fault_metrics(
                        previous_record,
                        current_record,
                        sample_interval_seconds=interval,
                    ),
                    memory=_calculate_memory_metrics(
                        current_record,
                        page_size_bytes=current.page_size_bytes,
                    ),
                    context_switches=context_metrics,
                    io=io_metrics,
                ),
                context_regressed_fields=context_regressions,
                io_regressed_fields=io_regressions,
            )
        )

    for previous_record in previous.processes:
        pid = previous_record.identity.pid

        if pid in current_by_pid:
            continue

        current_failure = current_dropped.get(pid)

        if current_failure is None:
            samples.append(
                ProcessSample(
                    start_record=previous_record,
                    end_record=None,
                    status=ProcessSampleStatus.EXITED,
                    metrics=None,
                )
            )
        else:
            samples.append(
                ProcessSample(
                    start_record=previous_record,
                    end_record=None,
                    status=ProcessSampleStatus.EXIT_UNVERIFIED,
                    metrics=None,
                    missing_endpoint_failure=current_failure,
                )
            )

    try:
        return ProcessObservation(
            sample_started_at=previous.captured_at,
            captured_at=current.captured_at,
            sample_interval_seconds=interval,
            logical_cpu_count=cpu_count,
            clock_ticks_per_second=current.clock_ticks_per_second,
            page_size_bytes=current.page_size_bytes,
            start_discovered_pid_count=previous.discovered_pid_count,
            end_discovered_pid_count=current.discovered_pid_count,
            samples=tuple(samples),
            start_dropped_failures=previous.dropped_failures,
            end_dropped_failures=current.dropped_failures,
        )

    except (TypeError, ValueError) as exc:
        raise ProcessSamplingError(f"invalid process observation: {exc}") from exc


def _find_core_regressions(
    previous: ProcessRecord,
    current: ProcessRecord,
) -> tuple[str, ...]:
    """Return core cumulative stat counters that decreased."""

    fields = (
        (
            "user_time_ticks",
            previous.stat.user_time_ticks,
            current.stat.user_time_ticks,
        ),
        (
            "system_time_ticks",
            previous.stat.system_time_ticks,
            current.stat.system_time_ticks,
        ),
        ("minor_faults", previous.stat.minor_faults, current.stat.minor_faults),
        ("major_faults", previous.stat.major_faults, current.stat.major_faults),
    )

    return tuple(name for name, start, end in fields if end < start)


def _calculate_cpu_metrics(
    previous: ProcessRecord,
    current: ProcessRecord,
    *,
    clock_ticks_per_second: int,
    sample_interval_seconds: float,
    logical_cpu_count: int,
) -> ProcessCpuMetrics:
    """Calculate process CPU time and utilization metrics."""

    user_ticks = current.stat.user_time_ticks - previous.stat.user_time_ticks
    system_ticks = current.stat.system_time_ticks - previous.stat.system_time_ticks
    total_ticks = user_ticks + system_ticks

    user_seconds = float(user_ticks) / clock_ticks_per_second
    system_seconds = float(system_ticks) / clock_ticks_per_second
    total_seconds = float(total_ticks) / clock_ticks_per_second
    single_core_percent = total_seconds / sample_interval_seconds * 100.0

    return ProcessCpuMetrics(
        user_time_ticks_delta=user_ticks,
        system_time_ticks_delta=system_ticks,
        total_time_ticks_delta=total_ticks,
        user_time_seconds=user_seconds,
        system_time_seconds=system_seconds,
        total_time_seconds=total_seconds,
        single_core_equivalent_percent=single_core_percent,
        host_capacity_percent=single_core_percent / logical_cpu_count,
    )


def _calculate_fault_metrics(
    previous: ProcessRecord,
    current: ProcessRecord,
    *,
    sample_interval_seconds: float,
) -> ProcessFaultMetrics:
    """Calculate process page-fault deltas and rates."""

    minor_delta = current.stat.minor_faults - previous.stat.minor_faults
    major_delta = current.stat.major_faults - previous.stat.major_faults

    return ProcessFaultMetrics(
        minor_faults_delta=minor_delta,
        major_faults_delta=major_delta,
        minor_faults_per_second=_rate(minor_delta, sample_interval_seconds),
        major_faults_per_second=_rate(major_delta, sample_interval_seconds),
    )


def _calculate_memory_metrics(
    current: ProcessRecord,
    *,
    page_size_bytes: int,
) -> ProcessMemoryMetrics:
    """Capture end-of-window process memory evidence."""

    status = current.status
    resident_pages = current.stat.resident_pages
    resident_memory_bytes = (
        None if resident_pages < 0 else resident_pages * page_size_bytes
    )

    return ProcessMemoryMetrics(
        virtual_memory_bytes=current.stat.virtual_memory_bytes,
        resident_pages=resident_pages,
        resident_memory_bytes=resident_memory_bytes,
        vm_size_kb=None if status is None else status.vm_size_kb,
        vm_rss_kb=None if status is None else status.vm_rss_kb,
        rss_anon_kb=None if status is None else status.rss_anon_kb,
        rss_file_kb=None if status is None else status.rss_file_kb,
        rss_shmem_kb=None if status is None else status.rss_shmem_kb,
        vm_swap_kb=None if status is None else status.vm_swap_kb,
        thread_count=current.stat.thread_count,
    )


def _calculate_context_switch_metrics(
    previous: ProcessStatus | None,
    current: ProcessStatus | None,
    *,
    sample_interval_seconds: float,
) -> tuple[ProcessContextSwitchMetrics | None, tuple[str, ...]]:
    """Calculate context-switch metrics when both status endpoints support them."""

    if previous is None or current is None:
        return None, ()

    values = (
        (
            "voluntary_context_switches",
            previous.voluntary_context_switches,
            current.voluntary_context_switches,
        ),
        (
            "nonvoluntary_context_switches",
            previous.nonvoluntary_context_switches,
            current.nonvoluntary_context_switches,
        ),
    )

    if any(start is None or end is None for _, start, end in values):
        return None, ()

    regressions = tuple(
        name
        for name, start, end in values
        if start is not None and end is not None and end < start
    )

    if regressions:
        return None, regressions

    voluntary_start = previous.voluntary_context_switches
    voluntary_end = current.voluntary_context_switches
    nonvoluntary_start = previous.nonvoluntary_context_switches
    nonvoluntary_end = current.nonvoluntary_context_switches

    assert voluntary_start is not None
    assert voluntary_end is not None
    assert nonvoluntary_start is not None
    assert nonvoluntary_end is not None

    voluntary_delta = voluntary_end - voluntary_start
    nonvoluntary_delta = nonvoluntary_end - nonvoluntary_start
    total_delta = voluntary_delta + nonvoluntary_delta

    return (
        ProcessContextSwitchMetrics(
            voluntary_delta=voluntary_delta,
            nonvoluntary_delta=nonvoluntary_delta,
            total_delta=total_delta,
            voluntary_per_second=_rate(
                voluntary_delta,
                sample_interval_seconds,
            ),
            nonvoluntary_per_second=_rate(
                nonvoluntary_delta,
                sample_interval_seconds,
            ),
            total_per_second=_rate(total_delta, sample_interval_seconds),
        ),
        (),
    )


def _calculate_io_metrics(
    previous: ProcessIo | None,
    current: ProcessIo | None,
    *,
    sample_interval_seconds: float,
) -> tuple[ProcessIoMetrics | None, tuple[str, ...]]:
    """Calculate process I/O metrics while isolating optional counter regressions."""

    if previous is None or current is None:
        return None, ()

    fields = (
        ("rchar", previous.rchar, current.rchar),
        ("wchar", previous.wchar, current.wchar),
        ("syscr", previous.syscr, current.syscr),
        ("syscw", previous.syscw, current.syscw),
        ("read_bytes", previous.read_bytes, current.read_bytes),
        ("write_bytes", previous.write_bytes, current.write_bytes),
        (
            "cancelled_write_bytes",
            previous.cancelled_write_bytes,
            current.cancelled_write_bytes,
        ),
    )
    regressions = tuple(name for name, start, end in fields if end < start)

    if regressions:
        return None, regressions

    rchar_delta = current.rchar - previous.rchar
    wchar_delta = current.wchar - previous.wchar
    syscr_delta = current.syscr - previous.syscr
    syscw_delta = current.syscw - previous.syscw
    read_bytes_delta = current.read_bytes - previous.read_bytes
    write_bytes_delta = current.write_bytes - previous.write_bytes
    cancelled_write_bytes_delta = (
        current.cancelled_write_bytes - previous.cancelled_write_bytes
    )

    return (
        ProcessIoMetrics(
            rchar_delta=rchar_delta,
            wchar_delta=wchar_delta,
            syscr_delta=syscr_delta,
            syscw_delta=syscw_delta,
            read_bytes_delta=read_bytes_delta,
            write_bytes_delta=write_bytes_delta,
            cancelled_write_bytes_delta=cancelled_write_bytes_delta,
            rchar_per_second=_rate(rchar_delta, sample_interval_seconds),
            wchar_per_second=_rate(wchar_delta, sample_interval_seconds),
            read_syscalls_per_second=_rate(syscr_delta, sample_interval_seconds),
            write_syscalls_per_second=_rate(syscw_delta, sample_interval_seconds),
            read_bytes_per_second=_rate(
                read_bytes_delta,
                sample_interval_seconds,
            ),
            write_bytes_per_second=_rate(
                write_bytes_delta,
                sample_interval_seconds,
            ),
            cancelled_write_bytes_per_second=_rate(
                cancelled_write_bytes_delta,
                sample_interval_seconds,
            ),
        ),
        (),
    )


def _normalize_sample_interval(value: object) -> float:
    """Validate and normalize an actual process sampling interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProcessSamplingError("sample interval must be a number")

    normalized = float(value)

    if not math.isfinite(normalized):
        raise ProcessSamplingError("sample interval must be finite")

    if normalized <= 0.0:
        raise ProcessSamplingError("sample interval must be greater than zero")

    return normalized


def _normalize_logical_cpu_count(value: object) -> int:
    """Validate the logical CPU capacity used for host-normalized utilization."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ProcessSamplingError("logical_cpu_count must be an integer")

    if value <= 0:
        raise ProcessSamplingError("logical_cpu_count must be greater than zero")

    return value


def _rate(value: int, sample_interval_seconds: float) -> float:
    """Return one non-negative per-second rate."""

    return float(value) / sample_interval_seconds


def _validate_unique_field_names(
    collection_name: str,
    field_names: tuple[str, ...],
) -> None:
    """Validate one deterministic collection of regression field names."""

    for field_name in field_names:
        _require_nonempty_text("regressed field", field_name)

    if len(set(field_names)) != len(field_names):
        raise ValueError(f"{collection_name} must not contain duplicates")


def _validate_failure_collection(
    name: str,
    failures: tuple[ProcessProbeFailure, ...],
) -> None:
    """Validate one snapshot-level dropped-failure collection."""

    pids: list[int] = []

    for failure in failures:
        if not isinstance(failure, ProcessProbeFailure):
            raise TypeError(f"{name} must contain ProcessProbeFailure objects")

        pids.append(failure.pid)

    if len(set(pids)) != len(pids):
        raise ValueError(f"{name} must not repeat a PID")


def _require_nonempty_text(name: str, value: str) -> None:
    """Require a non-empty string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_positive_int(name: str, value: int) -> None:
    """Require a strictly positive integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _require_int(name: str, value: int) -> None:
    """Require an integer that is not a bool."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


def _require_nonnegative_finite_float(name: str, value: float) -> None:
    """Require a finite non-negative number."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")

    if value < 0.0:
        raise ValueError(f"{name} must not be negative")


def _require_positive_finite_float(name: str, value: float) -> None:
    """Require a finite number greater than zero."""

    _require_nonnegative_finite_float(name, value)

    if value == 0.0:
        raise ValueError(f"{name} must be greater than zero")


def _require_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware datetime."""

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
