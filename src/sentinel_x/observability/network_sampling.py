"""Sampled Linux network-interface metrics for Sentinel-X."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sentinel_x.observability.network import (
    NetworkIdentityProbeFailure,
    NetworkInterfaceIdentity,
    NetworkInterfaceRecord,
    NetworkInterfaceStats,
    NetworkObservationError,
    NetworkStatsSnapshot,
)


class NetworkSamplingError(NetworkObservationError):
    """Raised when network snapshots cannot be compared safely."""


class NetworkSampleStatus(str, Enum):
    """Comparison status for one interface across two snapshots."""

    SAMPLED = "sampled"
    APPEARED = "appeared"
    DISAPPEARED = "disappeared"
    COUNTER_RESET = "counter_reset"
    IDENTITY_CHANGED = "identity_changed"
    IDENTITY_UNVERIFIED = "identity_unverified"


class NetworkMatchMethod(str, Enum):
    """Identity method used to associate snapshot records."""

    IFINDEX = "ifindex"
    NAME = "name"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class NetworkInterfaceMetrics:
    """Derived per-interface network metrics for one sampling interval."""

    rx_bytes_delta: int
    tx_bytes_delta: int
    rx_packets_delta: int
    tx_packets_delta: int
    rx_errors_delta: int
    tx_errors_delta: int
    rx_dropped_delta: int
    tx_dropped_delta: int
    rx_fifo_errors_delta: int
    rx_frame_errors_aggregate_delta: int
    rx_compressed_delta: int
    rx_multicast_delta: int
    tx_fifo_errors_delta: int
    tx_collisions_delta: int
    tx_carrier_errors_aggregate_delta: int
    tx_compressed_delta: int
    rx_bytes_per_second: float
    tx_bytes_per_second: float
    rx_packets_per_second: float
    tx_packets_per_second: float
    rx_errors_per_second: float
    tx_errors_per_second: float
    rx_dropped_per_second: float
    tx_dropped_per_second: float

    def __post_init__(self) -> None:
        """Validate sampled network metric values."""

        integer_metrics = (
            ("rx_bytes_delta", self.rx_bytes_delta),
            ("tx_bytes_delta", self.tx_bytes_delta),
            ("rx_packets_delta", self.rx_packets_delta),
            ("tx_packets_delta", self.tx_packets_delta),
            ("rx_errors_delta", self.rx_errors_delta),
            ("tx_errors_delta", self.tx_errors_delta),
            ("rx_dropped_delta", self.rx_dropped_delta),
            ("tx_dropped_delta", self.tx_dropped_delta),
            ("rx_fifo_errors_delta", self.rx_fifo_errors_delta),
            (
                "rx_frame_errors_aggregate_delta",
                self.rx_frame_errors_aggregate_delta,
            ),
            ("rx_compressed_delta", self.rx_compressed_delta),
            ("rx_multicast_delta", self.rx_multicast_delta),
            ("tx_fifo_errors_delta", self.tx_fifo_errors_delta),
            ("tx_collisions_delta", self.tx_collisions_delta),
            (
                "tx_carrier_errors_aggregate_delta",
                self.tx_carrier_errors_aggregate_delta,
            ),
            ("tx_compressed_delta", self.tx_compressed_delta),
        )

        for metric_name, metric_value in integer_metrics:
            _require_nonnegative_int(metric_name, metric_value)

        rate_metrics = (
            ("rx_bytes_per_second", self.rx_bytes_per_second),
            ("tx_bytes_per_second", self.tx_bytes_per_second),
            ("rx_packets_per_second", self.rx_packets_per_second),
            ("tx_packets_per_second", self.tx_packets_per_second),
            ("rx_errors_per_second", self.rx_errors_per_second),
            ("tx_errors_per_second", self.tx_errors_per_second),
            ("rx_dropped_per_second", self.rx_dropped_per_second),
            ("tx_dropped_per_second", self.tx_dropped_per_second),
        )

        for rate_name, rate_value in rate_metrics:
            _require_nonnegative_float(rate_name, rate_value)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly sampled network metrics."""

        return {
            "rx_bytes_delta": self.rx_bytes_delta,
            "tx_bytes_delta": self.tx_bytes_delta,
            "rx_packets_delta": self.rx_packets_delta,
            "tx_packets_delta": self.tx_packets_delta,
            "rx_errors_delta": self.rx_errors_delta,
            "tx_errors_delta": self.tx_errors_delta,
            "rx_dropped_delta": self.rx_dropped_delta,
            "tx_dropped_delta": self.tx_dropped_delta,
            "rx_fifo_errors_delta": self.rx_fifo_errors_delta,
            "rx_frame_errors_aggregate_delta": (self.rx_frame_errors_aggregate_delta),
            "rx_compressed_delta": self.rx_compressed_delta,
            "rx_multicast_delta": self.rx_multicast_delta,
            "tx_fifo_errors_delta": self.tx_fifo_errors_delta,
            "tx_collisions_delta": self.tx_collisions_delta,
            "tx_carrier_errors_aggregate_delta": (
                self.tx_carrier_errors_aggregate_delta
            ),
            "tx_compressed_delta": self.tx_compressed_delta,
            "rx_bytes_per_second": self.rx_bytes_per_second,
            "tx_bytes_per_second": self.tx_bytes_per_second,
            "rx_packets_per_second": self.rx_packets_per_second,
            "tx_packets_per_second": self.tx_packets_per_second,
            "rx_errors_per_second": self.rx_errors_per_second,
            "tx_errors_per_second": self.tx_errors_per_second,
            "rx_dropped_per_second": self.rx_dropped_per_second,
            "tx_dropped_per_second": self.tx_dropped_per_second,
        }


@dataclass(frozen=True, slots=True)
class NetworkInterfaceSample:
    """Comparison result for one network interface."""

    start_record: NetworkInterfaceRecord | None
    end_record: NetworkInterfaceRecord | None
    status: NetworkSampleStatus
    match_method: NetworkMatchMethod
    metrics: NetworkInterfaceMetrics | None
    regressed_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate status-specific network sample invariants."""

        if not isinstance(self.status, NetworkSampleStatus):
            raise TypeError("status must be a NetworkSampleStatus")

        if not isinstance(self.match_method, NetworkMatchMethod):
            raise TypeError("match_method must be a NetworkMatchMethod")

        if self.start_record is None and self.end_record is None:
            raise ValueError("at least one network endpoint must be present")

        for field_name in self.regressed_fields:
            _require_nonempty_text("regressed field", field_name)

        if len(set(self.regressed_fields)) != len(self.regressed_fields):
            raise ValueError("regressed_fields must not contain duplicates")

        if self.status is NetworkSampleStatus.SAMPLED:
            self._validate_sampled()

        elif self.status is NetworkSampleStatus.COUNTER_RESET:
            self._validate_counter_reset()

        elif self.status is NetworkSampleStatus.IDENTITY_CHANGED:
            self._validate_identity_changed()

        elif self.status is NetworkSampleStatus.IDENTITY_UNVERIFIED:
            self._validate_identity_unverified()

        elif self.status is NetworkSampleStatus.APPEARED:
            self._validate_appeared()

        elif self.status is NetworkSampleStatus.DISAPPEARED:
            self._validate_disappeared()

    def _validate_sampled(self) -> None:
        """Validate a successfully sampled interface."""

        self._require_both_endpoints()
        self._require_ifindex_match()

        if self.metrics is None:
            raise ValueError("sampled interface requires derived metrics")

        if self.regressed_fields:
            raise ValueError("sampled interface must not contain regressions")

    def _validate_counter_reset(self) -> None:
        """Validate a cumulative-counter reset result."""

        self._require_both_endpoints()
        self._require_ifindex_match()

        if self.metrics is not None:
            raise ValueError("counter reset must not contain derived metrics")

        if not self.regressed_fields:
            raise ValueError("counter reset must identify regressed fields")

    def _validate_identity_changed(self) -> None:
        """Validate a strong identity-change result."""

        self._require_both_endpoints()

        assert self.start_record is not None
        assert self.end_record is not None

        if self.match_method is NetworkMatchMethod.IFINDEX:
            self._require_ifindex_match()

        elif self.match_method is NetworkMatchMethod.NAME:
            if self.start_record.identity.name != self.end_record.identity.name:
                raise ValueError(
                    "name identity change requires the same interface name"
                )

            start_ifindex = self.start_record.identity.ifindex
            end_ifindex = self.end_record.identity.ifindex

            if start_ifindex is None or end_ifindex is None:
                raise ValueError("name identity change requires resolved ifindexes")

            if start_ifindex == end_ifindex:
                raise ValueError("name identity change requires different ifindexes")

        else:
            raise ValueError("identity change requires an identity match method")

        if self.metrics is not None or self.regressed_fields:
            raise ValueError(
                "identity change must not contain metrics or counter regressions"
            )

    def _validate_identity_unverified(self) -> None:
        """Validate a name-only association with incomplete identity."""

        self._require_both_endpoints()

        assert self.start_record is not None
        assert self.end_record is not None

        if self.match_method is not NetworkMatchMethod.NAME:
            raise ValueError("unverified identity must use name matching")

        if self.start_record.identity.name != self.end_record.identity.name:
            raise ValueError("unverified identity requires the same interface name")

        if (
            self.start_record.identity.ifindex is not None
            and self.end_record.identity.ifindex is not None
        ):
            raise ValueError("unverified identity requires a missing ifindex")

        if self.metrics is not None or self.regressed_fields:
            raise ValueError(
                "unverified identity must not contain metrics or counter regressions"
            )

    def _validate_appeared(self) -> None:
        """Validate an interface appearance result."""

        if self.start_record is not None or self.end_record is None:
            raise ValueError("appeared interface requires only end_record")

        self._require_unmatched_without_metrics()

    def _validate_disappeared(self) -> None:
        """Validate an interface disappearance result."""

        if self.start_record is None or self.end_record is not None:
            raise ValueError("disappeared interface requires only start_record")

        self._require_unmatched_without_metrics()

    def _require_both_endpoints(self) -> None:
        """Require both snapshot records."""

        if self.start_record is None or self.end_record is None:
            raise ValueError("comparison status requires both network endpoints")

    def _require_ifindex_match(self) -> None:
        """Require a verified same-ifindex association."""

        assert self.start_record is not None
        assert self.end_record is not None

        if self.match_method is not NetworkMatchMethod.IFINDEX:
            raise ValueError("sampled comparison requires ifindex matching")

        start_ifindex = self.start_record.identity.ifindex
        end_ifindex = self.end_record.identity.ifindex

        if start_ifindex is None or end_ifindex is None:
            raise ValueError("ifindex matching requires resolved ifindexes")

        if start_ifindex != end_ifindex:
            raise ValueError("ifindex-matched endpoints must use the same ifindex")

    def _require_unmatched_without_metrics(self) -> None:
        """Require no match metadata for one-sided samples."""

        if self.match_method is not NetworkMatchMethod.NONE:
            raise ValueError("one-sided interface status must be unmatched")

        if self.metrics is not None or self.regressed_fields:
            raise ValueError(
                "one-sided interface status must not contain metrics or regressions"
            )

    @property
    def identity(self) -> NetworkInterfaceIdentity:
        """Return the most recent available interface identity."""

        if self.end_record is not None:
            return self.end_record.identity

        assert self.start_record is not None

        return self.start_record.identity

    @property
    def name_changed(self) -> bool:
        """Return whether an associated interface name changed."""

        if self.start_record is None or self.end_record is None:
            return False

        return self.start_record.identity.name != self.end_record.identity.name

    @property
    def iflink_changed(self) -> bool:
        """Return whether resolved interface-link topology changed."""

        if self.start_record is None or self.end_record is None:
            return False

        start_value = self.start_record.identity.iflink
        end_value = self.end_record.identity.iflink

        return (
            start_value is not None
            and end_value is not None
            and start_value != end_value
        )

    @property
    def mtu_changed(self) -> bool:
        """Return whether resolved MTU changed."""

        if self.start_record is None or self.end_record is None:
            return False

        start_value = self.start_record.identity.mtu
        end_value = self.end_record.identity.mtu

        return (
            start_value is not None
            and end_value is not None
            and start_value != end_value
        )

    @property
    def operstate_changed(self) -> bool:
        """Return whether resolved operational state changed."""

        if self.start_record is None or self.end_record is None:
            return False

        start_value = self.start_record.identity.operstate
        end_value = self.end_record.identity.operstate

        return (
            start_value is not None
            and end_value is not None
            and start_value is not end_value
        )

    @property
    def carrier_changed(self) -> bool:
        """Return whether resolved carrier state changed."""

        if self.start_record is None or self.end_record is None:
            return False

        start_value = self.start_record.identity.carrier
        end_value = self.end_record.identity.carrier

        return (
            start_value is not None
            and end_value is not None
            and start_value != end_value
        )

    @property
    def address_changed(self) -> bool:
        """Return whether resolved interface address changed."""

        if self.start_record is None or self.end_record is None:
            return False

        start_value = self.start_record.identity.address
        end_value = self.end_record.identity.address

        return (
            start_value is not None
            and end_value is not None
            and start_value != end_value
        )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly interface sampling evidence."""

        return {
            "identity": self.identity.to_dict(),
            "status": self.status.value,
            "match_method": self.match_method.value,
            "regressed_fields": list(self.regressed_fields),
            "changes": {
                "name_changed": self.name_changed,
                "iflink_changed": self.iflink_changed,
                "mtu_changed": self.mtu_changed,
                "operstate_changed": self.operstate_changed,
                "carrier_changed": self.carrier_changed,
                "address_changed": self.address_changed,
            },
            "start_record": (
                None if self.start_record is None else self.start_record.to_dict()
            ),
            "end_record": (
                None if self.end_record is None else self.end_record.to_dict()
            ),
            "metrics": None if self.metrics is None else self.metrics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class NetworkObservation:
    """One sampled network observation across visible interfaces."""

    sample_started_at: datetime
    captured_at: datetime
    sample_interval_seconds: float
    interfaces: tuple[NetworkInterfaceSample, ...]
    start_identity_failures: tuple[NetworkIdentityProbeFailure, ...]
    end_identity_failures: tuple[NetworkIdentityProbeFailure, ...]

    def __post_init__(self) -> None:
        """Validate sampled network observation metadata."""

        _require_aware_datetime("sample_started_at", self.sample_started_at)
        _require_aware_datetime("captured_at", self.captured_at)
        _require_positive_finite_float(
            "sample_interval_seconds",
            self.sample_interval_seconds,
        )

        if not self.interfaces:
            raise ValueError("interfaces must not be empty")

        start_names: list[str] = []
        end_names: list[str] = []

        for sample in self.interfaces:
            if not isinstance(sample, NetworkInterfaceSample):
                raise TypeError(
                    "interfaces must contain NetworkInterfaceSample objects"
                )

            if sample.start_record is not None:
                start_names.append(sample.start_record.identity.name)

            if sample.end_record is not None:
                end_names.append(sample.end_record.identity.name)

        if len(set(start_names)) != len(start_names):
            raise ValueError("network observation reuses a start interface record")

        if len(set(end_names)) != len(end_names):
            raise ValueError("network observation reuses an end interface record")

        for failure in self.start_identity_failures:
            if not isinstance(failure, NetworkIdentityProbeFailure):
                raise TypeError(
                    "start_identity_failures must contain "
                    "NetworkIdentityProbeFailure objects"
                )

        for failure in self.end_identity_failures:
            if not isinstance(failure, NetworkIdentityProbeFailure):
                raise TypeError(
                    "end_identity_failures must contain "
                    "NetworkIdentityProbeFailure objects"
                )

    @property
    def sampled_count(self) -> int:
        """Return successfully sampled interfaces."""

        return sum(
            sample.status is NetworkSampleStatus.SAMPLED for sample in self.interfaces
        )

    @property
    def appeared_count(self) -> int:
        """Return interfaces appearing during the sampling interval."""

        return sum(
            sample.status is NetworkSampleStatus.APPEARED for sample in self.interfaces
        )

    @property
    def disappeared_count(self) -> int:
        """Return interfaces disappearing during the sampling interval."""

        return sum(
            sample.status is NetworkSampleStatus.DISAPPEARED
            for sample in self.interfaces
        )

    @property
    def counter_reset_count(self) -> int:
        """Return interfaces with cumulative counter regression."""

        return sum(
            sample.status is NetworkSampleStatus.COUNTER_RESET
            for sample in self.interfaces
        )

    @property
    def identity_changed_count(self) -> int:
        """Return interfaces with strong identity replacement evidence."""

        return sum(
            sample.status is NetworkSampleStatus.IDENTITY_CHANGED
            for sample in self.interfaces
        )

    @property
    def identity_unverified_count(self) -> int:
        """Return name-only matches without verified ifindex identity."""

        return sum(
            sample.status is NetworkSampleStatus.IDENTITY_UNVERIFIED
            for sample in self.interfaces
        )

    @property
    def renamed_count(self) -> int:
        """Return verified same-ifindex interfaces whose name changed."""

        return sum(
            sample.match_method is NetworkMatchMethod.IFINDEX
            and sample.status is not NetworkSampleStatus.IDENTITY_CHANGED
            and sample.name_changed
            for sample in self.interfaces
        )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly sampled network evidence."""

        return {
            "sample_started_at": self.sample_started_at.isoformat(),
            "captured_at": self.captured_at.isoformat(),
            "sample_interval_seconds": self.sample_interval_seconds,
            "summary": {
                "interface_sample_count": len(self.interfaces),
                "sampled_count": self.sampled_count,
                "appeared_count": self.appeared_count,
                "disappeared_count": self.disappeared_count,
                "counter_reset_count": self.counter_reset_count,
                "identity_changed_count": self.identity_changed_count,
                "identity_unverified_count": self.identity_unverified_count,
                "renamed_count": self.renamed_count,
                "start_identity_failure_count": len(self.start_identity_failures),
                "end_identity_failure_count": len(self.end_identity_failures),
            },
            "interfaces": [sample.to_dict() for sample in self.interfaces],
            "start_identity_failures": [
                failure.to_dict() for failure in self.start_identity_failures
            ],
            "end_identity_failures": [
                failure.to_dict() for failure in self.end_identity_failures
            ],
        }


def build_network_observation(
    previous: NetworkStatsSnapshot,
    current: NetworkStatsSnapshot,
    *,
    sample_interval_seconds: object,
) -> NetworkObservation:
    """Compare two raw network snapshots using conservative identity rules."""

    if not isinstance(previous, NetworkStatsSnapshot):
        raise NetworkSamplingError("previous must be a NetworkStatsSnapshot")

    if not isinstance(current, NetworkStatsSnapshot):
        raise NetworkSamplingError("current must be a NetworkStatsSnapshot")

    interval = _normalize_sample_interval(sample_interval_seconds)

    previous_by_ifindex = {
        record.identity.ifindex: record
        for record in previous.interfaces
        if record.identity.ifindex is not None
    }
    previous_by_name = {record.identity.name: record for record in previous.interfaces}

    consumed_previous_names: set[str] = set()
    samples: list[NetworkInterfaceSample] = []

    for current_record in current.interfaces:
        previous_record, match_method = _select_previous_record(
            current_record,
            previous_by_ifindex=previous_by_ifindex,
            previous_by_name=previous_by_name,
            consumed_previous_names=consumed_previous_names,
        )

        if previous_record is None:
            samples.append(
                NetworkInterfaceSample(
                    start_record=None,
                    end_record=current_record,
                    status=NetworkSampleStatus.APPEARED,
                    match_method=NetworkMatchMethod.NONE,
                    metrics=None,
                )
            )
            continue

        consumed_previous_names.add(previous_record.identity.name)

        if match_method is NetworkMatchMethod.NAME:
            samples.append(
                _build_name_matched_sample(
                    previous_record,
                    current_record,
                )
            )
            continue

        if _verified_identity_changed(previous_record, current_record):
            samples.append(
                NetworkInterfaceSample(
                    start_record=previous_record,
                    end_record=current_record,
                    status=NetworkSampleStatus.IDENTITY_CHANGED,
                    match_method=NetworkMatchMethod.IFINDEX,
                    metrics=None,
                )
            )
            continue

        regressed_fields = _find_regressed_fields(
            previous_record.stats,
            current_record.stats,
        )

        if regressed_fields:
            samples.append(
                NetworkInterfaceSample(
                    start_record=previous_record,
                    end_record=current_record,
                    status=NetworkSampleStatus.COUNTER_RESET,
                    match_method=NetworkMatchMethod.IFINDEX,
                    metrics=None,
                    regressed_fields=regressed_fields,
                )
            )
            continue

        samples.append(
            NetworkInterfaceSample(
                start_record=previous_record,
                end_record=current_record,
                status=NetworkSampleStatus.SAMPLED,
                match_method=NetworkMatchMethod.IFINDEX,
                metrics=_calculate_metrics(
                    previous_record.stats,
                    current_record.stats,
                    sample_interval_seconds=interval,
                ),
            )
        )

    for previous_record in previous.interfaces:
        if previous_record.identity.name in consumed_previous_names:
            continue

        samples.append(
            NetworkInterfaceSample(
                start_record=previous_record,
                end_record=None,
                status=NetworkSampleStatus.DISAPPEARED,
                match_method=NetworkMatchMethod.NONE,
                metrics=None,
            )
        )

    try:
        return NetworkObservation(
            sample_started_at=previous.captured_at,
            captured_at=current.captured_at,
            sample_interval_seconds=interval,
            interfaces=tuple(samples),
            start_identity_failures=previous.identity_failures,
            end_identity_failures=current.identity_failures,
        )

    except (TypeError, ValueError) as exc:
        raise NetworkSamplingError(f"invalid network observation: {exc}") from exc


def _select_previous_record(
    current_record: NetworkInterfaceRecord,
    *,
    previous_by_ifindex: dict[int, NetworkInterfaceRecord],
    previous_by_name: dict[str, NetworkInterfaceRecord],
    consumed_previous_names: set[str],
) -> tuple[NetworkInterfaceRecord | None, NetworkMatchMethod]:
    """Select one previous record without reusing snapshot evidence."""

    current_ifindex = current_record.identity.ifindex

    if current_ifindex is not None:
        ifindex_match = previous_by_ifindex.get(current_ifindex)

        if (
            ifindex_match is not None
            and ifindex_match.identity.name not in consumed_previous_names
        ):
            return ifindex_match, NetworkMatchMethod.IFINDEX

    name_match = previous_by_name.get(current_record.identity.name)

    if name_match is None or name_match.identity.name in consumed_previous_names:
        return None, NetworkMatchMethod.NONE

    return name_match, NetworkMatchMethod.NAME


def _build_name_matched_sample(
    previous: NetworkInterfaceRecord,
    current: NetworkInterfaceRecord,
) -> NetworkInterfaceSample:
    """Classify a same-name association which was not verified by ifindex."""

    previous_ifindex = previous.identity.ifindex
    current_ifindex = current.identity.ifindex

    if previous_ifindex is not None and current_ifindex is not None:
        return NetworkInterfaceSample(
            start_record=previous,
            end_record=current,
            status=NetworkSampleStatus.IDENTITY_CHANGED,
            match_method=NetworkMatchMethod.NAME,
            metrics=None,
        )

    return NetworkInterfaceSample(
        start_record=previous,
        end_record=current,
        status=NetworkSampleStatus.IDENTITY_UNVERIFIED,
        match_method=NetworkMatchMethod.NAME,
        metrics=None,
    )


def _verified_identity_changed(
    previous: NetworkInterfaceRecord,
    current: NetworkInterfaceRecord,
) -> bool:
    """Return strong replacement evidence for a same-ifindex association."""

    previous_type = previous.identity.protocol_type
    current_type = current.identity.protocol_type

    return (
        previous_type is not None
        and current_type is not None
        and previous_type != current_type
    )


def _find_regressed_fields(
    previous: NetworkInterfaceStats,
    current: NetworkInterfaceStats,
) -> tuple[str, ...]:
    """Return known cumulative /proc/net/dev counters that decreased."""

    regressed_fields: list[str] = []

    counter_pairs = (
        ("rx_bytes", previous.rx_bytes, current.rx_bytes),
        ("rx_packets", previous.rx_packets, current.rx_packets),
        ("rx_errors", previous.rx_errors, current.rx_errors),
        ("rx_dropped", previous.rx_dropped, current.rx_dropped),
        (
            "rx_fifo_errors",
            previous.rx_fifo_errors,
            current.rx_fifo_errors,
        ),
        (
            "rx_frame_errors_aggregate",
            previous.rx_frame_errors_aggregate,
            current.rx_frame_errors_aggregate,
        ),
        (
            "rx_compressed",
            previous.rx_compressed,
            current.rx_compressed,
        ),
        (
            "rx_multicast",
            previous.rx_multicast,
            current.rx_multicast,
        ),
        ("tx_bytes", previous.tx_bytes, current.tx_bytes),
        ("tx_packets", previous.tx_packets, current.tx_packets),
        ("tx_errors", previous.tx_errors, current.tx_errors),
        ("tx_dropped", previous.tx_dropped, current.tx_dropped),
        (
            "tx_fifo_errors",
            previous.tx_fifo_errors,
            current.tx_fifo_errors,
        ),
        (
            "tx_collisions",
            previous.tx_collisions,
            current.tx_collisions,
        ),
        (
            "tx_carrier_errors_aggregate",
            previous.tx_carrier_errors_aggregate,
            current.tx_carrier_errors_aggregate,
        ),
        (
            "tx_compressed",
            previous.tx_compressed,
            current.tx_compressed,
        ),
    )

    for counter_name, start_value, end_value in counter_pairs:
        if end_value < start_value:
            regressed_fields.append(counter_name)

    return tuple(regressed_fields)


def _calculate_metrics(
    previous: NetworkInterfaceStats,
    current: NetworkInterfaceStats,
    *,
    sample_interval_seconds: float,
) -> NetworkInterfaceMetrics:
    """Calculate known per-interface counter deltas and rates."""

    rx_bytes_delta = current.rx_bytes - previous.rx_bytes
    tx_bytes_delta = current.tx_bytes - previous.tx_bytes
    rx_packets_delta = current.rx_packets - previous.rx_packets
    tx_packets_delta = current.tx_packets - previous.tx_packets
    rx_errors_delta = current.rx_errors - previous.rx_errors
    tx_errors_delta = current.tx_errors - previous.tx_errors
    rx_dropped_delta = current.rx_dropped - previous.rx_dropped
    tx_dropped_delta = current.tx_dropped - previous.tx_dropped

    return NetworkInterfaceMetrics(
        rx_bytes_delta=rx_bytes_delta,
        tx_bytes_delta=tx_bytes_delta,
        rx_packets_delta=rx_packets_delta,
        tx_packets_delta=tx_packets_delta,
        rx_errors_delta=rx_errors_delta,
        tx_errors_delta=tx_errors_delta,
        rx_dropped_delta=rx_dropped_delta,
        tx_dropped_delta=tx_dropped_delta,
        rx_fifo_errors_delta=current.rx_fifo_errors - previous.rx_fifo_errors,
        rx_frame_errors_aggregate_delta=(
            current.rx_frame_errors_aggregate - previous.rx_frame_errors_aggregate
        ),
        rx_compressed_delta=current.rx_compressed - previous.rx_compressed,
        rx_multicast_delta=current.rx_multicast - previous.rx_multicast,
        tx_fifo_errors_delta=current.tx_fifo_errors - previous.tx_fifo_errors,
        tx_collisions_delta=current.tx_collisions - previous.tx_collisions,
        tx_carrier_errors_aggregate_delta=(
            current.tx_carrier_errors_aggregate - previous.tx_carrier_errors_aggregate
        ),
        tx_compressed_delta=current.tx_compressed - previous.tx_compressed,
        rx_bytes_per_second=_rate(rx_bytes_delta, sample_interval_seconds),
        tx_bytes_per_second=_rate(tx_bytes_delta, sample_interval_seconds),
        rx_packets_per_second=_rate(rx_packets_delta, sample_interval_seconds),
        tx_packets_per_second=_rate(tx_packets_delta, sample_interval_seconds),
        rx_errors_per_second=_rate(rx_errors_delta, sample_interval_seconds),
        tx_errors_per_second=_rate(tx_errors_delta, sample_interval_seconds),
        rx_dropped_per_second=_rate(rx_dropped_delta, sample_interval_seconds),
        tx_dropped_per_second=_rate(tx_dropped_delta, sample_interval_seconds),
    )


def _rate(value: int, sample_interval_seconds: float) -> float:
    """Return one non-negative per-second rate."""

    return float(value) / sample_interval_seconds


def _normalize_sample_interval(value: object) -> float:
    """Validate and normalize an actual network sampling interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NetworkSamplingError("sample interval must be a number")

    normalized = float(value)

    if not math.isfinite(normalized):
        raise NetworkSamplingError("sample interval must be finite")

    if normalized <= 0.0:
        raise NetworkSamplingError("sample interval must be greater than zero")

    return normalized


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
    """Require a finite non-negative number."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")

    if value < 0.0:
        raise ValueError(f"{name} must not be negative")


def _require_positive_finite_float(name: str, value: float) -> None:
    """Require a finite number greater than zero."""

    _require_nonnegative_float(name, value)

    if value == 0.0:
        raise ValueError(f"{name} must be greater than zero")


def _require_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware datetime."""

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
