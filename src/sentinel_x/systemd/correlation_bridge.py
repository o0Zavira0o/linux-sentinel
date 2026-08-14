"""Live EventBus bridge for bounded systemd correlation evidence."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Final

from sentinel_x.core.bus import EventBus, Subscription
from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.systemd.correlation_runtime import (
    SYSTEMD_CORRELATION_OBSERVATION_SOURCE,
    SYSTEMD_CORRELATION_OBSERVATION_TYPE,
    SystemdCorrelationTracker,
    SystemdCorrelationTrackerSnapshot,
)

_MAX_PENDING_DERIVED_EVENTS: Final[int] = 256


class SystemdCorrelationBridgeError(RuntimeError):
    """Base error for live systemd correlation integration."""


class SystemdCorrelationBridgeContractError(SystemdCorrelationBridgeError):
    """Raised when tracker output violates the bounded bridge contract."""


class SystemdCorrelationBridgePublicationError(SystemdCorrelationBridgeError):
    """Raised when a derived correlation event cannot reach all subscribers."""


@dataclass(frozen=True, slots=True)
class SystemdCorrelationBridgeSnapshot:
    """Read-only state for the live correlation bridge and retry outbox."""

    attached: bool
    pending_derived_events: int
    input_events_received: int
    self_derived_events_ignored: int
    tracker_ingest_attempts: int
    tracker_ingest_failures: int
    derived_events_enqueued: int
    derived_publish_attempts: int
    derived_events_published: int
    derived_publish_failures: int
    subscriber_failures_observed: int
    tracker: SystemdCorrelationTrackerSnapshot

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly bridge state."""

        return {
            "attached": self.attached,
            "pending_derived_events": self.pending_derived_events,
            "input_events_received": self.input_events_received,
            "self_derived_events_ignored": self.self_derived_events_ignored,
            "tracker_ingest_attempts": self.tracker_ingest_attempts,
            "tracker_ingest_failures": self.tracker_ingest_failures,
            "derived_events_enqueued": self.derived_events_enqueued,
            "derived_publish_attempts": self.derived_publish_attempts,
            "derived_events_published": self.derived_events_published,
            "derived_publish_failures": self.derived_publish_failures,
            "subscriber_failures_observed": self.subscriber_failures_observed,
            "tracker": self.tracker.to_dict(),
        }


class SystemdCorrelationEventBridge:
    """Attach stateful correlation to one EventBus with retry-safe derivation.

    Derived events are placed in a bounded in-memory outbox before publication.
    If a derived publication fails, the event remains pending and the bridge
    raises so the originating EventBus publication records the failure. On the
    next input event, pending derived evidence is retried before new tracker
    state is ingested. This preserves the exact derived event identity across
    retries and prevents the tracker from silently advancing past an
    unpublished correlation result.
    """

    def __init__(
        self,
        event_bus: EventBus,
        *,
        tracker: SystemdCorrelationTracker | None = None,
    ) -> None:
        if not isinstance(event_bus, EventBus):
            raise TypeError("event_bus must be an EventBus")
        if tracker is not None and not isinstance(tracker, SystemdCorrelationTracker):
            raise TypeError("tracker must be a SystemdCorrelationTracker or None")

        self._event_bus = event_bus
        self._tracker = SystemdCorrelationTracker() if tracker is None else tracker
        self._pending: deque[SentinelEvent] = deque()
        self._lock = RLock()
        self._attached = True
        self._input_events_received = 0
        self._self_derived_events_ignored = 0
        self._tracker_ingest_attempts = 0
        self._tracker_ingest_failures = 0
        self._derived_events_enqueued = 0
        self._derived_publish_attempts = 0
        self._derived_events_published = 0
        self._derived_publish_failures = 0
        self._subscriber_failures_observed = 0
        self._subscription: Subscription = event_bus.subscribe(self)

    def __call__(self, event: SentinelEvent) -> None:
        """Process one published event and synchronously publish derivations."""

        if not isinstance(event, SentinelEvent):
            raise TypeError("correlation bridge requires a SentinelEvent")

        with self._lock:
            if not self._attached:
                return

            self._input_events_received += 1
            if _is_derived_correlation_event(event):
                self._self_derived_events_ignored += 1
                return

            self._flush_pending_locked()
            self._tracker_ingest_attempts += 1

            try:
                derived_events = self._tracker.ingest(event)
            except Exception:
                self._tracker_ingest_failures += 1
                raise

            if not derived_events:
                return
            if len(derived_events) > _MAX_PENDING_DERIVED_EVENTS:
                raise SystemdCorrelationBridgeContractError(
                    "tracker produced more derived events than the bridge bound"
                )

            self._pending.extend(derived_events)
            self._derived_events_enqueued += len(derived_events)
            self._flush_pending_locked()

    def flush(self) -> None:
        """Retry every currently pending derived correlation event."""

        with self._lock:
            if not self._attached:
                return
            self._flush_pending_locked()

    def close(self) -> None:
        """Flush pending evidence and detach from the EventBus.

        Detachment is guaranteed even when the final flush fails. The failure
        is re-raised after unsubscribe so callers can surface an incomplete
        derived-evidence outbox during shutdown.
        """

        with self._lock:
            if not self._attached:
                return

            failure: SystemdCorrelationBridgePublicationError | None = None
            try:
                self._flush_pending_locked()
            except SystemdCorrelationBridgePublicationError as exc:
                failure = exc

            self._event_bus.unsubscribe(self._subscription)
            self._attached = False

            if failure is not None:
                raise failure

    def snapshot(self) -> SystemdCorrelationBridgeSnapshot:
        """Return immutable bridge, outbox, and tracker accounting."""

        with self._lock:
            return SystemdCorrelationBridgeSnapshot(
                attached=self._attached,
                pending_derived_events=len(self._pending),
                input_events_received=self._input_events_received,
                self_derived_events_ignored=self._self_derived_events_ignored,
                tracker_ingest_attempts=self._tracker_ingest_attempts,
                tracker_ingest_failures=self._tracker_ingest_failures,
                derived_events_enqueued=self._derived_events_enqueued,
                derived_publish_attempts=self._derived_publish_attempts,
                derived_events_published=self._derived_events_published,
                derived_publish_failures=self._derived_publish_failures,
                subscriber_failures_observed=self._subscriber_failures_observed,
                tracker=self._tracker.snapshot(),
            )

    def _flush_pending_locked(self) -> None:
        while self._pending:
            event = self._pending[0]
            self._derived_publish_attempts += 1
            report = self._event_bus.publish(event)
            if not report.succeeded:
                self._derived_publish_failures += 1
                self._subscriber_failures_observed += len(report.failures)
                raise SystemdCorrelationBridgePublicationError(
                    "derived correlation publication failed for "
                    f"{event.event_id} across {len(report.failures)} subscriber(s)"
                )

            self._pending.popleft()
            self._derived_events_published += 1


def _is_derived_correlation_event(event: SentinelEvent) -> bool:
    return (
        event.kind is EventKind.OBSERVATION
        and event.source == SYSTEMD_CORRELATION_OBSERVATION_SOURCE
        and event.attributes.get("observation_type")
        == SYSTEMD_CORRELATION_OBSERVATION_TYPE
    )
