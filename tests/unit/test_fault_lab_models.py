from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from sentinel_x.lab import (
    FaultExperimentManifest,
    FaultGroundTruthWindow,
    FaultLabModelError,
    FaultMode,
    FaultScenario,
    FaultTemporalLabel,
)


class FaultLabModelTests(unittest.TestCase):
    def _scenario(
        self,
        *,
        fault_mode: FaultMode = FaultMode.SERVICE_INACTIVE,
    ) -> FaultScenario:
        return FaultScenario(
            scenario_id="journald-outage",
            description="Controlled service-state fault for the lab fixture.",
            target_unit="sentinel-x-lab-fixture.service",
            fault_mode=fault_mode,
        )

    def _manifest(self) -> FaultExperimentManifest:
        return FaultExperimentManifest(
            scenario=self._scenario(),
            experiment_id="exp-0123456789abcdef0123456789abcdef",
            created_at=datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc),
        )

    def _open_window(self) -> FaultGroundTruthWindow:
        return FaultGroundTruthWindow.from_manifest(
            self._manifest(),
            started_at=datetime(2026, 8, 14, 8, 1, tzinfo=timezone.utc),
            started_monotonic_usec=1_000_000,
        )

    def test_inactive_scenario_exposes_expected_state_and_serializes(self) -> None:
        scenario = self._scenario()

        self.assertEqual(scenario.expected_fault_active_states, ("inactive",))
        payload = scenario.to_dict()
        self.assertEqual(payload["fault_mode"], "service_inactive")
        json.dumps(payload)

    def test_failed_scenario_exposes_failed_state(self) -> None:
        scenario = self._scenario(fault_mode=FaultMode.SERVICE_FAILED)

        self.assertEqual(scenario.expected_fault_active_states, ("failed",))

    def test_scenario_id_rejects_unsafe_shapes(self) -> None:
        for value in ("ab", "Uppercase", "has space", "-leading"):
            with self.subTest(value=value):
                with self.assertRaises(FaultLabModelError):
                    FaultScenario(
                        scenario_id=value,
                        description="Valid description.",
                        target_unit="sentinel-x-lab-fixture.service",
                        fault_mode=FaultMode.SERVICE_INACTIVE,
                    )

    def test_scenario_description_is_bounded_and_normalized(self) -> None:
        for value in ("", " leading", "trailing ", "bad\x00text", "x" * 513):
            with self.subTest(value=value[:20]):
                with self.assertRaises(FaultLabModelError):
                    FaultScenario(
                        scenario_id="valid-scenario",
                        description=value,
                        target_unit="sentinel-x-lab-fixture.service",
                        fault_mode=FaultMode.SERVICE_INACTIVE,
                    )

    def test_scenario_reuses_conservative_systemd_unit_validation(self) -> None:
        for value in ("ssh", "../bad.service", "-bad.service", "bad unit.service"):
            with self.subTest(value=value):
                with self.assertRaises(FaultLabModelError):
                    FaultScenario(
                        scenario_id="valid-scenario",
                        description="Valid description.",
                        target_unit=value,
                        fault_mode=FaultMode.SERVICE_INACTIVE,
                    )

    def test_timeout_bounds_reject_boolean_nonpositive_and_oversized(self) -> None:
        for value in (False, 0.0, -1.0, 301.0):
            with self.subTest(value=value):
                with self.assertRaises(FaultLabModelError):
                    FaultScenario(
                        scenario_id="valid-scenario",
                        description="Valid description.",
                        target_unit="sentinel-x-lab-fixture.service",
                        fault_mode=FaultMode.SERVICE_INACTIVE,
                        fault_timeout_seconds=value,
                    )

    def test_integer_timeouts_are_normalized_to_float(self) -> None:
        scenario = FaultScenario(
            scenario_id="valid-scenario",
            description="Valid description.",
            target_unit="sentinel-x-lab-fixture.service",
            fault_mode=FaultMode.SERVICE_INACTIVE,
            baseline_timeout_seconds=5,
        )

        self.assertEqual(scenario.baseline_timeout_seconds, 5.0)
        self.assertIsInstance(scenario.baseline_timeout_seconds, float)

    def test_evidence_grace_must_fit_inside_recovery_window(self) -> None:
        with self.assertRaises(FaultLabModelError):
            FaultScenario(
                scenario_id="valid-scenario",
                description="Valid description.",
                target_unit="sentinel-x-lab-fixture.service",
                fault_mode=FaultMode.SERVICE_INACTIVE,
                recovery_timeout_seconds=2.0,
                evidence_grace_seconds=3.0,
            )

    def test_manifest_defaults_to_unique_valid_identity(self) -> None:
        first = FaultExperimentManifest(self._scenario())
        second = FaultExperimentManifest(self._scenario())

        self.assertNotEqual(first.experiment_id, second.experiment_id)
        self.assertTrue(first.experiment_id.startswith("exp-"))
        json.dumps(first.to_dict())

    def test_manifest_rejects_invalid_experiment_identity(self) -> None:
        with self.assertRaises(FaultLabModelError):
            FaultExperimentManifest(
                scenario=self._scenario(),
                experiment_id="not-an-experiment-id",
            )

    def test_manifest_requires_timezone_aware_creation_time(self) -> None:
        with self.assertRaises(FaultLabModelError):
            FaultExperimentManifest(
                scenario=self._scenario(),
                created_at=datetime(2026, 8, 14, 8, 0),
            )

    def test_ground_truth_opens_from_manifest_identity(self) -> None:
        manifest = self._manifest()
        window = self._open_window()

        self.assertEqual(window.experiment_id, manifest.experiment_id)
        self.assertEqual(window.scenario_id, manifest.scenario.scenario_id)
        self.assertTrue(window.is_open)
        self.assertIsNone(window.duration_usec)

    def test_open_window_labels_pre_fault_and_active_time(self) -> None:
        window = self._open_window()

        self.assertIs(
            window.label_monotonic(999_999),
            FaultTemporalLabel.PRE_FAULT,
        )
        self.assertIs(
            window.label_monotonic(1_000_000),
            FaultTemporalLabel.FAULT_ACTIVE,
        )
        self.assertIs(
            window.label_monotonic(9_000_000),
            FaultTemporalLabel.FAULT_ACTIVE,
        )

    def test_close_returns_new_window_with_monotonic_duration(self) -> None:
        window = self._open_window()
        ended_at = window.started_at + timedelta(seconds=2)

        closed = window.close(
            ended_at=ended_at,
            ended_monotonic_usec=3_000_000,
        )

        self.assertTrue(window.is_open)
        self.assertFalse(closed.is_open)
        self.assertEqual(closed.duration_usec, 2_000_000)

    def test_closed_window_uses_closed_interval_boundary(self) -> None:
        window = self._open_window().close(
            ended_at=datetime(2026, 8, 14, 8, 1, 2, tzinfo=timezone.utc),
            ended_monotonic_usec=3_000_000,
        )

        self.assertTrue(window.contains_monotonic(1_000_000))
        self.assertTrue(window.contains_monotonic(3_000_000))
        self.assertFalse(window.contains_monotonic(3_000_001))
        self.assertIs(
            window.label_monotonic(3_000_001),
            FaultTemporalLabel.POST_FAULT,
        )

    def test_partial_end_metadata_is_rejected(self) -> None:
        manifest = self._manifest()
        with self.assertRaises(FaultLabModelError):
            FaultGroundTruthWindow(
                experiment_id=manifest.experiment_id,
                scenario_id=manifest.scenario.scenario_id,
                target_unit=manifest.scenario.target_unit,
                fault_mode=manifest.scenario.fault_mode,
                started_at=datetime(2026, 8, 14, 8, 1, tzinfo=timezone.utc),
                started_monotonic_usec=1_000_000,
                ended_at=datetime(2026, 8, 14, 8, 2, tzinfo=timezone.utc),
            )

    def test_wall_clock_end_must_not_precede_start(self) -> None:
        window = self._open_window()

        with self.assertRaises(FaultLabModelError):
            window.close(
                ended_at=window.started_at - timedelta(seconds=1),
                ended_monotonic_usec=2_000_000,
            )

    def test_monotonic_end_must_not_precede_start(self) -> None:
        window = self._open_window()

        with self.assertRaises(FaultLabModelError):
            window.close(
                ended_at=window.started_at + timedelta(seconds=1),
                ended_monotonic_usec=999_999,
            )

    def test_closed_window_cannot_be_closed_twice(self) -> None:
        closed = self._open_window().close(
            ended_at=datetime(2026, 8, 14, 8, 1, 1, tzinfo=timezone.utc),
            ended_monotonic_usec=2_000_000,
        )

        with self.assertRaises(FaultLabModelError):
            closed.close(
                ended_at=datetime(2026, 8, 14, 8, 1, 2, tzinfo=timezone.utc),
                ended_monotonic_usec=3_000_000,
            )

    def test_monotonic_label_rejects_boolean_and_negative_values(self) -> None:
        window = self._open_window()

        for value in (False, -1):
            with self.subTest(value=value):
                with self.assertRaises(FaultLabModelError):
                    window.label_monotonic(value)

    def test_ground_truth_serialization_is_json_friendly(self) -> None:
        closed = self._open_window().close(
            ended_at=datetime(2026, 8, 14, 8, 1, 1, tzinfo=timezone.utc),
            ended_monotonic_usec=2_000_000,
        )

        payload = closed.to_dict()
        self.assertEqual(payload["duration_usec"], 1_000_000)
        self.assertFalse(payload["is_open"])
        json.dumps(payload)


if __name__ == "__main__":
    unittest.main()
