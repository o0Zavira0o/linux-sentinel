from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.dependency.coverage import evaluate_propagation_sampling_coverage
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
    bind_systemd_assessment_evidence,
    bind_systemd_incident_temporal_evidence,
    build_dependency_propagation_candidates,
    evaluate_pairwise_fault_propagation,
)
from sentinel_x.dependency.propagation_experiment import (
    SystemdPropagationPairSpec,
    build_controlled_propagation_experiment_record,
    build_systemd_propagation_pair,
    evaluate_controlled_ground_truth_coverage,
)
from sentinel_x.dependency.synthesis import (
    DEFAULT_MAX_SYNTHESIS_INPUT_EVIDENCE,
    MAX_SYNTHESIS_INPUT_EVIDENCE,
    PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
    PropagationEvidenceContext,
    PropagationEvidenceSynthesisCapacityError,
    PropagationEvidenceSynthesisContractError,
    PropagationEvidenceSynthesisInterpretation,
    PropagationEvidenceSynthesisScope,
    synthesize_propagation_evidence,
)
from sentinel_x.detection.incidents import SystemdServiceIncident
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    DetectionBasis,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)
from sentinel_x.lab.fixture import SystemdLabFixtureSpec
from sentinel_x.lab.injector import (
    FaultInjectionOutcome,
    LabFaultOperation,
    SystemdLabFaultPlan,
    SystemdLabServiceState,
)
from sentinel_x.lab.models import FaultGroundTruthWindow, FaultMode

_BOOT = "a" * 32
_OTHER_BOOT = "b" * 32
_TIME = datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)


def _candidate(
    *,
    dependency_unit: str = "dependency.service",
    dependent_unit: str = "dependent.service",
    relation: DependencyRelation = DependencyRelation.REQUIRES,
    boot_id: str = _BOOT,
    observed_at: datetime = _TIME,
):
    event_id = f"dep-event-{relation.value}-{dependent_unit}"
    evidence = DependencyEvidence(
        evidence_id="depev-"
        + ("1" if relation is DependencyRelation.REQUIRES else "2") * 64,
        source_event_id=event_id,
        observed_at=observed_at,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=dependent_unit,
        ),
        relation=relation,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=dependency_unit,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="Requires"
        if relation is DependencyRelation.REQUIRES
        else "Wants",
    )
    ordering = DependencyEvidence(
        evidence_id="depev-" + "3" * 64,
        source_event_id=event_id,
        observed_at=observed_at,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=dependent_unit,
        ),
        relation=DependencyRelation.AFTER,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=dependency_unit,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="After",
    )
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name=dependent_unit,
        canonical_name=dependent_unit,
        names=(dependent_unit,),
        load_state="loaded",
        requires=(dependency_unit,) if relation is DependencyRelation.REQUIRES else (),
        wants=(dependency_unit,) if relation is DependencyRelation.WANTS else (),
        after=(dependency_unit,),
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
        evidence=(evidence, ordering),
    )
    report = SystemdDependencyDiscoveryReport(
        root_requested_unit=dependent_unit,
        root_canonical_unit=dependent_unit,
        boot_id=boot_id,
        max_depth=1,
        max_units=64,
        units=(unit,),
        failures=(),
        truncated_by_depth=True,
        unexpanded_requirement_count=1,
    )
    return build_dependency_propagation_candidates(build_dependency_graph(report))[0]


def _assessment_evidence(
    unit: str,
    *,
    digit: str,
    assessed_usec: int,
    state_change_usec: int | None,
    status: SystemdServiceHealthStatus,
    boot_id: str = _BOOT,
):
    if status is SystemdServiceHealthStatus.HEALTHY:
        anomaly_class = None
        severity = None
        active_state, sub_state, main_pid, result = "active", "running", 2222, "success"
    elif status is SystemdServiceHealthStatus.INACTIVE:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
        severity = EventSeverity.WARNING
        active_state, sub_state, main_pid, result = "inactive", "dead", None, "success"
    elif status is SystemdServiceHealthStatus.FAILED:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        severity = EventSeverity.ERROR
        active_state, sub_state, main_pid, result = (
            "failed",
            "failed",
            None,
            "exit-code",
        )
    else:
        anomaly_class = None
        severity = None
        active_state, sub_state, main_pid, result = "activating", "start", None, None
    event = SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.service",
        message=f"systemd service observation for {unit}",
        severity=EventSeverity.INFO,
        event_id=f"evt-{unit}-{digit}-{assessed_usec}",
        occurred_at=_TIME,
        attributes={
            "observation_type": "linux.systemd.service",
            "collector_name": "phase5e_test",
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


def _incident_evidence(
    unit: str,
    *,
    digit: str,
    assessed_usec: int,
    state_change_usec: int | None,
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.FAILED,
    boot_id: str = _BOOT,
):
    evidence = _assessment_evidence(
        unit,
        digit=digit,
        assessed_usec=assessed_usec,
        state_change_usec=state_change_usec,
        status=status,
        boot_id=boot_id,
    )
    assessment = evidence.assessment
    assert assessment.anomaly_class is not None
    assert assessment.severity is not None
    incident = SystemdServiceIncident(
        incident_id="inc-" + digit * 64,
        target_unit=unit,
        canonical_unit=unit,
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


def _forward_pairwise(candidate=None, *, source=None):
    candidate = _candidate() if candidate is None else candidate
    source = (
        _incident_evidence(
            "dependency.service",
            digit="a",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        if source is None
        else source
    )
    affected = _incident_evidence(
        "dependent.service",
        digit="b",
        assessed_usec=1_300_000,
        state_change_usec=1_200_000,
    )
    return evaluate_pairwise_fault_propagation(
        candidate,
        source,
        affected,
        analysis_window_usec=1_000_000,
    )


def _reverse_pairwise(candidate=None, *, source=None):
    candidate = _candidate() if candidate is None else candidate
    source = (
        _incident_evidence(
            "dependency.service",
            digit="c",
            assessed_usec=1_500_000,
            state_change_usec=1_400_000,
        )
        if source is None
        else source
    )
    affected = _incident_evidence(
        "dependent.service",
        digit="d",
        assessed_usec=1_300_000,
        state_change_usec=1_200_000,
    )
    return evaluate_pairwise_fault_propagation(
        candidate,
        source,
        affected,
        analysis_window_usec=1_000_000,
    )


def _observational_coverage(*, negative: bool, candidate=None, source=None):
    candidate = _candidate() if candidate is None else candidate
    source = (
        _incident_evidence(
            "dependency.service",
            digit="e",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        if source is None
        else source
    )
    if negative:
        assessments = tuple(
            _assessment_evidence(
                "dependent.service",
                digit=digit,
                assessed_usec=usec,
                state_change_usec=900_000,
                status=SystemdServiceHealthStatus.HEALTHY,
            )
            for digit, usec in zip(
                ("1", "2", "3", "4", "5"),
                (1_000_000, 1_250_000, 1_500_000, 1_750_000, 2_000_000),
                strict=True,
            )
        )
    else:
        assessments = (
            _assessment_evidence(
                "dependent.service",
                digit="6",
                assessed_usec=1_250_000,
                state_change_usec=1_200_000,
                status=SystemdServiceHealthStatus.INACTIVE,
            ),
        )
    return evaluate_propagation_sampling_coverage(
        candidate,
        source,
        assessments,
        analysis_window_usec=1_000_000,
        max_sample_gap_usec=300_000,
        max_input_assessments=32,
    )


def _controlled_record(*, negative: bool, with_pairwise: bool = False):
    source_unit = "sentinel-x-lab-p5e-source.service"
    dependent_unit = "sentinel-x-lab-p5e-dependent.service"
    artifact = build_systemd_propagation_pair(
        SystemdPropagationPairSpec(
            source=SystemdLabFixtureSpec("p5e-source", runtime_max_seconds=120),
            dependent=SystemdLabFixtureSpec("p5e-dependent", runtime_max_seconds=120),
            requirement_relation=DependencyRelation.REQUIRES,
        )
    )
    candidate = _candidate(
        dependency_unit=source_unit,
        dependent_unit=dependent_unit,
        observed_at=_TIME - timedelta(seconds=1),
    )
    plan = SystemdLabFaultPlan(
        experiment_id="exp-" + "1" * 32,
        scenario_id="phase5e-controlled",
        target_unit=source_unit,
        fault_mode=FaultMode.SERVICE_INACTIVE,
        operation=LabFaultOperation.STOP_SERVICE,
        expected_fault_active_states=("inactive",),
        recovery_operations=("start", "reset-failed"),
        artifact_sha256=artifact.source_artifact.sha256,
    )
    truth = FaultGroundTruthWindow(
        experiment_id=plan.experiment_id,
        scenario_id=plan.scenario_id,
        target_unit=source_unit,
        fault_mode=FaultMode.SERVICE_INACTIVE,
        started_at=_TIME,
        started_monotonic_usec=1_100_000,
        ended_at=_TIME + timedelta(seconds=1),
        ended_monotonic_usec=2_100_000,
    )
    healthy_state = SystemdLabServiceState(
        unit_name=source_unit,
        load_state="loaded",
        active_state="active",
        sub_state="running",
        main_pid=2222,
        result="success",
    )
    fault_state = SystemdLabServiceState(
        unit_name=source_unit,
        load_state="loaded",
        active_state="inactive",
        sub_state="dead",
        main_pid=0,
        result="success",
    )
    outcome = FaultInjectionOutcome(
        plan=plan,
        ground_truth=truth,
        baseline_state=healthy_state,
        fault_state=fault_state,
        recovered_state=healthy_state,
    )
    if negative:
        assessments = tuple(
            _assessment_evidence(
                dependent_unit,
                digit=digit,
                assessed_usec=usec,
                state_change_usec=900_000,
                status=SystemdServiceHealthStatus.HEALTHY,
            )
            for digit, usec in zip(
                ("7", "8", "9", "a", "b"),
                (1_100_000, 1_350_000, 1_600_000, 1_850_000, 2_100_000),
                strict=True,
            )
        )
    else:
        assessments = (
            _assessment_evidence(
                dependent_unit,
                digit="c",
                assessed_usec=1_300_000,
                state_change_usec=1_250_000,
                status=SystemdServiceHealthStatus.INACTIVE,
            ),
        )
    coverage = evaluate_controlled_ground_truth_coverage(
        candidate,
        truth,
        _BOOT,
        assessments,
        analysis_window_usec=1_000_000,
        max_sample_gap_usec=300_000,
        max_input_assessments=32,
    )
    source_incident = None
    pairwise = None
    if with_pairwise:
        source_incident = _incident_evidence(
            source_unit,
            digit="d",
            assessed_usec=1_150_000,
            state_change_usec=1_000_000,
            status=SystemdServiceHealthStatus.INACTIVE,
        )
        affected = _incident_evidence(
            dependent_unit,
            digit="e",
            assessed_usec=1_300_000,
            state_change_usec=1_200_000,
        )
        pairwise = evaluate_pairwise_fault_propagation(
            candidate,
            source_incident,
            affected,
            analysis_window_usec=1_000_000,
        )
    record = build_controlled_propagation_experiment_record(
        artifact,
        outcome,
        candidate,
        coverage,
        source_incident=source_incident,
        pairwise_evidence=pairwise,
    )
    return candidate, record


class PropagationEvidenceSynthesisTests(unittest.TestCase):
    def test_empty_input_is_explicit_no_evidence_not_negative(self) -> None:
        synthesis = synthesize_propagation_evidence(_candidate(), ())

        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.NO_EVIDENCE,
        )
        self.assertEqual(synthesis.contribution_count, 0)
        self.assertEqual(synthesis.bounded_negative_observation_count, 0)

    def test_forward_pairwise_becomes_forward_consistency_not_causal_claim(
        self,
    ) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_forward_pairwise(candidate),),
        )

        self.assertEqual(synthesis.forward_temporal_consistency_count, 1)
        self.assertEqual(synthesis.supportive_signal_count, 1)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.SUPPORTIVE_WITHOUT_COUNTER_OR_NEGATIVE,
        )
        self.assertFalse(synthesis.to_dict()["causal_claim"])

    def test_reverse_pairwise_preserves_directional_counterevidence(self) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_reverse_pairwise(candidate),),
        )

        self.assertEqual(synthesis.directional_counterevidence_count, 1)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.COUNTEREVIDENCE_WITHOUT_SUPPORT_OR_NEGATIVE,
        )

    def test_observed_anomaly_is_distinct_from_forward_temporal_consistency(
        self,
    ) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_observational_coverage(negative=False, candidate=candidate),),
        )

        self.assertEqual(synthesis.affected_anomaly_observed_count, 1)
        self.assertEqual(synthesis.forward_temporal_consistency_count, 0)
        self.assertEqual(synthesis.supportive_signal_count, 1)

    def test_bounded_negative_remains_negative_observation_not_nonpropagation_claim(
        self,
    ) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_observational_coverage(negative=True, candidate=candidate),),
        )
        payload = synthesis.to_dict()

        self.assertEqual(synthesis.bounded_negative_observation_count, 1)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.BOUNDED_NEGATIVE_WITHOUT_SUPPORT_OR_COUNTER,
        )
        self.assertFalse(payload["propagation_claim_assigned"])
        self.assertFalse(payload["generalization_beyond_evidence_context_permitted"])

    def test_supportive_plus_negative_is_explicit_conflict(self) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="a",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        synthesis = synthesize_propagation_evidence(
            candidate,
            (
                _forward_pairwise(candidate, source=source),
                _observational_coverage(
                    negative=True, candidate=candidate, source=source
                ),
            ),
        )

        self.assertTrue(synthesis.conflicting_evidence_present)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.CONFLICTING_EVIDENCE,
        )
        self.assertEqual(synthesis.forward_temporal_consistency_count, 1)
        self.assertEqual(synthesis.bounded_negative_observation_count, 1)

    def test_supportive_plus_counterevidence_is_explicit_conflict(self) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="a",
            assessed_usec=1_500_000,
            state_change_usec=1_400_000,
        )
        forward = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            _incident_evidence(
                "dependent.service",
                digit="b",
                assessed_usec=1_700_000,
                state_change_usec=1_600_000,
            ),
            analysis_window_usec=1_000_000,
        )
        reverse = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            _incident_evidence(
                "dependent.service",
                digit="d",
                assessed_usec=1_300_000,
                state_change_usec=1_200_000,
            ),
            analysis_window_usec=1_000_000,
        )
        synthesis = synthesize_propagation_evidence(candidate, (forward, reverse))

        self.assertTrue(synthesis.conflicting_evidence_present)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.CONFLICTING_EVIDENCE,
        )

    def test_counter_plus_negative_is_preserved_without_false_support_conflict(
        self,
    ) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="c",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        reverse = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            _incident_evidence(
                "dependent.service",
                digit="d",
                assessed_usec=900_000,
                state_change_usec=800_000,
            ),
            analysis_window_usec=1_000_000,
        )
        synthesis = synthesize_propagation_evidence(
            candidate,
            (
                reverse,
                _observational_coverage(
                    negative=True, candidate=candidate, source=source
                ),
            ),
        )

        self.assertFalse(synthesis.conflicting_evidence_present)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.COUNTER_AND_BOUNDED_NEGATIVE_WITHOUT_SUPPORT,
        )

    def test_controlled_negative_record_keeps_interventional_provenance(self) -> None:
        candidate, record = _controlled_record(negative=True)
        synthesis = synthesize_propagation_evidence(candidate, (record,))

        self.assertEqual(synthesis.controlled_contribution_count, 1)
        self.assertEqual(synthesis.observational_contribution_count, 0)
        self.assertEqual(synthesis.bounded_negative_observation_count, 1)
        contribution = synthesis.contributions[0]
        self.assertEqual(
            contribution.context,
            PropagationEvidenceContext.CONTROLLED_INTERVENTIONAL,
        )
        self.assertEqual(contribution.source_record_id, record.record_id)

    def test_controlled_anomaly_record_is_supportive_only_for_exact_context(
        self,
    ) -> None:
        candidate, record = _controlled_record(negative=False)
        synthesis = synthesize_propagation_evidence(candidate, (record,))
        payload = synthesis.to_dict()

        self.assertEqual(synthesis.affected_anomaly_observed_count, 1)
        self.assertEqual(synthesis.controlled_contribution_count, 1)
        self.assertFalse(payload["generalization_beyond_evidence_context_permitted"])
        self.assertFalse(payload["universal_systemd_behavior_claim"])

    def test_controlled_record_internal_sampling_pairwise_conflict_is_not_erased(
        self,
    ) -> None:
        candidate, record = _controlled_record(negative=True, with_pairwise=True)
        self.assertTrue(record.sampling_incident_evidence_conflict)

        synthesis = synthesize_propagation_evidence(candidate, (record,))

        self.assertEqual(synthesis.controlled_contribution_count, 2)
        self.assertEqual(synthesis.forward_temporal_consistency_count, 1)
        self.assertEqual(synthesis.bounded_negative_observation_count, 1)
        self.assertTrue(synthesis.conflicting_evidence_present)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.CONFLICTING_EVIDENCE,
        )

    def test_controlled_record_and_embedded_pairwise_cannot_double_count_source(
        self,
    ) -> None:
        candidate, record = _controlled_record(negative=True, with_pairwise=True)
        assert record.pairwise_evidence is not None

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(
                candidate,
                (record, record.pairwise_evidence),
            )

    def test_duplicate_input_identity_is_rejected_not_deduplicated(self) -> None:
        candidate = _candidate()
        evidence = _forward_pairwise(candidate)

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(candidate, (evidence, evidence))

    def test_exact_candidate_context_is_required_not_candidate_id_only(self) -> None:
        candidate = _candidate()
        evidence = _forward_pairwise(candidate)
        drifted = replace(candidate, graph_version_id="depgraphv-different")

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(drifted, (evidence,))

    def test_different_relation_candidate_cannot_be_collapsed(self) -> None:
        requires = _candidate(relation=DependencyRelation.REQUIRES)
        wants = _candidate(relation=DependencyRelation.WANTS)
        evidence = _forward_pairwise(requires)

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(wants, (evidence,))

    def test_cross_boot_candidate_context_is_rejected(self) -> None:
        candidate = _candidate()
        other_boot_candidate = _candidate(boot_id=_OTHER_BOOT)
        evidence = _forward_pairwise(candidate)

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(other_boot_candidate, (evidence,))

    def test_input_order_is_canonicalized_deterministically(self) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="a",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        forward = _forward_pairwise(candidate, source=source)
        negative = _observational_coverage(
            negative=True, candidate=candidate, source=source
        )

        first = synthesize_propagation_evidence(candidate, (forward, negative))
        second = synthesize_propagation_evidence(candidate, (negative, forward))

        self.assertEqual(first.synthesis_id, second.synthesis_id)
        self.assertEqual(first.input_evidence_ids, second.input_evidence_ids)
        self.assertEqual(first.contributions, second.contributions)

    def test_synthesis_identity_changes_with_input_scope(self) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="a",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        forward = _forward_pairwise(candidate, source=source)
        negative = _observational_coverage(
            negative=True, candidate=candidate, source=source
        )

        one = synthesize_propagation_evidence(candidate, (forward,))
        two = synthesize_propagation_evidence(candidate, (forward, negative))

        self.assertNotEqual(one.synthesis_id, two.synthesis_id)

    def test_synthesis_identity_changes_with_capacity_policy(self) -> None:
        candidate = _candidate()
        evidence = _forward_pairwise(candidate)

        first = synthesize_propagation_evidence(
            candidate,
            (evidence,),
            max_input_evidence=16,
        )
        second = synthesize_propagation_evidence(
            candidate,
            (evidence,),
            max_input_evidence=32,
        )

        self.assertNotEqual(first.synthesis_id, second.synthesis_id)

    def test_capacity_limit_consumes_at_most_max_plus_one(self) -> None:
        candidate = _candidate()
        evidence = _forward_pairwise(candidate)
        pulls = 0

        def stream():
            nonlocal pulls
            while True:
                pulls += 1
                yield evidence

        with self.assertRaises(PropagationEvidenceSynthesisCapacityError):
            synthesize_propagation_evidence(
                candidate,
                stream(),
                max_input_evidence=3,
            )
        self.assertEqual(pulls, 4)

    def test_text_iterables_are_rejected(self) -> None:
        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(_candidate(), "evidence")  # type: ignore[arg-type]

    def test_untyped_iterable_member_is_rejected(self) -> None:
        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(_candidate(), (object(),))  # type: ignore[arg-type]

    def test_capacity_bounds_reject_boolean_zero_and_excessive_values(self) -> None:
        candidate = _candidate()
        for value in (True, 0, -1, MAX_SYNTHESIS_INPUT_EVIDENCE + 1):
            with self.subTest(value=value):
                with self.assertRaises(PropagationEvidenceSynthesisContractError):
                    synthesize_propagation_evidence(
                        candidate,
                        (),
                        max_input_evidence=value,  # type: ignore[arg-type]
                    )

    def test_default_capacity_is_explicit_and_bounded(self) -> None:
        synthesis = synthesize_propagation_evidence(_candidate(), ())
        self.assertEqual(
            synthesis.max_input_evidence,
            DEFAULT_MAX_SYNTHESIS_INPUT_EVIDENCE,
        )
        self.assertLessEqual(
            DEFAULT_MAX_SYNTHESIS_INPUT_EVIDENCE,
            MAX_SYNTHESIS_INPUT_EVIDENCE,
        )

    def test_observational_synthesis_rejects_cross_episode_inputs(self) -> None:
        candidate = _candidate()
        forward = _forward_pairwise(candidate)
        negative = _observational_coverage(negative=True, candidate=candidate)

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(candidate, (forward, negative))

    def test_observational_synthesis_rejects_analysis_window_drift(self) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="a",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        forward = _forward_pairwise(candidate, source=source)
        coverage = evaluate_propagation_sampling_coverage(
            candidate,
            source,
            (),
            analysis_window_usec=2_000_000,
            max_sample_gap_usec=300_000,
            max_input_assessments=8,
        )

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(candidate, (forward, coverage))

    def test_controlled_record_cannot_mix_with_observational_episode(self) -> None:
        candidate, record = _controlled_record(negative=True)
        source = _incident_evidence(
            candidate.dependency_unit,
            digit="f",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
            status=SystemdServiceHealthStatus.INACTIVE,
        )
        coverage = evaluate_propagation_sampling_coverage(
            candidate,
            source,
            (),
            analysis_window_usec=1_000_000,
            max_sample_gap_usec=300_000,
            max_input_assessments=8,
        )

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            synthesize_propagation_evidence(candidate, (record, coverage))

    def test_scope_and_context_are_explicit_in_serialization(self) -> None:
        candidate = _candidate()
        observational = synthesize_propagation_evidence(
            candidate,
            (_forward_pairwise(candidate),),
        )
        controlled_candidate, record = _controlled_record(negative=True)
        controlled = synthesize_propagation_evidence(
            controlled_candidate,
            (record,),
        )
        empty = synthesize_propagation_evidence(candidate, ())

        self.assertEqual(
            observational.scope,
            PropagationEvidenceSynthesisScope.OBSERVATIONAL_EPISODE,
        )
        self.assertIsNotNone(observational.source_context_id)
        self.assertEqual(observational.analysis_window_usec, 1_000_000)
        self.assertEqual(
            controlled.scope,
            PropagationEvidenceSynthesisScope.CONTROLLED_EXPERIMENT,
        )
        self.assertEqual(controlled.source_context_id, record.record_id)
        self.assertEqual(
            empty.scope,
            PropagationEvidenceSynthesisScope.EMPTY_CANDIDATE_CONTEXT,
        )
        self.assertIsNone(empty.source_context_id)
        self.assertIsNone(empty.analysis_window_usec)

    def test_model_rejects_input_or_contribution_drift(self) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_forward_pairwise(candidate),),
        )

        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            replace(synthesis, input_evidence=())
        with self.assertRaises(PropagationEvidenceSynthesisContractError):
            replace(synthesis, contributions=())

    def test_serialization_exposes_provenance_ids_without_nested_input_duplication(
        self,
    ) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_forward_pairwise(candidate),),
        )
        payload = synthesis.to_dict()

        self.assertNotIn("input_evidence", payload)
        self.assertEqual(
            payload["input_evidence_ids"], list(synthesis.input_evidence_ids)
        )

    def test_serialization_preserves_claim_boundaries_and_no_scalar_score(self) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_forward_pairwise(candidate),),
        )
        payload = synthesis.to_dict()

        self.assertEqual(
            payload["schema_version"],
            PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
        )
        self.assertTrue(payload["candidate_context_exact_match_required"])
        self.assertFalse(payload["topology_temporal_applicability_claim"])
        self.assertFalse(payload["generalization_beyond_evidence_context_permitted"])
        self.assertFalse(payload["evidence_independence_assumed"])
        self.assertFalse(payload["scalar_score_assigned"])
        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["propagation_claim_assigned"])
        self.assertFalse(payload["root_cause_claim_assigned"])
        self.assertFalse(payload["universal_systemd_behavior_claim"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])

    def test_contribution_serialization_does_not_claim_independence(self) -> None:
        candidate = _candidate()
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_forward_pairwise(candidate),),
        )
        contribution = synthesis.contributions[0].to_dict()

        self.assertFalse(contribution["causal_claim"])
        self.assertFalse(contribution["propagation_claim_assigned"])
        self.assertFalse(contribution["evidence_independence_assumed"])
        self.assertFalse(contribution["probabilistic_confidence_assigned"])

    def test_supportive_signal_count_keeps_modalities_separate(self) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="a",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        synthesis = synthesize_propagation_evidence(
            candidate,
            (
                _forward_pairwise(candidate, source=source),
                _observational_coverage(
                    negative=False, candidate=candidate, source=source
                ),
            ),
        )

        self.assertEqual(synthesis.supportive_signal_count, 2)
        self.assertEqual(synthesis.forward_temporal_consistency_count, 1)
        self.assertEqual(synthesis.affected_anomaly_observed_count, 1)

    def test_insufficient_signal_does_not_override_usable_evidence(self) -> None:
        candidate = _candidate()
        source = _incident_evidence(
            "dependency.service",
            digit="f",
            assessed_usec=1_100_000,
            state_change_usec=1_000_000,
        )
        sparse = evaluate_propagation_sampling_coverage(
            candidate,
            source,
            (
                _assessment_evidence(
                    "dependent.service",
                    digit="0",
                    assessed_usec=1_500_000,
                    state_change_usec=900_000,
                    status=SystemdServiceHealthStatus.HEALTHY,
                ),
            ),
            analysis_window_usec=1_000_000,
            max_sample_gap_usec=100_000,
            max_input_assessments=8,
        )
        synthesis = synthesize_propagation_evidence(
            candidate,
            (_forward_pairwise(candidate, source=source), sparse),
        )

        self.assertEqual(synthesis.insufficient_evidence_count, 1)
        self.assertEqual(synthesis.supportive_signal_count, 1)
        self.assertEqual(
            synthesis.interpretation,
            PropagationEvidenceSynthesisInterpretation.SUPPORTIVE_WITHOUT_COUNTER_OR_NEGATIVE,
        )


if __name__ == "__main__":
    unittest.main()
