"""Stateful systemd incident lifecycle and deduplication for Sentinel-X.

Phase 4C turns repeated deterministic health assessments into bounded incident
state.  It does not publish to the EventBus; live publication is reserved for
Phase 4D so lifecycle semantics can be validated independently of transport.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from sentinel_x.core.events import EventSeverity
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)
from sentinel_x.systemd.models import validate_service_unit_name

INCIDENT_TRACKER_VERSION: Final[str] = "sentinel-x.systemd-incident-tracker.v1"

_INCIDENT_ID_DOMAIN: Final[bytes] = b"sentinel-x.systemd-incident-tracker.v1\x00"
_INCIDENT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"inc-[0-9a-f]{64}")
_DEFAULT_MAX_TRACKED_TARGETS: Final[int] = 256
_MAX_TRACKED_TARGETS: Final[int] = 4096
_MAX_TEXT_LENGTH: Final[int] = 512


class IncidentTrackingError(RuntimeError):
    """Base error for incident lifecycle tracking."""


class IncidentTrackingContractError(IncidentTrackingError):
    """Raised when lifecycle input or restored state violates the contract."""


class IncidentState(StrEnum):
    """Lifecycle state of a systemd incident."""

    OPEN = "open"
    RESOLVED = "resolved"


class IncidentChangeKind(StrEnum):
    """State mutation caused by one detector assessment."""

    OPENED = "opened"
    UPDATED = "updated"
    RESOLVED = "resolved"


class IncidentResolutionReason(StrEnum):
    """Why an open incident was closed."""

    RECOVERED = "recovered"
    RECLASSIFIED = "reclassified"


@dataclass(frozen=True, slots=True)
class SystemdServiceIncident:
    """Immutable current or resolved systemd incident episode."""

    incident_id: str
    target_unit: str
    canonical_unit: str
    anomaly_class: DetectionAnomalyClass
    severity: EventSeverity
    opened_at: datetime
    opened_monotonic_usec: int
    first_assessment_id: str
    first_source_event_id: str
    latest_assessment_id: str
    latest_source_event_id: str
    latest_assessed_at: datetime
    latest_assessed_monotonic_usec: int
    observation_count: int
    state: IncidentState = IncidentState.OPEN
    resolved_at: datetime | None = None
    resolved_monotonic_usec: int | None = None
    resolution_reason: IncidentResolutionReason | None = None
    resolution_assessment_id: str | None = None
    resolution_source_event_id: str | None = None
    replacement_incident_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.incident_id, str)
            or _INCIDENT_ID_PATTERN.fullmatch(self.incident_id) is None
        ):
            raise IncidentTrackingContractError(
                "incident_id must be an inc- prefixed SHA-256 identity"
            )
        validate_service_unit_name(self.target_unit, field_name="target_unit")
        validate_service_unit_name(self.canonical_unit, field_name="canonical_unit")
        if not isinstance(self.anomaly_class, DetectionAnomalyClass):
            raise IncidentTrackingContractError(
                "anomaly_class must be a DetectionAnomalyClass"
            )
        if not isinstance(self.severity, EventSeverity):
            raise IncidentTrackingContractError("severity must be an EventSeverity")
        _validate_aware_datetime(self.opened_at, field_name="opened_at")
        _validate_nonnegative_int(
            self.opened_monotonic_usec,
            field_name="opened_monotonic_usec",
        )
        for field_name, value in (
            ("first_assessment_id", self.first_assessment_id),
            ("first_source_event_id", self.first_source_event_id),
            ("latest_assessment_id", self.latest_assessment_id),
            ("latest_source_event_id", self.latest_source_event_id),
        ):
            _validate_text(value, field_name=field_name)
        _validate_aware_datetime(
            self.latest_assessed_at,
            field_name="latest_assessed_at",
        )
        _validate_nonnegative_int(
            self.latest_assessed_monotonic_usec,
            field_name="latest_assessed_monotonic_usec",
        )
        _validate_positive_int(self.observation_count, field_name="observation_count")
        if self.latest_assessed_monotonic_usec < self.opened_monotonic_usec:
            raise IncidentTrackingContractError(
                "latest assessment cannot precede incident opening"
            )
        if not isinstance(self.state, IncidentState):
            raise IncidentTrackingContractError("state must be an IncidentState")
        self._validate_resolution_contract()

    @property
    def is_open(self) -> bool:
        return self.state is IncidentState.OPEN

    def to_dict(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "target_unit": self.target_unit,
            "canonical_unit": self.canonical_unit,
            "anomaly_class": self.anomaly_class.value,
            "severity": self.severity.value,
            "opened_at": self.opened_at.isoformat(),
            "opened_monotonic_usec": self.opened_monotonic_usec,
            "first_assessment_id": self.first_assessment_id,
            "first_source_event_id": self.first_source_event_id,
            "latest_assessment_id": self.latest_assessment_id,
            "latest_source_event_id": self.latest_source_event_id,
            "latest_assessed_at": self.latest_assessed_at.isoformat(),
            "latest_assessed_monotonic_usec": self.latest_assessed_monotonic_usec,
            "observation_count": self.observation_count,
            "state": self.state.value,
            "resolved_at": (
                None if self.resolved_at is None else self.resolved_at.isoformat()
            ),
            "resolved_monotonic_usec": self.resolved_monotonic_usec,
            "resolution_reason": (
                None if self.resolution_reason is None else self.resolution_reason.value
            ),
            "resolution_assessment_id": self.resolution_assessment_id,
            "resolution_source_event_id": self.resolution_source_event_id,
            "replacement_incident_id": self.replacement_incident_id,
        }

    def _validate_resolution_contract(self) -> None:
        resolution_values = (
            self.resolved_at,
            self.resolved_monotonic_usec,
            self.resolution_reason,
            self.resolution_assessment_id,
            self.resolution_source_event_id,
        )
        if self.state is IncidentState.OPEN:
            if any(value is not None for value in resolution_values):
                raise IncidentTrackingContractError(
                    "open incidents must not carry resolution metadata"
                )
            if self.replacement_incident_id is not None:
                raise IncidentTrackingContractError(
                    "open incidents must not name a replacement incident"
                )
            return

        if any(value is None for value in resolution_values):
            raise IncidentTrackingContractError(
                "resolved incidents require complete resolution metadata"
            )
        if self.resolved_at is not None:
            _validate_aware_datetime(self.resolved_at, field_name="resolved_at")
        if self.resolved_monotonic_usec is not None:
            _validate_nonnegative_int(
                self.resolved_monotonic_usec,
                field_name="resolved_monotonic_usec",
            )
            if self.resolved_monotonic_usec < self.latest_assessed_monotonic_usec:
                raise IncidentTrackingContractError(
                    "resolution cannot precede the latest incident assessment"
                )
        if not isinstance(self.resolution_reason, IncidentResolutionReason):
            raise IncidentTrackingContractError(
                "resolution_reason must be an IncidentResolutionReason"
            )
        _validate_text(
            self.resolution_assessment_id,
            field_name="resolution_assessment_id",
        )
        _validate_text(
            self.resolution_source_event_id,
            field_name="resolution_source_event_id",
        )
        if self.resolution_reason is IncidentResolutionReason.RECLASSIFIED:
            _validate_incident_id(
                self.replacement_incident_id,
                field_name="replacement_incident_id",
            )
        elif self.replacement_incident_id is not None:
            raise IncidentTrackingContractError(
                "recovered incidents must not name a replacement incident"
            )


@dataclass(frozen=True, slots=True)
class IncidentLifecycleChange:
    """One explicit lifecycle mutation caused by an assessment."""

    kind: IncidentChangeKind
    incident: SystemdServiceIncident
    trigger_assessment_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IncidentChangeKind):
            raise IncidentTrackingContractError("kind must be an IncidentChangeKind")
        if not isinstance(self.incident, SystemdServiceIncident):
            raise IncidentTrackingContractError(
                "incident must be a SystemdServiceIncident"
            )
        _validate_text(
            self.trigger_assessment_id,
            field_name="trigger_assessment_id",
        )
        if self.kind is IncidentChangeKind.RESOLVED:
            if self.incident.state is not IncidentState.RESOLVED:
                raise IncidentTrackingContractError(
                    "resolved changes require a resolved incident"
                )
        elif self.incident.state is not IncidentState.OPEN:
            raise IncidentTrackingContractError(
                "opened and updated changes require an open incident"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "trigger_assessment_id": self.trigger_assessment_id,
            "incident": self.incident.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class IncidentTrackingResult:
    """Result of processing one detector assessment."""

    assessment_id: str
    target_unit: str
    deduplicated: bool
    changes: tuple[IncidentLifecycleChange, ...]
    active_incident: SystemdServiceIncident | None

    def __post_init__(self) -> None:
        _validate_text(self.assessment_id, field_name="assessment_id")
        validate_service_unit_name(self.target_unit, field_name="target_unit")
        if not isinstance(self.deduplicated, bool):
            raise IncidentTrackingContractError("deduplicated must be boolean")
        if self.deduplicated and self.changes:
            raise IncidentTrackingContractError(
                "deduplicated results must not contain lifecycle changes"
            )
        for change in self.changes:
            if not isinstance(change, IncidentLifecycleChange):
                raise IncidentTrackingContractError(
                    "changes must contain IncidentLifecycleChange values"
                )
        if self.active_incident is not None:
            if not isinstance(self.active_incident, SystemdServiceIncident):
                raise IncidentTrackingContractError(
                    "active_incident must be a SystemdServiceIncident or None"
                )
            if not self.active_incident.is_open:
                raise IncidentTrackingContractError(
                    "active_incident must reference an open incident"
                )
            if self.active_incident.target_unit != self.target_unit:
                raise IncidentTrackingContractError(
                    "active incident target must match tracking result"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "assessment_id": self.assessment_id,
            "target_unit": self.target_unit,
            "deduplicated": self.deduplicated,
            "change_count": len(self.changes),
            "changes": [change.to_dict() for change in self.changes],
            "active_incident": (
                None if self.active_incident is None else self.active_incident.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class IncidentTargetState:
    """Serializable typed tracker state for one target."""

    target_unit: str
    last_assessment_id: str
    last_assessed_monotonic_usec: int
    open_incident: SystemdServiceIncident | None

    def __post_init__(self) -> None:
        validate_service_unit_name(self.target_unit, field_name="target_unit")
        _validate_text(self.last_assessment_id, field_name="last_assessment_id")
        _validate_nonnegative_int(
            self.last_assessed_monotonic_usec,
            field_name="last_assessed_monotonic_usec",
        )
        if self.open_incident is not None:
            if not isinstance(self.open_incident, SystemdServiceIncident):
                raise IncidentTrackingContractError(
                    "open_incident must be a SystemdServiceIncident or None"
                )
            if not self.open_incident.is_open:
                raise IncidentTrackingContractError(
                    "target state can retain only open incidents"
                )
            if self.open_incident.target_unit != self.target_unit:
                raise IncidentTrackingContractError(
                    "open incident target must match target state"
                )
            if (
                self.open_incident.latest_assessed_monotonic_usec
                > self.last_assessed_monotonic_usec
            ):
                raise IncidentTrackingContractError(
                    "open incident cannot be newer than target checkpoint"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "target_unit": self.target_unit,
            "last_assessment_id": self.last_assessment_id,
            "last_assessed_monotonic_usec": self.last_assessed_monotonic_usec,
            "open_incident": (
                None if self.open_incident is None else self.open_incident.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class IncidentTrackerSnapshot:
    """Bounded typed snapshot that can be restored by a new tracker instance."""

    version: str
    targets: tuple[IncidentTargetState, ...]

    def __post_init__(self) -> None:
        if self.version != INCIDENT_TRACKER_VERSION:
            raise IncidentTrackingContractError(
                "incident tracker snapshot version is unsupported"
            )
        seen: set[str] = set()
        previous = ""
        for target_state in self.targets:
            if not isinstance(target_state, IncidentTargetState):
                raise IncidentTrackingContractError(
                    "snapshot targets must contain IncidentTargetState values"
                )
            if target_state.target_unit in seen:
                raise IncidentTrackingContractError(
                    "snapshot target identities must be unique"
                )
            if previous and target_state.target_unit < previous:
                raise IncidentTrackingContractError(
                    "snapshot targets must use deterministic sorted order"
                )
            seen.add(target_state.target_unit)
            previous = target_state.target_unit

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "target_count": len(self.targets),
            "targets": [target.to_dict() for target in self.targets],
        }


class SystemdIncidentTracker:
    """Track one open incident episode per target with explicit deduplication."""

    def __init__(
        self,
        *,
        max_tracked_targets: int = _DEFAULT_MAX_TRACKED_TARGETS,
        snapshot: IncidentTrackerSnapshot | None = None,
    ) -> None:
        self._max_tracked_targets = _validate_target_limit(max_tracked_targets)
        self._targets: dict[str, IncidentTargetState] = {}
        if snapshot is not None:
            if not isinstance(snapshot, IncidentTrackerSnapshot):
                raise TypeError("snapshot must be an IncidentTrackerSnapshot or None")
            if len(snapshot.targets) > self._max_tracked_targets:
                raise IncidentTrackingContractError(
                    "snapshot exceeds max_tracked_targets"
                )
            self._targets = {target.target_unit: target for target in snapshot.targets}

    @property
    def max_tracked_targets(self) -> int:
        return self._max_tracked_targets

    @property
    def tracked_target_count(self) -> int:
        return len(self._targets)

    @property
    def open_incident_count(self) -> int:
        return sum(
            target.open_incident is not None for target in self._targets.values()
        )

    def active_incident(self, target_unit: str) -> SystemdServiceIncident | None:
        validate_service_unit_name(target_unit, field_name="target_unit")
        state = self._targets.get(target_unit)
        return None if state is None else state.open_incident

    def snapshot(self) -> IncidentTrackerSnapshot:
        return IncidentTrackerSnapshot(
            version=INCIDENT_TRACKER_VERSION,
            targets=tuple(self._targets[target] for target in sorted(self._targets)),
        )

    def process(
        self,
        assessment: SystemdServiceHealthAssessment,
    ) -> IncidentTrackingResult:
        if not isinstance(assessment, SystemdServiceHealthAssessment):
            raise IncidentTrackingContractError(
                "assessment must be a SystemdServiceHealthAssessment"
            )

        target = assessment.target_unit
        previous = self._targets.get(target)
        if previous is not None:
            if previous.last_assessment_id == assessment.assessment_id:
                return IncidentTrackingResult(
                    assessment_id=assessment.assessment_id,
                    target_unit=target,
                    deduplicated=True,
                    changes=(),
                    active_incident=previous.open_incident,
                )
            if (
                assessment.assessed_monotonic_usec
                < previous.last_assessed_monotonic_usec
            ):
                raise IncidentTrackingContractError(
                    "assessment monotonic time regressed for target"
                )
        elif len(self._targets) >= self._max_tracked_targets:
            raise IncidentTrackingError("incident tracker target capacity is exhausted")

        current = None if previous is None else previous.open_incident
        if current is not None and current.canonical_unit != assessment.canonical_unit:
            raise IncidentTrackingContractError(
                "canonical unit drift is not allowed within an incident target"
            )
        changes: tuple[IncidentLifecycleChange, ...]
        active: SystemdServiceIncident | None

        if assessment.status is SystemdServiceHealthStatus.UNASSESSED:
            changes = ()
            active = current
        elif assessment.status is SystemdServiceHealthStatus.HEALTHY:
            if current is None:
                changes = ()
                active = None
            else:
                resolved = _resolve_incident(
                    current,
                    assessment,
                    reason=IncidentResolutionReason.RECOVERED,
                    replacement_incident_id=None,
                )
                changes = (
                    IncidentLifecycleChange(
                        kind=IncidentChangeKind.RESOLVED,
                        incident=resolved,
                        trigger_assessment_id=assessment.assessment_id,
                    ),
                )
                active = None
        else:
            active, changes = self._process_anomaly(current, assessment)

        self._targets[target] = IncidentTargetState(
            target_unit=target,
            last_assessment_id=assessment.assessment_id,
            last_assessed_monotonic_usec=assessment.assessed_monotonic_usec,
            open_incident=active,
        )
        return IncidentTrackingResult(
            assessment_id=assessment.assessment_id,
            target_unit=target,
            deduplicated=False,
            changes=changes,
            active_incident=active,
        )

    def _process_anomaly(
        self,
        current: SystemdServiceIncident | None,
        assessment: SystemdServiceHealthAssessment,
    ) -> tuple[SystemdServiceIncident, tuple[IncidentLifecycleChange, ...]]:
        anomaly_class = assessment.anomaly_class
        severity = assessment.severity
        if anomaly_class is None or severity is None:
            raise IncidentTrackingContractError(
                "anomalous assessments require anomaly metadata"
            )
        if current is None:
            opened = _open_incident(assessment)
            return opened, (
                IncidentLifecycleChange(
                    kind=IncidentChangeKind.OPENED,
                    incident=opened,
                    trigger_assessment_id=assessment.assessment_id,
                ),
            )
        if current.anomaly_class is anomaly_class:
            updated = _update_incident(current, assessment)
            return updated, (
                IncidentLifecycleChange(
                    kind=IncidentChangeKind.UPDATED,
                    incident=updated,
                    trigger_assessment_id=assessment.assessment_id,
                ),
            )

        replacement = _open_incident(assessment)
        resolved = _resolve_incident(
            current,
            assessment,
            reason=IncidentResolutionReason.RECLASSIFIED,
            replacement_incident_id=replacement.incident_id,
        )
        return replacement, (
            IncidentLifecycleChange(
                kind=IncidentChangeKind.RESOLVED,
                incident=resolved,
                trigger_assessment_id=assessment.assessment_id,
            ),
            IncidentLifecycleChange(
                kind=IncidentChangeKind.OPENED,
                incident=replacement,
                trigger_assessment_id=assessment.assessment_id,
            ),
        )


def _open_incident(
    assessment: SystemdServiceHealthAssessment,
) -> SystemdServiceIncident:
    if not assessment.is_anomalous:
        raise IncidentTrackingContractError(
            "only anomalous assessments can open incidents"
        )
    if assessment.anomaly_class is None or assessment.severity is None:
        raise IncidentTrackingContractError(
            "anomalous assessments require anomaly metadata"
        )
    return SystemdServiceIncident(
        incident_id=_incident_id(assessment),
        target_unit=assessment.target_unit,
        canonical_unit=assessment.canonical_unit,
        anomaly_class=assessment.anomaly_class,
        severity=assessment.severity,
        opened_at=assessment.assessed_at,
        opened_monotonic_usec=assessment.assessed_monotonic_usec,
        first_assessment_id=assessment.assessment_id,
        first_source_event_id=assessment.source_event_id,
        latest_assessment_id=assessment.assessment_id,
        latest_source_event_id=assessment.source_event_id,
        latest_assessed_at=assessment.assessed_at,
        latest_assessed_monotonic_usec=assessment.assessed_monotonic_usec,
        observation_count=1,
    )


def _update_incident(
    incident: SystemdServiceIncident,
    assessment: SystemdServiceHealthAssessment,
) -> SystemdServiceIncident:
    if not incident.is_open:
        raise IncidentTrackingContractError("only open incidents can be updated")
    if assessment.assessed_monotonic_usec < incident.latest_assessed_monotonic_usec:
        raise IncidentTrackingContractError(
            "incident update cannot regress monotonic time"
        )
    return SystemdServiceIncident(
        incident_id=incident.incident_id,
        target_unit=incident.target_unit,
        canonical_unit=incident.canonical_unit,
        anomaly_class=incident.anomaly_class,
        severity=incident.severity,
        opened_at=incident.opened_at,
        opened_monotonic_usec=incident.opened_monotonic_usec,
        first_assessment_id=incident.first_assessment_id,
        first_source_event_id=incident.first_source_event_id,
        latest_assessment_id=assessment.assessment_id,
        latest_source_event_id=assessment.source_event_id,
        latest_assessed_at=assessment.assessed_at,
        latest_assessed_monotonic_usec=assessment.assessed_monotonic_usec,
        observation_count=incident.observation_count + 1,
    )


def _resolve_incident(
    incident: SystemdServiceIncident,
    assessment: SystemdServiceHealthAssessment,
    *,
    reason: IncidentResolutionReason,
    replacement_incident_id: str | None,
) -> SystemdServiceIncident:
    if not incident.is_open:
        raise IncidentTrackingContractError("only open incidents can be resolved")
    if assessment.assessed_monotonic_usec < incident.latest_assessed_monotonic_usec:
        raise IncidentTrackingContractError(
            "incident resolution cannot regress monotonic time"
        )
    return SystemdServiceIncident(
        incident_id=incident.incident_id,
        target_unit=incident.target_unit,
        canonical_unit=incident.canonical_unit,
        anomaly_class=incident.anomaly_class,
        severity=incident.severity,
        opened_at=incident.opened_at,
        opened_monotonic_usec=incident.opened_monotonic_usec,
        first_assessment_id=incident.first_assessment_id,
        first_source_event_id=incident.first_source_event_id,
        latest_assessment_id=incident.latest_assessment_id,
        latest_source_event_id=incident.latest_source_event_id,
        latest_assessed_at=incident.latest_assessed_at,
        latest_assessed_monotonic_usec=incident.latest_assessed_monotonic_usec,
        observation_count=incident.observation_count,
        state=IncidentState.RESOLVED,
        resolved_at=assessment.assessed_at,
        resolved_monotonic_usec=assessment.assessed_monotonic_usec,
        resolution_reason=reason,
        resolution_assessment_id=assessment.assessment_id,
        resolution_source_event_id=assessment.source_event_id,
        replacement_incident_id=replacement_incident_id,
    )


def _incident_id(assessment: SystemdServiceHealthAssessment) -> str:
    if assessment.anomaly_class is None:
        raise IncidentTrackingContractError(
            "incident identity requires anomaly metadata"
        )
    digest = hashlib.sha256()
    digest.update(_INCIDENT_ID_DOMAIN)
    digest.update(assessment.target_unit.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(assessment.anomaly_class.value.encode("ascii"))
    digest.update(b"\x00")
    digest.update(assessment.assessment_id.encode("ascii"))
    return f"inc-{digest.hexdigest()}"


def _validate_target_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_tracked_targets must be an integer")
    if not 1 <= value <= _MAX_TRACKED_TARGETS:
        raise ValueError(
            f"max_tracked_targets must be between 1 and {_MAX_TRACKED_TARGETS}"
        )
    return value


def _validate_incident_id(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _INCIDENT_ID_PATTERN.fullmatch(value) is None:
        raise IncidentTrackingContractError(
            f"{field_name} must be an inc- prefixed SHA-256 identity"
        )


def _validate_text(value: object, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_TEXT_LENGTH
        or "\x00" in value
    ):
        raise IncidentTrackingContractError(
            f"{field_name} must be bounded non-empty text"
        )


def _validate_nonnegative_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IncidentTrackingContractError(
            f"{field_name} must be a non-negative integer"
        )


def _validate_positive_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IncidentTrackingContractError(f"{field_name} must be a positive integer")


def _validate_aware_datetime(value: object, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise IncidentTrackingContractError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise IncidentTrackingContractError(f"{field_name} must be timezone-aware")
