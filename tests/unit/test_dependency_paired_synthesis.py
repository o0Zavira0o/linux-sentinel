from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
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
from sentinel_x.dependency.paired_synthesis import (
    CONTROLLED_PROPAGATION_PAIRED_SYNTHESIS_SCHEMA_VERSION,
    ControlledPropagationPairedContrastProfile,
    ControlledPropagationPairedSynthesisContractError,
    build_controlled_propagation_paired_synthesis,
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
from sentinel_x.dependency.propagation_live import (
    ControlledPropagationLivePolicy,
    ControlledPropagationLiveRun,
)
from sentinel_x.dependency.synthesis import (
    PropagationEvidenceSynthesisScope,
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
_START_USEC = 1_100_000


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


def _live_run(
    relation: DependencyRelation,
    *,
    evidence_mode: str,
    experiment_digit: str,
    scenario_id: str,
    boot_id: str = _BOOT,
    source_fixture_id: str = "p5e2-source",
    dependent_fixture_id: str = "p5e2-dependent",
    max_sample_gap_usec: int = 300_000,
    sample_interval_seconds: float = 0.1,
    max_samples: int = 32,
    capture_join_timeout_seconds: float = 5.0,
    duration_usec: int = 1_000_000,
    ordering_context_empty: bool = False,
    expected_fault_active_states: tuple[str, ...] = ("inactive",),
    fault_sub_state: str = "dead",
    with_pairwise: bool = False,
    pairwise_reverse: bool = False,
    baseline_pid: int = 2222,
    recovered_pid: int = 3333,
) -> ControlledPropagationLiveRun:
    artifact = build_systemd_propagation_pair(
        SystemdPropagationPairSpec(
            source=SystemdLabFixtureSpec(
                source_fixture_id,
                runtime_max_seconds=120,
            ),
            dependent=SystemdLabFixtureSpec(
                dependent_fixture_id,
                runtime_max_seconds=120,
            ),
            requirement_relation=relation,
        )
    )
    candidate = _candidate(
        dependency_unit=artifact.source_unit,
        dependent_unit=artifact.dependent_unit,
        relation=relation,
        boot_id=boot_id,
        observed_at=_TIME - timedelta(seconds=1),
    )
    if ordering_context_empty:
        candidate = replace(candidate, ordering_context=())

    plan = SystemdLabFaultPlan(
        experiment_id="exp-" + experiment_digit * 32,
        scenario_id=scenario_id,
        target_unit=artifact.source_unit,
        fault_mode=FaultMode.SERVICE_INACTIVE,
        operation=LabFaultOperation.STOP_SERVICE,
        expected_fault_active_states=expected_fault_active_states,
        recovery_operations=("start", "reset-failed"),
        artifact_sha256=artifact.source_artifact.sha256,
    )
    truth = FaultGroundTruthWindow(
        experiment_id=plan.experiment_id,
        scenario_id=plan.scenario_id,
        target_unit=artifact.source_unit,
        fault_mode=FaultMode.SERVICE_INACTIVE,
        started_at=_TIME,
        started_monotonic_usec=_START_USEC,
        ended_at=_TIME + timedelta(microseconds=duration_usec),
        ended_monotonic_usec=_START_USEC + duration_usec,
    )
    baseline = SystemdLabServiceState(
        unit_name=artifact.source_unit,
        load_state="loaded",
        active_state="active",
        sub_state="running",
        main_pid=baseline_pid,
        result="success",
    )
    fault = SystemdLabServiceState(
        unit_name=artifact.source_unit,
        load_state="loaded",
        active_state="inactive",
        sub_state=fault_sub_state,
        main_pid=0,
        result="success",
    )
    recovered = SystemdLabServiceState(
        unit_name=artifact.source_unit,
        load_state="loaded",
        active_state="active",
        sub_state="running",
        main_pid=recovered_pid,
        result="success",
    )
    outcome = FaultInjectionOutcome(
        plan=plan,
        ground_truth=truth,
        baseline_state=baseline,
        fault_state=fault,
        recovered_state=recovered,
    )

    in_window_points = tuple(
        _START_USEC + round(index * duration_usec / 4) for index in range(5)
    )
    digits = (
        ("1", "2", "3", "4", "5", "6")
        if relation is DependencyRelation.REQUIRES
        else ("7", "8", "9", "a", "b", "c")
    )
    pre_fault = _assessment_evidence(
        artifact.dependent_unit,
        digit=digits[0],
        assessed_usec=_START_USEC - 100_000,
        state_change_usec=_START_USEC - 200_000,
        status=SystemdServiceHealthStatus.HEALTHY,
        boot_id=boot_id,
    )
    if evidence_mode == "negative":
        window_assessments = tuple(
            _assessment_evidence(
                artifact.dependent_unit,
                digit=digit,
                assessed_usec=usec,
                state_change_usec=_START_USEC - 200_000,
                status=SystemdServiceHealthStatus.HEALTHY,
                boot_id=boot_id,
            )
            for digit, usec in zip(digits[1:], in_window_points, strict=True)
        )
    elif evidence_mode == "anomaly":
        window_assessments = tuple(
            _assessment_evidence(
                artifact.dependent_unit,
                digit=digit,
                assessed_usec=usec,
                state_change_usec=_START_USEC,
                status=SystemdServiceHealthStatus.INACTIVE,
                boot_id=boot_id,
            )
            for digit, usec in zip(digits[1:], in_window_points, strict=True)
        )
    elif evidence_mode == "inconclusive":
        window_assessments = (
            _assessment_evidence(
                artifact.dependent_unit,
                digit=digits[1],
                assessed_usec=_START_USEC + duration_usec // 2,
                state_change_usec=_START_USEC - 200_000,
                status=SystemdServiceHealthStatus.HEALTHY,
                boot_id=boot_id,
            ),
        )
    else:
        raise AssertionError(f"unsupported evidence_mode: {evidence_mode}")
    assessments = (pre_fault, *window_assessments)

    coverage = evaluate_controlled_ground_truth_coverage(
        candidate,
        truth,
        boot_id,
        assessments,
        analysis_window_usec=duration_usec,
        max_sample_gap_usec=max_sample_gap_usec,
        max_input_assessments=max_samples,
    )

    source_incident = None
    pairwise = None
    if with_pairwise:
        if pairwise_reverse:
            source_usec = _START_USEC + 300_000
            affected_usec = _START_USEC + 100_000
        else:
            source_usec = _START_USEC
            affected_usec = _START_USEC + 200_000
        source_incident = _incident_evidence(
            artifact.source_unit,
            digit="d",
            assessed_usec=source_usec + 50_000,
            state_change_usec=source_usec,
            status=SystemdServiceHealthStatus.INACTIVE,
            boot_id=boot_id,
        )
        affected = _incident_evidence(
            artifact.dependent_unit,
            digit="e",
            assessed_usec=affected_usec + 50_000,
            state_change_usec=affected_usec,
            status=SystemdServiceHealthStatus.INACTIVE,
            boot_id=boot_id,
        )
        pairwise = evaluate_pairwise_fault_propagation(
            candidate,
            source_incident,
            affected,
            analysis_window_usec=duration_usec,
        )

    record = build_controlled_propagation_experiment_record(
        artifact,
        outcome,
        candidate,
        coverage,
        source_incident=source_incident,
        pairwise_evidence=pairwise,
    )
    policy = ControlledPropagationLivePolicy(
        max_sample_gap_usec=max_sample_gap_usec,
        sample_interval_seconds=sample_interval_seconds,
        max_samples=max_samples,
        capture_join_timeout_seconds=capture_join_timeout_seconds,
    )
    return ControlledPropagationLiveRun(
        pair_artifact=artifact,
        policy=policy,
        candidate=candidate,
        injection_outcome=outcome,
        dependent_assessments=assessments,
        experiment_record=record,
        boot_id_before=boot_id,
        boot_id_after=boot_id,
        post_recovery_verified=True,
    )


def _canonical_pair():
    requires = _live_run(
        DependencyRelation.REQUIRES,
        evidence_mode="anomaly",
        experiment_digit="1",
        scenario_id="p5e2-requires-stop",
        baseline_pid=2222,
        recovered_pid=3333,
    )
    wants = _live_run(
        DependencyRelation.WANTS,
        evidence_mode="negative",
        experiment_digit="2",
        scenario_id="p5e2-wants-stop",
        baseline_pid=4444,
        recovered_pid=5555,
    )
    return requires, wants


class ControlledPropagationPairedSynthesisTests(unittest.TestCase):
    def test_canonical_pair_preserves_requires_anomaly_wants_negative(self) -> None:
        requires, wants = _canonical_pair()
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)

        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.REQUIRES_ANOMALY_WANTS_BOUNDED_NEGATIVE,
        )
        self.assertTrue(contrast.arm_evidence_profiles_differ)
        self.assertTrue(contrast.same_boot)
        self.assertTrue(contrast.sampling_policy_equal)

    def test_each_arm_uses_exact_single_record_controlled_synthesis(self) -> None:
        requires, wants = _canonical_pair()
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)

        for arm in (contrast.requires_arm, contrast.wants_arm):
            self.assertEqual(
                arm.synthesis.scope,
                PropagationEvidenceSynthesisScope.CONTROLLED_EXPERIMENT,
            )
            self.assertEqual(arm.synthesis.max_input_evidence, 1)
            self.assertEqual(
                arm.synthesis.input_evidence, (arm.live_run.experiment_record,)
            )

    def test_reverse_observed_profile_is_descriptive_not_rejected(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="negative",
            experiment_digit="1",
            scenario_id="requires-negative",
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="anomaly",
            experiment_digit="2",
            scenario_id="wants-anomaly",
        )
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)

        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.REQUIRES_BOUNDED_NEGATIVE_WANTS_ANOMALY,
        )

    def test_both_anomaly_profile_is_preserved(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="anomaly",
            experiment_digit="1",
            scenario_id="requires-anomaly",
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="anomaly",
            experiment_digit="2",
            scenario_id="wants-anomaly",
        )
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)
        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.BOTH_ANOMALY_OBSERVED,
        )

    def test_both_negative_profile_is_preserved(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="negative",
            experiment_digit="1",
            scenario_id="requires-negative",
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-negative",
        )
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)
        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.BOTH_BOUNDED_NEGATIVE,
        )

    def test_inconclusive_arm_remains_explicit(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="inconclusive",
            experiment_digit="1",
            scenario_id="requires-inconclusive",
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-negative",
        )
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)
        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.INCONCLUSIVE_PRESENT,
        )

    def test_internal_arm_conflict_is_not_erased_by_pairing(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="negative",
            experiment_digit="1",
            scenario_id="requires-conflict",
            with_pairwise=True,
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-negative",
        )
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)

        self.assertTrue(contrast.requires_arm.synthesis.conflicting_evidence_present)
        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.CONFLICT_PRESENT,
        )

    def test_directional_counterevidence_is_not_hidden_by_evidence_class(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="inconclusive",
            experiment_digit="1",
            scenario_id="requires-reverse",
            with_pairwise=True,
            pairwise_reverse=True,
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-negative",
        )
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)

        self.assertGreater(
            contrast.requires_arm.synthesis.directional_counterevidence_count,
            0,
        )
        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.DIRECTIONAL_COUNTEREVIDENCE_PRESENT,
        )

    def test_wrong_relation_assignment_is_rejected(self) -> None:
        requires = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="1",
            scenario_id="wrong-requires",
        )
        _, wants = _canonical_pair()
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_untyped_inputs_are_rejected(self) -> None:
        requires, wants = _canonical_pair()
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis("requires", wants)  # type: ignore[arg-type]
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, "wants")  # type: ignore[arg-type]

    def test_source_fixture_spec_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-other-source",
            source_fixture_id="p5e2-source-other",
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_dependent_fixture_spec_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-other-dependent",
            dependent_fixture_id="p5e2-dependent-other",
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_sampling_gap_policy_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-gap-drift",
            max_sample_gap_usec=350_000,
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_sampling_interval_policy_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-interval-drift",
            sample_interval_seconds=0.2,
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_capture_join_policy_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-join-drift",
            capture_join_timeout_seconds=6.0,
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_cross_boot_pair_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-other-boot",
            boot_id=_OTHER_BOOT,
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_duplicate_experiment_identity_is_rejected(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="anomaly",
            experiment_digit="1",
            scenario_id="requires-stop",
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="1",
            scenario_id="wants-stop",
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_duplicate_scenario_identity_is_rejected(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="anomaly",
            experiment_digit="1",
            scenario_id="same-scenario",
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="same-scenario",
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_candidate_nonrelation_context_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-context-drift",
            ordering_context_empty=True,
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_fault_plan_semantic_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-plan-drift",
            expected_fault_active_states=("inactive", "failed"),
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_fault_state_category_drift_is_rejected(self) -> None:
        requires, _ = _canonical_pair()
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-state-drift",
            fault_sub_state="exited",
        )
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            build_controlled_propagation_paired_synthesis(requires, wants)

    def test_source_pid_differences_do_not_fake_semantic_drift(self) -> None:
        requires, wants = _canonical_pair()
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)
        self.assertEqual(
            contrast.profile,
            ControlledPropagationPairedContrastProfile.REQUIRES_ANOMALY_WANTS_BOUNDED_NEGATIVE,
        )

    def test_realized_fault_duration_equality_is_not_required(self) -> None:
        requires = _live_run(
            DependencyRelation.REQUIRES,
            evidence_mode="anomaly",
            experiment_digit="1",
            scenario_id="requires-duration",
            duration_usec=1_000_000,
        )
        wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="2",
            scenario_id="wants-duration",
            duration_usec=1_100_000,
        )
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)

        self.assertNotEqual(
            contrast.requires_arm.fault_duration_usec,
            contrast.wants_arm.fault_duration_usec,
        )
        self.assertFalse(contrast.to_dict()["fault_duration_equality_required"])

    def test_manifest_timing_policy_missingness_is_explicit_not_invented(self) -> None:
        requires, wants = _canonical_pair()
        payload = build_controlled_propagation_paired_synthesis(
            requires, wants
        ).to_dict()

        self.assertFalse(
            payload[
                "execution_manifest_timing_policy_available_in_frozen_live_contract"
            ]
        )
        self.assertFalse(payload["execution_manifest_timing_policy_compared"])

    def test_serialization_preserves_claim_boundaries(self) -> None:
        requires, wants = _canonical_pair()
        payload = build_controlled_propagation_paired_synthesis(
            requires, wants
        ).to_dict()

        self.assertEqual(
            payload["schema_version"],
            CONTROLLED_PROPAGATION_PAIRED_SYNTHESIS_SCHEMA_VERSION,
        )
        for key in (
            "experiment_wide_single_variable_isolation_claim",
            "cross_arm_temporal_latency_comparison_assigned",
            "arm_statistical_independence_assumed",
            "sample_statistical_independence_assumed",
            "replication_claim_assigned",
            "statistical_significance_assigned",
            "causal_effect_estimate_assigned",
            "treatment_effect_claim_assigned",
            "topology_temporal_applicability_claim",
            "generalization_beyond_controlled_pair_permitted",
            "causal_claim",
            "propagation_claim_assigned",
            "root_cause_claim_assigned",
            "universal_systemd_behavior_claim",
            "probabilistic_confidence_assigned",
            "scalar_score_assigned",
        ):
            self.assertIs(payload[key], False, key)

    def test_serialization_omits_raw_live_sample_streams(self) -> None:
        requires, wants = _canonical_pair()
        payload = build_controlled_propagation_paired_synthesis(
            requires, wants
        ).to_dict()

        for arm_name in ("requires_arm", "wants_arm"):
            arm = payload[arm_name]
            self.assertNotIn("dependent_assessments", arm)
            self.assertNotIn("input_evidence", arm)
            self.assertIn("dependent_assessment_count", arm)
            self.assertIn("experiment_record_id", arm)

    def test_identity_is_deterministic_for_same_pair(self) -> None:
        requires, wants = _canonical_pair()
        first = build_controlled_propagation_paired_synthesis(requires, wants)
        second = build_controlled_propagation_paired_synthesis(requires, wants)

        self.assertEqual(first.contrast_id, second.contrast_id)
        self.assertEqual(first.requires_arm.arm_id, second.requires_arm.arm_id)
        self.assertEqual(first.wants_arm.arm_id, second.wants_arm.arm_id)

    def test_identity_changes_when_one_experiment_identity_changes(self) -> None:
        requires, wants = _canonical_pair()
        first = build_controlled_propagation_paired_synthesis(requires, wants)
        changed_wants = _live_run(
            DependencyRelation.WANTS,
            evidence_mode="negative",
            experiment_digit="3",
            scenario_id="p5e2-wants-stop-2",
        )
        second = build_controlled_propagation_paired_synthesis(requires, changed_wants)

        self.assertNotEqual(first.contrast_id, second.contrast_id)

    def test_arm_model_rejects_live_fingerprint_drift(self) -> None:
        requires, wants = _canonical_pair()
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            replace(
                contrast.requires_arm,
                live_run_fingerprint="proplivefp-" + "0" * 64,
            )

    def test_contrast_model_rejects_identity_drift(self) -> None:
        requires, wants = _canonical_pair()
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)
        with self.assertRaises(ControlledPropagationPairedSynthesisContractError):
            replace(contrast, contrast_id="propcontrast-" + "0" * 64)

    def test_relation_specific_topologies_remain_distinct(self) -> None:
        requires, wants = _canonical_pair()
        contrast = build_controlled_propagation_paired_synthesis(requires, wants)

        self.assertNotEqual(
            contrast.requires_arm.live_run.candidate.topology_id,
            contrast.wants_arm.live_run.candidate.topology_id,
        )
        self.assertNotEqual(
            contrast.requires_arm.live_run.candidate.candidate_id,
            contrast.wants_arm.live_run.candidate.candidate_id,
        )

    def test_controlled_variable_is_declared_as_requirement_relation(self) -> None:
        requires, wants = _canonical_pair()
        payload = build_controlled_propagation_paired_synthesis(
            requires, wants
        ).to_dict()
        self.assertEqual(
            payload["pair_spec_controlled_variable"], "requirement_relation"
        )
        self.assertFalse(payload["experiment_wide_single_variable_isolation_claim"])
        self.assertTrue(
            payload["dependent_unit_text_exact_relation_only_difference_required"]
        )


if __name__ == "__main__":
    unittest.main()
