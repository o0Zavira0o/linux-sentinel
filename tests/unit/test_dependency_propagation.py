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
    DependencySemanticClass,
)
from sentinel_x.dependency.propagation import (
    FaultPropagationEvidenceContractError,
    PairwiseTemporalFinding,
    PairwiseTemporalInterpretation,
    TemporalEvidenceBasis,
    bind_systemd_assessment_evidence,
    bind_systemd_incident_temporal_evidence,
    build_dependency_propagation_candidates,
    evaluate_pairwise_fault_propagation,
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


def _service_event(
    *,
    event_id: str,
    target: str,
    canonical: str | None = None,
    boot_id: str = _BOOT,
    observed_at: datetime = _TIME,
    state_change_usec: int | None = 900_000,
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.FAILED,
) -> SentinelEvent:
    canonical_name = target if canonical is None else canonical
    if status is SystemdServiceHealthStatus.FAILED:
        active_state = "failed"
        sub_state = "failed"
        main_pid = None
        result = "exit-code"
    elif status is SystemdServiceHealthStatus.INACTIVE:
        active_state = "inactive"
        sub_state = "dead"
        main_pid = None
        result = "success"
    elif status is SystemdServiceHealthStatus.HEALTHY:
        active_state = "active"
        sub_state = "running"
        main_pid = 1234
        result = "success"
    else:
        active_state = "activating"
        sub_state = "start"
        main_pid = None
        result = None
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.service",
        message=f"systemd service observation for {target}",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=observed_at,
        attributes={
            "observation_type": "linux.systemd.service",
            "collector_name": "systemd_test",
            "boot_id": boot_id,
            "requested_name": target,
            "canonical_name": canonical_name,
            "load_state": "loaded",
            "active_state": active_state,
            "sub_state": sub_state,
            "main_pid": main_pid,
            "result": result,
            "state_change_monotonic_usec": state_change_usec,
        },
    )


def _assessment(
    *,
    event: SentinelEvent,
    assessment_hex: str,
    assessed_usec: int = 1_000_000,
    state_change_usec: int | None = 900_000,
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.FAILED,
) -> SystemdServiceHealthAssessment:
    if status is SystemdServiceHealthStatus.FAILED:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        severity = EventSeverity.ERROR
        active_state = "failed"
        sub_state = "failed"
        main_pid = None
        result = "exit-code"
    elif status is SystemdServiceHealthStatus.INACTIVE:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
        severity = EventSeverity.WARNING
        active_state = "inactive"
        sub_state = "dead"
        main_pid = None
        result = "success"
    elif status is SystemdServiceHealthStatus.HEALTHY:
        anomaly_class = None
        severity = None
        active_state = "active"
        sub_state = "running"
        main_pid = 1234
        result = "success"
    else:
        anomaly_class = None
        severity = None
        active_state = "activating"
        sub_state = "start"
        main_pid = None
        result = None
    return SystemdServiceHealthAssessment(
        assessment_id="asmt-" + assessment_hex * 64,
        source_event_id=event.event_id,
        target_unit=str(event.attributes["requested_name"]),
        canonical_unit=str(event.attributes["canonical_name"]),
        source_observed_at=event.occurred_at,
        assessed_at=event.occurred_at + timedelta(milliseconds=10),
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


def _incident(
    *,
    assessment: SystemdServiceHealthAssessment,
    incident_hex: str,
) -> SystemdServiceIncident:
    assert assessment.anomaly_class is not None
    assert assessment.severity is not None
    return SystemdServiceIncident(
        incident_id="inc-" + incident_hex * 64,
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


def _dependency_observation_event(
    event_id: str,
    unit: str,
    *,
    observed_at: datetime = _TIME,
) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message=f"dependency observation for {unit}",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=observed_at,
        attributes={"observation_type": "linux.systemd.unit.dependency"},
    )


def _dependency_edge(
    *,
    hex_digit: str,
    event_id: str,
    subject: str,
    relation: DependencyRelation,
    object_unit: str,
    boot_id: str = _BOOT,
    observed_at: datetime = _TIME,
) -> DependencyEvidence:
    property_name = {
        DependencyRelation.REQUIRES: "Requires",
        DependencyRelation.WANTS: "Wants",
        DependencyRelation.AFTER: "After",
        DependencyRelation.BEFORE: "Before",
    }[relation]
    return DependencyEvidence(
        evidence_id="depev-" + hex_digit * 64,
        source_event_id=event_id,
        observed_at=observed_at,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=subject,
        ),
        relation=relation,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=object_unit,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=(DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED),
        source_property=property_name,
    )


def _graph(
    *,
    relation: DependencyRelation = DependencyRelation.REQUIRES,
    boot_id: str = _BOOT,
    observed_at: datetime = _TIME,
    event_id: str = "dep-event",
    include_ordering: bool = True,
    truncated: bool = False,
):
    edges = [
        _dependency_edge(
            hex_digit="1",
            event_id=event_id,
            subject="dependent.service",
            relation=relation,
            object_unit="dependency.service",
            boot_id=boot_id,
            observed_at=observed_at,
        )
    ]
    if include_ordering:
        edges.append(
            _dependency_edge(
                hex_digit="2",
                event_id=event_id,
                subject="dependent.service",
                relation=DependencyRelation.AFTER,
                object_unit="dependency.service",
                boot_id=boot_id,
                observed_at=observed_at,
            )
        )
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name="dependent.service",
        canonical_name="dependent.service",
        names=("dependent.service",),
        load_state="loaded",
        requires=("dependency.service",)
        if relation is DependencyRelation.REQUIRES
        else (),
        wants=("dependency.service",) if relation is DependencyRelation.WANTS else (),
        after=("dependency.service",) if include_ordering else (),
        before=(),
        captured_at=observed_at,
    )
    unit = DiscoveredSystemdUnit(
        depth=0,
        snapshot=snapshot,
        source_event=_dependency_observation_event(
            event_id,
            "dependent.service",
            observed_at=observed_at,
        ),
        evidence=tuple(edges),
    )
    report = SystemdDependencyDiscoveryReport(
        root_requested_unit="dependent.service",
        root_canonical_unit="dependent.service",
        boot_id=boot_id,
        max_depth=1,
        max_units=64,
        units=(unit,),
        failures=(),
        truncated_by_depth=truncated,
        unexpanded_requirement_count=1 if truncated else 0,
    )
    return build_dependency_graph(report)


def _incident_evidence(
    *,
    unit: str,
    event_id: str,
    assessment_hex: str,
    incident_hex: str,
    boot_id: str = _BOOT,
    assessed_usec: int = 1_000_000,
    state_change_usec: int | None = 900_000,
    status: SystemdServiceHealthStatus = SystemdServiceHealthStatus.FAILED,
):
    event = _service_event(
        event_id=event_id,
        target=unit,
        boot_id=boot_id,
        state_change_usec=state_change_usec,
        status=status,
    )
    assessment = _assessment(
        event=event,
        assessment_hex=assessment_hex,
        assessed_usec=assessed_usec,
        state_change_usec=state_change_usec,
        status=status,
    )
    assessment_evidence = bind_systemd_assessment_evidence(assessment, event)
    return bind_systemd_incident_temporal_evidence(
        _incident(assessment=assessment, incident_hex=incident_hex),
        assessment_evidence,
    )


class DependencyPropagationTests(unittest.TestCase):
    def test_assessment_binding_captures_boot_and_state_change_basis(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        assessment = _assessment(event=event, assessment_hex="1")

        evidence = bind_systemd_assessment_evidence(assessment, event)

        self.assertEqual(evidence.boot_id, _BOOT)
        self.assertEqual(
            evidence.temporal_basis, TemporalEvidenceBasis.STATE_CHANGE_MONOTONIC
        )
        self.assertEqual(evidence.temporal_usec, 900_000)
        self.assertFalse(evidence.to_dict()["causal_claim"])

    def test_assessment_binding_falls_back_to_assessment_time_explicitly(self) -> None:
        event = _service_event(
            event_id="evt-a",
            target="a.service",
            state_change_usec=None,
        )
        assessment = _assessment(
            event=event,
            assessment_hex="2",
            assessed_usec=1_500_000,
            state_change_usec=None,
        )

        evidence = bind_systemd_assessment_evidence(assessment, event)

        self.assertEqual(
            evidence.temporal_basis, TemporalEvidenceBasis.ASSESSMENT_MONOTONIC
        )
        self.assertEqual(evidence.temporal_usec, 1_500_000)

    def test_assessment_binding_rejects_wrong_source_event_identity(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        assessment = _assessment(event=event, assessment_hex="3")
        wrong = replace(event, event_id="evt-b")

        with self.assertRaises(FaultPropagationEvidenceContractError):
            bind_systemd_assessment_evidence(assessment, wrong)

    def test_assessment_binding_rejects_invalid_boot_identity(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        assessment = _assessment(event=event, assessment_hex="4")
        wrong = replace(event, attributes={**event.attributes, "boot_id": "not-a-boot"})

        with self.assertRaises(FaultPropagationEvidenceContractError):
            bind_systemd_assessment_evidence(assessment, wrong)

    def test_assessment_binding_rejects_unit_drift(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        assessment = _assessment(event=event, assessment_hex="5")
        wrong = replace(
            event,
            attributes={**event.attributes, "canonical_name": "other.service"},
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            bind_systemd_assessment_evidence(assessment, wrong)

    def test_assessment_binding_rejects_state_change_drift(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        assessment = _assessment(event=event, assessment_hex="6")
        wrong = replace(
            event,
            attributes={**event.attributes, "state_change_monotonic_usec": 800_000},
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            bind_systemd_assessment_evidence(assessment, wrong)

    def test_assessment_binding_rejects_future_state_change_timestamp(self) -> None:
        event = _service_event(
            event_id="evt-a",
            target="a.service",
            state_change_usec=2_000_000,
        )
        assessment = _assessment(
            event=event,
            assessment_hex="7",
            assessed_usec=1_000_000,
            state_change_usec=2_000_000,
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            bind_systemd_assessment_evidence(assessment, event)

    def test_assessment_evidence_identity_drift_is_rejected(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        evidence = bind_systemd_assessment_evidence(
            _assessment(event=event, assessment_hex="8"),
            event,
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(evidence, evidence_id="asmev-" + "f" * 64)

    def test_assessment_evidence_identity_covers_assessment_content(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        evidence = bind_systemd_assessment_evidence(
            _assessment(event=event, assessment_hex="8"),
            event,
        )
        changed = replace(
            evidence.assessment,
            assessed_monotonic_usec=evidence.assessment.assessed_monotonic_usec + 1,
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(evidence, assessment=changed)

    def test_incident_binding_uses_opening_assessment_temporal_anchor(self) -> None:
        evidence = _incident_evidence(
            unit="a.service",
            event_id="evt-a",
            assessment_hex="9",
            incident_hex="a",
            assessed_usec=1_000_000,
            state_change_usec=850_000,
        )

        self.assertEqual(evidence.temporal_usec, 850_000)
        self.assertEqual(
            evidence.temporal_basis, TemporalEvidenceBasis.STATE_CHANGE_MONOTONIC
        )
        self.assertEqual(evidence.boot_id, _BOOT)

    def test_incident_binding_rejects_healthy_opening_assessment(self) -> None:
        event = _service_event(
            event_id="evt-a",
            target="a.service",
            status=SystemdServiceHealthStatus.HEALTHY,
        )
        assessment = _assessment(
            event=event,
            assessment_hex="a",
            status=SystemdServiceHealthStatus.HEALTHY,
        )
        assessment_evidence = bind_systemd_assessment_evidence(assessment, event)
        failed_event = _service_event(event_id="evt-f", target="a.service")
        failed_assessment = _assessment(event=failed_event, assessment_hex="b")
        incident = _incident(assessment=failed_assessment, incident_hex="b")

        with self.assertRaises(FaultPropagationEvidenceContractError):
            bind_systemd_incident_temporal_evidence(incident, assessment_evidence)

    def test_incident_binding_rejects_first_assessment_identity_drift(self) -> None:
        event = _service_event(event_id="evt-a", target="a.service")
        assessment = _assessment(event=event, assessment_hex="c")
        assessment_evidence = bind_systemd_assessment_evidence(assessment, event)
        incident = replace(
            _incident(assessment=assessment, incident_hex="c"),
            first_assessment_id="asmt-" + "d" * 64,
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            bind_systemd_incident_temporal_evidence(incident, assessment_evidence)

    def test_incident_temporal_evidence_identity_drift_is_rejected(self) -> None:
        evidence = _incident_evidence(
            unit="a.service",
            event_id="evt-a",
            assessment_hex="d",
            incident_hex="d",
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(evidence, evidence_id="inctev-" + "e" * 64)

    def test_candidate_builder_uses_only_requirement_relations(self) -> None:
        graph = _graph()

        candidates = build_dependency_propagation_candidates(graph)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].requirement_relation, DependencyRelation.REQUIRES
        )
        self.assertEqual(candidates[0].dependency_unit, "dependency.service")
        self.assertEqual(candidates[0].dependent_unit, "dependent.service")

    def test_candidate_builder_preserves_strong_requirement_semantics(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]

        self.assertEqual(
            candidate.requirement_semantic_class,
            DependencySemanticClass.STRONG_REQUIREMENT,
        )
        self.assertFalse(candidate.propagation_expectation_assigned)

    def test_candidate_builder_preserves_weak_requirement_semantics(self) -> None:
        candidate = build_dependency_propagation_candidates(
            _graph(relation=DependencyRelation.WANTS)
        )[0]

        self.assertEqual(candidate.requirement_relation, DependencyRelation.WANTS)
        self.assertEqual(
            candidate.requirement_semantic_class,
            DependencySemanticClass.WEAK_REQUIREMENT,
        )

    def test_candidate_ordering_context_preserves_only_real_edges(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]

        self.assertEqual(
            candidate.ordering_context,
            (("dependent.service", "after", "dependency.service"),),
        )
        payload = candidate.to_dict()
        self.assertTrue(payload["ordering_context_is_derived_index"])
        self.assertFalse(payload["topology_temporal_applicability_claim"])
        self.assertEqual(payload["requirement_observed_at"], _TIME.isoformat())

    def test_candidate_preserves_partial_graph_scope(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph(truncated=True))[0]

        self.assertFalse(candidate.graph_complete_within_scope)
        self.assertTrue(candidate.graph_truncated_by_depth)
        self.assertEqual(candidate.graph_failure_count, 0)

    def test_candidate_identity_is_stable_across_evidence_refresh(self) -> None:
        first = build_dependency_propagation_candidates(_graph(event_id="dep-a"))[0]
        second = build_dependency_propagation_candidates(
            _graph(event_id="dep-b", observed_at=_TIME + timedelta(seconds=1))
        )[0]

        self.assertEqual(first.topology_id, second.topology_id)
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertNotEqual(first.graph_version_id, second.graph_version_id)

    def test_candidate_identity_is_stable_across_boots_for_same_topology(self) -> None:
        first = build_dependency_propagation_candidates(_graph(boot_id=_BOOT))[0]
        second = build_dependency_propagation_candidates(_graph(boot_id=_OTHER_BOOT))[0]

        self.assertEqual(first.topology_id, second.topology_id)
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertNotEqual(first.boot_id, second.boot_id)

    def test_candidate_rejects_causal_or_propagation_expectation_claims(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]

        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(candidate, causal_claim=True)
        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(candidate, propagation_expectation_assigned=True)

    def test_pairwise_forward_sequence_within_window_is_descriptive_only(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="1",
            incident_hex="1",
            assessed_usec=1_000_000,
            state_change_usec=900_000,
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="2",
            incident_hex="2",
            assessed_usec=1_300_000,
            state_change_usec=1_200_000,
        )

        evidence = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            affected,
            analysis_window_usec=500_000,
        )

        self.assertEqual(evidence.signed_offset_usec, 300_000)
        self.assertEqual(
            evidence.finding, PairwiseTemporalFinding.FORWARD_WITHIN_WINDOW
        )
        self.assertEqual(
            evidence.interpretation,
            PairwiseTemporalInterpretation.CONSISTENT_WITH_CANDIDATE_DIRECTION,
        )
        self.assertFalse(evidence.causal_claim)

    def test_pairwise_forward_sequence_outside_window_is_explicit(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="3",
            incident_hex="3",
            state_change_usec=900_000,
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="4",
            incident_hex="4",
            assessed_usec=3_000_000,
            state_change_usec=2_900_000,
        )

        evidence = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            affected,
            analysis_window_usec=500_000,
        )

        self.assertEqual(
            evidence.finding, PairwiseTemporalFinding.FORWARD_OUTSIDE_WINDOW
        )
        self.assertEqual(
            evidence.interpretation,
            PairwiseTemporalInterpretation.OUTSIDE_ANALYSIS_WINDOW,
        )

    def test_pairwise_simultaneous_sequence_is_ambiguous(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="5",
            incident_hex="5",
            state_change_usec=900_000,
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="6",
            incident_hex="6",
            state_change_usec=900_000,
        )

        evidence = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            affected,
            analysis_window_usec=500_000,
        )

        self.assertEqual(evidence.signed_offset_usec, 0)
        self.assertEqual(evidence.finding, PairwiseTemporalFinding.SIMULTANEOUS)
        self.assertEqual(
            evidence.interpretation,
            PairwiseTemporalInterpretation.AMBIGUOUS_SIMULTANEOUS,
        )

    def test_pairwise_reverse_sequence_is_counterevidence(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="7",
            incident_hex="7",
            assessed_usec=1_300_000,
            state_change_usec=1_200_000,
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="8",
            incident_hex="8",
            state_change_usec=900_000,
        )

        evidence = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            affected,
            analysis_window_usec=500_000,
        )

        self.assertEqual(evidence.signed_offset_usec, -300_000)
        self.assertEqual(evidence.finding, PairwiseTemporalFinding.REVERSE_SEQUENCE)
        self.assertEqual(
            evidence.interpretation,
            PairwiseTemporalInterpretation.COUNTEREVIDENCE_TO_CANDIDATE_DIRECTION,
        )

    def test_pairwise_rejects_cross_boot_incidents(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="9",
            incident_hex="9",
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="a",
            incident_hex="a",
            boot_id=_OTHER_BOOT,
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            evaluate_pairwise_fault_propagation(
                candidate,
                source,
                affected,
                analysis_window_usec=500_000,
            )

    def test_pairwise_rejects_wrong_source_unit(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="other.service",
            event_id="evt-source",
            assessment_hex="b",
            incident_hex="b",
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="c",
            incident_hex="c",
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            evaluate_pairwise_fault_propagation(
                candidate,
                source,
                affected,
                analysis_window_usec=500_000,
            )

    def test_pairwise_rejects_wrong_affected_unit(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="d",
            incident_hex="d",
        )
        affected = _incident_evidence(
            unit="other.service",
            event_id="evt-affected",
            assessment_hex="e",
            incident_hex="e",
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            evaluate_pairwise_fault_propagation(
                candidate,
                source,
                affected,
                analysis_window_usec=500_000,
            )

    def test_analysis_window_rejects_boolean_and_zero(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="f",
            incident_hex="f",
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="0",
            incident_hex="0",
        )

        for invalid in (True, 0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(FaultPropagationEvidenceContractError):
                    evaluate_pairwise_fault_propagation(
                        candidate,
                        source,
                        affected,
                        analysis_window_usec=invalid,
                    )

    def test_pairwise_evidence_identity_drift_is_rejected(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="1",
            incident_hex="1",
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="2",
            incident_hex="2",
            state_change_usec=950_000,
        )
        evidence = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            affected,
            analysis_window_usec=500_000,
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(evidence, evidence_id="propev-" + "f" * 64)

    def test_pairwise_model_rejects_finding_or_interpretation_drift(self) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="3",
            incident_hex="3",
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="4",
            incident_hex="4",
            state_change_usec=950_000,
        )
        evidence = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            affected,
            analysis_window_usec=500_000,
        )

        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(evidence, finding=PairwiseTemporalFinding.REVERSE_SEQUENCE)
        with self.assertRaises(FaultPropagationEvidenceContractError):
            replace(
                evidence,
                interpretation=(
                    PairwiseTemporalInterpretation.COUNTEREVIDENCE_TO_CANDIDATE_DIRECTION
                ),
            )

    def test_pairwise_serialization_contains_no_causal_root_cause_or_confidence_claim(
        self,
    ) -> None:
        candidate = build_dependency_propagation_candidates(_graph())[0]
        source = _incident_evidence(
            unit="dependency.service",
            event_id="evt-source",
            assessment_hex="5",
            incident_hex="5",
        )
        affected = _incident_evidence(
            unit="dependent.service",
            event_id="evt-affected",
            assessment_hex="6",
            incident_hex="6",
            state_change_usec=950_000,
        )
        evidence = evaluate_pairwise_fault_propagation(
            candidate,
            source,
            affected,
            analysis_window_usec=500_000,
        )

        payload = evidence.to_dict()

        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["topology_temporal_applicability_claim"])
        self.assertFalse(payload["propagation_claim_assigned"])
        self.assertFalse(payload["root_cause_claim_assigned"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])
        self.assertNotIn("confidence", payload)
        self.assertNotIn("probability", payload)


if __name__ == "__main__":
    unittest.main()
