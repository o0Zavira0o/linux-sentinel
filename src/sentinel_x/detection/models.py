"""Typed anomaly-detection contracts for Sentinel-X.

Phase 4 starts with deterministic, auditable detection primitives.  These
models deliberately avoid probabilistic confidence scores until Sentinel-X has
sufficient calibration data to justify them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.systemd.models import validate_service_unit_name

DETECTION_EVENT_SOURCE: Final[str] = "sentinel_x.detection.systemd"
SYSTEMD_HEALTH_DETECTION_TYPE: Final[str] = "linux.systemd.service.health"
SYSTEMD_STATE_BASELINE_VERSION: Final[str] = "sentinel-x.systemd-state-baseline.v1"

_ASSESSMENT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"asmt-[0-9a-f]{64}")
_MAX_TEXT_LENGTH: Final[int] = 512


class DetectionModelError(ValueError):
    """Raised when typed detector output violates its contract."""


class SystemdServiceHealthStatus(StrEnum):
    """Conservative state-health classification for a monitored service."""

    HEALTHY = "healthy"
    INACTIVE = "inactive"
    FAILED = "failed"
    UNASSESSED = "unassessed"


class DetectionAnomalyClass(StrEnum):
    """Anomaly classes currently justified by labeled Phase 3 evidence."""

    SYSTEMD_SERVICE_INACTIVE = "systemd.service.inactive"
    SYSTEMD_SERVICE_FAILED = "systemd.service.failed"


class DetectionBasis(StrEnum):
    """Evidence basis used by a deterministic detection assessment."""

    DIRECT_SYSTEMD_STATE = "direct_systemd_state"


@dataclass(frozen=True, slots=True)
class SystemdServiceHealthAssessment:
    """One immutable detector assessment of a systemd service observation.

    HEALTHY and UNASSESSED are explicit outcomes rather than absence of data.
    Only INACTIVE and FAILED are currently emitted as anomaly events because
    those are the two fault classes covered by the Phase 3 labeled dataset.
    """

    assessment_id: str
    source_event_id: str
    target_unit: str
    canonical_unit: str
    source_observed_at: datetime
    assessed_at: datetime
    assessed_monotonic_usec: int
    state_change_monotonic_usec: int | None
    load_state: str
    active_state: str
    sub_state: str
    main_pid: int | None
    result: str | None
    status: SystemdServiceHealthStatus
    anomaly_class: DetectionAnomalyClass | None
    severity: EventSeverity | None
    basis: DetectionBasis = DetectionBasis.DIRECT_SYSTEMD_STATE

    def __post_init__(self) -> None:
        """Validate detector-output invariants."""

        if (
            not isinstance(self.assessment_id, str)
            or _ASSESSMENT_ID_PATTERN.fullmatch(self.assessment_id) is None
        ):
            raise DetectionModelError(
                "assessment_id must be an asmt- prefixed SHA-256 identity"
            )
        _validate_text(self.source_event_id, field_name="source_event_id")
        validate_service_unit_name(self.target_unit, field_name="target_unit")
        validate_service_unit_name(self.canonical_unit, field_name="canonical_unit")
        _validate_aware_datetime(
            self.source_observed_at,
            field_name="source_observed_at",
        )
        _validate_aware_datetime(self.assessed_at, field_name="assessed_at")
        _validate_nonnegative_int(
            self.assessed_monotonic_usec,
            field_name="assessed_monotonic_usec",
        )
        if self.state_change_monotonic_usec is not None:
            _validate_nonnegative_int(
                self.state_change_monotonic_usec,
                field_name="state_change_monotonic_usec",
            )
        for field_name, value in (
            ("load_state", self.load_state),
            ("active_state", self.active_state),
            ("sub_state", self.sub_state),
        ):
            _validate_text(value, field_name=field_name)
        if self.main_pid is not None:
            _validate_nonnegative_int(self.main_pid, field_name="main_pid")
        _validate_optional_text(self.result, field_name="result")
        if not isinstance(self.status, SystemdServiceHealthStatus):
            raise DetectionModelError("status must be a SystemdServiceHealthStatus")
        if not isinstance(self.basis, DetectionBasis):
            raise DetectionModelError("basis must be a DetectionBasis")
        if self.anomaly_class is not None and not isinstance(
            self.anomaly_class,
            DetectionAnomalyClass,
        ):
            raise DetectionModelError(
                "anomaly_class must be a DetectionAnomalyClass or None"
            )
        if self.severity is not None and not isinstance(self.severity, EventSeverity):
            raise DetectionModelError("severity must be an EventSeverity or None")
        self._validate_status_contract()

    @property
    def is_anomalous(self) -> bool:
        """Return whether this assessment represents a supported anomaly."""

        return self.status in {
            SystemdServiceHealthStatus.INACTIVE,
            SystemdServiceHealthStatus.FAILED,
        }

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly assessment."""

        return {
            "assessment_id": self.assessment_id,
            "source_event_id": self.source_event_id,
            "target_unit": self.target_unit,
            "canonical_unit": self.canonical_unit,
            "source_observed_at": self.source_observed_at.isoformat(),
            "assessed_at": self.assessed_at.isoformat(),
            "assessed_monotonic_usec": self.assessed_monotonic_usec,
            "state_change_monotonic_usec": self.state_change_monotonic_usec,
            "load_state": self.load_state,
            "active_state": self.active_state,
            "sub_state": self.sub_state,
            "main_pid": self.main_pid,
            "result": self.result,
            "status": self.status.value,
            "anomaly_class": (
                None if self.anomaly_class is None else self.anomaly_class.value
            ),
            "severity": None if self.severity is None else self.severity.value,
            "basis": self.basis.value,
            "is_anomalous": self.is_anomalous,
        }

    def to_anomaly_event(self) -> SentinelEvent:
        """Convert a supported anomaly assessment into a stable anomaly event."""

        if not self.is_anomalous:
            raise DetectionModelError(
                "only anomalous assessments can be converted to anomaly events"
            )
        if self.anomaly_class is None or self.severity is None:
            raise DetectionModelError("anomalous assessment metadata is incomplete")

        if self.status is SystemdServiceHealthStatus.FAILED:
            message = f"systemd service {self.target_unit} entered failed state."
        else:
            message = f"systemd service {self.target_unit} is inactive."

        return SentinelEvent(
            kind=EventKind.ANOMALY,
            source=DETECTION_EVENT_SOURCE,
            message=message,
            severity=self.severity,
            event_id=self.assessment_id,
            occurred_at=self.assessed_at,
            attributes={
                "detection_type": SYSTEMD_HEALTH_DETECTION_TYPE,
                "detector_version": SYSTEMD_STATE_BASELINE_VERSION,
                "assessment_id": self.assessment_id,
                "source_event_id": self.source_event_id,
                "target_unit": self.target_unit,
                "canonical_unit": self.canonical_unit,
                "status": self.status.value,
                "anomaly_class": self.anomaly_class.value,
                "basis": self.basis.value,
                "source_observed_at": self.source_observed_at.isoformat(),
                "assessed_monotonic_usec": self.assessed_monotonic_usec,
                "state_change_monotonic_usec": self.state_change_monotonic_usec,
                "load_state": self.load_state,
                "active_state": self.active_state,
                "sub_state": self.sub_state,
                "main_pid": self.main_pid,
                "result": self.result,
            },
        )

    def _validate_status_contract(self) -> None:
        if self.status is SystemdServiceHealthStatus.HEALTHY:
            if self.anomaly_class is not None or self.severity is not None:
                raise DetectionModelError(
                    "healthy assessments must not carry anomaly metadata"
                )
            if not (
                self.load_state == "loaded"
                and self.active_state == "active"
                and self.sub_state == "running"
                and self.main_pid is not None
                and self.main_pid > 0
            ):
                raise DetectionModelError(
                    "healthy assessment requires loaded active/running state "
                    "with a live main PID"
                )
            return

        if self.status is SystemdServiceHealthStatus.INACTIVE:
            if self.load_state != "loaded" or self.active_state != "inactive":
                raise DetectionModelError(
                    "inactive assessment requires a loaded inactive service"
                )
            if (
                self.anomaly_class is not DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
                or self.severity is not EventSeverity.WARNING
            ):
                raise DetectionModelError(
                    "inactive assessment requires inactive anomaly metadata"
                )
            return

        if self.status is SystemdServiceHealthStatus.FAILED:
            if self.load_state != "loaded" or self.active_state != "failed":
                raise DetectionModelError(
                    "failed assessment requires a loaded failed service"
                )
            if (
                self.anomaly_class is not DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
                or self.severity is not EventSeverity.ERROR
            ):
                raise DetectionModelError(
                    "failed assessment requires failed anomaly metadata"
                )
            return

        if self.anomaly_class is not None or self.severity is not None:
            raise DetectionModelError(
                "unassessed states must not carry anomaly metadata"
            )


def _validate_nonnegative_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DetectionModelError(f"{field_name} must be a non-negative integer")


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT_LENGTH:
        raise DetectionModelError(
            f"{field_name} must be a non-empty string up to "
            f"{_MAX_TEXT_LENGTH} characters"
        )
    if "\x00" in value:
        raise DetectionModelError(f"{field_name} must not contain NUL bytes")


def _validate_optional_text(value: object, *, field_name: str) -> None:
    if value is None:
        return
    _validate_text(value, field_name=field_name)


def _validate_aware_datetime(value: object, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise DetectionModelError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DetectionModelError(f"{field_name} must be timezone-aware")
