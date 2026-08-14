"""Tests for the conservative systemd state detector baseline."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.detection import (
    DetectionAnomalyClass,
    SystemdDetectionContractError,
    SystemdServiceHealthStatus,
    SystemdServiceStateDetector,
)
from sentinel_x.systemd import SystemdServiceSnapshot, systemd_service_snapshot_to_event


_BOOT_ID = "a" * 32
_NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
_ASSESSED_AT = datetime(2026, 8, 14, 18, 0, 1, tzinfo=timezone.utc)


def _snapshot() -> SystemdServiceSnapshot:
    return SystemdServiceSnapshot(
        requested_name="example.service",
        canonical_name="example.service",
        names=("example.service",),
        description="Example service",
        load_state="loaded",
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        service_type="simple",
        restart_policy="on-failure",
        result="success",
        invocation_id="0123456789abcdef",
        control_group="/system.slice/example.service",
        fragment_path="/usr/lib/systemd/system/example.service",
        source_path=None,
        drop_in_paths=(),
        requires=(),
        wants=(),
        after=(),
        before=(),
        can_start=True,
        can_stop=True,
        can_reload=False,
        main_pid=321,
        exec_main_code=1,
        exec_main_status=0,
        restart_count=0,
        state_change_monotonic_usec=1_000,
        active_enter_monotonic_usec=900,
        inactive_enter_monotonic_usec=None,
        exec_main_start_monotonic_usec=850,
        exec_main_exit_monotonic_usec=None,
        captured_at=_NOW,
    )


def _event(snapshot: SystemdServiceSnapshot | None = None) -> SentinelEvent:
    return systemd_service_snapshot_to_event(
        _snapshot() if snapshot is None else snapshot,
        collector_name="systemd.example.0123456789ab",
        boot_id=_BOOT_ID,
    )


def _detector(
    *,
    assessed_at: datetime = _ASSESSED_AT,
    monotonic_ns: int | float | bool = 5_000_000,
) -> SystemdServiceStateDetector:
    def wall_clock() -> datetime:
        return assessed_at

    def monotonic_clock():
        return monotonic_ns

    return SystemdServiceStateDetector(
        wall_clock=wall_clock,
        monotonic_ns_clock=monotonic_clock,
    )


class SystemdServiceStateDetectorTests(unittest.TestCase):
    """Validate deterministic detector behavior and input contracts."""

    def test_loaded_active_running_service_is_healthy(self) -> None:
        assessment = _detector().assess(_event())

        self.assertIs(assessment.status, SystemdServiceHealthStatus.HEALTHY)
        self.assertFalse(assessment.is_anomalous)
        self.assertEqual(assessment.assessed_monotonic_usec, 5_000)

    def test_loaded_inactive_service_is_detected(self) -> None:
        snapshot = replace(
            _snapshot(),
            active_state="inactive",
            sub_state="dead",
            main_pid=None,
            state_change_monotonic_usec=None,
        )

        assessment = _detector().assess(_event(snapshot))

        self.assertIs(assessment.status, SystemdServiceHealthStatus.INACTIVE)
        self.assertIs(
            assessment.anomaly_class,
            DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE,
        )
        self.assertIs(assessment.severity, EventSeverity.WARNING)
        self.assertIsNone(assessment.state_change_monotonic_usec)

    def test_loaded_failed_service_is_detected(self) -> None:
        snapshot = replace(
            _snapshot(),
            active_state="failed",
            sub_state="failed",
            main_pid=None,
            result="signal",
            state_change_monotonic_usec=2_000,
        )

        assessment = _detector().assess(_event(snapshot))

        self.assertIs(assessment.status, SystemdServiceHealthStatus.FAILED)
        self.assertIs(
            assessment.anomaly_class,
            DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
        )
        self.assertIs(assessment.severity, EventSeverity.ERROR)

    def test_not_found_service_is_unassessed_not_guessed(self) -> None:
        snapshot = replace(
            _snapshot(),
            load_state="not-found",
            active_state="inactive",
            sub_state="dead",
            main_pid=None,
        )

        assessment = _detector().assess(_event(snapshot))

        self.assertIs(assessment.status, SystemdServiceHealthStatus.UNASSESSED)
        self.assertFalse(assessment.is_anomalous)

    def test_unknown_future_active_state_is_unassessed(self) -> None:
        snapshot = replace(
            _snapshot(),
            active_state="future-state",
            sub_state="future-substate",
            main_pid=None,
        )

        assessment = _detector().assess(_event(snapshot))

        self.assertIs(assessment.status, SystemdServiceHealthStatus.UNASSESSED)

    def test_transitional_service_state_is_unassessed(self) -> None:
        snapshot = replace(
            _snapshot(),
            active_state="activating",
            sub_state="start",
            main_pid=None,
        )

        assessment = _detector().assess(_event(snapshot))

        self.assertIs(assessment.status, SystemdServiceHealthStatus.UNASSESSED)

    def test_active_nonrunning_service_is_unassessed(self) -> None:
        snapshot = replace(
            _snapshot(),
            sub_state="exited",
            main_pid=None,
        )

        assessment = _detector().assess(_event(snapshot))

        self.assertIs(assessment.status, SystemdServiceHealthStatus.UNASSESSED)

    def test_active_running_without_live_pid_is_unassessed(self) -> None:
        assessment = _detector().assess(_event(replace(_snapshot(), main_pid=None)))

        self.assertIs(assessment.status, SystemdServiceHealthStatus.UNASSESSED)

    def test_assessment_id_is_stable_for_same_source_event_and_state(self) -> None:
        event = _event(
            replace(
                _snapshot(),
                active_state="failed",
                sub_state="failed",
                main_pid=None,
            )
        )
        first = _detector().assess(event)
        second = _detector(
            assessed_at=datetime(2026, 8, 14, 18, 0, 2, tzinfo=timezone.utc),
            monotonic_ns=9_000_000,
        ).assess(event)

        self.assertEqual(first.assessment_id, second.assessment_id)
        self.assertNotEqual(first.assessed_at, second.assessed_at)

    def test_different_source_event_gets_different_assessment_identity(self) -> None:
        event = _event()
        other = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            severity=event.severity,
            attributes=event.attributes,
            occurred_at=event.occurred_at,
        )

        self.assertNotEqual(
            _detector().assess(event).assessment_id,
            _detector().assess(other).assessment_id,
        )

    def test_wrong_event_kind_is_rejected(self) -> None:
        event = _event()
        wrong = SentinelEvent(
            kind=EventKind.ANOMALY,
            source=event.source,
            message=event.message,
            attributes=event.attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_wrong_event_source_is_rejected(self) -> None:
        event = _event()
        wrong = SentinelEvent(
            kind=event.kind,
            source="other.source",
            message=event.message,
            attributes=event.attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_wrong_observation_type_is_rejected(self) -> None:
        event = _event()
        attributes = dict(event.attributes)
        attributes["observation_type"] = "other.type"
        wrong = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            attributes=attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_missing_required_attribute_is_rejected(self) -> None:
        event = _event()
        attributes = dict(event.attributes)
        del attributes["active_state"]
        wrong = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            attributes=attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_unsafe_unit_identity_is_rejected(self) -> None:
        event = _event()
        attributes = dict(event.attributes)
        attributes["requested_name"] = "unsafe unit"
        wrong = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            attributes=attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_invalid_boot_identity_is_rejected(self) -> None:
        event = _event()
        attributes = dict(event.attributes)
        attributes["boot_id"] = "bad"
        wrong = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            attributes=attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_boolean_main_pid_is_rejected(self) -> None:
        event = _event()
        attributes = dict(event.attributes)
        attributes["main_pid"] = True
        wrong = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            attributes=attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_negative_transition_timestamp_is_rejected(self) -> None:
        event = _event()
        attributes = dict(event.attributes)
        attributes["state_change_monotonic_usec"] = -1
        wrong = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            attributes=attributes,
            occurred_at=event.occurred_at,
        )

        with self.assertRaises(SystemdDetectionContractError):
            _detector().assess(wrong)

    def test_naive_wall_clock_is_rejected(self) -> None:
        with self.assertRaises(SystemdDetectionContractError):
            _detector(
                assessed_at=datetime(2026, 8, 14, 18, 0),
            ).assess(_event())

    def test_invalid_monotonic_clock_values_are_rejected(self) -> None:
        for value in (True, -1, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(SystemdDetectionContractError):
                    _detector(monotonic_ns=value).assess(_event())

    def test_constructor_rejects_noncallable_clocks(self) -> None:
        with self.assertRaises(TypeError):
            SystemdServiceStateDetector(wall_clock=None)
        with self.assertRaises(TypeError):
            SystemdServiceStateDetector(monotonic_ns_clock=None)

    def test_failed_assessment_converts_to_anomaly_event(self) -> None:
        snapshot = replace(
            _snapshot(),
            active_state="failed",
            sub_state="failed",
            main_pid=None,
            result="signal",
        )

        assessment = _detector().assess(_event(snapshot))
        anomaly = assessment.to_anomaly_event()

        self.assertIs(anomaly.kind, EventKind.ANOMALY)
        self.assertIs(anomaly.severity, EventSeverity.ERROR)
        self.assertEqual(anomaly.event_id, assessment.assessment_id)
        self.assertEqual(
            anomaly.attributes["source_event_id"],
            assessment.source_event_id,
        )
        self.assertEqual(
            anomaly.attributes["anomaly_class"],
            "systemd.service.failed",
        )


if __name__ == "__main__":
    unittest.main()
