from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.dependency.coverage import (
    AssessmentCoverageOutcome,
    PropagationCoverageInterpretation,
    SamplingCoverageStatus,
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
    bind_systemd_assessment_evidence,
    bind_systemd_incident_temporal_evidence,
    build_dependency_propagation_candidates,
    evaluate_pairwise_fault_propagation,
)
from sentinel_x.dependency.propagation_experiment import (
    ControlledPropagationEvidenceClass,
    ControlledPropagationExperimentCapacityError,
    ControlledPropagationExperimentContractError,
    ControlledPropagationExperimentPreconditionError,
    SystemdPropagationPairArtifact,
    SystemdPropagationPairSpec,
    build_controlled_propagation_experiment_record,
    build_systemd_propagation_pair,
    evaluate_controlled_ground_truth_coverage,
    verify_installed_systemd_propagation_pair,
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
_TIME = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
_SOURCE_ID = "p53-source"
_DEPENDENT_ID = "p53-dependent"
_SOURCE = f"sentinel-x-lab-{_SOURCE_ID}.service"
_DEPENDENT = f"sentinel-x-lab-{_DEPENDENT_ID}.service"


def _pair_spec(
    relation: DependencyRelation = DependencyRelation.REQUIRES,
) -> SystemdPropagationPairSpec:
    return SystemdPropagationPairSpec(
        source=SystemdLabFixtureSpec(_SOURCE_ID, runtime_max_seconds=120),
        dependent=SystemdLabFixtureSpec(_DEPENDENT_ID, runtime_max_seconds=120),
        requirement_relation=relation,
    )


def _candidate(
    relation: DependencyRelation = DependencyRelation.REQUIRES,
    *,
    boot_id: str = _BOOT,
):
    graph_time = _TIME - timedelta(seconds=1)
    event_id = "dep-event"
    evidence = DependencyEvidence(
        evidence_id="depev-" + "1" * 64,
        source_event_id=event_id,
        observed_at=graph_time,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_DEPENDENT,
        ),
        relation=relation,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_SOURCE,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="Requires"
        if relation is DependencyRelation.REQUIRES
        else "Wants",
    )
    ordering = DependencyEvidence(
        evidence_id="depev-" + "2" * 64,
        source_event_id=event_id,
        observed_at=graph_time,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_DEPENDENT,
        ),
        relation=DependencyRelation.AFTER,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_SOURCE,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="After",
    )
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name=_DEPENDENT,
        canonical_name=_DEPENDENT,
        names=(_DEPENDENT,),
        load_state="loaded",
        requires=(_SOURCE,) if relation is DependencyRelation.REQUIRES else (),
        wants=(_SOURCE,) if relation is DependencyRelation.WANTS else (),
        after=(_SOURCE,),
        before=(),
        captured_at=graph_time,
    )
    source_event = SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message=f"dependency observation for {_DEPENDENT}",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=graph_time,
        attributes={"observation_type": "linux.systemd.unit.dependency"},
    )
    unit = DiscoveredSystemdUnit(
        depth=0,
        snapshot=snapshot,
        source_event=source_event,
        evidence=(evidence, ordering),
    )
    report = SystemdDependencyDiscoveryReport(
        root_requested_unit=_DEPENDENT,
        root_canonical_unit=_DEPENDENT,
        boot_id=boot_id,
        max_depth=1,
        max_units=64,
        units=(unit,),
        failures=(),
        truncated_by_depth=True,
        unexpanded_requirement_count=1,
    )
    return build_dependency_propagation_candidates(build_dependency_graph(report))[0]


def _event(
    unit: str,
    *,
    event_id: str,
    assessed_status: SystemdServiceHealthStatus,
    state_change_usec: int | None,
    boot_id: str = _BOOT,
) -> SentinelEvent:
    if assessed_status is SystemdServiceHealthStatus.HEALTHY:
        active_state, sub_state, main_pid, result = "active", "running", 2222, "success"
    elif assessed_status is SystemdServiceHealthStatus.INACTIVE:
        active_state, sub_state, main_pid, result = "inactive", "dead", None, "success"
    elif assessed_status is SystemdServiceHealthStatus.FAILED:
        active_state, sub_state, main_pid, result = (
            "failed",
            "failed",
            None,
            "exit-code",
        )
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
            "collector_name": "phase5d3_test",
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
    state_change_usec: int | None,
    status: SystemdServiceHealthStatus,
    boot_id: str = _BOOT,
):
    event = _event(
        unit,
        event_id=f"event-{digit}",
        assessed_status=status,
        state_change_usec=state_change_usec,
        boot_id=boot_id,
    )
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
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.INACTIVE,
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


def _outcome(
    artifact: SystemdPropagationPairArtifact,
    *,
    mode: FaultMode = FaultMode.SERVICE_INACTIVE,
) -> FaultInjectionOutcome:
    operation = (
        LabFaultOperation.STOP_SERVICE
        if mode is FaultMode.SERVICE_INACTIVE
        else LabFaultOperation.ABORT_MAIN_PROCESS
    )
    plan = SystemdLabFaultPlan(
        experiment_id="exp-" + "1" * 32,
        scenario_id="phase5d3-case",
        target_unit=artifact.source_unit,
        fault_mode=mode,
        operation=operation,
        expected_fault_active_states=("inactive",)
        if mode is FaultMode.SERVICE_INACTIVE
        else ("failed",),
        recovery_operations=("start", "reset-failed"),
        artifact_sha256=artifact.source_artifact.sha256,
    )
    ground_truth = FaultGroundTruthWindow(
        experiment_id=plan.experiment_id,
        scenario_id=plan.scenario_id,
        target_unit=artifact.source_unit,
        fault_mode=mode,
        started_at=_TIME,
        started_monotonic_usec=1_100_000,
        ended_at=_TIME + timedelta(seconds=1),
        ended_monotonic_usec=2_100_000,
    )
    healthy = SystemdLabServiceState(
        unit_name=artifact.source_unit,
        load_state="loaded",
        active_state="active",
        sub_state="running",
        main_pid=2222,
        result="success",
    )
    fault = SystemdLabServiceState(
        unit_name=artifact.source_unit,
        load_state="loaded",
        active_state="inactive" if mode is FaultMode.SERVICE_INACTIVE else "failed",
        sub_state="dead" if mode is FaultMode.SERVICE_INACTIVE else "failed",
        main_pid=0,
        result="success" if mode is FaultMode.SERVICE_INACTIVE else "signal",
    )
    return FaultInjectionOutcome(
        plan=plan,
        ground_truth=ground_truth,
        baseline_state=healthy,
        fault_state=fault,
        recovered_state=healthy,
    )


def _healthy_samples() -> tuple:
    return (
        _assessment_evidence(
            _DEPENDENT,
            digit="1",
            assessed_usec=1_100_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
        ),
        _assessment_evidence(
            _DEPENDENT,
            digit="2",
            assessed_usec=1_350_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
        ),
        _assessment_evidence(
            _DEPENDENT,
            digit="3",
            assessed_usec=1_650_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
        ),
        _assessment_evidence(
            _DEPENDENT,
            digit="4",
            assessed_usec=1_900_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
        ),
    )


def _controlled_coverage(
    candidate,
    outcome: FaultInjectionOutcome,
    assessments,
    *,
    boot_id: str = _BOOT,
    analysis_window_usec: int = 1_000_000,
    max_sample_gap_usec: int = 300_000,
    max_input_assessments: int = 4096,
):
    return evaluate_controlled_ground_truth_coverage(
        candidate,
        outcome.ground_truth,
        boot_id,
        assessments,
        analysis_window_usec=analysis_window_usec,
        max_sample_gap_usec=max_sample_gap_usec,
        max_input_assessments=max_input_assessments,
    )


def _record_inputs(
    relation: DependencyRelation = DependencyRelation.REQUIRES,
    *,
    negative: bool = False,
    source_fallback: bool = False,
):
    artifact = build_systemd_propagation_pair(_pair_spec(relation))
    outcome = _outcome(artifact)
    candidate = _candidate(relation)
    source = _incident_evidence(
        _SOURCE,
        digit="a",
        assessed_usec=1_150_000,
        state_change_usec=None if source_fallback else 1_000_000,
    )
    if negative:
        coverage = _controlled_coverage(candidate, outcome, _healthy_samples())
        pairwise = None
    else:
        affected = _incident_evidence(
            _DEPENDENT,
            digit="b",
            assessed_usec=1_250_000,
            state_change_usec=1_200_000,
        )
        coverage = _controlled_coverage(
            candidate, outcome, (affected.opening_assessment,)
        )
        pairwise = evaluate_pairwise_fault_propagation(
            candidate, source, affected, analysis_window_usec=1_000_000
        )
    return artifact, outcome, candidate, coverage, source, pairwise


class ControlledPropagationExperimentTests(unittest.TestCase):
    def test_pair_rejects_same_source_and_dependent(self) -> None:
        spec = SystemdLabFixtureSpec("same-fixture")
        with self.assertRaises(ControlledPropagationExperimentContractError):
            SystemdPropagationPairSpec(
                source=spec,
                dependent=spec,
                requirement_relation=DependencyRelation.REQUIRES,
            )

    def test_pair_rejects_ordering_relation_as_requirement(self) -> None:
        with self.assertRaises(ControlledPropagationExperimentContractError):
            SystemdPropagationPairSpec(
                source=SystemdLabFixtureSpec(_SOURCE_ID),
                dependent=SystemdLabFixtureSpec(_DEPENDENT_ID),
                requirement_relation=DependencyRelation.AFTER,
            )

    def test_pair_id_is_deterministic_and_relation_sensitive(self) -> None:
        self.assertEqual(_pair_spec().pair_id, _pair_spec().pair_id)
        self.assertNotEqual(
            _pair_spec().pair_id,
            _pair_spec(DependencyRelation.WANTS).pair_id,
        )

    def test_requires_pair_renders_only_requires_plus_after(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        self.assertIn(f"Requires={_SOURCE}\n", artifact.dependent_unit_text)
        self.assertIn(f"After={_SOURCE}\n", artifact.dependent_unit_text)
        self.assertNotIn(f"Wants={_SOURCE}\n", artifact.dependent_unit_text)

    def test_wants_pair_renders_only_wants_plus_after(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec(DependencyRelation.WANTS))
        self.assertIn(f"Wants={_SOURCE}\n", artifact.dependent_unit_text)
        self.assertIn(f"After={_SOURCE}\n", artifact.dependent_unit_text)
        self.assertNotIn(f"Requires={_SOURCE}\n", artifact.dependent_unit_text)

    def test_dependent_fixture_keeps_hardening_and_no_install_section(self) -> None:
        text = build_systemd_propagation_pair(_pair_spec()).dependent_unit_text
        for line in (
            "DynamicUser=yes",
            "NoNewPrivileges=yes",
            "PrivateNetwork=yes",
            "ProtectSystem=strict",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
        ):
            self.assertIn(line, text)
        self.assertNotIn("[Install]", text)

    def test_artifact_rejects_dependent_text_or_digest_drift(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        with self.assertRaises(ControlledPropagationExperimentContractError):
            replace(artifact, dependent_unit_text=artifact.dependent_unit_text + "#")
        with self.assertRaises(ControlledPropagationExperimentContractError):
            replace(artifact, dependent_sha256="0" * 64)

    def test_private_pair_staging_uses_0600_and_exact_bytes(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        with tempfile.TemporaryDirectory() as directory:
            source_path, dependent_path = artifact.write_private_copies(directory)
            self.assertEqual(
                source_path.read_text(), artifact.source_artifact.unit_text
            )
            self.assertEqual(dependent_path.read_text(), artifact.dependent_unit_text)
            self.assertEqual(source_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(dependent_path.stat().st_mode & 0o777, 0o600)

    def test_staging_failure_cleans_source_copy(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        with tempfile.TemporaryDirectory() as directory:
            dependent = Path(directory) / artifact.dependent_unit
            dependent.write_text("occupied")
            with self.assertRaises(ControlledPropagationExperimentPreconditionError):
                artifact.write_private_copies(directory)
            self.assertFalse((Path(directory) / artifact.source_unit).exists())
            self.assertEqual(dependent.read_text(), "occupied")

    def test_artifact_serialization_omits_unit_bodies_and_claims(self) -> None:
        payload = build_systemd_propagation_pair(_pair_spec()).to_dict()
        self.assertNotIn("unit_text", payload)
        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["propagation_expectation_assigned"])

    def test_installed_pair_verifier_rejects_untyped_input(self) -> None:
        with self.assertRaises(ControlledPropagationExperimentPreconditionError):
            verify_installed_systemd_propagation_pair(object())  # type: ignore[arg-type]

    def test_ground_truth_bounded_healthy_sampling_assigns_negative_evidence(
        self,
    ) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec(DependencyRelation.WANTS))
        outcome = _outcome(artifact)
        evidence = _controlled_coverage(
            _candidate(DependencyRelation.WANTS), outcome, _healthy_samples()
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

    def test_ground_truth_sparse_healthy_sampling_is_insufficient(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec(DependencyRelation.WANTS))
        outcome = _outcome(artifact)
        sample = _assessment_evidence(
            _DEPENDENT,
            digit="1",
            assessed_usec=1_500_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
        )
        evidence = _controlled_coverage(
            _candidate(DependencyRelation.WANTS), outcome, (sample,)
        )
        self.assertIs(
            evidence.sampling_coverage, SamplingCoverageStatus.GAP_BOUND_EXCEEDED
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.INSUFFICIENT_SAMPLING_COVERAGE,
        )
        self.assertFalse(evidence.negative_evidence_assigned)

    def test_ground_truth_unassessed_sample_blocks_negative_evidence(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec(DependencyRelation.WANTS))
        outcome = _outcome(artifact)
        samples = list(_healthy_samples())
        samples[1] = _assessment_evidence(
            _DEPENDENT,
            digit="5",
            assessed_usec=1_350_000,
            state_change_usec=None,
            status=SystemdServiceHealthStatus.UNASSESSED,
        )
        evidence = _controlled_coverage(
            _candidate(DependencyRelation.WANTS), outcome, tuple(samples)
        )
        self.assertIs(
            evidence.assessment_outcome, AssessmentCoverageOutcome.UNASSESSED_PRESENT
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.UNASSESSED_COVERAGE,
        )
        self.assertFalse(evidence.negative_evidence_assigned)

    def test_ground_truth_anomaly_observation_is_positive_even_if_sparse(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        outcome = _outcome(artifact)
        sample = _assessment_evidence(
            _DEPENDENT,
            digit="b",
            assessed_usec=1_250_000,
            state_change_usec=1_200_000,
            status=SystemdServiceHealthStatus.INACTIVE,
        )
        evidence = _controlled_coverage(_candidate(), outcome, (sample,))
        self.assertIs(
            evidence.assessment_outcome, AssessmentCoverageOutcome.ANOMALY_OBSERVED
        )
        self.assertIs(
            evidence.interpretation,
            PropagationCoverageInterpretation.AFFECTED_ANOMALY_OBSERVED_IN_WINDOW,
        )

    def test_ground_truth_window_must_be_closed_and_contain_analysis_window(
        self,
    ) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        outcome = _outcome(artifact)
        open_truth = replace(
            outcome.ground_truth, ended_at=None, ended_monotonic_usec=None
        )
        with self.assertRaises(ControlledPropagationExperimentContractError):
            evaluate_controlled_ground_truth_coverage(
                _candidate(),
                open_truth,
                _BOOT,
                (),
                analysis_window_usec=100_000,
                max_sample_gap_usec=50_000,
            )
        with self.assertRaises(ControlledPropagationExperimentContractError):
            _controlled_coverage(
                _candidate(), outcome, (), analysis_window_usec=1_000_001
            )

    def test_ground_truth_target_and_fault_mode_must_match_candidate_contract(
        self,
    ) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        outcome = _outcome(artifact)
        with self.assertRaises(ControlledPropagationExperimentContractError):
            evaluate_controlled_ground_truth_coverage(
                _candidate(),
                replace(outcome.ground_truth, target_unit=_DEPENDENT),
                _BOOT,
                (),
                analysis_window_usec=100_000,
                max_sample_gap_usec=50_000,
            )
        with self.assertRaises(ControlledPropagationExperimentContractError):
            evaluate_controlled_ground_truth_coverage(
                _candidate(),
                replace(outcome.ground_truth, fault_mode=FaultMode.SERVICE_FAILED),
                _BOOT,
                (),
                analysis_window_usec=100_000,
                max_sample_gap_usec=50_000,
            )

    def test_ground_truth_boot_must_match_graph_boot(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        with self.assertRaises(ControlledPropagationExperimentContractError):
            evaluate_controlled_ground_truth_coverage(
                _candidate(),
                _outcome(artifact).ground_truth,
                "b" * 32,
                (),
                analysis_window_usec=100_000,
                max_sample_gap_usec=50_000,
            )

    def test_topology_evidence_must_precede_controlled_fault_start(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        candidate = replace(
            _candidate(), requirement_observed_at=_TIME + timedelta(microseconds=1)
        )
        with self.assertRaises(ControlledPropagationExperimentContractError):
            _controlled_coverage(candidate, _outcome(artifact), ())

    def test_wrong_boot_or_unit_assessment_is_rejected(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        outcome = _outcome(artifact)
        wrong_boot = _assessment_evidence(
            _DEPENDENT,
            digit="1",
            assessed_usec=1_200_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
            boot_id="b" * 32,
        )
        wrong_unit = _assessment_evidence(
            _SOURCE,
            digit="2",
            assessed_usec=1_200_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
        )
        for sample in (wrong_boot, wrong_unit):
            with self.assertRaises(ControlledPropagationExperimentContractError):
                _controlled_coverage(_candidate(), outcome, (sample,))

    def test_duplicate_assessment_is_rejected_not_deduplicated(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        sample = _healthy_samples()[0]
        with self.assertRaises(ControlledPropagationExperimentContractError):
            _controlled_coverage(_candidate(), _outcome(artifact), (sample, sample))

    def test_text_and_untyped_assessment_inputs_are_rejected(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        outcome = _outcome(artifact)
        with self.assertRaises(ControlledPropagationExperimentContractError):
            _controlled_coverage(_candidate(), outcome, "bad")  # type: ignore[arg-type]
        with self.assertRaises(ControlledPropagationExperimentContractError):
            _controlled_coverage(_candidate(), outcome, (object(),))  # type: ignore[arg-type]

    def test_capacity_limit_bounds_generator_consumption(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        sample = _healthy_samples()[0]
        consumed = 0

        def stream():
            nonlocal consumed
            for _ in range(10):
                consumed += 1
                yield sample

        with self.assertRaises(ControlledPropagationExperimentCapacityError):
            _controlled_coverage(
                _candidate(), _outcome(artifact), stream(), max_input_assessments=2
            )
        self.assertEqual(consumed, 3)

    def test_input_outside_window_is_counted_but_not_used_as_coverage(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec(DependencyRelation.WANTS))
        outcome = _outcome(artifact)
        outside = _assessment_evidence(
            _DEPENDENT,
            digit="9",
            assessed_usec=2_500_000,
            state_change_usec=800_000,
            status=SystemdServiceHealthStatus.HEALTHY,
        )
        evidence = _controlled_coverage(
            _candidate(DependencyRelation.WANTS),
            outcome,
            _healthy_samples() + (outside,),
        )
        self.assertEqual(evidence.input_assessment_count, 5)
        self.assertEqual(len(evidence.in_window_assessments), 4)
        self.assertTrue(evidence.negative_evidence_assigned)

    def test_controlled_coverage_serialization_preserves_claim_boundaries(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec(DependencyRelation.WANTS))
        evidence = _controlled_coverage(
            _candidate(DependencyRelation.WANTS), _outcome(artifact), _healthy_samples()
        )
        payload = evidence.to_dict()
        self.assertEqual(payload["ground_truth_boot_id"], _BOOT)
        self.assertEqual(
            payload["temporal_anchor_basis"], "controlled_fault_ground_truth"
        )
        self.assertFalse(payload["cross_boot_temporal_comparison_permitted"])
        self.assertFalse(payload["continuous_health_claim_assigned"])
        self.assertFalse(payload["non_propagation_claim_assigned"])
        self.assertFalse(payload["topology_temporal_applicability_claim"])
        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["root_cause_claim_assigned"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])

    def test_controlled_coverage_identity_is_deterministic_and_content_addressed(
        self,
    ) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec(DependencyRelation.WANTS))
        outcome = _outcome(artifact)
        candidate = _candidate(DependencyRelation.WANTS)
        first = _controlled_coverage(candidate, outcome, _healthy_samples())
        second = _controlled_coverage(candidate, outcome, reversed(_healthy_samples()))
        self.assertEqual(first.evidence_id, second.evidence_id)
        with self.assertRaises(ControlledPropagationExperimentContractError):
            replace(first, evidence_id="propgtcov-" + "0" * 64)

    def test_positive_record_preserves_affected_anomaly_observation(self) -> None:
        artifact, outcome, candidate, coverage, source, pairwise = _record_inputs()
        record = build_controlled_propagation_experiment_record(
            artifact,
            outcome,
            candidate,
            coverage,
            source_incident=source,
            pairwise_evidence=pairwise,
        )
        self.assertIs(
            record.evidence_class,
            ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED,
        )
        self.assertTrue(record.forward_temporal_consistency_observed)

    def test_negative_record_can_use_ground_truth_without_source_incident(self) -> None:
        artifact, outcome, candidate, coverage, _, _ = _record_inputs(
            DependencyRelation.WANTS, negative=True
        )
        record = build_controlled_propagation_experiment_record(
            artifact, outcome, candidate, coverage
        )
        self.assertIs(
            record.evidence_class,
            ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE,
        )
        self.assertIsNone(record.ground_truth_confirmation_offset_usec)

    def test_source_fallback_is_descriptive_and_does_not_block_ground_truth_negative(
        self,
    ) -> None:
        artifact, outcome, candidate, coverage, source, _ = _record_inputs(
            DependencyRelation.WANTS, negative=True, source_fallback=True
        )
        record = build_controlled_propagation_experiment_record(
            artifact, outcome, candidate, coverage, source_incident=source
        )
        self.assertIs(
            record.evidence_class,
            ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE,
        )
        self.assertIsNone(record.ground_truth_confirmation_offset_usec)

    def test_record_rejects_failed_fault_mode_for_stop_propagation_contrast(
        self,
    ) -> None:
        artifact, _, candidate, coverage, source, pairwise = _record_inputs()
        with self.assertRaises(ControlledPropagationExperimentContractError):
            build_controlled_propagation_experiment_record(
                artifact,
                _outcome(artifact, mode=FaultMode.SERVICE_FAILED),
                candidate,
                coverage,
                source_incident=source,
                pairwise_evidence=pairwise,
            )

    def test_record_rejects_candidate_relation_drift(self) -> None:
        artifact, outcome, _, _, source, _ = _record_inputs()
        weak = _candidate(DependencyRelation.WANTS)
        weak_coverage = _controlled_coverage(weak, outcome, _healthy_samples())
        with self.assertRaises(ControlledPropagationExperimentContractError):
            build_controlled_propagation_experiment_record(
                artifact, outcome, weak, weak_coverage, source_incident=source
            )

    def test_record_rejects_source_incident_unit_or_boot_drift(self) -> None:
        artifact, outcome, candidate, coverage, _, _ = _record_inputs(negative=True)
        wrong_unit = _incident_evidence(
            _DEPENDENT, digit="c", assessed_usec=1_150_000, state_change_usec=1_000_000
        )
        wrong_boot = _incident_evidence(
            _SOURCE,
            digit="d",
            assessed_usec=1_150_000,
            state_change_usec=1_000_000,
            boot_id="b" * 32,
        )
        for source in (wrong_unit, wrong_boot):
            with self.assertRaises(ControlledPropagationExperimentContractError):
                build_controlled_propagation_experiment_record(
                    artifact, outcome, candidate, coverage, source_incident=source
                )

    def test_record_rejects_noninactive_source_incident(self) -> None:
        artifact, outcome, candidate, coverage, _, _ = _record_inputs(negative=True)
        failed = _incident_evidence(
            _SOURCE,
            digit="c",
            assessed_usec=1_150_000,
            state_change_usec=1_000_000,
            status=SystemdServiceHealthStatus.FAILED,
        )
        with self.assertRaises(ControlledPropagationExperimentContractError):
            build_controlled_propagation_experiment_record(
                artifact, outcome, candidate, coverage, source_incident=failed
            )

    def test_record_rejects_pairwise_window_drift(self) -> None:
        artifact, outcome, candidate, coverage, source, pairwise = _record_inputs()
        assert pairwise is not None
        wider = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            pairwise.affected_incident,
            analysis_window_usec=2_000_000,
        )
        with self.assertRaises(ControlledPropagationExperimentContractError):
            build_controlled_propagation_experiment_record(
                artifact,
                outcome,
                candidate,
                coverage,
                source_incident=source,
                pairwise_evidence=wider,
            )

    def test_pairwise_observation_prevents_negative_record_misclassification(
        self,
    ) -> None:
        positive = _record_inputs()
        negative = _record_inputs(DependencyRelation.REQUIRES, negative=True)
        record = build_controlled_propagation_experiment_record(
            positive[0],
            positive[1],
            positive[2],
            negative[3],
            source_incident=positive[4],
            pairwise_evidence=positive[5],
        )
        self.assertIs(
            record.evidence_class,
            ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED,
        )
        self.assertTrue(record.sampling_incident_evidence_conflict)
        self.assertTrue(record.to_dict()["sampling_incident_evidence_conflict"])

    def test_record_identity_is_deterministic_and_covers_injection_state(self) -> None:
        artifact, outcome, candidate, coverage, source, pairwise = _record_inputs()
        first = build_controlled_propagation_experiment_record(
            artifact,
            outcome,
            candidate,
            coverage,
            source_incident=source,
            pairwise_evidence=pairwise,
        )
        second = build_controlled_propagation_experiment_record(
            artifact,
            outcome,
            candidate,
            coverage,
            source_incident=source,
            pairwise_evidence=pairwise,
        )
        self.assertEqual(first.record_id, second.record_id)
        altered = replace(
            outcome, fault_state=replace(outcome.fault_state, result="operator-stop")
        )
        changed = build_controlled_propagation_experiment_record(
            artifact,
            altered,
            candidate,
            coverage,
            source_incident=source,
            pairwise_evidence=pairwise,
        )
        self.assertNotEqual(first.record_id, changed.record_id)
        with self.assertRaises(ControlledPropagationExperimentContractError):
            replace(first, record_id="propexp-" + "0" * 64)

    def test_record_serialization_keeps_claim_boundaries_explicit(self) -> None:
        artifact, outcome, candidate, coverage, _, _ = _record_inputs(
            DependencyRelation.WANTS, negative=True
        )
        payload = build_controlled_propagation_experiment_record(
            artifact, outcome, candidate, coverage
        ).to_dict()
        self.assertTrue(payload["controlled_lab_observation_only"])
        self.assertFalse(payload["universal_systemd_behavior_claim"])
        self.assertFalse(payload["topology_temporal_applicability_claim"])
        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["root_cause_claim_assigned"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])

    def test_ground_truth_confirmation_offset_only_uses_exact_transition_basis(
        self,
    ) -> None:
        artifact, outcome, candidate, coverage, source, _ = _record_inputs(
            negative=True
        )
        exact = build_controlled_propagation_experiment_record(
            artifact, outcome, candidate, coverage, source_incident=source
        )
        self.assertEqual(exact.ground_truth_confirmation_offset_usec, 100_000)
        fallback_source = _incident_evidence(
            _SOURCE, digit="f", assessed_usec=1_150_000, state_change_usec=None
        )
        fallback = build_controlled_propagation_experiment_record(
            artifact, outcome, candidate, coverage, source_incident=fallback_source
        )
        self.assertIsNone(fallback.ground_truth_confirmation_offset_usec)


if __name__ == "__main__":
    unittest.main()
