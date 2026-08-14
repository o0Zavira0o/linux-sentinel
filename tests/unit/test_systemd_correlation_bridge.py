"""Tests for live retry-safe systemd correlation EventBus integration."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.core import EventBus, EventKind, SentinelEvent
from sentinel_x.systemd import (
    SYSTEMD_CORRELATION_OBSERVATION_SOURCE,
    SYSTEMD_CORRELATION_OBSERVATION_TYPE,
    SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
    SYSTEMD_JOURNAL_OBSERVATION_TYPE,
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
    SystemdCorrelationBridgePublicationError,
    SystemdCorrelationEventBridge,
    SystemdCorrelationTracker,
)

_BOOT_ID = "a" * 32
_INVOCATION_ID = "1" * 32
_NOW = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)


def _service_event(event_id: str = "service-1") -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_SERVICE_OBSERVATION_SOURCE,
        message="service state",
        event_id=event_id,
        occurred_at=_NOW,
        attributes={
            "observation_type": SYSTEMD_SERVICE_OBSERVATION_TYPE,
            "requested_name": "demo.service",
            "canonical_name": "demo.service",
            "names": ["demo.service"],
            "boot_id": _BOOT_ID,
            "invocation_id": _INVOCATION_ID,
            "state_change_monotonic_usec": 1_000_000,
        },
    )


def _journal_event(event_id: str = "journal-1") -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
        message="journal evidence",
        event_id=event_id,
        occurred_at=_NOW,
        attributes={
            "observation_type": SYSTEMD_JOURNAL_OBSERVATION_TYPE,
            "requested_unit": "demo.service",
            "boot_id": _BOOT_ID,
            "entry_count": 1,
            "entries": [
                {
                    "index": 0,
                    "cursor_sha256": "b" * 64,
                    "realtime_timestamp_usec": 2_000_000,
                    "monotonic_timestamp_usec": 1_000_100,
                    "boot_id": _BOOT_ID,
                    "priority": 6,
                    "message": {"kind": "text", "value": "started"},
                    "systemd_unit": "demo.service",
                    "systemd_invocation_id": _INVOCATION_ID,
                    "invocation_id": None,
                    "unit": None,
                    "object_systemd_unit": None,
                    "object_systemd_invocation_id": None,
                    "coredump_unit": None,
                }
            ],
        },
    )


def _unrelated_event(event_id: str = "host-1") -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.host.cpu",
        message="cpu",
        event_id=event_id,
        occurred_at=_NOW,
        attributes={"observation_type": "linux.host.cpu"},
    )


class _FailingCorrelationSink:
    def __init__(self, failures_remaining: int) -> None:
        self.failures_remaining = failures_remaining
        self.correlation_event_ids: list[str] = []

    def __call__(self, event: SentinelEvent) -> None:
        if event.source != SYSTEMD_CORRELATION_OBSERVATION_SOURCE:
            return
        self.correlation_event_ids.append(event.event_id)
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("simulated derived-event sink failure")


class SystemdCorrelationEventBridgeTests(unittest.TestCase):
    """Validate live derivation, retry outbox, and safe detachment."""

    def test_service_and_journal_publish_derived_event_to_existing_sinks(self) -> None:
        bus = EventBus()
        events: list[SentinelEvent] = []
        bus.subscribe(events.append)
        bridge = SystemdCorrelationEventBridge(bus)

        self.assertTrue(bus.publish(_service_event()).succeeded)
        self.assertTrue(bus.publish(_journal_event()).succeeded)

        derived = [
            event
            for event in events
            if event.source == SYSTEMD_CORRELATION_OBSERVATION_SOURCE
        ]
        self.assertEqual(len(derived), 1)
        self.assertEqual(
            derived[0].attributes["observation_type"],
            SYSTEMD_CORRELATION_OBSERVATION_TYPE,
        )
        self.assertEqual(derived[0].attributes["evidence_strength"], "strong")
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.derived_events_published, 1)
        self.assertEqual(snapshot.pending_derived_events, 0)
        self.assertEqual(snapshot.self_derived_events_ignored, 1)

    def test_failed_derived_publication_is_retried_with_same_event_id(self) -> None:
        bus = EventBus()
        sink = _FailingCorrelationSink(failures_remaining=1)
        bus.subscribe(sink)
        bridge = SystemdCorrelationEventBridge(bus)

        self.assertTrue(bus.publish(_service_event()).succeeded)
        first_report = bus.publish(_journal_event())

        self.assertFalse(first_report.succeeded)
        self.assertEqual(bridge.snapshot().pending_derived_events, 1)
        self.assertEqual(len(sink.correlation_event_ids), 1)

        retry_report = bus.publish(_journal_event())

        self.assertTrue(retry_report.succeeded)
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.pending_derived_events, 0)
        self.assertEqual(snapshot.derived_publish_attempts, 2)
        self.assertEqual(snapshot.derived_publish_failures, 1)
        self.assertEqual(snapshot.derived_events_published, 1)
        self.assertEqual(len(sink.correlation_event_ids), 2)
        self.assertEqual(
            sink.correlation_event_ids[0],
            sink.correlation_event_ids[1],
        )
        self.assertEqual(
            snapshot.tracker.duplicate_input_events_ignored,
            1,
        )

    def test_pending_failure_blocks_new_tracker_ingest_until_outbox_clears(
        self,
    ) -> None:
        bus = EventBus()
        sink = _FailingCorrelationSink(failures_remaining=2)
        bus.subscribe(sink)
        bridge = SystemdCorrelationEventBridge(bus)

        bus.publish(_service_event())
        first = bus.publish(_journal_event())
        self.assertFalse(first.succeeded)

        blocked = bus.publish(_unrelated_event())
        self.assertFalse(blocked.succeeded)
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.pending_derived_events, 1)
        self.assertEqual(snapshot.tracker.unrelated_events_ignored, 0)
        self.assertEqual(snapshot.tracker_ingest_attempts, 2)

    def test_close_flushes_pending_event_before_unsubscribe(self) -> None:
        bus = EventBus()
        sink = _FailingCorrelationSink(failures_remaining=1)
        bus.subscribe(sink)
        bridge = SystemdCorrelationEventBridge(bus)

        bus.publish(_service_event())
        bus.publish(_journal_event())
        self.assertEqual(bridge.snapshot().pending_derived_events, 1)

        bridge.close()

        snapshot = bridge.snapshot()
        self.assertFalse(snapshot.attached)
        self.assertEqual(snapshot.pending_derived_events, 0)
        self.assertEqual(snapshot.derived_events_published, 1)
        self.assertEqual(bus.subscriber_count, 1)

    def test_close_failure_detaches_and_preserves_pending_evidence(self) -> None:
        bus = EventBus()
        sink = _FailingCorrelationSink(failures_remaining=10)
        bus.subscribe(sink)
        bridge = SystemdCorrelationEventBridge(bus)

        bus.publish(_service_event())
        bus.publish(_journal_event())

        with self.assertRaises(SystemdCorrelationBridgePublicationError):
            bridge.close()

        snapshot = bridge.snapshot()
        self.assertFalse(snapshot.attached)
        self.assertEqual(snapshot.pending_derived_events, 1)
        self.assertEqual(bus.subscriber_count, 1)

    def test_unrelated_event_is_retained_only_in_tracker_accounting(self) -> None:
        bus = EventBus()
        bridge = SystemdCorrelationEventBridge(bus)

        report = bus.publish(_unrelated_event())

        self.assertTrue(report.succeeded)
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.tracker.unrelated_events_ignored, 1)
        self.assertEqual(snapshot.derived_events_enqueued, 0)
        self.assertEqual(snapshot.pending_derived_events, 0)

    def test_closed_bridge_is_idempotent_and_ignores_direct_calls(self) -> None:
        bus = EventBus()
        bridge = SystemdCorrelationEventBridge(bus)

        bridge.close()
        bridge.close()
        bridge(_service_event())

        snapshot = bridge.snapshot()
        self.assertFalse(snapshot.attached)
        self.assertEqual(snapshot.input_events_received, 0)

    def test_snapshot_is_serialization_friendly(self) -> None:
        bridge = SystemdCorrelationEventBridge(EventBus())
        bridge(_service_event())

        payload = bridge.snapshot().to_dict()

        self.assertIs(payload["attached"], True)
        self.assertIsInstance(payload["tracker"], dict)
        self.assertEqual(payload["tracker_ingest_attempts"], 1)

    def test_constructor_rejects_invalid_dependencies(self) -> None:
        with self.assertRaises(TypeError):
            SystemdCorrelationEventBridge(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            SystemdCorrelationEventBridge(
                EventBus(),
                tracker=object(),  # type: ignore[arg-type]
            )
        bridge = SystemdCorrelationEventBridge(
            EventBus(),
            tracker=SystemdCorrelationTracker(),
        )
        self.assertTrue(bridge.snapshot().attached)


if __name__ == "__main__":
    unittest.main()
