"""Tests for typed Sentinel-X detection models."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, EventSeverity
from sentinel_x.detection import (
    DETECTION_EVENT_SOURCE,
    SYSTEMD_HEALTH_DETECTION_TYPE,
    SYSTEMD_STATE_BASELINE_VERSION,
    DetectionAnomalyClass,
    DetectionBasis,
    DetectionModelError,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)


_NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)


def _assessment(
    *,
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.HEALTHY,
) -> SystemdServiceHealthAssessment:
    anomaly_class: DetectionAnomalyClass | None = None
    severity: EventSeverity | None = None
    active_state = "active"
    sub_state = "running"
    main_pid: int | None = 123

    if status is SystemdServiceHealthStatus.INACTIVE:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
        severity = EventSeverity.WARNING
        active_state = "inactive"
        sub_state = "dead"
        main_pid = None
    elif status is SystemdServiceHealthStatus.FAILED:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        severity = EventSeverity.ERROR
        active_state = "failed"
        sub_state = "failed"
        main_pid = None
    elif status is SystemdServiceHealthStatus.UNASSESSED:
        active_state = "activating"
        sub_state = "start"
        main_pid = None

    return SystemdServiceHealthAssessment(
        assessment_id="asmt-" + "a" * 64,
        source_event_id="event-1",
        target_unit="example.service",
        canonical_unit="example.service",
        source_observed_at=_NOW,
        assessed_at=_NOW,
        assessed_monotonic_usec=10_000,
        state_change_monotonic_usec=9_000,
        load_state="loaded",
        active_state=active_state,
        sub_state=sub_state,
        main_pid=main_pid,
        result="success",
        status=status,
        anomaly_class=anomaly_class,
        severity=severity,
    )


class DetectionModelTests(unittest.TestCase):
    """Validate conservative typed detection invariants."""

    def test_healthy_assessment_serializes_without_anomaly_metadata(self) -> None:
        assessment = _assessment()

        payload = assessment.to_dict()

        self.assertFalse(assessment.is_anomalous)
        self.assertEqual(payload["status"], "healthy")
        self.assertIsNone(payload["anomaly_class"])
        self.assertIsNone(payload["severity"])
        self.assertEqual(payload["basis"], "direct_systemd_state")

    def test_inactive_assessment_is_anomalous(self) -> None:
        assessment = _assessment(status=SystemdServiceHealthStatus.INACTIVE)

        self.assertTrue(assessment.is_anomalous)
        self.assertIs(
            assessment.anomaly_class,
            DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE,
        )
        self.assertIs(assessment.severity, EventSeverity.WARNING)

    def test_failed_assessment_is_anomalous(self) -> None:
        assessment = _assessment(status=SystemdServiceHealthStatus.FAILED)

        self.assertTrue(assessment.is_anomalous)
        self.assertIs(
            assessment.anomaly_class,
            DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
        )
        self.assertIs(assessment.severity, EventSeverity.ERROR)

    def test_unassessed_state_is_explicit_and_non_anomalous(self) -> None:
        assessment = _assessment(status=SystemdServiceHealthStatus.UNASSESSED)

        self.assertFalse(assessment.is_anomalous)
        self.assertEqual(assessment.to_dict()["status"], "unassessed")

    def test_healthy_contract_requires_live_loaded_running_service(self) -> None:
        with self.assertRaises(DetectionModelError):
            replace(_assessment(), main_pid=None)

    def test_healthy_contract_rejects_anomaly_metadata(self) -> None:
        with self.assertRaises(DetectionModelError):
            replace(
                _assessment(),
                anomaly_class=DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
                severity=EventSeverity.ERROR,
            )

    def test_inactive_contract_requires_matching_state_and_metadata(self) -> None:
        assessment = _assessment(status=SystemdServiceHealthStatus.INACTIVE)

        with self.assertRaises(DetectionModelError):
            replace(assessment, active_state="active")
        with self.assertRaises(DetectionModelError):
            replace(assessment, severity=EventSeverity.ERROR)

    def test_failed_contract_requires_matching_state_and_metadata(self) -> None:
        assessment = _assessment(status=SystemdServiceHealthStatus.FAILED)

        with self.assertRaises(DetectionModelError):
            replace(assessment, load_state="not-found")
        with self.assertRaises(DetectionModelError):
            replace(
                assessment,
                anomaly_class=DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE,
            )

    def test_unassessed_contract_rejects_anomaly_metadata(self) -> None:
        assessment = _assessment(status=SystemdServiceHealthStatus.UNASSESSED)

        with self.assertRaises(DetectionModelError):
            replace(
                assessment,
                anomaly_class=DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE,
                severity=EventSeverity.WARNING,
            )

    def test_assessment_identity_and_units_are_validated(self) -> None:
        with self.assertRaises(DetectionModelError):
            replace(_assessment(), assessment_id="bad")
        with self.assertRaises(ValueError):
            replace(_assessment(), target_unit="unsafe target")

    def test_time_and_integer_fields_are_strictly_validated(self) -> None:
        with self.assertRaises(DetectionModelError):
            replace(_assessment(), assessed_at=datetime(2026, 8, 14, 18, 0))
        with self.assertRaises(DetectionModelError):
            replace(_assessment(), assessed_monotonic_usec=-1)
        with self.assertRaises(DetectionModelError):
            replace(_assessment(), main_pid=True)

    def test_non_anomalous_assessment_cannot_become_anomaly_event(self) -> None:
        with self.assertRaises(DetectionModelError):
            _assessment().to_anomaly_event()

    def test_anomaly_event_is_typed_stable_and_bounded(self) -> None:
        assessment = _assessment(status=SystemdServiceHealthStatus.FAILED)

        event = assessment.to_anomaly_event()

        self.assertIs(event.kind, EventKind.ANOMALY)
        self.assertEqual(event.source, DETECTION_EVENT_SOURCE)
        self.assertEqual(event.event_id, assessment.assessment_id)
        self.assertIs(event.severity, EventSeverity.ERROR)
        self.assertEqual(
            event.attributes["detection_type"],
            SYSTEMD_HEALTH_DETECTION_TYPE,
        )
        self.assertEqual(
            event.attributes["detector_version"],
            SYSTEMD_STATE_BASELINE_VERSION,
        )
        self.assertEqual(
            event.attributes["source_event_id"],
            assessment.source_event_id,
        )
        self.assertEqual(
            event.attributes["basis"],
            DetectionBasis.DIRECT_SYSTEMD_STATE.value,
        )


if __name__ == "__main__":
    unittest.main()
