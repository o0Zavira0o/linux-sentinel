"""Tests for live EventBus systemd detection and incident lifecycle wiring."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import cast

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.core.bus import EventBus, PublishReport
from sentinel_x.detection.bridge import (
    INCIDENT_UPDATE_PUBLICATION_MILESTONES,
    SYSTEMD_INCIDENT_LIFECYCLE_SOURCE,
    SYSTEMD_INCIDENT_LIFECYCLE_TYPE,
    SYSTEMD_INCIDENT_LIFECYCLE_VERSION,
    SystemdDetectionBridgeContractError,
    SystemdDetectionBridgePublicationError,
    SystemdDetectionEventBridge,
    project_incident_lifecycle_event,
)
from sentinel_x.detection.incidents import (
    IncidentChangeKind,
    IncidentLifecycleChange,
    IncidentTrackingResult,
    SystemdIncidentTracker,
)
from sentinel_x.detection.models import SystemdServiceHealthAssessment
from sentinel_x.detection.systemd import (
    SystemdDetectionContractError,
    SystemdServiceStateDetector,
)
from sentinel_x.systemd import SystemdServiceSnapshot, systemd_service_snapshot_to_event
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

_BOOT_ID = "a" * 32
_BASE_TIME = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)


class _AssessmentClock:
    def __init__(self) -> None:
        self.wall = _BASE_TIME
        self.monotonic_ns = 1_000_000

    def wall_clock(self) -> datetime:
        return self.wall

    def monotonic_clock(self) -> int:
        return self.monotonic_ns

    def advance(self) -> None:
        self.wall += timedelta(milliseconds=20)
        self.monotonic_ns += 20_000_000


class _LifecycleSink:
    def __init__(self) -> None:
        self.fail = False
        self.events: list[SentinelEvent] = []

    def __call__(self, event: SentinelEvent) -> None:
        if event.source != SYSTEMD_INCIDENT_LIFECYCLE_SOURCE:
            return
        self.events.append(event)
        if self.fail:
            raise RuntimeError("lifecycle sink unavailable")


class _TooManyChangeTracker(SystemdIncidentTracker):
    def __init__(self, result: IncidentTrackingResult) -> None:
        super().__init__()
        self._result = result

    def process(
        self,
        _assessment: SystemdServiceHealthAssessment,
    ) -> IncidentTrackingResult:
        return self._result


def _snapshot(
    *,
    unit: str = "example.service",
    canonical: str | None = None,
    active_state: str = "active",
    sub_state: str = "running",
    main_pid: int | None = 321,
    result: str | None = "success",
) -> SystemdServiceSnapshot:
    canonical_name = unit if canonical is None else canonical
    names = tuple(dict.fromkeys((canonical_name, unit)))
    return SystemdServiceSnapshot(
        requested_name=unit,
        canonical_name=canonical_name,
        names=names,
        description="Example service",
        load_state="loaded",
        active_state=active_state,
        sub_state=sub_state,
        unit_file_state="enabled",
        service_type="simple",
        restart_policy="on-failure",
        result=result,
        invocation_id="0123456789abcdef",
        control_group=f"/system.slice/{canonical_name}",
        fragment_path=f"/usr/lib/systemd/system/{canonical_name}",
        source_path=None,
        drop_in_paths=(),
        requires=(),
        wants=(),
        after=(),
        before=(),
        can_start=True,
        can_stop=True,
        can_reload=False,
        main_pid=main_pid,
        exec_main_code=1,
        exec_main_status=0,
        restart_count=0,
        state_change_monotonic_usec=1_000,
        active_enter_monotonic_usec=900,
        inactive_enter_monotonic_usec=None,
        exec_main_start_monotonic_usec=850,
        exec_main_exit_monotonic_usec=None,
        captured_at=_BASE_TIME,
    )


def _service_event(snapshot: SystemdServiceSnapshot) -> SentinelEvent:
    return systemd_service_snapshot_to_event(
        snapshot,
        collector_name="systemd.example.0123456789ab",
        boot_id=_BOOT_ID,
    )


def _inactive_event(*, unit: str = "example.service") -> SentinelEvent:
    return _service_event(
        _snapshot(
            unit=unit,
            active_state="inactive",
            sub_state="dead",
            main_pid=None,
        )
    )


def _failed_event(*, unit: str = "example.service") -> SentinelEvent:
    return _service_event(
        _snapshot(
            unit=unit,
            active_state="failed",
            sub_state="failed",
            main_pid=None,
            result="signal",
        )
    )


def _healthy_event(*, unit: str = "example.service") -> SentinelEvent:
    return _service_event(_snapshot(unit=unit))


def _bridge(
    bus: EventBus,
    clock: _AssessmentClock,
    *,
    tracker: SystemdIncidentTracker | None = None,
) -> SystemdDetectionEventBridge:
    return SystemdDetectionEventBridge(
        bus,
        detector=SystemdServiceStateDetector(
            wall_clock=clock.wall_clock,
            monotonic_ns_clock=clock.monotonic_clock,
        ),
        tracker=tracker,
    )


def _lifecycle_events(events: list[SentinelEvent]) -> list[SentinelEvent]:
    return [
        event for event in events if event.source == SYSTEMD_INCIDENT_LIFECYCLE_SOURCE
    ]


class SystemdDetectionBridgeTests(unittest.TestCase):
    def test_constructor_rejects_invalid_dependencies(self) -> None:
        bus = EventBus()
        with self.assertRaises(TypeError):
            SystemdDetectionEventBridge(cast(EventBus, object()))
        with self.assertRaises(TypeError):
            SystemdDetectionEventBridge(
                bus,
                detector=cast(SystemdServiceStateDetector, object()),
            )
        with self.assertRaises(TypeError):
            SystemdDetectionEventBridge(
                bus,
                tracker=cast(SystemdIncidentTracker, object()),
            )

    def test_attach_and_close_update_subscriber_count(self) -> None:
        bus = EventBus()
        bridge = SystemdDetectionEventBridge(bus)
        self.assertEqual(bus.subscriber_count, 1)
        bridge.close()
        self.assertEqual(bus.subscriber_count, 0)
        self.assertFalse(bridge.snapshot().attached)

    def test_close_is_idempotent(self) -> None:
        bus = EventBus()
        bridge = SystemdDetectionEventBridge(bus)
        bridge.close()
        bridge.close()
        self.assertEqual(bus.subscriber_count, 0)

    def test_unsupported_event_is_ignored_without_detection(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        bridge = _bridge(bus, clock)
        bridge(
            SentinelEvent(
                kind=EventKind.OBSERVATION,
                source="other.source",
                message="other observation",
            )
        )
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.unsupported_events_ignored, 1)
        self.assertEqual(snapshot.detection_attempts, 0)

    def test_healthy_observation_does_not_emit_lifecycle_event(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_healthy_event())
        self.assertEqual(sink.events, [])
        tracker_snapshot = bridge.snapshot().tracker
        self.assertEqual(len(tracker_snapshot.targets), 1)
        self.assertIsNone(tracker_snapshot.targets[0].open_incident)

    def test_inactive_observation_opens_warning_incident_event(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        self.assertEqual(len(sink.events), 1)
        event = sink.events[0]
        self.assertIs(event.kind, EventKind.ANOMALY)
        self.assertIs(event.severity, EventSeverity.WARNING)
        self.assertEqual(event.attributes["change_kind"], "opened")

    def test_failed_observation_opens_error_incident_event(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_failed_event())
        self.assertEqual(len(sink.events), 1)
        self.assertIs(sink.events[0].severity, EventSeverity.ERROR)

    def test_exact_source_event_replay_is_deduplicated(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        event = _inactive_event()
        bridge(event)
        clock.advance()
        bridge(event)
        snapshot = bridge.snapshot()
        self.assertEqual(len(sink.events), 1)
        self.assertEqual(snapshot.deduplicated_assessments, 1)
        self.assertEqual(snapshot.lifecycle_changes_observed, 1)

    def test_update_milestones_are_fixed_and_bounded(self) -> None:
        self.assertEqual(
            INCIDENT_UPDATE_PUBLICATION_MILESTONES,
            (2, 4, 8, 16, 32, 64, 128, 256),
        )

    def test_updates_publish_only_on_milestones(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        for _ in range(4):
            bridge(_inactive_event())
            clock.advance()
        changes = [event.attributes["change_kind"] for event in sink.events]
        counts = [
            event.attributes["incident"]["observation_count"] for event in sink.events
        ]
        self.assertEqual(changes, ["opened", "updated", "updated"])
        self.assertEqual(counts, [1, 2, 4])
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.lifecycle_changes_observed, 4)
        self.assertEqual(snapshot.lifecycle_changes_suppressed, 1)

    def test_persistent_fault_external_updates_remain_strictly_bounded(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)

        for _ in range(300):
            bridge(_inactive_event())
            clock.advance()

        active_events = list(sink.events)
        published_counts = [
            event.attributes["incident"]["observation_count"] for event in active_events
        ]
        self.assertEqual(
            published_counts,
            [1, 2, 4, 8, 16, 32, 64, 128, 256],
        )
        self.assertEqual(len(active_events), 9)
        self.assertEqual(bridge.snapshot().lifecycle_changes_observed, 300)
        self.assertEqual(bridge.snapshot().lifecycle_changes_suppressed, 291)

        bridge(_healthy_event())
        self.assertEqual(len(sink.events), 10)
        self.assertEqual(sink.events[-1].attributes["change_kind"], "resolved")
        self.assertEqual(
            sink.events[-1].attributes["incident"]["observation_count"],
            300,
        )

    def test_resolution_is_always_published_as_verification(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        clock.advance()
        bridge(_healthy_event())
        self.assertEqual(len(sink.events), 2)
        resolved = sink.events[-1]
        self.assertIs(resolved.kind, EventKind.VERIFICATION)
        self.assertIs(resolved.severity, EventSeverity.INFO)
        self.assertEqual(resolved.attributes["change_kind"], "resolved")
        self.assertEqual(
            resolved.attributes["incident"]["resolution_reason"],
            "recovered",
        )

    def test_recurrence_gets_new_incident_and_event_identity(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        clock.advance()
        bridge(_healthy_event())
        clock.advance()
        bridge(_inactive_event())
        opened = [
            event
            for event in sink.events
            if event.attributes["change_kind"] == "opened"
        ]
        self.assertEqual(len(opened), 2)
        self.assertNotEqual(
            opened[0].attributes["incident"]["incident_id"],
            opened[1].attributes["incident"]["incident_id"],
        )
        self.assertNotEqual(opened[0].event_id, opened[1].event_id)

    def test_reclassification_publishes_resolved_then_opened(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        clock.advance()
        bridge(_failed_event())
        changes = [event.attributes["change_kind"] for event in sink.events]
        self.assertEqual(changes, ["opened", "resolved", "opened"])
        resolved = sink.events[1].attributes["incident"]
        replacement = sink.events[2].attributes["incident"]
        self.assertEqual(resolved["resolution_reason"], "reclassified")
        self.assertEqual(
            resolved["replacement_incident_id"],
            replacement["incident_id"],
        )

    def test_self_derived_events_are_ignored_without_recursion(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.lifecycle_events_published, 1)
        self.assertEqual(snapshot.self_derived_events_ignored, 1)

    def test_projection_has_stable_identity(self) -> None:
        clock = _AssessmentClock()
        detector = SystemdServiceStateDetector(
            wall_clock=clock.wall_clock,
            monotonic_ns_clock=clock.monotonic_clock,
        )
        result = SystemdIncidentTracker().process(detector.assess(_inactive_event()))
        change = result.changes[0]
        first = project_incident_lifecycle_event(change)
        second = project_incident_lifecycle_event(change)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.occurred_at, second.occurred_at)

    def test_projection_rejects_untyped_change(self) -> None:
        with self.assertRaises(TypeError):
            project_incident_lifecycle_event(cast(IncidentLifecycleChange, object()))

    def test_projection_suppresses_nonmilestone_update(self) -> None:
        clock = _AssessmentClock()
        detector = SystemdServiceStateDetector(
            wall_clock=clock.wall_clock,
            monotonic_ns_clock=clock.monotonic_clock,
        )
        tracker = SystemdIncidentTracker()
        tracker.process(detector.assess(_inactive_event()))
        clock.advance()
        tracker.process(detector.assess(_inactive_event()))
        clock.advance()
        third = tracker.process(detector.assess(_inactive_event()))
        self.assertIs(third.changes[0].kind, IncidentChangeKind.UPDATED)
        self.assertEqual(third.changes[0].incident.observation_count, 3)
        self.assertIsNone(project_incident_lifecycle_event(third.changes[0]))

    def test_lifecycle_event_projection_is_bounded_and_versioned(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        event = sink.events[0]
        self.assertEqual(
            set(event.attributes),
            {
                "observation_type",
                "lifecycle_version",
                "change_kind",
                "trigger_assessment_id",
                "incident",
            },
        )
        self.assertEqual(
            event.attributes["observation_type"],
            SYSTEMD_INCIDENT_LIFECYCLE_TYPE,
        )
        self.assertEqual(
            event.attributes["lifecycle_version"],
            SYSTEMD_INCIDENT_LIFECYCLE_VERSION,
        )
        json.dumps(event.to_dict())

    def test_publication_failure_retains_exact_pending_event(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        first_id = sink.events[-1].event_id
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.pending_lifecycle_events, 1)
        self.assertEqual(snapshot.lifecycle_publish_failures, 1)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge.flush()
        self.assertEqual(sink.events[-1].event_id, first_id)

    def test_pending_event_retries_before_new_detection(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        attempts_before = bridge.snapshot().detection_attempts
        clock.advance()
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        blocked = bridge.snapshot()
        self.assertEqual(blocked.detection_attempts, attempts_before)
        self.assertEqual(blocked.deferred_service_observations, 1)
        self.assertEqual(blocked.service_observations_deferred, 1)

        sink.fail = False
        clock.advance()
        bridge(_inactive_event())
        recovered = bridge.snapshot()
        self.assertEqual(recovered.pending_lifecycle_events, 0)
        self.assertEqual(recovered.deferred_service_observations, 0)
        self.assertEqual(
            recovered.deferred_service_observations_processed,
            1,
        )
        self.assertEqual(recovered.detection_attempts, attempts_before + 2)

    def test_partial_delivery_retry_preserves_event_id(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        delivered: list[str] = []
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(
            lambda event: (
                delivered.append(event.event_id)
                if event.source == SYSTEMD_INCIDENT_LIFECYCLE_SOURCE
                else None
            )
        )
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        first_id = delivered[-1]
        sink.fail = False
        bridge.flush()
        self.assertEqual(delivered, [first_id, first_id])

    def test_subscriber_failure_accounting_is_explicit(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        first = _LifecycleSink()
        second = _LifecycleSink()
        first.fail = True
        second.fail = True
        bus.subscribe(first)
        bus.subscribe(second)
        bridge = _bridge(bus, clock)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.subscriber_failures_observed, 2)
        self.assertEqual(snapshot.lifecycle_publish_failures, 1)

    def test_close_flushes_pending_then_detaches(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        sink.fail = False
        bridge.close()
        snapshot = bridge.snapshot()
        self.assertFalse(snapshot.attached)
        self.assertEqual(snapshot.pending_lifecycle_events, 0)
        self.assertEqual(bus.subscriber_count, 1)

    def test_deferred_service_observation_capacity_fails_without_eviction(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)

        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())

        for _ in range(256):
            clock.advance()
            with self.assertRaises(SystemdDetectionBridgePublicationError):
                bridge(_inactive_event())

        self.assertEqual(bridge.snapshot().deferred_service_observations, 256)
        clock.advance()
        with self.assertRaises(SystemdDetectionBridgeContractError):
            bridge(_inactive_event())

        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.deferred_service_observations, 256)
        self.assertEqual(snapshot.contract_failures, 1)

    def test_close_drains_deferred_service_observation_after_recovery(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)

        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        clock.advance()
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())

        self.assertEqual(bridge.snapshot().deferred_service_observations, 1)
        sink.fail = False
        bridge.close()

        snapshot = bridge.snapshot()
        self.assertFalse(snapshot.attached)
        self.assertEqual(snapshot.pending_lifecycle_events, 0)
        self.assertEqual(snapshot.deferred_service_observations, 0)
        self.assertEqual(snapshot.deferred_service_observations_processed, 1)

    def test_close_detaches_even_when_final_flush_fails(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge.close()
        snapshot = bridge.snapshot()
        self.assertFalse(snapshot.attached)
        self.assertEqual(snapshot.pending_lifecycle_events, 1)
        self.assertEqual(bus.subscriber_count, 1)

    def test_flush_after_close_is_noop(self) -> None:
        bus = EventBus()
        bridge = SystemdDetectionEventBridge(bus)
        bridge.close()
        bridge.flush()
        self.assertFalse(bridge.snapshot().attached)

    def test_malformed_matching_observation_counts_detection_failure(self) -> None:
        bus = EventBus()
        bridge = SystemdDetectionEventBridge(bus)
        malformed = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source=SYSTEMD_SERVICE_OBSERVATION_SOURCE,
            message="malformed systemd observation",
            attributes={"observation_type": SYSTEMD_SERVICE_OBSERVATION_TYPE},
        )
        with self.assertRaises(SystemdDetectionContractError):
            bridge(malformed)
        self.assertEqual(bridge.snapshot().detection_failures, 1)

    def test_canonical_drift_counts_tracker_failure(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        clock.advance()
        drifted = _service_event(
            _snapshot(
                canonical="other.service",
                active_state="inactive",
                sub_state="dead",
                main_pid=None,
            )
        )
        with self.assertRaisesRegex(Exception, "canonical unit drift"):
            bridge(drifted)
        self.assertEqual(bridge.snapshot().tracker_process_failures, 1)

    def test_two_targets_keep_distinct_incident_state(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        bridge(_inactive_event(unit="alpha.service"))
        clock.advance()
        bridge(_failed_event(unit="beta.service"))
        incident_ids = {
            event.attributes["incident"]["incident_id"] for event in sink.events
        }
        self.assertEqual(len(incident_ids), 2)
        self.assertEqual(len(bridge.snapshot().tracker.targets), 2)

    def test_bridge_rejects_more_than_two_tracker_changes(self) -> None:
        clock = _AssessmentClock()
        detector = SystemdServiceStateDetector(
            wall_clock=clock.wall_clock,
            monotonic_ns_clock=clock.monotonic_clock,
        )
        assessment = detector.assess(_inactive_event())
        normal = SystemdIncidentTracker().process(assessment)
        change = normal.changes[0]
        invalid_result = IncidentTrackingResult(
            assessment_id=normal.assessment_id,
            target_unit=normal.target_unit,
            deduplicated=False,
            changes=(change, change, change),
            active_incident=normal.active_incident,
        )
        tracker = _TooManyChangeTracker(invalid_result)
        bus = EventBus()
        bridge = _bridge(bus, clock, tracker=tracker)
        with self.assertRaises(SystemdDetectionBridgeContractError):
            bridge(_inactive_event())
        self.assertEqual(bridge.snapshot().contract_failures, 1)

    def test_snapshot_serialization_tracks_bridge_accounting(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        bridge = _bridge(bus, clock)
        bridge(_inactive_event())
        snapshot = bridge.snapshot()
        payload = snapshot.to_dict()
        self.assertTrue(payload["attached"])
        self.assertEqual(payload["assessments_processed"], 1)
        self.assertEqual(payload["lifecycle_events_published"], 1)
        self.assertEqual(payload["contract_failures"], 0)
        json.dumps(payload)

    def test_unrelated_input_does_not_reenter_pending_outbox(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())

        pending_before = bridge.snapshot().pending_lifecycle_events
        ignored = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="sentinel_x.systemd.correlation",
            message="unrelated derived evidence",
            attributes={
                "observation_type": "linux.systemd.service_journal.correlation"
            },
        )
        bridge(ignored)

        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.pending_lifecycle_events, pending_before)
        self.assertEqual(snapshot.unsupported_events_ignored, 1)
        self.assertEqual(snapshot.lifecycle_publish_attempts, 1)

    def test_nested_correlation_event_cannot_reenter_detection_outbox(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)

        nested_reports: list[PublishReport] = []

        def publish_correlation(event: SentinelEvent) -> None:
            if event.source != SYSTEMD_INCIDENT_LIFECYCLE_SOURCE:
                return
            nested_reports.append(
                bus.publish(
                    SentinelEvent(
                        kind=EventKind.OBSERVATION,
                        source="sentinel_x.systemd.correlation",
                        message="nested correlation evidence",
                        attributes={
                            "observation_type": (
                                "linux.systemd.service_journal.correlation"
                            )
                        },
                    )
                )
            )

        bus.subscribe(publish_correlation)
        bridge = _bridge(bus, clock)

        with self.assertRaises(SystemdDetectionBridgePublicationError):
            bridge(_inactive_event())

        self.assertEqual(len(nested_reports), 1)
        self.assertTrue(nested_reports[0].succeeded)
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot.pending_lifecycle_events, 1)
        self.assertEqual(snapshot.unsupported_events_ignored, 1)
        self.assertEqual(snapshot.lifecycle_publish_attempts, 1)

    def test_bus_publish_surfaces_bridge_failure_in_report(self) -> None:
        bus = EventBus()
        clock = _AssessmentClock()
        sink = _LifecycleSink()
        sink.fail = True
        bus.subscribe(sink)
        bridge = _bridge(bus, clock)
        report = bus.publish(_inactive_event())
        self.assertFalse(report.succeeded)
        self.assertTrue(
            any(
                failure.error_type == "SystemdDetectionBridgePublicationError"
                for failure in report.failures
            )
        )
        self.assertEqual(bridge.snapshot().pending_lifecycle_events, 1)


if __name__ == "__main__":
    unittest.main()
