"""Sampled Linux block-device I/O metrics for Sentinel-X."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sentinel_x.observability.disk_io import (
    DISK_SECTOR_BYTES,
    BlockDeviceIdentity,
    BlockDeviceKind,
    DiskIoObservationError,
    DiskStats,
    DiskStatsSnapshot,
)


class DiskIoSamplingError(DiskIoObservationError):
    """Raised when disk I/O snapshots cannot be compared safely."""


class DiskIoSampleStatus(str, Enum):
    """Comparison status for one block device across two snapshots."""

    SAMPLED = "sampled"
    APPEARED = "appeared"
    DISAPPEARED = "disappeared"
    COUNTER_RESET = "counter_reset"
    IDENTITY_CHANGED = "identity_changed"


@dataclass(frozen=True, slots=True)
class DiskIoMetrics:
    """Derived per-device I/O metrics for one sampling interval."""

    reads_completed_delta: int
    writes_completed_delta: int
    read_bytes_delta: int
    written_bytes_delta: int
    read_time_delta_ms: int
    write_time_delta_ms: int
    total_rw_operations_delta: int
    total_rw_bytes_delta: int
    io_time_delta_ms: int
    weighted_io_time_delta_ms: int
    read_iops: float
    write_iops: float
    total_rw_iops: float
    read_bytes_per_second: float
    write_bytes_per_second: float
    total_rw_bytes_per_second: float
    average_read_time_ms: float | None
    average_write_time_ms: float | None
    io_busy_percent_estimate: float
    weighted_queue_depth_estimate: float
    discards_completed_delta: int | None
    discarded_bytes_delta: int | None
    discard_time_delta_ms: int | None
    discard_iops: float | None
    discard_bytes_per_second: float | None
    average_discard_time_ms: float | None
    flushes_completed_delta: int | None
    flush_time_delta_ms: int | None
    flush_iops: float | None
    average_flush_time_ms: float | None

    def __post_init__(self) -> None:
        """Validate derived disk I/O metrics."""

        required_integer_metrics = (
            ("reads_completed_delta", self.reads_completed_delta),
            ("writes_completed_delta", self.writes_completed_delta),
            ("read_bytes_delta", self.read_bytes_delta),
            ("written_bytes_delta", self.written_bytes_delta),
            ("read_time_delta_ms", self.read_time_delta_ms),
            ("write_time_delta_ms", self.write_time_delta_ms),
            (
                "total_rw_operations_delta",
                self.total_rw_operations_delta,
            ),
            ("total_rw_bytes_delta", self.total_rw_bytes_delta),
            ("io_time_delta_ms", self.io_time_delta_ms),
            (
                "weighted_io_time_delta_ms",
                self.weighted_io_time_delta_ms,
            ),
        )

        for integer_name, integer_value in required_integer_metrics:
            _require_nonnegative_int(integer_name, integer_value)

        required_float_metrics = (
            ("read_iops", self.read_iops),
            ("write_iops", self.write_iops),
            ("total_rw_iops", self.total_rw_iops),
            (
                "read_bytes_per_second",
                self.read_bytes_per_second,
            ),
            (
                "write_bytes_per_second",
                self.write_bytes_per_second,
            ),
            (
                "total_rw_bytes_per_second",
                self.total_rw_bytes_per_second,
            ),
            (
                "io_busy_percent_estimate",
                self.io_busy_percent_estimate,
            ),
            (
                "weighted_queue_depth_estimate",
                self.weighted_queue_depth_estimate,
            ),
        )

        for float_name, float_value in required_float_metrics:
            _require_nonnegative_float(float_name, float_value)

        if self.total_rw_operations_delta != (
            self.reads_completed_delta + self.writes_completed_delta
        ):
            raise ValueError("total_rw_operations_delta must equal reads plus writes")

        if self.total_rw_bytes_delta != (
            self.read_bytes_delta + self.written_bytes_delta
        ):
            raise ValueError("total_rw_bytes_delta must equal read plus written bytes")

        if not math.isclose(
            self.total_rw_iops,
            self.read_iops + self.write_iops,
            abs_tol=1e-9,
        ):
            raise ValueError("total_rw_iops must equal read_iops plus write_iops")

        if not math.isclose(
            self.total_rw_bytes_per_second,
            self.read_bytes_per_second + self.write_bytes_per_second,
            abs_tol=1e-6,
        ):
            raise ValueError(
                "total_rw_bytes_per_second must equal read plus write throughput"
            )

        _validate_average_metric(
            "average_read_time_ms",
            self.average_read_time_ms,
            self.reads_completed_delta,
        )

        _validate_average_metric(
            "average_write_time_ms",
            self.average_write_time_ms,
            self.writes_completed_delta,
        )

        self._validate_discard_metrics()
        self._validate_flush_metrics()

    def _validate_discard_metrics(self) -> None:
        """Validate optional discard metrics as one capability group."""

        if self.discards_completed_delta is None:
            optional_values = (
                self.discarded_bytes_delta,
                self.discard_time_delta_ms,
                self.discard_iops,
                self.discard_bytes_per_second,
                self.average_discard_time_ms,
            )

            if any(value is not None for value in optional_values):
                raise ValueError("discard metrics must be unavailable together")

            return

        assert self.discarded_bytes_delta is not None
        assert self.discard_time_delta_ms is not None
        assert self.discard_iops is not None
        assert self.discard_bytes_per_second is not None

        _require_nonnegative_int(
            "discards_completed_delta",
            self.discards_completed_delta,
        )
        _require_nonnegative_int(
            "discarded_bytes_delta",
            self.discarded_bytes_delta,
        )
        _require_nonnegative_int(
            "discard_time_delta_ms",
            self.discard_time_delta_ms,
        )
        _require_nonnegative_float(
            "discard_iops",
            self.discard_iops,
        )
        _require_nonnegative_float(
            "discard_bytes_per_second",
            self.discard_bytes_per_second,
        )

        _validate_average_metric(
            "average_discard_time_ms",
            self.average_discard_time_ms,
            self.discards_completed_delta,
        )

    def _validate_flush_metrics(self) -> None:
        """Validate optional flush metrics as one capability group."""

        if self.flushes_completed_delta is None:
            optional_values = (
                self.flush_time_delta_ms,
                self.flush_iops,
                self.average_flush_time_ms,
            )

            if any(value is not None for value in optional_values):
                raise ValueError("flush metrics must be unavailable together")

            return

        assert self.flush_time_delta_ms is not None
        assert self.flush_iops is not None

        _require_nonnegative_int(
            "flushes_completed_delta",
            self.flushes_completed_delta,
        )
        _require_nonnegative_int(
            "flush_time_delta_ms",
            self.flush_time_delta_ms,
        )
        _require_nonnegative_float(
            "flush_iops",
            self.flush_iops,
        )

        _validate_average_metric(
            "average_flush_time_ms",
            self.average_flush_time_ms,
            self.flushes_completed_delta,
        )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly sampled I/O metrics."""

        return {
            "reads_completed_delta": self.reads_completed_delta,
            "writes_completed_delta": self.writes_completed_delta,
            "read_bytes_delta": self.read_bytes_delta,
            "written_bytes_delta": self.written_bytes_delta,
            "read_time_delta_ms": self.read_time_delta_ms,
            "write_time_delta_ms": self.write_time_delta_ms,
            "total_rw_operations_delta": self.total_rw_operations_delta,
            "total_rw_bytes_delta": self.total_rw_bytes_delta,
            "io_time_delta_ms": self.io_time_delta_ms,
            "weighted_io_time_delta_ms": self.weighted_io_time_delta_ms,
            "read_iops": self.read_iops,
            "write_iops": self.write_iops,
            "total_rw_iops": self.total_rw_iops,
            "read_bytes_per_second": self.read_bytes_per_second,
            "write_bytes_per_second": self.write_bytes_per_second,
            "total_rw_bytes_per_second": self.total_rw_bytes_per_second,
            "average_read_time_ms": self.average_read_time_ms,
            "average_write_time_ms": self.average_write_time_ms,
            "io_busy_percent_estimate": self.io_busy_percent_estimate,
            "weighted_queue_depth_estimate": (self.weighted_queue_depth_estimate),
            "discards_completed_delta": self.discards_completed_delta,
            "discarded_bytes_delta": self.discarded_bytes_delta,
            "discard_time_delta_ms": self.discard_time_delta_ms,
            "discard_iops": self.discard_iops,
            "discard_bytes_per_second": self.discard_bytes_per_second,
            "average_discard_time_ms": self.average_discard_time_ms,
            "flushes_completed_delta": self.flushes_completed_delta,
            "flush_time_delta_ms": self.flush_time_delta_ms,
            "flush_iops": self.flush_iops,
            "average_flush_time_ms": self.average_flush_time_ms,
        }


@dataclass(frozen=True, slots=True)
class DiskIoDeviceSample:
    """Comparison result for one block device."""

    start_stats: DiskStats | None
    end_stats: DiskStats | None
    status: DiskIoSampleStatus
    metrics: DiskIoMetrics | None
    regressed_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate status-specific sample invariants."""

        if not isinstance(self.status, DiskIoSampleStatus):
            raise TypeError("status must be a DiskIoSampleStatus")

        if self.start_stats is None and self.end_stats is None:
            raise ValueError("at least one diskstats endpoint must be present")

        if (
            self.start_stats is not None
            and self.end_stats is not None
            and self.start_stats.identity.device_id != self.end_stats.identity.device_id
        ):
            raise ValueError("start and end diskstats must refer to the same device ID")

        for field_name in self.regressed_fields:
            _require_nonempty_text(
                "regressed field",
                field_name,
            )

        if len(set(self.regressed_fields)) != len(self.regressed_fields):
            raise ValueError("regressed_fields must not contain duplicates")

        if self.status is DiskIoSampleStatus.SAMPLED:
            self._validate_sampled_status()

        elif self.status is DiskIoSampleStatus.APPEARED:
            if self.start_stats is not None or self.end_stats is None:
                raise ValueError("appeared device requires only end_stats")

            if self.metrics is not None or self.regressed_fields:
                raise ValueError(
                    "appeared device must not contain metrics or regressions"
                )

        elif self.status is DiskIoSampleStatus.DISAPPEARED:
            if self.start_stats is None or self.end_stats is not None:
                raise ValueError("disappeared device requires only start_stats")

            if self.metrics is not None or self.regressed_fields:
                raise ValueError(
                    "disappeared device must not contain metrics or regressions"
                )

        elif self.status is DiskIoSampleStatus.COUNTER_RESET:
            if self.start_stats is None or self.end_stats is None:
                raise ValueError("counter reset requires both diskstats endpoints")

            if self.metrics is not None:
                raise ValueError("counter reset must not contain derived metrics")

            if not self.regressed_fields:
                raise ValueError("counter reset must identify regressed fields")

        elif self.status is DiskIoSampleStatus.IDENTITY_CHANGED:
            if self.start_stats is None or self.end_stats is None:
                raise ValueError("identity change requires both diskstats endpoints")

            if self.metrics is not None or self.regressed_fields:
                raise ValueError(
                    "identity change must not contain metrics or regressions"
                )

    def _validate_sampled_status(self) -> None:
        """Validate a successful comparable sample."""

        if self.start_stats is None or self.end_stats is None:
            raise ValueError("sampled device requires both diskstats endpoints")

        if self.metrics is None:
            raise ValueError("sampled device requires derived metrics")

        if self.regressed_fields:
            raise ValueError("sampled device must not contain counter regressions")

    @property
    def identity(self) -> BlockDeviceIdentity:
        """Return the most recent available device identity."""

        if self.end_stats is not None:
            return self.end_stats.identity

        assert self.start_stats is not None

        return self.start_stats.identity

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly per-device sampling evidence."""

        return {
            "identity": self.identity.to_dict(),
            "status": self.status.value,
            "regressed_fields": list(self.regressed_fields),
            "start_stats": (
                None if self.start_stats is None else self.start_stats.to_dict()
            ),
            "end_stats": (None if self.end_stats is None else self.end_stats.to_dict()),
            "metrics": (None if self.metrics is None else self.metrics.to_dict()),
        }


@dataclass(frozen=True, slots=True)
class DiskIoObservation:
    """One sampled disk I/O observation across visible block devices."""

    sample_started_at: datetime
    captured_at: datetime
    sample_interval_seconds: float
    devices: tuple[DiskIoDeviceSample, ...]

    def __post_init__(self) -> None:
        """Validate sampled observation metadata and unique devices."""

        _require_aware_datetime(
            "sample_started_at",
            self.sample_started_at,
        )
        _require_aware_datetime(
            "captured_at",
            self.captured_at,
        )
        _require_positive_finite_float(
            "sample_interval_seconds",
            self.sample_interval_seconds,
        )

        if not self.devices:
            raise ValueError("devices must not be empty")

        device_ids: list[str] = []

        for sample in self.devices:
            if not isinstance(sample, DiskIoDeviceSample):
                raise TypeError("devices must contain DiskIoDeviceSample objects")

            device_ids.append(sample.identity.device_id)

        if len(set(device_ids)) != len(device_ids):
            raise ValueError("disk I/O observation contains duplicate device IDs")

    @property
    def sampled_count(self) -> int:
        """Return the number of successfully sampled devices."""

        return sum(
            sample.status is DiskIoSampleStatus.SAMPLED for sample in self.devices
        )

    @property
    def appeared_count(self) -> int:
        """Return the number of devices appearing during the interval."""

        return sum(
            sample.status is DiskIoSampleStatus.APPEARED for sample in self.devices
        )

    @property
    def disappeared_count(self) -> int:
        """Return the number of devices disappearing during the interval."""

        return sum(
            sample.status is DiskIoSampleStatus.DISAPPEARED for sample in self.devices
        )

    @property
    def counter_reset_count(self) -> int:
        """Return the number of devices with cumulative counter regression."""

        return sum(
            sample.status is DiskIoSampleStatus.COUNTER_RESET for sample in self.devices
        )

    @property
    def identity_changed_count(self) -> int:
        """Return the number of reused device IDs with changed identity."""

        return sum(
            sample.status is DiskIoSampleStatus.IDENTITY_CHANGED
            for sample in self.devices
        )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly sampled disk I/O evidence."""

        return {
            "sample_started_at": self.sample_started_at.isoformat(),
            "captured_at": self.captured_at.isoformat(),
            "sample_interval_seconds": self.sample_interval_seconds,
            "summary": {
                "device_count": len(self.devices),
                "sampled_count": self.sampled_count,
                "appeared_count": self.appeared_count,
                "disappeared_count": self.disappeared_count,
                "counter_reset_count": self.counter_reset_count,
                "identity_changed_count": self.identity_changed_count,
            },
            "devices": [sample.to_dict() for sample in self.devices],
        }


def build_disk_io_observation(
    previous: DiskStatsSnapshot,
    current: DiskStatsSnapshot,
    *,
    sample_interval_seconds: object,
) -> DiskIoObservation:
    """Compare two diskstats snapshots and derive safe per-device metrics."""

    if not isinstance(previous, DiskStatsSnapshot):
        raise DiskIoSamplingError("previous must be a DiskStatsSnapshot")

    if not isinstance(current, DiskStatsSnapshot):
        raise DiskIoSamplingError("current must be a DiskStatsSnapshot")

    interval = _normalize_sample_interval(sample_interval_seconds)

    previous_by_id = {stats.identity.device_id: stats for stats in previous.devices}
    current_by_id = {stats.identity.device_id: stats for stats in current.devices}

    samples: list[DiskIoDeviceSample] = []

    for current_stats in current.devices:
        device_id = current_stats.identity.device_id
        previous_stats = previous_by_id.get(device_id)

        if previous_stats is None:
            samples.append(
                DiskIoDeviceSample(
                    start_stats=None,
                    end_stats=current_stats,
                    status=DiskIoSampleStatus.APPEARED,
                    metrics=None,
                )
            )
            continue

        if not _device_identity_is_comparable(
            previous_stats,
            current_stats,
        ):
            samples.append(
                DiskIoDeviceSample(
                    start_stats=previous_stats,
                    end_stats=current_stats,
                    status=DiskIoSampleStatus.IDENTITY_CHANGED,
                    metrics=None,
                )
            )
            continue

        regressed_fields = _find_regressed_fields(
            previous_stats,
            current_stats,
        )

        if regressed_fields:
            samples.append(
                DiskIoDeviceSample(
                    start_stats=previous_stats,
                    end_stats=current_stats,
                    status=DiskIoSampleStatus.COUNTER_RESET,
                    metrics=None,
                    regressed_fields=regressed_fields,
                )
            )
            continue

        samples.append(
            DiskIoDeviceSample(
                start_stats=previous_stats,
                end_stats=current_stats,
                status=DiskIoSampleStatus.SAMPLED,
                metrics=_calculate_metrics(
                    previous_stats,
                    current_stats,
                    sample_interval_seconds=interval,
                ),
            )
        )

    for previous_stats in previous.devices:
        device_id = previous_stats.identity.device_id

        if device_id in current_by_id:
            continue

        samples.append(
            DiskIoDeviceSample(
                start_stats=previous_stats,
                end_stats=None,
                status=DiskIoSampleStatus.DISAPPEARED,
                metrics=None,
            )
        )

    try:
        return DiskIoObservation(
            sample_started_at=previous.captured_at,
            captured_at=current.captured_at,
            sample_interval_seconds=interval,
            devices=tuple(samples),
        )

    except (TypeError, ValueError) as exc:
        raise DiskIoSamplingError(f"invalid disk I/O observation: {exc}") from exc


def _device_identity_is_comparable(
    previous: DiskStats,
    current: DiskStats,
) -> bool:
    """Return whether two major:minor records still represent one device."""

    if previous.identity.name != current.identity.name:
        return False

    previous_kind = previous.identity.kind
    current_kind = current.identity.kind

    if (
        previous_kind is not BlockDeviceKind.UNKNOWN
        and current_kind is not BlockDeviceKind.UNKNOWN
        and previous_kind is not current_kind
    ):
        return False

    return True


def _find_regressed_fields(
    previous: DiskStats,
    current: DiskStats,
) -> tuple[str, ...]:
    """Return known cumulative counters that decreased between snapshots."""

    regressed_fields: list[str] = []

    base_counter_pairs = (
        (
            "reads_completed",
            previous.reads_completed,
            current.reads_completed,
        ),
        (
            "reads_merged",
            previous.reads_merged,
            current.reads_merged,
        ),
        (
            "sectors_read",
            previous.sectors_read,
            current.sectors_read,
        ),
        (
            "read_time_ms",
            previous.read_time_ms,
            current.read_time_ms,
        ),
        (
            "writes_completed",
            previous.writes_completed,
            current.writes_completed,
        ),
        (
            "writes_merged",
            previous.writes_merged,
            current.writes_merged,
        ),
        (
            "sectors_written",
            previous.sectors_written,
            current.sectors_written,
        ),
        (
            "write_time_ms",
            previous.write_time_ms,
            current.write_time_ms,
        ),
        (
            "io_time_ms",
            previous.io_time_ms,
            current.io_time_ms,
        ),
        (
            "weighted_io_time_ms",
            previous.weighted_io_time_ms,
            current.weighted_io_time_ms,
        ),
    )

    for counter_name, start_value, end_value in base_counter_pairs:
        if end_value < start_value:
            regressed_fields.append(counter_name)

    optional_counter_pairs = (
        (
            "discards_completed",
            previous.discards_completed,
            current.discards_completed,
        ),
        (
            "discards_merged",
            previous.discards_merged,
            current.discards_merged,
        ),
        (
            "sectors_discarded",
            previous.sectors_discarded,
            current.sectors_discarded,
        ),
        (
            "discard_time_ms",
            previous.discard_time_ms,
            current.discard_time_ms,
        ),
        (
            "flushes_completed",
            previous.flushes_completed,
            current.flushes_completed,
        ),
        (
            "flush_time_ms",
            previous.flush_time_ms,
            current.flush_time_ms,
        ),
    )

    for optional_name, start_optional, end_optional in optional_counter_pairs:
        if (
            start_optional is not None
            and end_optional is not None
            and end_optional < start_optional
        ):
            regressed_fields.append(optional_name)

    return tuple(regressed_fields)


def _calculate_metrics(
    previous: DiskStats,
    current: DiskStats,
    *,
    sample_interval_seconds: float,
) -> DiskIoMetrics:
    """Calculate safe per-device metrics after reset validation."""

    reads_completed_delta = current.reads_completed - previous.reads_completed
    writes_completed_delta = current.writes_completed - previous.writes_completed
    sectors_read_delta = current.sectors_read - previous.sectors_read
    sectors_written_delta = current.sectors_written - previous.sectors_written
    read_time_delta_ms = current.read_time_ms - previous.read_time_ms
    write_time_delta_ms = current.write_time_ms - previous.write_time_ms
    io_time_delta_ms = current.io_time_ms - previous.io_time_ms
    weighted_io_time_delta_ms = (
        current.weighted_io_time_ms - previous.weighted_io_time_ms
    )

    read_bytes_delta = sectors_read_delta * DISK_SECTOR_BYTES
    written_bytes_delta = sectors_written_delta * DISK_SECTOR_BYTES

    total_rw_operations_delta = reads_completed_delta + writes_completed_delta
    total_rw_bytes_delta = read_bytes_delta + written_bytes_delta

    interval_ms = sample_interval_seconds * 1000.0

    read_iops = _rate(
        reads_completed_delta,
        sample_interval_seconds,
    )
    write_iops = _rate(
        writes_completed_delta,
        sample_interval_seconds,
    )
    total_rw_iops = _rate(
        total_rw_operations_delta,
        sample_interval_seconds,
    )

    read_bytes_per_second = _rate(
        read_bytes_delta,
        sample_interval_seconds,
    )
    write_bytes_per_second = _rate(
        written_bytes_delta,
        sample_interval_seconds,
    )
    total_rw_bytes_per_second = _rate(
        total_rw_bytes_delta,
        sample_interval_seconds,
    )

    average_read_time_ms = _average_time(
        read_time_delta_ms,
        reads_completed_delta,
    )
    average_write_time_ms = _average_time(
        write_time_delta_ms,
        writes_completed_delta,
    )

    io_busy_percent_estimate = (float(io_time_delta_ms) / interval_ms) * 100.0

    weighted_queue_depth_estimate = float(weighted_io_time_delta_ms) / interval_ms

    (
        discards_completed_delta,
        discarded_bytes_delta,
        discard_time_delta_ms,
        discard_iops,
        discard_bytes_per_second,
        average_discard_time_ms,
    ) = _calculate_discard_metrics(
        previous,
        current,
        sample_interval_seconds=sample_interval_seconds,
    )

    (
        flushes_completed_delta,
        flush_time_delta_ms,
        flush_iops,
        average_flush_time_ms,
    ) = _calculate_flush_metrics(
        previous,
        current,
        sample_interval_seconds=sample_interval_seconds,
    )

    return DiskIoMetrics(
        reads_completed_delta=reads_completed_delta,
        writes_completed_delta=writes_completed_delta,
        read_bytes_delta=read_bytes_delta,
        written_bytes_delta=written_bytes_delta,
        read_time_delta_ms=read_time_delta_ms,
        write_time_delta_ms=write_time_delta_ms,
        total_rw_operations_delta=total_rw_operations_delta,
        total_rw_bytes_delta=total_rw_bytes_delta,
        io_time_delta_ms=io_time_delta_ms,
        weighted_io_time_delta_ms=weighted_io_time_delta_ms,
        read_iops=read_iops,
        write_iops=write_iops,
        total_rw_iops=total_rw_iops,
        read_bytes_per_second=read_bytes_per_second,
        write_bytes_per_second=write_bytes_per_second,
        total_rw_bytes_per_second=total_rw_bytes_per_second,
        average_read_time_ms=average_read_time_ms,
        average_write_time_ms=average_write_time_ms,
        io_busy_percent_estimate=io_busy_percent_estimate,
        weighted_queue_depth_estimate=weighted_queue_depth_estimate,
        discards_completed_delta=discards_completed_delta,
        discarded_bytes_delta=discarded_bytes_delta,
        discard_time_delta_ms=discard_time_delta_ms,
        discard_iops=discard_iops,
        discard_bytes_per_second=discard_bytes_per_second,
        average_discard_time_ms=average_discard_time_ms,
        flushes_completed_delta=flushes_completed_delta,
        flush_time_delta_ms=flush_time_delta_ms,
        flush_iops=flush_iops,
        average_flush_time_ms=average_flush_time_ms,
    )


def _calculate_discard_metrics(
    previous: DiskStats,
    current: DiskStats,
    *,
    sample_interval_seconds: float,
) -> tuple[
    int | None,
    int | None,
    int | None,
    float | None,
    float | None,
    float | None,
]:
    """Calculate optional discard metrics when both snapshots support them."""

    completed_delta = _optional_delta(
        previous.discards_completed,
        current.discards_completed,
    )
    sectors_delta = _optional_delta(
        previous.sectors_discarded,
        current.sectors_discarded,
    )
    time_delta_ms = _optional_delta(
        previous.discard_time_ms,
        current.discard_time_ms,
    )

    if completed_delta is None or sectors_delta is None or time_delta_ms is None:
        return (
            None,
            None,
            None,
            None,
            None,
            None,
        )

    discarded_bytes_delta = sectors_delta * DISK_SECTOR_BYTES

    return (
        completed_delta,
        discarded_bytes_delta,
        time_delta_ms,
        _rate(
            completed_delta,
            sample_interval_seconds,
        ),
        _rate(
            discarded_bytes_delta,
            sample_interval_seconds,
        ),
        _average_time(
            time_delta_ms,
            completed_delta,
        ),
    )


def _calculate_flush_metrics(
    previous: DiskStats,
    current: DiskStats,
    *,
    sample_interval_seconds: float,
) -> tuple[
    int | None,
    int | None,
    float | None,
    float | None,
]:
    """Calculate optional flush metrics when both snapshots support them."""

    completed_delta = _optional_delta(
        previous.flushes_completed,
        current.flushes_completed,
    )
    time_delta_ms = _optional_delta(
        previous.flush_time_ms,
        current.flush_time_ms,
    )

    if completed_delta is None or time_delta_ms is None:
        return (
            None,
            None,
            None,
            None,
        )

    return (
        completed_delta,
        time_delta_ms,
        _rate(
            completed_delta,
            sample_interval_seconds,
        ),
        _average_time(
            time_delta_ms,
            completed_delta,
        ),
    )


def _optional_delta(
    previous: int | None,
    current: int | None,
) -> int | None:
    """Return an optional cumulative-counter delta."""

    if previous is None or current is None:
        return None

    return current - previous


def _average_time(
    total_time_delta_ms: int,
    completed_delta: int,
) -> float | None:
    """Return average accumulated request time per completed operation."""

    if completed_delta == 0:
        return None

    return float(total_time_delta_ms) / float(completed_delta)


def _rate(
    value: int,
    sample_interval_seconds: float,
) -> float:
    """Return a per-second rate."""

    return float(value) / sample_interval_seconds


def _normalize_sample_interval(value: object) -> float:
    """Validate and normalize an actual disk I/O sampling interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiskIoSamplingError("sample interval must be a number")

    normalized = float(value)

    if not math.isfinite(normalized):
        raise DiskIoSamplingError("sample interval must be finite")

    if normalized <= 0.0:
        raise DiskIoSamplingError("sample interval must be greater than zero")

    return normalized


def _validate_average_metric(
    name: str,
    value: float | None,
    completed_delta: int,
) -> None:
    """Validate an average-time metric against operation count."""

    if completed_delta == 0:
        if value is not None:
            raise ValueError(f"{name} must be None when no operations completed")

        return

    if value is None:
        raise ValueError(f"{name} must be present when operations completed")

    _require_nonnegative_float(name, value)


def _require_nonempty_text(name: str, value: str) -> None:
    """Require a non-empty string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _require_nonnegative_float(name: str, value: float) -> None:
    """Require a finite non-negative numeric value."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")

    if value < 0.0:
        raise ValueError(f"{name} must not be negative")


def _require_positive_finite_float(
    name: str,
    value: float,
) -> None:
    """Require a finite floating-point value greater than zero."""

    _require_nonnegative_float(name, value)

    if value == 0.0:
        raise ValueError(f"{name} must be greater than zero")


def _require_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware datetime value."""

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
