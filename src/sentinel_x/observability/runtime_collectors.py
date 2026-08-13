"""Stateful scheduler-facing host collectors for Sentinel-X."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Generic, Protocol, TypeVar

from sentinel_x.core.events import SentinelEvent
from sentinel_x.observability.disk_io import (
    DiskStatsSnapshot,
    LinuxDiskStatsReader,
)
from sentinel_x.observability.disk_io_sampling import build_disk_io_observation
from sentinel_x.observability.events import (
    disk_io_observation_to_event,
    filesystem_observation_to_event,
    host_observation_to_event,
    memory_observation_to_event,
    network_observation_to_event,
    process_observation_to_event,
)
from sentinel_x.observability.filesystem import (
    LinuxFilesystemReader,
    build_filesystem_observation,
)
from sentinel_x.observability.linux_host import LinuxHostReader
from sentinel_x.observability.memory import build_memory_observation
from sentinel_x.observability.models import HostSnapshot
from sentinel_x.observability.network import (
    LinuxNetworkReader,
    NetworkStatsSnapshot,
)
from sentinel_x.observability.network_sampling import build_network_observation
from sentinel_x.observability.process import LinuxProcessReader, ProcessSnapshot
from sentinel_x.observability.process_sampling import build_process_observation
from sentinel_x.observability.sampling import build_host_observation


class RuntimeCollectorError(RuntimeError):
    """Base error for scheduler-facing runtime collector adapters."""


class RuntimeCollectorClockError(RuntimeCollectorError):
    """Raised when an injected monotonic clock violates its contract."""


class RuntimeCollectorContractError(RuntimeCollectorError):
    """Raised when a collector callback returns an invalid value."""


class ObservationEmissionStatus(str, Enum):
    """Result status for one runtime collector invocation."""

    WARMING_UP = "warming_up"
    EMITTED = "emitted"


@dataclass(frozen=True, slots=True)
class ObservationEmission:
    """One collector invocation result consumed by the runtime engine."""

    collector_name: str
    status: ObservationEmissionStatus
    event: SentinelEvent | None
    sample_interval_seconds: float | None = None

    def __post_init__(self) -> None:
        """Validate status-specific emission invariants."""

        normalized_name = _normalize_collector_name(self.collector_name)
        object.__setattr__(self, "collector_name", normalized_name)

        if not isinstance(self.status, ObservationEmissionStatus):
            raise TypeError("status must be an ObservationEmissionStatus")

        if self.status is ObservationEmissionStatus.WARMING_UP:
            if self.event is not None:
                raise ValueError("warming-up emission must not contain an event")
            if self.sample_interval_seconds is not None:
                raise ValueError(
                    "warming-up emission must not contain a sample interval"
                )
            return

        if not isinstance(self.event, SentinelEvent):
            raise ValueError("emitted result requires a SentinelEvent")

        if self.sample_interval_seconds is not None:
            _require_positive_finite_seconds(
                "sample_interval_seconds",
                self.sample_interval_seconds,
            )

    def to_dict(self) -> dict[str, object]:
        """Return bounded metadata without duplicating the event payload."""

        return {
            "collector_name": self.collector_name,
            "status": self.status.value,
            "event_id": None if self.event is None else self.event.event_id,
            "event_kind": None if self.event is None else self.event.kind.value,
            "event_source": None if self.event is None else self.event.source,
            "sample_interval_seconds": self.sample_interval_seconds,
        }


@dataclass(frozen=True, slots=True)
class RuntimeCollectorSnapshot:
    """Read-only runtime state for one scheduler-facing collector."""

    collector_name: str
    requires_baseline: bool
    baseline_ready: bool
    invocation_count: int
    successful_read_count: int
    warmup_count: int
    emission_count: int
    failure_count: int
    baseline_reset_count: int
    last_sample_interval_seconds: float | None

    def __post_init__(self) -> None:
        """Validate runtime accounting invariants."""

        normalized_name = _normalize_collector_name(self.collector_name)
        object.__setattr__(self, "collector_name", normalized_name)

        for boolean_field_name, boolean_value in (
            ("requires_baseline", self.requires_baseline),
            ("baseline_ready", self.baseline_ready),
        ):
            if type(boolean_value) is not bool:
                raise TypeError(f"{boolean_field_name} must be a boolean")

        for integer_field_name, integer_value in (
            ("invocation_count", self.invocation_count),
            ("successful_read_count", self.successful_read_count),
            ("warmup_count", self.warmup_count),
            ("emission_count", self.emission_count),
            ("failure_count", self.failure_count),
            ("baseline_reset_count", self.baseline_reset_count),
        ):
            _require_nonnegative_int(integer_field_name, integer_value)

        if self.successful_read_count > self.invocation_count:
            raise ValueError("successful_read_count must not exceed invocation_count")
        if self.warmup_count > self.successful_read_count:
            raise ValueError("warmup_count must not exceed successful_read_count")
        if self.emission_count > self.successful_read_count:
            raise ValueError("emission_count must not exceed successful_read_count")

        if not self.requires_baseline and self.baseline_ready:
            raise ValueError(
                "point-in-time collector snapshot must not report a baseline"
            )

        if self.last_sample_interval_seconds is not None:
            _require_positive_finite_seconds(
                "last_sample_interval_seconds",
                self.last_sample_interval_seconds,
            )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly collector state."""

        return {
            "collector_name": self.collector_name,
            "requires_baseline": self.requires_baseline,
            "baseline_ready": self.baseline_ready,
            "invocation_count": self.invocation_count,
            "successful_read_count": self.successful_read_count,
            "warmup_count": self.warmup_count,
            "emission_count": self.emission_count,
            "failure_count": self.failure_count,
            "baseline_reset_count": self.baseline_reset_count,
            "last_sample_interval_seconds": self.last_sample_interval_seconds,
        }


class RuntimeObservationCollector(Protocol):
    """Structural contract exposed to runtime registry integration."""

    @property
    def name(self) -> str:
        """Return stable collector name."""

    def collect(self) -> ObservationEmission:
        """Perform one non-sleeping collector invocation."""

    def snapshot(self) -> RuntimeCollectorSnapshot:
        """Return current adapter state."""


_SnapshotT = TypeVar("_SnapshotT")


@dataclass(frozen=True, slots=True)
class _Baseline(Generic[_SnapshotT]):
    snapshot: _SnapshotT
    captured_monotonic_ns: int


class SuccessiveSnapshotEventCollector(Generic[_SnapshotT]):
    """Build sampled observation events from successive scheduled captures."""

    def __init__(
        self,
        name: str,
        snapshot_reader: Callable[[], _SnapshotT],
        event_builder: Callable[[_SnapshotT, _SnapshotT, float], SentinelEvent],
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._name = _normalize_collector_name(name)
        _require_callable("snapshot_reader", snapshot_reader)
        _require_callable("event_builder", event_builder)
        _require_callable("clock_ns", clock_ns)

        self._snapshot_reader = snapshot_reader
        self._event_builder = event_builder
        self._clock_ns = clock_ns
        self._lock = RLock()
        self._baseline: _Baseline[_SnapshotT] | None = None
        self._invocation_count = 0
        self._successful_read_count = 0
        self._warmup_count = 0
        self._emission_count = 0
        self._failure_count = 0
        self._baseline_reset_count = 0
        self._last_sample_interval_seconds: float | None = None

    @property
    def name(self) -> str:
        """Return stable collector name."""

        return self._name

    def collect(self) -> ObservationEmission:
        """Capture one endpoint without sleeping and emit after warm-up."""

        with self._lock:
            self._invocation_count += 1

            try:
                current_snapshot = self._snapshot_reader()
                captured_ns = _read_monotonic_ns(self._clock_ns)
            except Exception:
                self._failure_count += 1
                raise

            self._successful_read_count += 1
            previous = self._baseline
            self._baseline = _Baseline(
                snapshot=current_snapshot,
                captured_monotonic_ns=captured_ns,
            )

            if previous is None:
                self._warmup_count += 1
                return ObservationEmission(
                    collector_name=self._name,
                    status=ObservationEmissionStatus.WARMING_UP,
                    event=None,
                )

            if captured_ns <= previous.captured_monotonic_ns:
                self._failure_count += 1
                raise RuntimeCollectorClockError(
                    f"collector {self._name!r} monotonic capture time did not advance"
                )

            interval_seconds = (
                captured_ns - previous.captured_monotonic_ns
            ) / 1_000_000_000.0

            try:
                event = self._event_builder(
                    previous.snapshot,
                    current_snapshot,
                    interval_seconds,
                )
                _require_sentinel_event(event)
            except Exception:
                self._failure_count += 1
                raise

            self._emission_count += 1
            self._last_sample_interval_seconds = interval_seconds
            return ObservationEmission(
                collector_name=self._name,
                status=ObservationEmissionStatus.EMITTED,
                event=event,
                sample_interval_seconds=interval_seconds,
            )

    def reset_baseline(self) -> bool:
        """Discard the current endpoint while preserving lifetime counters."""

        with self._lock:
            if self._baseline is None:
                return False

            self._baseline = None
            self._baseline_reset_count += 1
            self._last_sample_interval_seconds = None
            return True

    def snapshot(self) -> RuntimeCollectorSnapshot:
        """Return current adapter state."""

        with self._lock:
            return RuntimeCollectorSnapshot(
                collector_name=self._name,
                requires_baseline=True,
                baseline_ready=self._baseline is not None,
                invocation_count=self._invocation_count,
                successful_read_count=self._successful_read_count,
                warmup_count=self._warmup_count,
                emission_count=self._emission_count,
                failure_count=self._failure_count,
                baseline_reset_count=self._baseline_reset_count,
                last_sample_interval_seconds=self._last_sample_interval_seconds,
            )


class PointInTimeEventCollector:
    """Produce one observation event on every successful invocation."""

    def __init__(
        self,
        name: str,
        event_producer: Callable[[], SentinelEvent],
    ) -> None:
        self._name = _normalize_collector_name(name)
        _require_callable("event_producer", event_producer)
        self._event_producer = event_producer
        self._lock = RLock()
        self._invocation_count = 0
        self._emission_count = 0
        self._failure_count = 0

    @property
    def name(self) -> str:
        """Return stable collector name."""

        return self._name

    def collect(self) -> ObservationEmission:
        """Produce and validate one point-in-time observation event."""

        with self._lock:
            self._invocation_count += 1

            try:
                event = self._event_producer()
                _require_sentinel_event(event)
            except Exception:
                self._failure_count += 1
                raise

            self._emission_count += 1
            return ObservationEmission(
                collector_name=self._name,
                status=ObservationEmissionStatus.EMITTED,
                event=event,
            )

    def snapshot(self) -> RuntimeCollectorSnapshot:
        """Return current adapter state."""

        with self._lock:
            return RuntimeCollectorSnapshot(
                collector_name=self._name,
                requires_baseline=False,
                baseline_ready=False,
                invocation_count=self._invocation_count,
                successful_read_count=self._emission_count,
                warmup_count=0,
                emission_count=self._emission_count,
                failure_count=self._failure_count,
                baseline_reset_count=0,
                last_sample_interval_seconds=None,
            )


@dataclass(frozen=True, slots=True)
class _ProcessRuntimeSnapshot:
    process_snapshot: ProcessSnapshot
    logical_cpu_count: int


@dataclass(frozen=True, slots=True)
class BuiltinHostCollectors:
    """Trusted built-in Linux host collectors used by scheduled runtime wiring."""

    cpu_load: RuntimeObservationCollector
    memory: RuntimeObservationCollector
    filesystem: RuntimeObservationCollector
    disk_io: RuntimeObservationCollector
    network: RuntimeObservationCollector
    process: RuntimeObservationCollector

    def __post_init__(self) -> None:
        """Require exact built-in names and unique collector objects."""

        expected = (
            ("cpu_load", self.cpu_load),
            ("memory", self.memory),
            ("filesystem", self.filesystem),
            ("disk_io", self.disk_io),
            ("network", self.network),
            ("process", self.process),
        )

        object_ids: list[int] = []
        for expected_name, collector in expected:
            if collector.name != expected_name:
                raise RuntimeCollectorContractError(
                    f"expected collector {expected_name!r}, got {collector.name!r}"
                )
            object_ids.append(id(collector))

        if len(set(object_ids)) != len(object_ids):
            raise RuntimeCollectorContractError(
                "built-in collector set must use distinct collector objects"
            )

    def collectors(self) -> tuple[RuntimeObservationCollector, ...]:
        """Return built-ins in stable semantic order."""

        return (
            self.cpu_load,
            self.memory,
            self.filesystem,
            self.disk_io,
            self.network,
            self.process,
        )

    def handlers(self) -> Mapping[str, Callable[[], object]]:
        """Return immutable exact-name handlers for CollectorRegistry binding."""

        return MappingProxyType(
            {collector.name: collector.collect for collector in self.collectors()}
        )

    def snapshots(self) -> tuple[RuntimeCollectorSnapshot, ...]:
        """Return adapter state in stable semantic order."""

        return tuple(collector.snapshot() for collector in self.collectors())


def build_builtin_host_collectors(
    *,
    cpu_host_reader: LinuxHostReader | None = None,
    memory_host_reader: LinuxHostReader | None = None,
    filesystem_reader: LinuxFilesystemReader | None = None,
    disk_io_reader: LinuxDiskStatsReader | None = None,
    network_reader: LinuxNetworkReader | None = None,
    process_reader: LinuxProcessReader | None = None,
    logical_cpu_count_reader: Callable[[], int | None] = os.cpu_count,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    utc_now: Callable[[], datetime] | None = None,
) -> BuiltinHostCollectors:
    """Create trusted non-sleeping host collectors for scheduled runtime use."""

    cpu_reader = cpu_host_reader or LinuxHostReader()
    memory_reader = memory_host_reader or LinuxHostReader()
    fs_reader = filesystem_reader or LinuxFilesystemReader()
    disk_reader = disk_io_reader or LinuxDiskStatsReader()
    net_reader = network_reader or LinuxNetworkReader()
    proc_reader = process_reader or LinuxProcessReader()
    wall_clock = utc_now or _utc_now

    _require_callable("logical_cpu_count_reader", logical_cpu_count_reader)
    _require_callable("clock_ns", clock_ns)
    _require_callable("utc_now", wall_clock)

    def build_cpu_event(
        previous: HostSnapshot,
        current: HostSnapshot,
        interval_seconds: float,
    ) -> SentinelEvent:
        observation = build_host_observation(
            previous,
            current,
            sample_interval_seconds=interval_seconds,
        )
        return host_observation_to_event(observation)

    def produce_memory_event() -> SentinelEvent:
        stats = memory_reader.read_memory_stats()
        observation = build_memory_observation(
            stats,
            captured_at=wall_clock(),
        )
        return memory_observation_to_event(observation)

    def produce_filesystem_event() -> SentinelEvent:
        report = fs_reader.read_report()
        observation = build_filesystem_observation(
            report,
            captured_at=wall_clock(),
        )
        return filesystem_observation_to_event(observation)

    def build_disk_event(
        previous: DiskStatsSnapshot,
        current: DiskStatsSnapshot,
        interval_seconds: float,
    ) -> SentinelEvent:
        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=interval_seconds,
        )
        return disk_io_observation_to_event(observation)

    def build_network_event(
        previous: NetworkStatsSnapshot,
        current: NetworkStatsSnapshot,
        interval_seconds: float,
    ) -> SentinelEvent:
        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=interval_seconds,
        )
        return network_observation_to_event(observation)

    def read_process_runtime_snapshot() -> _ProcessRuntimeSnapshot:
        process_snapshot = proc_reader.read_snapshot()
        logical_cpu_count = logical_cpu_count_reader()
        if (
            isinstance(logical_cpu_count, bool)
            or not isinstance(logical_cpu_count, int)
            or logical_cpu_count <= 0
        ):
            raise RuntimeCollectorContractError(
                "logical CPU count reader must return a positive integer"
            )
        return _ProcessRuntimeSnapshot(
            process_snapshot=process_snapshot,
            logical_cpu_count=logical_cpu_count,
        )

    def build_process_event(
        previous: _ProcessRuntimeSnapshot,
        current: _ProcessRuntimeSnapshot,
        interval_seconds: float,
    ) -> SentinelEvent:
        observation = build_process_observation(
            previous.process_snapshot,
            current.process_snapshot,
            sample_interval_seconds=interval_seconds,
            logical_cpu_count=current.logical_cpu_count,
        )
        return process_observation_to_event(observation)

    return BuiltinHostCollectors(
        cpu_load=SuccessiveSnapshotEventCollector(
            "cpu_load",
            cpu_reader.read_snapshot,
            build_cpu_event,
            clock_ns=clock_ns,
        ),
        memory=PointInTimeEventCollector(
            "memory",
            produce_memory_event,
        ),
        filesystem=PointInTimeEventCollector(
            "filesystem",
            produce_filesystem_event,
        ),
        disk_io=SuccessiveSnapshotEventCollector(
            "disk_io",
            disk_reader.read_snapshot,
            build_disk_event,
            clock_ns=clock_ns,
        ),
        network=SuccessiveSnapshotEventCollector(
            "network",
            net_reader.read_snapshot,
            build_network_event,
            clock_ns=clock_ns,
        ),
        process=SuccessiveSnapshotEventCollector(
            "process",
            read_process_runtime_snapshot,
            build_process_event,
            clock_ns=clock_ns,
        ),
    )


def _utc_now() -> datetime:
    """Return one timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _normalize_collector_name(value: str) -> str:
    """Normalize one stable collector identity."""

    if not isinstance(value, str):
        raise TypeError("collector name must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("collector name must not be empty")
    return normalized


def _require_callable(name: str, value: object) -> None:
    """Require a callable runtime dependency."""

    if not callable(value):
        raise TypeError(f"{name} must be callable")


def _read_monotonic_ns(clock_ns: Callable[[], int]) -> int:
    """Read and validate one integer monotonic timestamp."""

    value = clock_ns()
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeCollectorClockError(
            "runtime collector monotonic clock must return an integer"
        )
    if value < 0:
        raise RuntimeCollectorClockError(
            "runtime collector monotonic clock must not return a negative value"
        )
    return value


def _require_sentinel_event(value: object) -> None:
    """Require a builder or producer to return a SentinelEvent."""

    if not isinstance(value, SentinelEvent):
        raise RuntimeCollectorContractError(
            "runtime collector callback must return a SentinelEvent"
        )


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer counter."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _require_positive_finite_seconds(name: str, value: float) -> None:
    """Require a positive finite duration."""

    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{name} must be finite and greater than zero")
