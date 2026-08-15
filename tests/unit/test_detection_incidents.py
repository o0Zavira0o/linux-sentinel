from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sentinel_x.core.events import EventSeverity
from sentinel_x.detection.incidents import (
    INCIDENT_TRACKER_VERSION,
    IncidentChangeKind,
    IncidentLifecycleChange,
    IncidentResolutionReason,
    IncidentState,
    IncidentTargetState,
    IncidentTrackerSnapshot,
    IncidentTrackingContractError,
    IncidentTrackingError,
    IncidentTrackingResult,
    SystemdIncidentTracker,
)
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    DetectionBasis,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)


BASE_TIME = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)


def _assessment(
    *,
    ordinal: int,
    status: SystemdServiceHealthStatus,
    target: str = "sentinel-x-lab-incident.service",
    canonical: str | None = None,
    monotonic_usec: int | None = None,
) -> SystemdServiceHealthAssessment:
    if canonical is None:
        canonical = target
    if monotonic_usec is None:
        monotonic_usec = 1_000_000 + ordinal * 10_000

    if status is SystemdServiceHealthStatus.HEALTHY:
        active_state = "active"
        sub_state = "running"
        main_pid = 1234
        anomaly = None
        severity = None
    elif status is SystemdServiceHealthStatus.INACTIVE:
        active_state = "inactive"
        sub_state = "dead"
        main_pid = None
        anomaly = DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
        severity = EventSeverity.WARNING
    elif status is SystemdServiceHealthStatus.FAILED:
        active_state = "failed"
        sub_state = "failed"
        main_pid = None
        anomaly = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        severity = EventSeverity.ERROR
    else:
        active_state = "activating"
        sub_state = "start"
        main_pid = None
        anomaly = None
        severity = None

    digest = f"{ordinal:064x}"[-64:]
    return SystemdServiceHealthAssessment(
        assessment_id=f"asmt-{digest}",
        source_event_id=f"source-{ordinal}",
        target_unit=target,
        canonical_unit=canonical,
        source_observed_at=BASE_TIME + timedelta(microseconds=ordinal),
        assessed_at=BASE_TIME + timedelta(microseconds=ordinal + 1),
        assessed_monotonic_usec=monotonic_usec,
        state_change_monotonic_usec=None,
        load_state="loaded",
        active_state=active_state,
        sub_state=sub_state,
        main_pid=main_pid,
        result=None,
        status=status,
        anomaly_class=anomaly,
        severity=severity,
        basis=DetectionBasis.DIRECT_SYSTEMD_STATE,
    )


class SystemdIncidentTrackerTests(unittest.TestCase):
    def test_inactive_assessment_opens_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        self.assertFalse(result.deduplicated)
        self.assertEqual(len(result.changes), 1)
        self.assertIs(result.changes[0].kind, IncidentChangeKind.OPENED)
        self.assertIsNotNone(result.active_incident)
        assert result.active_incident is not None
        self.assertEqual(result.active_incident.observation_count, 1)
        self.assertTrue(result.active_incident.is_open)

    def test_failed_assessment_opens_failed_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.FAILED)
        )
        assert result.active_incident is not None
        self.assertIs(
            result.active_incident.anomaly_class,
            DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
        )
        self.assertIs(result.active_incident.severity, EventSeverity.ERROR)

    def test_repeated_same_fault_updates_one_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        first = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        second = tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.INACTIVE)
        )
        assert first.active_incident is not None
        assert second.active_incident is not None
        self.assertEqual(
            first.active_incident.incident_id,
            second.active_incident.incident_id,
        )
        self.assertEqual(second.active_incident.observation_count, 2)
        self.assertIs(second.changes[0].kind, IncidentChangeKind.UPDATED)

    def test_exact_assessment_retry_is_deduplicated_without_count_change(self) -> None:
        tracker = SystemdIncidentTracker()
        assessment = _assessment(
            ordinal=1,
            status=SystemdServiceHealthStatus.INACTIVE,
        )
        first = tracker.process(assessment)
        retry = tracker.process(assessment)
        assert first.active_incident is not None
        assert retry.active_incident is not None
        self.assertTrue(retry.deduplicated)
        self.assertEqual(retry.changes, ())
        self.assertEqual(retry.active_incident.observation_count, 1)

    def test_healthy_assessment_resolves_open_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.FAILED)
        )
        result = tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.HEALTHY)
        )
        self.assertIsNone(result.active_incident)
        self.assertEqual(len(result.changes), 1)
        change = result.changes[0]
        self.assertIs(change.kind, IncidentChangeKind.RESOLVED)
        self.assertIs(change.incident.state, IncidentState.RESOLVED)
        self.assertIs(
            change.incident.resolution_reason,
            IncidentResolutionReason.RECOVERED,
        )

    def test_healthy_without_incident_is_noop(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.HEALTHY)
        )
        self.assertEqual(result.changes, ())
        self.assertIsNone(result.active_incident)
        self.assertFalse(result.deduplicated)

    def test_unassessed_does_not_open_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.UNASSESSED)
        )
        self.assertEqual(result.changes, ())
        self.assertIsNone(result.active_incident)

    def test_unassessed_does_not_resolve_existing_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        opened = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        result = tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.UNASSESSED)
        )
        assert opened.active_incident is not None
        assert result.active_incident is not None
        self.assertEqual(
            opened.active_incident.incident_id,
            result.active_incident.incident_id,
        )
        self.assertEqual(result.changes, ())

    def test_fault_reclassification_resolves_then_opens(self) -> None:
        tracker = SystemdIncidentTracker()
        first = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        second = tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.FAILED)
        )
        assert first.active_incident is not None
        assert second.active_incident is not None
        self.assertNotEqual(
            first.active_incident.incident_id,
            second.active_incident.incident_id,
        )
        self.assertEqual(
            tuple(change.kind for change in second.changes),
            (IncidentChangeKind.RESOLVED, IncidentChangeKind.OPENED),
        )
        resolved = second.changes[0].incident
        self.assertIs(
            resolved.resolution_reason,
            IncidentResolutionReason.RECLASSIFIED,
        )
        self.assertEqual(
            resolved.replacement_incident_id,
            second.active_incident.incident_id,
        )

    def test_recurrence_after_recovery_gets_new_incident_identity(self) -> None:
        tracker = SystemdIncidentTracker()
        first = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.HEALTHY)
        )
        recurrence = tracker.process(
            _assessment(ordinal=3, status=SystemdServiceHealthStatus.INACTIVE)
        )
        assert first.active_incident is not None
        assert recurrence.active_incident is not None
        self.assertNotEqual(
            first.active_incident.incident_id,
            recurrence.active_incident.incident_id,
        )

    def test_incident_identity_is_deterministic(self) -> None:
        assessment = _assessment(
            ordinal=1,
            status=SystemdServiceHealthStatus.INACTIVE,
        )
        left = SystemdIncidentTracker().process(assessment)
        right = SystemdIncidentTracker().process(assessment)
        assert left.active_incident is not None
        assert right.active_incident is not None
        self.assertEqual(
            left.active_incident.incident_id,
            right.active_incident.incident_id,
        )

    def test_tracker_rejects_monotonic_regression(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(
                ordinal=1,
                status=SystemdServiceHealthStatus.INACTIVE,
                monotonic_usec=100,
            )
        )
        with self.assertRaises(IncidentTrackingContractError):
            tracker.process(
                _assessment(
                    ordinal=2,
                    status=SystemdServiceHealthStatus.INACTIVE,
                    monotonic_usec=99,
                )
            )

    def test_same_monotonic_time_with_new_assessment_is_allowed(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(
                ordinal=1,
                status=SystemdServiceHealthStatus.INACTIVE,
                monotonic_usec=100,
            )
        )
        result = tracker.process(
            _assessment(
                ordinal=2,
                status=SystemdServiceHealthStatus.INACTIVE,
                monotonic_usec=100,
            )
        )
        assert result.active_incident is not None
        self.assertEqual(result.active_incident.observation_count, 2)

    def test_canonical_unit_drift_is_rejected_for_open_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        with self.assertRaises(IncidentTrackingContractError):
            tracker.process(
                _assessment(
                    ordinal=2,
                    status=SystemdServiceHealthStatus.INACTIVE,
                    canonical="sentinel-x-lab-other.service",
                )
            )

    def test_healthy_canonical_drift_is_rejected(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        with self.assertRaises(IncidentTrackingContractError):
            tracker.process(
                _assessment(
                    ordinal=2,
                    status=SystemdServiceHealthStatus.HEALTHY,
                    canonical="sentinel-x-lab-other.service",
                )
            )

    def test_unassessed_canonical_drift_is_rejected(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        with self.assertRaises(IncidentTrackingContractError):
            tracker.process(
                _assessment(
                    ordinal=2,
                    status=SystemdServiceHealthStatus.UNASSESSED,
                    canonical="sentinel-x-lab-other.service",
                )
            )

    def test_targets_are_isolated(self) -> None:
        tracker = SystemdIncidentTracker()
        left = tracker.process(
            _assessment(
                ordinal=1,
                status=SystemdServiceHealthStatus.INACTIVE,
                target="sentinel-x-lab-left.service",
            )
        )
        right = tracker.process(
            _assessment(
                ordinal=2,
                status=SystemdServiceHealthStatus.FAILED,
                target="sentinel-x-lab-right.service",
            )
        )
        self.assertEqual(tracker.open_incident_count, 2)
        assert left.active_incident is not None
        assert right.active_incident is not None
        self.assertNotEqual(
            left.active_incident.incident_id,
            right.active_incident.incident_id,
        )

    def test_capacity_is_bounded_and_fails_without_eviction(self) -> None:
        tracker = SystemdIncidentTracker(max_tracked_targets=1)
        tracker.process(
            _assessment(
                ordinal=1,
                status=SystemdServiceHealthStatus.HEALTHY,
                target="sentinel-x-lab-one.service",
            )
        )
        with self.assertRaises(IncidentTrackingError):
            tracker.process(
                _assessment(
                    ordinal=2,
                    status=SystemdServiceHealthStatus.HEALTHY,
                    target="sentinel-x-lab-two.service",
                )
            )
        self.assertEqual(tracker.tracked_target_count, 1)

    def test_constructor_rejects_invalid_capacity(self) -> None:
        for value in (True, 0, 4097):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    SystemdIncidentTracker(max_tracked_targets=value)

    def test_active_incident_validates_target_name(self) -> None:
        tracker = SystemdIncidentTracker()
        with self.assertRaises(ValueError):
            tracker.active_incident("../unsafe.service")

    def test_snapshot_is_sorted_and_serialization_friendly(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(
                ordinal=1,
                status=SystemdServiceHealthStatus.INACTIVE,
                target="sentinel-x-lab-z.service",
            )
        )
        tracker.process(
            _assessment(
                ordinal=2,
                status=SystemdServiceHealthStatus.FAILED,
                target="sentinel-x-lab-a.service",
            )
        )
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot.version, INCIDENT_TRACKER_VERSION)
        self.assertEqual(
            tuple(target.target_unit for target in snapshot.targets),
            (
                "sentinel-x-lab-a.service",
                "sentinel-x-lab-z.service",
            ),
        )
        payload = snapshot.to_dict()
        self.assertEqual(payload["target_count"], 2)

    def test_snapshot_restore_preserves_open_incident_identity_and_count(self) -> None:
        tracker = SystemdIncidentTracker()
        first = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.INACTIVE)
        )
        restored = SystemdIncidentTracker(snapshot=tracker.snapshot())
        result = restored.process(
            _assessment(ordinal=3, status=SystemdServiceHealthStatus.INACTIVE)
        )
        assert first.active_incident is not None
        assert result.active_incident is not None
        self.assertEqual(
            result.active_incident.incident_id,
            first.active_incident.incident_id,
        )
        self.assertEqual(result.active_incident.observation_count, 3)

    def test_snapshot_restore_preserves_exact_retry_deduplication(self) -> None:
        tracker = SystemdIncidentTracker()
        assessment = _assessment(
            ordinal=1,
            status=SystemdServiceHealthStatus.INACTIVE,
        )
        tracker.process(assessment)
        restored = SystemdIncidentTracker(snapshot=tracker.snapshot())
        retry = restored.process(assessment)
        self.assertTrue(retry.deduplicated)
        self.assertEqual(retry.changes, ())

    def test_snapshot_over_capacity_is_rejected(self) -> None:
        tracker = SystemdIncidentTracker(max_tracked_targets=2)
        tracker.process(
            _assessment(
                ordinal=1,
                status=SystemdServiceHealthStatus.HEALTHY,
                target="sentinel-x-lab-one.service",
            )
        )
        tracker.process(
            _assessment(
                ordinal=2,
                status=SystemdServiceHealthStatus.HEALTHY,
                target="sentinel-x-lab-two.service",
            )
        )
        with self.assertRaises(IncidentTrackingContractError):
            SystemdIncidentTracker(
                max_tracked_targets=1,
                snapshot=tracker.snapshot(),
            )

    def test_snapshot_rejects_unsorted_targets(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(
                ordinal=1,
                status=SystemdServiceHealthStatus.HEALTHY,
                target="sentinel-x-lab-a.service",
            )
        )
        tracker.process(
            _assessment(
                ordinal=2,
                status=SystemdServiceHealthStatus.HEALTHY,
                target="sentinel-x-lab-z.service",
            )
        )
        targets = tracker.snapshot().targets
        with self.assertRaises(IncidentTrackingContractError):
            IncidentTrackerSnapshot(
                version=INCIDENT_TRACKER_VERSION,
                targets=tuple(reversed(targets)),
            )

    def test_snapshot_rejects_wrong_version(self) -> None:
        with self.assertRaises(IncidentTrackingContractError):
            IncidentTrackerSnapshot(version="wrong", targets=())

    def test_target_state_rejects_resolved_incident(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        resolved_result = tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.HEALTHY)
        )
        resolved = resolved_result.changes[0].incident
        with self.assertRaises(IncidentTrackingContractError):
            IncidentTargetState(
                target_unit=resolved.target_unit,
                last_assessment_id="last",
                last_assessed_monotonic_usec=resolved.resolved_monotonic_usec or 0,
                open_incident=resolved,
            )

    def test_incident_model_rejects_open_resolution_metadata(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        assert result.active_incident is not None
        with self.assertRaises(IncidentTrackingContractError):
            replace(
                result.active_incident,
                resolved_at=BASE_TIME,
            )

    def test_incident_model_rejects_recovered_replacement_identity(self) -> None:
        tracker = SystemdIncidentTracker()
        tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        result = tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.HEALTHY)
        )
        resolved = result.changes[0].incident
        with self.assertRaises(IncidentTrackingContractError):
            replace(
                resolved,
                replacement_incident_id="inc-" + "0" * 64,
            )

    def test_incident_model_rejects_resolved_missing_metadata(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        assert result.active_incident is not None
        with self.assertRaises(IncidentTrackingContractError):
            replace(
                result.active_incident,
                state=IncidentState.RESOLVED,
            )

    def test_lifecycle_change_rejects_state_kind_mismatch(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        assert result.active_incident is not None
        with self.assertRaises(IncidentTrackingContractError):
            IncidentLifecycleChange(
                kind=IncidentChangeKind.RESOLVED,
                incident=result.active_incident,
                trigger_assessment_id=result.assessment_id,
            )

    def test_tracking_result_rejects_deduplicated_changes(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        with self.assertRaises(IncidentTrackingContractError):
            IncidentTrackingResult(
                assessment_id=result.assessment_id,
                target_unit=result.target_unit,
                deduplicated=True,
                changes=result.changes,
                active_incident=result.active_incident,
            )

    def test_incident_serialization_preserves_lifecycle_fields(self) -> None:
        tracker = SystemdIncidentTracker()
        opened = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.FAILED)
        )
        assert opened.active_incident is not None
        payload = opened.active_incident.to_dict()
        self.assertEqual(payload["state"], "open")
        self.assertEqual(payload["observation_count"], 1)
        self.assertIsNone(payload["resolution_reason"])

    def test_result_serialization_is_bounded_and_omits_assessment_payload(self) -> None:
        tracker = SystemdIncidentTracker()
        result = tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        payload = result.to_dict()
        self.assertEqual(payload["change_count"], 1)
        self.assertNotIn("assessment", payload)

    def test_open_incident_count_tracks_open_and_resolved_state(self) -> None:
        tracker = SystemdIncidentTracker()
        self.assertEqual(tracker.open_incident_count, 0)
        tracker.process(
            _assessment(ordinal=1, status=SystemdServiceHealthStatus.INACTIVE)
        )
        self.assertEqual(tracker.open_incident_count, 1)
        tracker.process(
            _assessment(ordinal=2, status=SystemdServiceHealthStatus.HEALTHY)
        )
        self.assertEqual(tracker.open_incident_count, 0)


if __name__ == "__main__":
    unittest.main()
