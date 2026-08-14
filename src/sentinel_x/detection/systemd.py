"""Conservative systemd state detector baseline for Sentinel-X."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Final, Protocol

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    DetectionBasis,
    DetectionModelError,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)
from sentinel_x.systemd.boot import SystemBootIdError, normalize_boot_id
from sentinel_x.systemd.models import SystemdUnitNameError, validate_service_unit_name
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

_ASSESSMENT_ID_DOMAIN: Final[bytes] = b"sentinel-x.systemd-state-baseline.v1\x00"


class SystemdDetectionError(RuntimeError):
    """Base error for conservative systemd detector execution."""


class SystemdDetectionContractError(SystemdDetectionError):
    """Raised when an input event violates the systemd observation contract."""


class MonotonicNanosecondClock(Protocol):
    """Structural monotonic nanosecond clock dependency."""

    def __call__(self) -> int:
        """Return monotonic nanoseconds."""

        ...


class WallClock(Protocol):
    """Structural timezone-aware wall-clock dependency."""

    def __call__(self) -> datetime:
        """Return the current wall-clock time."""

        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic_ns() -> int:
    return time.monotonic_ns()


class SystemdServiceStateDetector:
    """Classify only Phase-3-supported direct service-state anomalies.

    This baseline intentionally does not guess about activating, deactivating,
    unknown, unloaded, oneshot, or PID-less active states.  Such observations
    are returned as UNASSESSED until a later phase has an explicit policy and
    labeled evidence for them.
    """

    def __init__(
        self,
        *,
        wall_clock: WallClock = _utc_now,
        monotonic_ns_clock: MonotonicNanosecondClock = _monotonic_ns,
    ) -> None:
        if not callable(wall_clock):
            raise TypeError("wall_clock must be callable")
        if not callable(monotonic_ns_clock):
            raise TypeError("monotonic_ns_clock must be callable")
        self._wall_clock = wall_clock
        self._monotonic_ns_clock = monotonic_ns_clock

    def assess(self, event: SentinelEvent) -> SystemdServiceHealthAssessment:
        """Assess one typed systemd service observation without side effects."""

        if not isinstance(event, SentinelEvent):
            raise SystemdDetectionContractError("event must be a SentinelEvent")
        if event.kind is not EventKind.OBSERVATION:
            raise SystemdDetectionContractError(
                "systemd detector accepts observation events only"
            )
        if event.source != SYSTEMD_SERVICE_OBSERVATION_SOURCE:
            raise SystemdDetectionContractError(
                "systemd detector received an unsupported event source"
            )

        attributes = event.attributes
        observation_type = _required_text(
            attributes,
            "observation_type",
        )
        if observation_type != SYSTEMD_SERVICE_OBSERVATION_TYPE:
            raise SystemdDetectionContractError(
                "systemd detector received an unsupported observation type"
            )

        target_unit = _service_name(attributes, "requested_name")
        canonical_unit = _service_name(attributes, "canonical_name")
        load_state = _required_text(attributes, "load_state")
        active_state = _required_text(attributes, "active_state")
        sub_state = _required_text(attributes, "sub_state")
        main_pid = _optional_nonnegative_int(attributes, "main_pid")
        state_change_monotonic_usec = _optional_nonnegative_int(
            attributes,
            "state_change_monotonic_usec",
        )
        result = _optional_text(attributes, "result")
        _validate_boot_id(attributes)
        _required_text(attributes, "collector_name")

        assessed_at = self._wall_clock()
        if not isinstance(assessed_at, datetime):
            raise SystemdDetectionContractError("wall_clock must return a datetime")
        if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
            raise SystemdDetectionContractError(
                "wall_clock must return a timezone-aware datetime"
            )

        monotonic_ns = self._monotonic_ns_clock()
        if (
            isinstance(monotonic_ns, bool)
            or not isinstance(monotonic_ns, int)
            or monotonic_ns < 0
        ):
            raise SystemdDetectionContractError(
                "monotonic_ns_clock must return a non-negative integer"
            )
        assessed_monotonic_usec = monotonic_ns // 1_000

        status, anomaly_class, severity = _classify_state(
            load_state=load_state,
            active_state=active_state,
            sub_state=sub_state,
            main_pid=main_pid,
        )
        assessment_id = _assessment_id(
            source_event_id=event.event_id,
            status=status,
        )

        try:
            return SystemdServiceHealthAssessment(
                assessment_id=assessment_id,
                source_event_id=event.event_id,
                target_unit=target_unit,
                canonical_unit=canonical_unit,
                source_observed_at=event.occurred_at,
                assessed_at=assessed_at,
                assessed_monotonic_usec=assessed_monotonic_usec,
                state_change_monotonic_usec=state_change_monotonic_usec,
                load_state=load_state,
                active_state=active_state,
                sub_state=sub_state,
                main_pid=main_pid,
                result=result,
                status=status,
                anomaly_class=anomaly_class,
                severity=severity,
                basis=DetectionBasis.DIRECT_SYSTEMD_STATE,
            )
        except (DetectionModelError, SystemdUnitNameError) as exc:
            raise SystemdDetectionContractError(str(exc)) from exc


def _classify_state(
    *,
    load_state: str,
    active_state: str,
    sub_state: str,
    main_pid: int | None,
) -> tuple[
    SystemdServiceHealthStatus,
    DetectionAnomalyClass | None,
    EventSeverity | None,
]:
    if load_state != "loaded":
        return SystemdServiceHealthStatus.UNASSESSED, None, None
    if active_state == "failed":
        return (
            SystemdServiceHealthStatus.FAILED,
            DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
            EventSeverity.ERROR,
        )
    if active_state == "inactive":
        return (
            SystemdServiceHealthStatus.INACTIVE,
            DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE,
            EventSeverity.WARNING,
        )
    if (
        active_state == "active"
        and sub_state == "running"
        and main_pid is not None
        and main_pid > 0
    ):
        return SystemdServiceHealthStatus.HEALTHY, None, None
    return SystemdServiceHealthStatus.UNASSESSED, None, None


def _assessment_id(
    *,
    source_event_id: str,
    status: SystemdServiceHealthStatus,
) -> str:
    digest = hashlib.sha256()
    digest.update(_ASSESSMENT_ID_DOMAIN)
    digest.update(source_event_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(status.value.encode("ascii"))
    return f"asmt-{digest.hexdigest()}"


def _required_text(attributes: Mapping[str, object], field_name: str) -> str:
    if field_name not in attributes:
        raise SystemdDetectionContractError(
            f"systemd observation is missing {field_name}"
        )
    value = attributes[field_name]
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SystemdDetectionContractError(
            f"systemd observation field {field_name} must be non-empty text"
        )
    return value


def _optional_text(
    attributes: Mapping[str, object],
    field_name: str,
) -> str | None:
    if field_name not in attributes:
        raise SystemdDetectionContractError(
            f"systemd observation is missing {field_name}"
        )
    value = attributes[field_name]
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SystemdDetectionContractError(
            f"systemd observation field {field_name} must be text or None"
        )
    return value


def _optional_nonnegative_int(
    attributes: Mapping[str, object],
    field_name: str,
) -> int | None:
    if field_name not in attributes:
        raise SystemdDetectionContractError(
            f"systemd observation is missing {field_name}"
        )
    value = attributes[field_name]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemdDetectionContractError(
            f"systemd observation field {field_name} "
            "must be a non-negative integer or None"
        )
    return value


def _service_name(attributes: Mapping[str, object], field_name: str) -> str:
    value = _required_text(attributes, field_name)
    try:
        return validate_service_unit_name(value, field_name=field_name)
    except SystemdUnitNameError as exc:
        raise SystemdDetectionContractError(str(exc)) from exc


def _validate_boot_id(attributes: Mapping[str, object]) -> None:
    value = _required_text(attributes, "boot_id")
    try:
        normalize_boot_id(value, field_name="boot_id")
    except SystemBootIdError as exc:
        raise SystemdDetectionContractError(str(exc)) from exc
