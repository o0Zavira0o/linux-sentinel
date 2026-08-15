from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.dependency.coverage import (
    DEFAULT_MAX_COVERAGE_INPUT_ASSESSMENTS,
    AssessmentCoverageOutcome,
    PropagationCoverageCapacityError,
    PropagationCoverageContractError,
    PropagationCoverageInterpretation,
    PropagationEndpointObservability,
    SamplingCoverageStatus,
    evaluate_propagation_sampling_coverage,
    propagation_candidate_observability,
)
from sentinel_x.dependency.discovery import (
    SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
    DiscoveredSystemdUnit,
    SystemdDependencyDiscoveryReport,
    SystemdDependencyUnitSnapshot,
)
from sentinel_x.dependency.graph import build_dependency_graph
from sentinel_x.dependency.models import (
    DependencyConfigurationOrigin,
    DependencyEndpoint,
    DependencyEntityKind,
    DependencyEvidence,
    DependencyEvidenceOrigin,
    DependencyRelation,
)
from sentinel_x.dependency.propagation import (
    TemporalEvidenceBasis,
    bind_systemd_assessment_evidence,
    bind_systemd_incident_temporal_evidence,
    build_dependency_propagation_candidates,
)
from sentinel_x.detection.incidents import SystemdServiceIncident
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    DetectionBasis,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)

_BOOT = "a" * 32
_OTHER_BOOT = "b" * 32
_TIME = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)


def _event(
    unit: str,
    *,
    event_id: str,
    boot_id: str = _BOOT,
    state_change_usec: int | None = 900_000,
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.HEALTHY,
) -> SentinelEvent:
    if status is SystemdServiceHealthStatus.FAILED:
        active_state, sub_state, main_pid, result = (
            "failed",
            "failed",
            None,
            "exit-code",
        )
    elif status is SystemdServiceHealthStatus.INACTIVE:
        active_state, sub_state, main_pid, result = "inactive", "dead", None, "success"
    elif status is SystemdServiceHealthStatus.HEALTHY:
        active_state, sub_state, main_pid, result = "active", "running", 1234, "success"
    else:
        active_state, sub_state, main_pid, result = "activating", "start", None, None
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.service",
        message=f"systemd service observation for {unit}",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=_TIME,
        attributes={
            "observation_type": "linux.systemd.service",
            "collector_name": "coverage_test",
            "boot_id": boot_id,
            "requested_name": unit,
            "canonical_name": unit,
            "load_state": "loaded",
            "active_state": active_state,
            "sub_state": sub_state,
            "main_pid": main_pid,
            "result": result,
            "state_change_monotonic_usec": state_change_usec,
        },
    )


def _assessment_evidence(
    unit: str,
    *,
    digit: str,
    assessed_usec: int,
    boot_id: str = _BOOT,
    state_change_usec: int | None = 900_000,
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.HEALTHY,
):
    event = _event(
        unit,
        event_id=f"event-{unit}-{digit}-{assessed_usec}",
        boot_id=boot_id,
        state_change_usec=state_change_usec,
        status=status,
    )
    if status is SystemdServiceHealthStatus.FAILED:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        severity = EventSeverity.ERROR
        active_state, sub_state, main_pid, result = (
            "failed",
            "failed",
            None,
            "exit-code",
        )
    elif status is SystemdServiceHealthStatus.INACTIVE:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
        severity = EventSeverity.WARNING
        active_state, sub_state, main_pid, result = "inactive", "dead", None, "success"
    elif status is SystemdServiceHealthStatus.HEALTHY:
        anomaly_class = None
        severity = None
        active_state, sub_state, main_pid, result = "active", "running", 1234, "success"
    else:
        anomaly_class = None
        severity = None
        active_state, sub_state, main_pid, result = "activating", "start", None, None
    assessment = SystemdServiceHealthAssessment(
        assessment_id="asmt-" + digit * 64,
        source_event_id=event.event_id,
        target_unit=unit,
        canonical_unit=unit,
        source_observed_at=event.occurred_at,
        assessed_at=event.occurred_at + timedelta(microseconds=assessed_usec),
        assessed_monotonic_usec=assessed_usec,
        state_change_monotonic_usec=state_change_usec,
        load_state="loaded",
        active_state=active_state,
        sub_state=sub_state,
        main_pid=main_pid,
        result=result,
        status=status,
        anomaly_class=anomaly_class,
        severity=severity,
        basis=DetectionBasis.DIRECT_SYSTEMD_STATE,
    )
    return bind_systemd_assessment_evidence(assessment, event)


def _source_incident(*, state_change_usec: int | None = 1_000_000):
    evidence = _assessment_evidence(
        "dependency.service",
        digit="f",
        assessed_usec=1_100_000,
        state_change_usec=state_change_usec,
        status=SystemdServiceHealthStatus.FAILED,
    )
    assessment = evidence.assessment
    assert assessment.anomaly_class is not None
    assert assessment.severity is not None
    incident = SystemdServiceIncident(
        incident_id="inc-" + "e" * 64,
        target_unit=assessment.target_unit,
        canonical_unit=assessment.canonical_unit,
        anomaly_class=assessment.anomaly_class,
        severity=assessment.severity,
        opened_at=assessment.assessed_at,
        opened_monotonic_usec=assessment.assessed_monotonic_usec,
        first_assessment_id=assessment.assessment_id,
        first_source_event_id=assessment.source_event_id,
        latest_assessment_id=assessment.assessment_id,
        latest_source_event_id=assessment.source_event_id,
        latest_assessed_at=assessment.assessed_at,
        latest_assessed_monotonic_usec=assessment.assessed_monotonic_usec,
        observation_count=1,
    )
    return bind_systemd_incident_temporal_evidence(incident, evidence)


def _graph(
    *,
    dependency_unit: str = "dependency.service",
    dependent_unit: str = "dependent.service",
):
    event_id = "dep-event"
    observed_at = _TIME
    evidence = DependencyEvidence(
        evidence_id="depev-" + "1" * 64,
        source_event_id=event_id,
        observed_at=observed_at,
        boot_id=_BOOT,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=dependent_unit,
        ),
        relation=DependencyRelation.REQUIRES,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=dependency_unit,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="Requires",
    )
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name=dependent_unit,
        canonical_name=dependent_unit,
        names=(dependent_unit,),
        load_state="loaded",
        requires=(dependency_unit,),
        wants=(),
        after=(),
        before=(),
        captured_at=observed_at,
    )
    source_event = SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message=f"dependency observation for {dependent_unit}",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=observed_at,
        attributes={"observation_type": "linux.systemd.unit.dependency"},
    )
    unit = DiscoveredSystemdUnit(
        depth=0,
        snapshot=snapshot,
        source_event=source_event,
        evidence=(evidence,),
    )
    report = SystemdDependencyDiscoveryReport(
        root_requested_unit=dependent_unit,
        root_canonical_unit=dependent_unit,
        boot_id=_BOOT,
        max_depth=1,
        max_units=64,
        units=(unit,),
        failures=(),
        truncated_by_depth=True,
        unexpanded_requirement_count=1,
    )
    return build_dependency_graph(report)


def _candidate(
    *,
    dependency_unit: str = "dependency.service",
    dependent_unit: str = "dependent.service",
):
    return build_dependency_propagation_candidates(
        _graph(dependency_unit=dependency_unit, dependent_unit=dependent_unit)
    )[0]


def _evaluate(
    assessments,
    *,
    source=None,
    candidate=None,
    window: int = 1_000_000,
    gap: int = 300_000,
    max_input: int = DEFAULT_MAX_COVERAGE_INPUT_ASSESSMENTS,
):
    return evaluate_propagation_sampling_coverage(
        _candidate() if candidate is None else candidate,
        _source_incident() if source is None else source,
        assessments,
        analysis_window_usec=window,
        max_sample_gap_usec=gap,
        max_input_assessments=max_input,
    )


class PropagationCoverageTests(unittest.TestCase):
    def test_all_healthy_bounded_sampling_assigns_negative_evidence_only(self) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_100_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="2", assessed_usec=1_350_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="3", assessed_usec=1_650_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="4", assessed_usec=1_900_000
                ),
            ),
            gap=300_000,
        )
        self.assertIs(evidence.sampling_coverage, SamplingCoverageStatus.BOUNDED)
        self.assertIs(
            evidence.assessment_outcome, AssessmentCoverageOutcome.ALL_HEALTHY
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING,
        )
        self.assertTrue(evidence.negative_evidence_assigned)
        self.assertEqual(evidence.largest_observed_gap_usec, 300_000)
        payload = evidence.to_dict()
        self.assertFalse(payload["continuous_health_claim_assigned"])
        self.assertFalse(payload["non_propagation_claim_assigned"])
        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])

    def test_sparse_healthy_sampling_is_insufficient_not_negative(self) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_500_000
                ),
            ),
            gap=300_000,
        )
        self.assertIs(
            evidence.sampling_coverage,
            SamplingCoverageStatus.GAP_BOUND_EXCEEDED,
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.INSUFFICIENT_SAMPLING_COVERAGE,
        )
        self.assertFalse(evidence.negative_evidence_assigned)

    def test_no_samples_is_explicitly_insufficient(self) -> None:
        evidence = _evaluate(())
        self.assertIs(
            evidence.sampling_coverage,
            SamplingCoverageStatus.NO_IN_WINDOW_SAMPLES,
        )
        self.assertIs(
            evidence.assessment_outcome,
            AssessmentCoverageOutcome.NO_IN_WINDOW_SAMPLES,
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.INSUFFICIENT_SAMPLING_COVERAGE,
        )
        self.assertIsNone(evidence.largest_observed_gap_usec)

    def test_unassessed_sample_blocks_negative_evidence(self) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_100_000
                ),
                _assessment_evidence(
                    "dependent.service",
                    digit="2",
                    assessed_usec=1_350_000,
                    status=SystemdServiceHealthStatus.UNASSESSED,
                ),
                _assessment_evidence(
                    "dependent.service", digit="3", assessed_usec=1_650_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="4", assessed_usec=1_900_000
                ),
            )
        )
        self.assertIs(
            evidence.assessment_outcome,
            AssessmentCoverageOutcome.UNASSESSED_PRESENT,
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.UNASSESSED_COVERAGE,
        )
        self.assertFalse(evidence.negative_evidence_assigned)

    def test_anomaly_sample_is_positive_observation_even_if_sampling_is_sparse(
        self,
    ) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service",
                    digit="1",
                    assessed_usec=1_800_000,
                    status=SystemdServiceHealthStatus.FAILED,
                ),
            ),
            gap=100_000,
        )
        self.assertIs(
            evidence.assessment_outcome,
            AssessmentCoverageOutcome.ANOMALY_OBSERVED,
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.AFFECTED_ANOMALY_OBSERVED_IN_WINDOW,
        )
        self.assertEqual(evidence.failed_count, 1)
        self.assertFalse(evidence.negative_evidence_assigned)

    def test_source_assessment_fallback_blocks_negative_interpretation(self) -> None:
        source = _source_incident(state_change_usec=None)
        self.assertIs(source.temporal_basis, TemporalEvidenceBasis.ASSESSMENT_MONOTONIC)
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_200_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="2", assessed_usec=1_450_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="3", assessed_usec=1_700_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="4", assessed_usec=1_950_000
                ),
            ),
            source=source,
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.SOURCE_TRANSITION_TIMING_LIMITED,
        )
        self.assertFalse(evidence.negative_evidence_assigned)

    def test_anomaly_observation_remains_explicit_when_source_timing_is_fallback(
        self,
    ) -> None:
        source = _source_incident(state_change_usec=None)
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service",
                    digit="1",
                    assessed_usec=1_500_000,
                    status=SystemdServiceHealthStatus.INACTIVE,
                ),
            ),
            source=source,
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.AFFECTED_ANOMALY_OBSERVED_IN_WINDOW,
        )

    def test_candidate_observability_exposes_unsupported_source_endpoint_without_incident(
        self,
    ) -> None:
        candidate = _candidate(
            dependency_unit="dependency.socket",
            dependent_unit="dependent.service",
        )
        self.assertIs(
            propagation_candidate_observability(candidate),
            PropagationEndpointObservability.CURRENT_DETECTOR_SCOPE_UNSUPPORTED,
        )

    def test_service_to_service_candidate_is_currently_observable(self) -> None:
        self.assertIs(
            propagation_candidate_observability(_candidate()),
            PropagationEndpointObservability.SERVICE_TO_SERVICE_SUPPORTED,
        )

    def test_nonservice_candidate_is_explicitly_unsupported(self) -> None:
        # Phase 4 can provide a source service incident, but not assessments for
        # a non-service dependent endpoint.  The gap is explicit rather than
        # silently interpreted as non-propagation.
        candidate = _candidate(
            dependency_unit="dependency.service",
            dependent_unit="dependent.socket",
        )
        evidence = evaluate_propagation_sampling_coverage(
            candidate,
            _source_incident(),
            (),
            analysis_window_usec=1_000_000,
            max_sample_gap_usec=300_000,
        )
        self.assertIs(
            evidence.endpoint_observability,
            PropagationEndpointObservability.CURRENT_DETECTOR_SCOPE_UNSUPPORTED,
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.CURRENT_DETECTOR_SCOPE_UNSUPPORTED,
        )

    def test_input_outside_window_is_counted_but_not_used_as_coverage(self) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=900_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="2", assessed_usec=1_100_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="3", assessed_usec=2_100_001
                ),
            )
        )
        self.assertEqual(evidence.input_assessment_count, 3)
        self.assertEqual(evidence.in_window_assessment_count, 1)
        self.assertEqual(evidence.out_of_window_assessment_count, 2)

    def test_window_boundaries_are_inclusive(self) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_000_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="2", assessed_usec=2_000_000
                ),
            ),
            gap=1_000_000,
        )
        self.assertEqual(evidence.in_window_assessment_count, 2)
        self.assertEqual(evidence.largest_observed_gap_usec, 1_000_000)

    def test_largest_gap_includes_leading_and_trailing_window_edges(self) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_250_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="2", assessed_usec=1_500_000
                ),
                _assessment_evidence(
                    "dependent.service", digit="3", assessed_usec=1_750_000
                ),
            ),
            gap=250_000,
        )
        self.assertEqual(evidence.largest_observed_gap_usec, 250_000)
        self.assertIs(evidence.sampling_coverage, SamplingCoverageStatus.BOUNDED)

    def test_input_order_is_canonicalized_deterministically(self) -> None:
        a = _assessment_evidence(
            "dependent.service", digit="1", assessed_usec=1_250_000
        )
        b = _assessment_evidence(
            "dependent.service", digit="2", assessed_usec=1_500_000
        )
        first = _evaluate((b, a), gap=500_000)
        second = _evaluate((a, b), gap=500_000)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(first.in_window_assessments, second.in_window_assessments)

    def test_duplicate_assessment_evidence_is_rejected_not_deduplicated(self) -> None:
        sample = _assessment_evidence(
            "dependent.service", digit="1", assessed_usec=1_250_000
        )
        with self.assertRaises(PropagationCoverageContractError):
            _evaluate((sample, sample))

    def test_wrong_boot_assessment_is_rejected(self) -> None:
        sample = _assessment_evidence(
            "dependent.service",
            digit="1",
            assessed_usec=1_250_000,
            boot_id=_OTHER_BOOT,
        )
        with self.assertRaises(PropagationCoverageContractError):
            _evaluate((sample,))

    def test_wrong_unit_assessment_is_rejected(self) -> None:
        sample = _assessment_evidence(
            "other.service", digit="1", assessed_usec=1_250_000
        )
        with self.assertRaises(PropagationCoverageContractError):
            _evaluate((sample,))

    def test_wrong_source_incident_unit_is_rejected(self) -> None:
        candidate = _candidate(dependency_unit="other.service")
        source = _source_incident()
        with self.assertRaises(PropagationCoverageContractError):
            evaluate_propagation_sampling_coverage(
                candidate,
                source,
                (),
                analysis_window_usec=1_000_000,
                max_sample_gap_usec=300_000,
            )

    def test_capacity_limit_fails_explicitly(self) -> None:
        assessments = tuple(
            _assessment_evidence(
                "dependent.service",
                digit=str((index % 9) + 1),
                assessed_usec=1_000_000 + index,
            )
            for index in range(3)
        )
        # Use unique event/evidence IDs by replacing IDs after creation is not valid,
        # so a smaller explicit capacity is tested with two unique samples instead.
        assessments = (
            _assessment_evidence(
                "dependent.service", digit="1", assessed_usec=1_100_000
            ),
            _assessment_evidence(
                "dependent.service", digit="2", assessed_usec=1_200_000
            ),
        )
        with self.assertRaises(PropagationCoverageCapacityError):
            _evaluate(assessments, max_input=1)

    def test_capacity_limit_bounds_generator_consumption(self) -> None:
        consumed = 0

        def assessments():
            nonlocal consumed
            for index, digit in enumerate(("1", "2", "3", "4", "5"), start=1):
                consumed += 1
                if consumed > 3:
                    raise AssertionError("coverage input was consumed past max + 1")
                yield _assessment_evidence(
                    "dependent.service",
                    digit=digit,
                    assessed_usec=1_000_000 + index,
                )

        with self.assertRaises(PropagationCoverageCapacityError):
            _evaluate(assessments(), max_input=2)
        self.assertEqual(consumed, 3)

    def test_boolean_and_zero_thresholds_are_rejected(self) -> None:
        with self.assertRaises(PropagationCoverageContractError):
            _evaluate((), window=0)
        with self.assertRaises(PropagationCoverageContractError):
            _evaluate((), gap=0)
        with self.assertRaises(PropagationCoverageContractError):
            evaluate_propagation_sampling_coverage(
                _candidate(),
                _source_incident(),
                (),
                analysis_window_usec=True,
                max_sample_gap_usec=1,
            )

    def test_untyped_assessment_input_is_rejected(self) -> None:
        with self.assertRaises(PropagationCoverageContractError):
            _evaluate((object(),))

    def test_string_assessment_input_is_rejected(self) -> None:
        with self.assertRaises(PropagationCoverageContractError):
            _evaluate("bad")

    def test_status_counts_preserve_failed_inactive_unassessed_and_healthy(
        self,
    ) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_100_000
                ),
                _assessment_evidence(
                    "dependent.service",
                    digit="2",
                    assessed_usec=1_200_000,
                    status=SystemdServiceHealthStatus.INACTIVE,
                ),
                _assessment_evidence(
                    "dependent.service",
                    digit="3",
                    assessed_usec=1_300_000,
                    status=SystemdServiceHealthStatus.FAILED,
                ),
                _assessment_evidence(
                    "dependent.service",
                    digit="4",
                    assessed_usec=1_400_000,
                    status=SystemdServiceHealthStatus.UNASSESSED,
                ),
            ),
            gap=700_000,
        )
        self.assertEqual(evidence.healthy_count, 1)
        self.assertEqual(evidence.inactive_count, 1)
        self.assertEqual(evidence.failed_count, 1)
        self.assertEqual(evidence.unassessed_count, 1)
        self.assertEqual(evidence.anomalous_count, 2)
        self.assertIs(
            evidence.assessment_outcome, AssessmentCoverageOutcome.ANOMALY_OBSERVED
        )

    def test_evidence_identity_changes_with_gap_policy(self) -> None:
        samples = (
            _assessment_evidence(
                "dependent.service", digit="1", assessed_usec=1_250_000
            ),
            _assessment_evidence(
                "dependent.service", digit="2", assessed_usec=1_750_000
            ),
        )
        first = _evaluate(samples, gap=500_000)
        second = _evaluate(samples, gap=600_000)
        self.assertNotEqual(first.evidence_id, second.evidence_id)

    def test_evidence_identity_changes_with_input_scope_even_same_in_window_samples(
        self,
    ) -> None:
        in_window = _assessment_evidence(
            "dependent.service", digit="1", assessed_usec=1_250_000
        )
        outside = _assessment_evidence(
            "dependent.service", digit="2", assessed_usec=2_500_000
        )
        first = _evaluate((in_window,))
        second = _evaluate((in_window, outside))
        self.assertNotEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(first.in_window_assessments, second.in_window_assessments)

    def test_model_rejects_derived_interpretation_drift(self) -> None:
        evidence = _evaluate(())
        with self.assertRaises(PropagationCoverageContractError):
            replace(
                evidence,
                interpretation=(
                    PropagationCoverageInterpretation.NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING
                ),
            )

    def test_model_rejects_status_count_drift(self) -> None:
        evidence = _evaluate(
            (
                _assessment_evidence(
                    "dependent.service", digit="1", assessed_usec=1_250_000
                ),
            )
        )
        with self.assertRaises(PropagationCoverageContractError):
            replace(evidence, healthy_count=99)

    def test_model_rejects_evidence_identity_drift(self) -> None:
        evidence = _evaluate(())
        with self.assertRaises(PropagationCoverageContractError):
            replace(evidence, evidence_id="propcov-" + "0" * 64)

    def test_serialization_keeps_topology_and_claim_boundaries_explicit(self) -> None:
        evidence = _evaluate(())
        payload = evidence.to_dict()
        self.assertEqual(
            payload["graph_version_id"], evidence.candidate.graph_version_id
        )
        self.assertEqual(payload["topology_id"], evidence.candidate.topology_id)
        self.assertFalse(payload["topology_temporal_applicability_claim"])
        self.assertFalse(payload["continuous_health_claim_assigned"])
        self.assertFalse(payload["non_propagation_claim_assigned"])
        self.assertFalse(payload["root_cause_claim_assigned"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])


if __name__ == "__main__":
    unittest.main()
