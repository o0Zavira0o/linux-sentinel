"""Live EventBus integration for systemd detection and incident lifecycle.

Phase 4D attaches the frozen Phase-4A state detector and Phase-4C incident
tracker to the existing Sentinel-X EventBus.  Lifecycle events use stable
identities and a bounded retry outbox.  OPENED and RESOLVED changes are always
published; repetitive UPDATED changes are coalesced onto deterministic
observation-count milestones so a persistent fault cannot emit one external
event per polling cycle.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Final

from sentinel_x.core.bus import EventBus, Subscription
from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.detection.incidents import (
    IncidentChangeKind,
    IncidentLifecycleChange,
    IncidentTrackerSnapshot,
    SystemdIncidentTracker,
)
from sentinel_x.detection.systemd import SystemdServiceStateDetector
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

SYSTEMD_INCIDENT_LIFECYCLE_SOURCE: Final[str] = "sentinel_x.detection.incidents"
SYSTEMD_INCIDENT_LIFECYCLE_TYPE: Final[str] = "linux.systemd.service.incident.lifecycle"
SYSTEMD_INCIDENT_LIFECYCLE_VERSION: Final[str] = (
    "sentinel-x.systemd-incident-lifecycle.v1"
)

INCIDENT_UPDATE_PUBLICATION_MILESTONES: Final[tuple[int, ...]] = (
    2,
    4,
    8,
    16,
    32,
    64,
    128,
    256,
)

_LIFECYCLE_EVENT_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.systemd-incident-lifecycle.v1\x00"
)
_MAX_PENDING_LIFECYCLE_EVENTS: Final[int] = 16
_MAX_DEFERRED_SERVICE_OBSERVATIONS: Final[int] = 256
_MAX_LIFECYCLE_CHANGES_PER_ASSESSMENT: Final[int] = 2


class SystemdDetectionBridgeError(RuntimeError):
    """Base error for live systemd detection integration."""


class SystemdDetectionBridgeContractError(SystemdDetectionBridgeError):
    """Raised when detector or tracker output violates the bridge contract."""


class SystemdDetectionBridgePublicationError(SystemdDetectionBridgeError):
    """Raised when a lifecycle event cannot reach every EventBus subscriber."""


@dataclass(frozen=True, slots=True)
class SystemdDetectionBridgeSnapshot:
    """Read-only bridge, outbox, detector, and tracker accounting."""

    attached: bool
    pending_lifecycle_events: int
    deferred_service_observations: int
    input_events_received: int
    unsupported_events_ignored: int
    self_derived_events_ignored: int
    service_observations_deferred: int
    deferred_service_observations_processed: int
    detection_attempts: int
    detection_failures: int
    tracker_process_attempts: int
    tracker_process_failures: int
    assessments_processed: int
    deduplicated_assessments: int
    lifecycle_changes_observed: int
    lifecycle_changes_suppressed: int
    lifecycle_events_enqueued: int
    lifecycle_publish_attempts: int
    lifecycle_events_published: int
    lifecycle_publish_failures: int
    subscriber_failures_observed: int
    contract_failures: int
    tracker: IncidentTrackerSnapshot

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly bridge state."""

        return {
            "attached": self.attached,
            "pending_lifecycle_events": self.pending_lifecycle_events,
            "deferred_service_observations": self.deferred_service_observations,
            "input_events_received": self.input_events_received,
            "unsupported_events_ignored": self.unsupported_events_ignored,
            "self_derived_events_ignored": self.self_derived_events_ignored,
            "service_observations_deferred": self.service_observations_deferred,
            "deferred_service_observations_processed": (
                self.deferred_service_observations_processed
            ),
            "detection_attempts": self.detection_attempts,
            "detection_failures": self.detection_failures,
            "tracker_process_attempts": self.tracker_process_attempts,
            "tracker_process_failures": self.tracker_process_failures,
            "assessments_processed": self.assessments_processed,
            "deduplicated_assessments": self.deduplicated_assessments,
            "lifecycle_changes_observed": self.lifecycle_changes_observed,
            "lifecycle_changes_suppressed": self.lifecycle_changes_suppressed,
            "lifecycle_events_enqueued": self.lifecycle_events_enqueued,
            "lifecycle_publish_attempts": self.lifecycle_publish_attempts,
            "lifecycle_events_published": self.lifecycle_events_published,
            "lifecycle_publish_failures": self.lifecycle_publish_failures,
            "subscriber_failures_observed": self.subscriber_failures_observed,
            "contract_failures": self.contract_failures,
            "tracker": self.tracker.to_dict(),
        }


class SystemdDetectionEventBridge:
    """Attach direct-state detection and incident lifecycle to one EventBus.

    Tracker state may advance only to the lifecycle change currently retained
    in the in-memory outbox.  If publication fails, the exact derived event
    remains pending and is retried before any later non-self input is assessed.
    This preserves at-least-once lifecycle delivery without allowing tracker
    state to silently advance past unpublished derived evidence.
    """

    def __init__(
        self,
        event_bus: EventBus,
        *,
        detector: SystemdServiceStateDetector | None = None,
        tracker: SystemdIncidentTracker | None = None,
    ) -> None:
        if not isinstance(event_bus, EventBus):
            raise TypeError("event_bus must be an EventBus")
        if detector is not None and not isinstance(
            detector,
            SystemdServiceStateDetector,
        ):
            raise TypeError("detector must be a SystemdServiceStateDetector or None")
        if tracker is not None and not isinstance(tracker, SystemdIncidentTracker):
            raise TypeError("tracker must be a SystemdIncidentTracker or None")

        self._event_bus = event_bus
        self._detector = SystemdServiceStateDetector() if detector is None else detector
        self._tracker = SystemdIncidentTracker() if tracker is None else tracker
        self._pending: deque[SentinelEvent] = deque()
        self._deferred_service_observations: deque[SentinelEvent] = deque()
        self._lock = RLock()
        self._attached = True
        self._flushing = False
        self._input_events_received = 0
        self._unsupported_events_ignored = 0
        self._self_derived_events_ignored = 0
        self._service_observations_deferred = 0
        self._deferred_service_observations_processed = 0
        self._detection_attempts = 0
        self._detection_failures = 0
        self._tracker_process_attempts = 0
        self._tracker_process_failures = 0
        self._assessments_processed = 0
        self._deduplicated_assessments = 0
        self._lifecycle_changes_observed = 0
        self._lifecycle_changes_suppressed = 0
        self._lifecycle_events_enqueued = 0
        self._lifecycle_publish_attempts = 0
        self._lifecycle_events_published = 0
        self._lifecycle_publish_failures = 0
        self._subscriber_failures_observed = 0
        self._contract_failures = 0
        self._subscription: Subscription = event_bus.subscribe(self)

    def __call__(self, event: SentinelEvent) -> None:
        """Process one EventBus event and synchronously publish lifecycle output."""

        if not isinstance(event, SentinelEvent):
            raise TypeError("detection bridge requires a SentinelEvent")

        with self._lock:
            if not self._attached:
                return

            self._input_events_received += 1
            if _is_derived_lifecycle_event(event):
                self._self_derived_events_ignored += 1
                return

            if not _is_systemd_service_observation(event):
                # Detection lifecycle progression is intentionally driven only
                # by service-state observations.  In particular, do not flush
                # a pending lifecycle event while handling another bridge's
                # derived event: nested EventBus publication could otherwise
                # create a retry cycle between independent bridge outboxes.
                self._unsupported_events_ignored += 1
                return

            if self._flushing:
                self._defer_service_observation_locked(event)
                return

            # Retry unpublished lifecycle evidence before observing a newer
            # service-state assessment.  If retry is still blocked, preserve
            # the newer service observation in a bounded FIFO rather than
            # silently losing detector input at the EventBus boundary.
            try:
                self._flush_pending_locked()
            except SystemdDetectionBridgePublicationError:
                self._defer_service_observation_locked(event)
                raise

            try:
                self._drain_deferred_service_observations_locked()
            except SystemdDetectionBridgePublicationError:
                self._defer_service_observation_locked(event)
                raise

            self._process_service_observation_locked(event)

    def flush(self) -> None:
        """Retry all currently pending lifecycle events."""

        with self._lock:
            if not self._attached:
                return
            self._flush_pending_locked()

    def close(self) -> None:
        """Flush pending lifecycle evidence and detach from the EventBus.

        Detachment is guaranteed even when the final flush fails.  The pending
        event is retained in the bridge snapshot so callers can surface the
        incomplete outbox during shutdown.
        """

        with self._lock:
            if not self._attached:
                return

            failure: Exception | None = None
            try:
                self._flush_pending_locked()
                self._drain_deferred_service_observations_locked()
            except Exception as exc:
                failure = exc

            self._event_bus.unsubscribe(self._subscription)
            self._attached = False

            if failure is not None:
                raise failure

    def snapshot(self) -> SystemdDetectionBridgeSnapshot:
        """Return immutable bridge, outbox, and incident-tracker accounting."""

        with self._lock:
            return SystemdDetectionBridgeSnapshot(
                attached=self._attached,
                pending_lifecycle_events=len(self._pending),
                deferred_service_observations=len(self._deferred_service_observations),
                input_events_received=self._input_events_received,
                unsupported_events_ignored=self._unsupported_events_ignored,
                self_derived_events_ignored=self._self_derived_events_ignored,
                service_observations_deferred=(self._service_observations_deferred),
                deferred_service_observations_processed=(
                    self._deferred_service_observations_processed
                ),
                detection_attempts=self._detection_attempts,
                detection_failures=self._detection_failures,
                tracker_process_attempts=self._tracker_process_attempts,
                tracker_process_failures=self._tracker_process_failures,
                assessments_processed=self._assessments_processed,
                deduplicated_assessments=self._deduplicated_assessments,
                lifecycle_changes_observed=self._lifecycle_changes_observed,
                lifecycle_changes_suppressed=self._lifecycle_changes_suppressed,
                lifecycle_events_enqueued=self._lifecycle_events_enqueued,
                lifecycle_publish_attempts=self._lifecycle_publish_attempts,
                lifecycle_events_published=self._lifecycle_events_published,
                lifecycle_publish_failures=self._lifecycle_publish_failures,
                subscriber_failures_observed=self._subscriber_failures_observed,
                contract_failures=self._contract_failures,
                tracker=self._tracker.snapshot(),
            )

    def _process_service_observation_locked(
        self,
        event: SentinelEvent,
    ) -> None:
        self._detection_attempts += 1
        try:
            assessment = self._detector.assess(event)
        except Exception:
            self._detection_failures += 1
            raise

        self._tracker_process_attempts += 1
        try:
            result = self._tracker.process(assessment)
        except Exception:
            self._tracker_process_failures += 1
            raise

        self._assessments_processed += 1
        if result.deduplicated:
            self._deduplicated_assessments += 1

        changes = result.changes
        self._lifecycle_changes_observed += len(changes)
        try:
            derived = self._prepare_lifecycle_events_locked(changes)
        except Exception:
            self._contract_failures += 1
            raise

        if not derived:
            return

        self._pending.extend(derived)
        self._lifecycle_events_enqueued += len(derived)
        self._flush_pending_locked()

    def _defer_service_observation_locked(self, event: SentinelEvent) -> None:
        if (
            len(self._deferred_service_observations)
            >= _MAX_DEFERRED_SERVICE_OBSERVATIONS
        ):
            self._contract_failures += 1
            raise SystemdDetectionBridgeContractError(
                "deferred service observation capacity is exhausted"
            )
        self._deferred_service_observations.append(event)
        self._service_observations_deferred += 1

    def _drain_deferred_service_observations_locked(self) -> None:
        while self._deferred_service_observations:
            event = self._deferred_service_observations[0]
            try:
                self._process_service_observation_locked(event)
            except SystemdDetectionBridgePublicationError:
                # The source observation has already advanced detector/tracker
                # state to the exact lifecycle event now retained in _pending.
                self._deferred_service_observations.popleft()
                self._deferred_service_observations_processed += 1
                raise
            else:
                self._deferred_service_observations.popleft()
                self._deferred_service_observations_processed += 1

    def _prepare_lifecycle_events_locked(
        self,
        changes: tuple[IncidentLifecycleChange, ...],
    ) -> list[SentinelEvent]:
        if len(changes) > _MAX_LIFECYCLE_CHANGES_PER_ASSESSMENT:
            raise SystemdDetectionBridgeContractError(
                "incident tracker produced too many lifecycle changes"
            )

        derived: list[SentinelEvent] = []
        for change in changes:
            lifecycle_event = project_incident_lifecycle_event(change)
            if lifecycle_event is None:
                self._lifecycle_changes_suppressed += 1
                continue
            derived.append(lifecycle_event)

        if len(self._pending) + len(derived) > _MAX_PENDING_LIFECYCLE_EVENTS:
            raise SystemdDetectionBridgeContractError(
                "lifecycle retry outbox capacity is exhausted"
            )

        event_ids = {event.event_id for event in derived}
        if len(event_ids) != len(derived):
            raise SystemdDetectionBridgeContractError(
                "one assessment produced duplicate lifecycle event identities"
            )
        return derived

    def _flush_pending_locked(self) -> None:
        if self._flushing:
            return

        self._flushing = True
        try:
            while self._pending:
                event = self._pending[0]
                self._lifecycle_publish_attempts += 1
                report = self._event_bus.publish(event)
                if not report.succeeded:
                    self._lifecycle_publish_failures += 1
                    self._subscriber_failures_observed += len(report.failures)
                    raise SystemdDetectionBridgePublicationError(
                        "incident lifecycle publication failed for "
                        f"{event.event_id} across "
                        f"{len(report.failures)} subscriber(s)"
                    )

                self._pending.popleft()
                self._lifecycle_events_published += 1
        finally:
            self._flushing = False


def project_incident_lifecycle_event(
    change: IncidentLifecycleChange,
) -> SentinelEvent | None:
    """Project one lifecycle change, coalescing repetitive update observations."""

    if not isinstance(change, IncidentLifecycleChange):
        raise TypeError("change must be an IncidentLifecycleChange")

    if (
        change.kind is IncidentChangeKind.UPDATED
        and change.incident.observation_count
        not in INCIDENT_UPDATE_PUBLICATION_MILESTONES
    ):
        return None

    if change.kind is IncidentChangeKind.RESOLVED:
        kind = EventKind.VERIFICATION
        severity = EventSeverity.INFO
        occurred_at = change.incident.resolved_at
        message = "Systemd service incident resolved."
        if occurred_at is None:
            raise SystemdDetectionBridgeContractError(
                "resolved lifecycle change is missing resolved_at"
            )
    elif change.kind is IncidentChangeKind.OPENED:
        kind = EventKind.ANOMALY
        severity = change.incident.severity
        occurred_at = change.incident.opened_at
        message = "Systemd service incident opened."
    else:
        kind = EventKind.ANOMALY
        severity = change.incident.severity
        occurred_at = change.incident.latest_assessed_at
        message = "Systemd service incident remains active."

    return SentinelEvent(
        event_id=_lifecycle_event_id(change),
        occurred_at=_require_aware_datetime(occurred_at),
        kind=kind,
        source=SYSTEMD_INCIDENT_LIFECYCLE_SOURCE,
        message=message,
        severity=severity,
        attributes={
            "observation_type": SYSTEMD_INCIDENT_LIFECYCLE_TYPE,
            "lifecycle_version": SYSTEMD_INCIDENT_LIFECYCLE_VERSION,
            "change_kind": change.kind.value,
            "trigger_assessment_id": change.trigger_assessment_id,
            "incident": change.incident.to_dict(),
        },
    )


def _lifecycle_event_id(change: IncidentLifecycleChange) -> str:
    digest = hashlib.sha256()
    digest.update(_LIFECYCLE_EVENT_ID_DOMAIN)
    digest.update(change.kind.value.encode("ascii"))
    digest.update(b"\x00")
    digest.update(change.incident.incident_id.encode("ascii"))
    digest.update(b"\x00")
    digest.update(change.trigger_assessment_id.encode("ascii"))
    digest.update(b"\x00")
    digest.update(str(change.incident.observation_count).encode("ascii"))
    return f"incevt-{digest.hexdigest()}"


def _is_systemd_service_observation(event: SentinelEvent) -> bool:
    return (
        event.kind is EventKind.OBSERVATION
        and event.source == SYSTEMD_SERVICE_OBSERVATION_SOURCE
        and event.attributes.get("observation_type") == SYSTEMD_SERVICE_OBSERVATION_TYPE
    )


def _is_derived_lifecycle_event(event: SentinelEvent) -> bool:
    return (
        event.source == SYSTEMD_INCIDENT_LIFECYCLE_SOURCE
        and event.attributes.get("observation_type") == SYSTEMD_INCIDENT_LIFECYCLE_TYPE
    )


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SystemdDetectionBridgeContractError(
            "lifecycle event timestamp must be timezone-aware"
        )
    return value
