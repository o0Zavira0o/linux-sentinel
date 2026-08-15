"""Boot-aware temporal fault-propagation evidence for Sentinel-X Phase 5D.1.

This module deliberately stops short of causal inference.  It binds frozen
Phase 4 detector/incident artifacts back to their original systemd observation
so boot identity and timing provenance are explicit, derives propagation
*candidates* only from requirement dependencies, and classifies pairwise
incident ordering without assigning probability, confidence, or root cause.

Ordering dependencies are preserved only as contextual evidence.  They are not
silently converted into requirement edges or causal claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Mapping

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.dependency.graph import (
    DependencyGraphNodeObservation,
    DependencyGraphSnapshot,
)
from sentinel_x.dependency.models import DependencyRelation, DependencySemanticClass
from sentinel_x.detection.incidents import SystemdServiceIncident
from sentinel_x.detection.models import SystemdServiceHealthAssessment
from sentinel_x.systemd.boot import SystemBootIdError, normalize_boot_id
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

FAULT_PROPAGATION_EVIDENCE_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.fault-propagation-evidence.v1"
)

_ASSESSMENT_EVIDENCE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.fault-propagation-assessment-evidence.v1\x00"
)
_INCIDENT_EVIDENCE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.fault-propagation-incident-evidence.v1\x00"
)
_PROPAGATION_CANDIDATE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.fault-propagation-candidate.v1\x00"
)
_PROPAGATION_EVIDENCE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.fault-propagation-pairwise-evidence.v1\x00"
)

_ASSESSMENT_EVIDENCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"asmev-[0-9a-f]{64}"
)
_INCIDENT_EVIDENCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"inctev-[0-9a-f]{64}"
)
_PROPAGATION_CANDIDATE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"propcand-[0-9a-f]{64}"
)
_PROPAGATION_EVIDENCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"propev-[0-9a-f]{64}"
)


class FaultPropagationEvidenceError(RuntimeError):
    """Base error for Phase 5D temporal propagation evidence."""


class FaultPropagationEvidenceContractError(FaultPropagationEvidenceError):
    """Raised when evidence inputs violate a cross-phase identity contract."""


class TemporalEvidenceBasis(StrEnum):
    """Clock basis used to anchor one incident in boot-monotonic time."""

    STATE_CHANGE_MONOTONIC = "state_change_monotonic"
    ASSESSMENT_MONOTONIC = "assessment_monotonic"


class PairwiseTemporalFinding(StrEnum):
    """Descriptive ordering of two incidents for one dependency candidate."""

    FORWARD_WITHIN_WINDOW = "forward_within_window"
    FORWARD_OUTSIDE_WINDOW = "forward_outside_window"
    SIMULTANEOUS = "simultaneous"
    REVERSE_SEQUENCE = "reverse_sequence"


class PairwiseTemporalInterpretation(StrEnum):
    """Non-causal interpretation of a pairwise temporal finding."""

    CONSISTENT_WITH_CANDIDATE_DIRECTION = "consistent_with_candidate_direction"
    OUTSIDE_ANALYSIS_WINDOW = "outside_analysis_window"
    AMBIGUOUS_SIMULTANEOUS = "ambiguous_simultaneous"
    COUNTEREVIDENCE_TO_CANDIDATE_DIRECTION = "counterevidence_to_candidate_direction"


@dataclass(frozen=True, slots=True)
class SystemdAssessmentEvidence:
    """A detector assessment rebound to its original boot-aware observation."""

    evidence_id: str
    assessment: SystemdServiceHealthAssessment
    source_event_id: str
    boot_id: str

    def __post_init__(self) -> None:
        if _ASSESSMENT_EVIDENCE_ID_PATTERN.fullmatch(self.evidence_id) is None:
            raise FaultPropagationEvidenceContractError(
                "evidence_id must be an asmev- prefixed SHA-256 identity"
            )
        if not isinstance(self.assessment, SystemdServiceHealthAssessment):
            raise FaultPropagationEvidenceContractError(
                "assessment must be a SystemdServiceHealthAssessment"
            )
        _validate_text(self.source_event_id, field_name="source_event_id")
        if self.source_event_id != self.assessment.source_event_id:
            raise FaultPropagationEvidenceContractError(
                "source_event_id must match assessment source_event_id"
            )
        boot_id = _normalize_boot_id(self.boot_id)
        if boot_id != self.boot_id:
            raise FaultPropagationEvidenceContractError(
                "boot_id must already be normalized lowercase hexadecimal"
            )
        if (
            self.assessment.state_change_monotonic_usec is not None
            and self.assessment.state_change_monotonic_usec
            > self.assessment.assessed_monotonic_usec
        ):
            raise FaultPropagationEvidenceContractError(
                "state change timestamp cannot follow detector assessment time"
            )
        expected_id = _assessment_evidence_id(
            assessment=self.assessment,
            source_event_id=self.source_event_id,
            boot_id=self.boot_id,
        )
        if self.evidence_id != expected_id:
            raise FaultPropagationEvidenceContractError(
                "assessment evidence identity does not match its content"
            )

    @property
    def target_unit(self) -> str:
        return self.assessment.target_unit

    @property
    def canonical_unit(self) -> str:
        return self.assessment.canonical_unit

    @property
    def temporal_basis(self) -> TemporalEvidenceBasis:
        if self.assessment.state_change_monotonic_usec is not None:
            return TemporalEvidenceBasis.STATE_CHANGE_MONOTONIC
        return TemporalEvidenceBasis.ASSESSMENT_MONOTONIC

    @property
    def temporal_usec(self) -> int:
        if self.assessment.state_change_monotonic_usec is not None:
            return self.assessment.state_change_monotonic_usec
        return self.assessment.assessed_monotonic_usec

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": FAULT_PROPAGATION_EVIDENCE_SCHEMA_VERSION,
            "evidence_id": self.evidence_id,
            "assessment_id": self.assessment.assessment_id,
            "source_event_id": self.source_event_id,
            "boot_id": self.boot_id,
            "target_unit": self.target_unit,
            "canonical_unit": self.canonical_unit,
            "source_observed_at": self.assessment.source_observed_at.isoformat(),
            "assessed_at": self.assessment.assessed_at.isoformat(),
            "assessed_monotonic_usec": self.assessment.assessed_monotonic_usec,
            "state_change_monotonic_usec": (
                self.assessment.state_change_monotonic_usec
            ),
            "temporal_basis": self.temporal_basis.value,
            "temporal_usec": self.temporal_usec,
            "status": self.assessment.status.value,
            "anomaly_class": (
                None
                if self.assessment.anomaly_class is None
                else self.assessment.anomaly_class.value
            ),
            "causal_claim": False,
            "probabilistic_confidence_assigned": False,
        }


@dataclass(frozen=True, slots=True)
class SystemdIncidentTemporalEvidence:
    """One incident episode anchored to its first boot-aware assessment evidence."""

    evidence_id: str
    incident: SystemdServiceIncident
    opening_assessment: SystemdAssessmentEvidence

    def __post_init__(self) -> None:
        if _INCIDENT_EVIDENCE_ID_PATTERN.fullmatch(self.evidence_id) is None:
            raise FaultPropagationEvidenceContractError(
                "evidence_id must be an inctev- prefixed SHA-256 identity"
            )
        if not isinstance(self.incident, SystemdServiceIncident):
            raise FaultPropagationEvidenceContractError(
                "incident must be a SystemdServiceIncident"
            )
        if not isinstance(self.opening_assessment, SystemdAssessmentEvidence):
            raise FaultPropagationEvidenceContractError(
                "opening_assessment must be SystemdAssessmentEvidence"
            )
        assessment = self.opening_assessment.assessment
        if not assessment.is_anomalous:
            raise FaultPropagationEvidenceContractError(
                "incident opening assessment must be anomalous"
            )
        if assessment.anomaly_class is None or assessment.severity is None:
            raise FaultPropagationEvidenceContractError(
                "incident opening assessment anomaly metadata is incomplete"
            )
        comparisons = (
            ("target unit", self.incident.target_unit, assessment.target_unit),
            (
                "canonical unit",
                self.incident.canonical_unit,
                assessment.canonical_unit,
            ),
            (
                "first assessment ID",
                self.incident.first_assessment_id,
                assessment.assessment_id,
            ),
            (
                "first source event ID",
                self.incident.first_source_event_id,
                assessment.source_event_id,
            ),
            (
                "anomaly class",
                self.incident.anomaly_class,
                assessment.anomaly_class,
            ),
            ("severity", self.incident.severity, assessment.severity),
            ("opened_at", self.incident.opened_at, assessment.assessed_at),
            (
                "opened monotonic time",
                self.incident.opened_monotonic_usec,
                assessment.assessed_monotonic_usec,
            ),
        )
        for label, incident_value, assessment_value in comparisons:
            if incident_value != assessment_value:
                raise FaultPropagationEvidenceContractError(
                    f"incident {label} does not match its opening assessment"
                )
        expected_id = _incident_evidence_id(
            incident_id=self.incident.incident_id,
            assessment_evidence_id=self.opening_assessment.evidence_id,
        )
        if self.evidence_id != expected_id:
            raise FaultPropagationEvidenceContractError(
                "incident temporal evidence identity does not match its content"
            )

    @property
    def boot_id(self) -> str:
        return self.opening_assessment.boot_id

    @property
    def canonical_unit(self) -> str:
        return self.incident.canonical_unit

    @property
    def temporal_basis(self) -> TemporalEvidenceBasis:
        return self.opening_assessment.temporal_basis

    @property
    def temporal_usec(self) -> int:
        return self.opening_assessment.temporal_usec

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": FAULT_PROPAGATION_EVIDENCE_SCHEMA_VERSION,
            "evidence_id": self.evidence_id,
            "incident_id": self.incident.incident_id,
            "boot_id": self.boot_id,
            "target_unit": self.incident.target_unit,
            "canonical_unit": self.canonical_unit,
            "anomaly_class": self.incident.anomaly_class.value,
            "opened_at": self.incident.opened_at.isoformat(),
            "opened_monotonic_usec": self.incident.opened_monotonic_usec,
            "opening_assessment_evidence_id": self.opening_assessment.evidence_id,
            "temporal_basis": self.temporal_basis.value,
            "temporal_usec": self.temporal_usec,
            "causal_claim": False,
            "probabilistic_confidence_assigned": False,
        }


@dataclass(frozen=True, slots=True)
class DependencyPropagationCandidate:
    """One requirement-edge candidate direction for temporal propagation study.

    The candidate direction is dependency object -> dependent subject.  This is
    an analysis orientation, not a claim that a failure must propagate.
    """

    candidate_id: str
    graph_version_id: str
    topology_id: str
    boot_id: str
    dependency_unit: str
    dependent_unit: str
    requirement_relation: DependencyRelation
    requirement_evidence_id: str
    requirement_observed_at: datetime
    dependency_node_observation: DependencyGraphNodeObservation
    dependent_node_observation: DependencyGraphNodeObservation
    ordering_context: tuple[tuple[str, str, str], ...]
    graph_complete_within_scope: bool
    graph_truncated_by_depth: bool
    graph_failure_count: int
    causal_claim: bool = False
    propagation_expectation_assigned: bool = False

    def __post_init__(self) -> None:
        if _PROPAGATION_CANDIDATE_ID_PATTERN.fullmatch(self.candidate_id) is None:
            raise FaultPropagationEvidenceContractError(
                "candidate_id must be a propcand- prefixed SHA-256 identity"
            )
        _validate_text(self.graph_version_id, field_name="graph_version_id")
        _validate_text(self.topology_id, field_name="topology_id")
        boot_id = _normalize_boot_id(self.boot_id)
        if boot_id != self.boot_id:
            raise FaultPropagationEvidenceContractError(
                "candidate boot_id must already be normalized"
            )
        _validate_text(self.dependency_unit, field_name="dependency_unit")
        _validate_text(self.dependent_unit, field_name="dependent_unit")
        if self.dependency_unit == self.dependent_unit:
            raise FaultPropagationEvidenceContractError(
                "dependency and dependent units must be distinct"
            )
        if self.requirement_relation not in {
            DependencyRelation.REQUIRES,
            DependencyRelation.WANTS,
        }:
            raise FaultPropagationEvidenceContractError(
                "propagation candidates require Requires or Wants evidence"
            )
        _validate_text(
            self.requirement_evidence_id,
            field_name="requirement_evidence_id",
        )
        _validate_aware_datetime(
            self.requirement_observed_at,
            field_name="requirement_observed_at",
        )
        if not isinstance(
            self.dependency_node_observation,
            DependencyGraphNodeObservation,
        ):
            raise FaultPropagationEvidenceContractError(
                "dependency_node_observation must be typed"
            )
        if not isinstance(
            self.dependent_node_observation,
            DependencyGraphNodeObservation,
        ):
            raise FaultPropagationEvidenceContractError(
                "dependent_node_observation must be typed"
            )
        if not isinstance(self.ordering_context, tuple):
            raise FaultPropagationEvidenceContractError(
                "ordering_context must be a tuple"
            )
        if tuple(sorted(set(self.ordering_context))) != self.ordering_context:
            raise FaultPropagationEvidenceContractError(
                "ordering_context must be sorted and unique"
            )
        for key in self.ordering_context:
            _validate_ordering_key(
                key,
                dependency_unit=self.dependency_unit,
                dependent_unit=self.dependent_unit,
            )
        if type(self.graph_complete_within_scope) is not bool:
            raise FaultPropagationEvidenceContractError(
                "graph_complete_within_scope must be a boolean"
            )
        if type(self.graph_truncated_by_depth) is not bool:
            raise FaultPropagationEvidenceContractError(
                "graph_truncated_by_depth must be a boolean"
            )
        _validate_nonnegative_int(
            self.graph_failure_count,
            field_name="graph_failure_count",
        )
        if type(self.causal_claim) is not bool or self.causal_claim:
            raise FaultPropagationEvidenceContractError(
                "Phase 5D candidates must keep causal_claim=False"
            )
        if (
            type(self.propagation_expectation_assigned) is not bool
            or self.propagation_expectation_assigned
        ):
            raise FaultPropagationEvidenceContractError(
                "Phase 5D candidates must not assign propagation expectation"
            )
        expected_id = _propagation_candidate_id(
            topology_id=self.topology_id,
            dependency_unit=self.dependency_unit,
            dependent_unit=self.dependent_unit,
            requirement_relation=self.requirement_relation,
        )
        if self.candidate_id != expected_id:
            raise FaultPropagationEvidenceContractError(
                "propagation candidate identity does not match its topology"
            )

    @property
    def requirement_semantic_class(self) -> DependencySemanticClass:
        return self.requirement_relation.semantic_class

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": FAULT_PROPAGATION_EVIDENCE_SCHEMA_VERSION,
            "candidate_id": self.candidate_id,
            "graph_version_id": self.graph_version_id,
            "topology_id": self.topology_id,
            "boot_id": self.boot_id,
            "dependency_unit": self.dependency_unit,
            "dependent_unit": self.dependent_unit,
            "requirement_relation": self.requirement_relation.value,
            "requirement_semantic_class": self.requirement_semantic_class.value,
            "requirement_evidence_id": self.requirement_evidence_id,
            "requirement_observed_at": self.requirement_observed_at.isoformat(),
            "topology_temporal_applicability_claim": False,
            "dependency_node_observation": self.dependency_node_observation.value,
            "dependent_node_observation": self.dependent_node_observation.value,
            "ordering_context": [list(key) for key in self.ordering_context],
            "ordering_context_is_derived_index": True,
            "graph_complete_within_scope": self.graph_complete_within_scope,
            "graph_truncated_by_depth": self.graph_truncated_by_depth,
            "graph_failure_count": self.graph_failure_count,
            "causal_claim": False,
            "propagation_expectation_assigned": False,
            "probabilistic_confidence_assigned": False,
        }


@dataclass(frozen=True, slots=True)
class PairwiseFaultPropagationEvidence:
    """Descriptive temporal relation between two incident episodes."""

    evidence_id: str
    candidate: DependencyPropagationCandidate
    source_incident: SystemdIncidentTemporalEvidence
    affected_incident: SystemdIncidentTemporalEvidence
    analysis_window_usec: int
    signed_offset_usec: int
    finding: PairwiseTemporalFinding
    interpretation: PairwiseTemporalInterpretation
    causal_claim: bool = False
    probabilistic_confidence_assigned: bool = False

    def __post_init__(self) -> None:
        if _PROPAGATION_EVIDENCE_ID_PATTERN.fullmatch(self.evidence_id) is None:
            raise FaultPropagationEvidenceContractError(
                "evidence_id must be a propev- prefixed SHA-256 identity"
            )
        if not isinstance(self.candidate, DependencyPropagationCandidate):
            raise FaultPropagationEvidenceContractError(
                "candidate must be a DependencyPropagationCandidate"
            )
        if not isinstance(self.source_incident, SystemdIncidentTemporalEvidence):
            raise FaultPropagationEvidenceContractError(
                "source_incident must be SystemdIncidentTemporalEvidence"
            )
        if not isinstance(self.affected_incident, SystemdIncidentTemporalEvidence):
            raise FaultPropagationEvidenceContractError(
                "affected_incident must be SystemdIncidentTemporalEvidence"
            )
        _validate_analysis_window(self.analysis_window_usec)
        _validate_signed_int(self.signed_offset_usec, field_name="signed_offset_usec")
        if not isinstance(self.finding, PairwiseTemporalFinding):
            raise FaultPropagationEvidenceContractError("finding must be typed")
        if not isinstance(self.interpretation, PairwiseTemporalInterpretation):
            raise FaultPropagationEvidenceContractError("interpretation must be typed")
        if type(self.causal_claim) is not bool or self.causal_claim:
            raise FaultPropagationEvidenceContractError(
                "Phase 5D pairwise evidence must keep causal_claim=False"
            )
        if (
            type(self.probabilistic_confidence_assigned) is not bool
            or self.probabilistic_confidence_assigned
        ):
            raise FaultPropagationEvidenceContractError(
                "Phase 5D pairwise evidence must not assign confidence"
            )
        self._validate_cross_evidence_contract()
        expected_finding, expected_interpretation = _classify_pairwise_offset(
            self.signed_offset_usec,
            analysis_window_usec=self.analysis_window_usec,
        )
        if self.finding is not expected_finding:
            raise FaultPropagationEvidenceContractError(
                "pairwise finding does not match signed temporal offset"
            )
        if self.interpretation is not expected_interpretation:
            raise FaultPropagationEvidenceContractError(
                "pairwise interpretation does not match temporal finding"
            )
        expected_id = _pairwise_evidence_id(
            candidate=self.candidate,
            source_incident=self.source_incident,
            affected_incident=self.affected_incident,
            analysis_window_usec=self.analysis_window_usec,
            signed_offset_usec=self.signed_offset_usec,
        )
        if self.evidence_id != expected_id:
            raise FaultPropagationEvidenceContractError(
                "pairwise evidence identity does not match its content"
            )

    def _validate_cross_evidence_contract(self) -> None:
        if self.source_incident.boot_id != self.candidate.boot_id:
            raise FaultPropagationEvidenceContractError(
                "source incident boot does not match candidate graph boot"
            )
        if self.affected_incident.boot_id != self.candidate.boot_id:
            raise FaultPropagationEvidenceContractError(
                "affected incident boot does not match candidate graph boot"
            )
        if self.source_incident.canonical_unit != self.candidate.dependency_unit:
            raise FaultPropagationEvidenceContractError(
                "source incident unit does not match candidate dependency unit"
            )
        if self.affected_incident.canonical_unit != self.candidate.dependent_unit:
            raise FaultPropagationEvidenceContractError(
                "affected incident unit does not match candidate dependent unit"
            )
        expected_offset = (
            self.affected_incident.temporal_usec - self.source_incident.temporal_usec
        )
        if self.signed_offset_usec != expected_offset:
            raise FaultPropagationEvidenceContractError(
                "signed_offset_usec does not match incident temporal anchors"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": FAULT_PROPAGATION_EVIDENCE_SCHEMA_VERSION,
            "evidence_id": self.evidence_id,
            "candidate_id": self.candidate.candidate_id,
            "graph_version_id": self.candidate.graph_version_id,
            "topology_id": self.candidate.topology_id,
            "boot_id": self.candidate.boot_id,
            "dependency_unit": self.candidate.dependency_unit,
            "dependent_unit": self.candidate.dependent_unit,
            "requirement_relation": self.candidate.requirement_relation.value,
            "source_incident_id": self.source_incident.incident.incident_id,
            "affected_incident_id": self.affected_incident.incident.incident_id,
            "source_temporal_basis": self.source_incident.temporal_basis.value,
            "affected_temporal_basis": self.affected_incident.temporal_basis.value,
            "source_temporal_usec": self.source_incident.temporal_usec,
            "affected_temporal_usec": self.affected_incident.temporal_usec,
            "analysis_window_usec": self.analysis_window_usec,
            "signed_offset_usec": self.signed_offset_usec,
            "finding": self.finding.value,
            "interpretation": self.interpretation.value,
            "ordering_context": [list(key) for key in self.candidate.ordering_context],
            "requirement_observed_at": (
                self.candidate.requirement_observed_at.isoformat()
            ),
            "topology_temporal_applicability_claim": False,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
        }


def bind_systemd_assessment_evidence(
    assessment: SystemdServiceHealthAssessment,
    source_event: SentinelEvent,
) -> SystemdAssessmentEvidence:
    """Bind one Phase 4 assessment to its exact source observation and boot."""

    if not isinstance(assessment, SystemdServiceHealthAssessment):
        raise FaultPropagationEvidenceContractError(
            "assessment must be a SystemdServiceHealthAssessment"
        )
    if not isinstance(source_event, SentinelEvent):
        raise FaultPropagationEvidenceContractError(
            "source_event must be a SentinelEvent"
        )
    if source_event.kind is not EventKind.OBSERVATION:
        raise FaultPropagationEvidenceContractError(
            "source_event must be an observation"
        )
    if source_event.source != SYSTEMD_SERVICE_OBSERVATION_SOURCE:
        raise FaultPropagationEvidenceContractError(
            "source_event has an unexpected source"
        )
    if (
        source_event.attributes.get("observation_type")
        != SYSTEMD_SERVICE_OBSERVATION_TYPE
    ):
        raise FaultPropagationEvidenceContractError(
            "source_event has an unexpected observation type"
        )
    if source_event.event_id != assessment.source_event_id:
        raise FaultPropagationEvidenceContractError(
            "assessment source_event_id does not match source_event"
        )
    if source_event.occurred_at != assessment.source_observed_at:
        raise FaultPropagationEvidenceContractError(
            "assessment source timestamp does not match source_event"
        )

    attributes = source_event.attributes
    boot_id = _normalize_boot_id(_required_text_attribute(attributes, "boot_id"))
    _require_attribute_equal(
        attributes,
        "requested_name",
        assessment.target_unit,
    )
    _require_attribute_equal(
        attributes,
        "canonical_name",
        assessment.canonical_unit,
    )
    _require_attribute_equal(attributes, "load_state", assessment.load_state)
    _require_attribute_equal(attributes, "active_state", assessment.active_state)
    _require_attribute_equal(attributes, "sub_state", assessment.sub_state)
    _require_attribute_equal(attributes, "main_pid", assessment.main_pid)
    _require_attribute_equal(attributes, "result", assessment.result)
    _require_attribute_equal(
        attributes,
        "state_change_monotonic_usec",
        assessment.state_change_monotonic_usec,
    )

    evidence_id = _assessment_evidence_id(
        assessment=assessment,
        source_event_id=source_event.event_id,
        boot_id=boot_id,
    )
    return SystemdAssessmentEvidence(
        evidence_id=evidence_id,
        assessment=assessment,
        source_event_id=source_event.event_id,
        boot_id=boot_id,
    )


def bind_systemd_incident_temporal_evidence(
    incident: SystemdServiceIncident,
    opening_assessment: SystemdAssessmentEvidence,
) -> SystemdIncidentTemporalEvidence:
    """Bind one incident episode to its first assessment evidence."""

    if not isinstance(incident, SystemdServiceIncident):
        raise FaultPropagationEvidenceContractError(
            "incident must be a SystemdServiceIncident"
        )
    if not isinstance(opening_assessment, SystemdAssessmentEvidence):
        raise FaultPropagationEvidenceContractError(
            "opening_assessment must be SystemdAssessmentEvidence"
        )
    evidence_id = _incident_evidence_id(
        incident_id=incident.incident_id,
        assessment_evidence_id=opening_assessment.evidence_id,
    )
    return SystemdIncidentTemporalEvidence(
        evidence_id=evidence_id,
        incident=incident,
        opening_assessment=opening_assessment,
    )


def build_dependency_propagation_candidates(
    graph: DependencyGraphSnapshot,
) -> tuple[DependencyPropagationCandidate, ...]:
    """Derive requirement-edge analysis candidates from one graph version.

    Only Requires/Wants edges create candidates.  After/Before edges remain
    contextual ordering evidence and are never promoted to requirement edges.
    """

    if not isinstance(graph, DependencyGraphSnapshot):
        raise FaultPropagationEvidenceContractError(
            "graph must be a DependencyGraphSnapshot"
        )
    node_states = {node.identity: node.observation for node in graph.nodes}
    ordering_edges = tuple(
        edge
        for edge in graph.edges
        if edge.relation in {DependencyRelation.AFTER, DependencyRelation.BEFORE}
    )
    candidates: list[DependencyPropagationCandidate] = []
    for edge in graph.edges:
        if edge.relation not in {
            DependencyRelation.REQUIRES,
            DependencyRelation.WANTS,
        }:
            continue
        dependent_unit = edge.subject_identity
        dependency_unit = edge.object_identity
        ordering_context = tuple(
            sorted(
                ordering.topology_key
                for ordering in ordering_edges
                if {
                    ordering.subject_identity,
                    ordering.object_identity,
                }
                == {dependency_unit, dependent_unit}
            )
        )
        candidate_id = _propagation_candidate_id(
            topology_id=graph.topology_id,
            dependency_unit=dependency_unit,
            dependent_unit=dependent_unit,
            requirement_relation=edge.relation,
        )
        candidates.append(
            DependencyPropagationCandidate(
                candidate_id=candidate_id,
                graph_version_id=graph.graph_version_id,
                topology_id=graph.topology_id,
                boot_id=graph.boot_id,
                dependency_unit=dependency_unit,
                dependent_unit=dependent_unit,
                requirement_relation=edge.relation,
                requirement_evidence_id=edge.evidence.evidence_id,
                requirement_observed_at=edge.evidence.observed_at,
                dependency_node_observation=node_states[dependency_unit],
                dependent_node_observation=node_states[dependent_unit],
                ordering_context=ordering_context,
                graph_complete_within_scope=graph.complete_within_scope,
                graph_truncated_by_depth=graph.truncated_by_depth,
                graph_failure_count=len(graph.failures),
            )
        )
    return tuple(sorted(candidates, key=lambda candidate: candidate.candidate_id))


def evaluate_pairwise_fault_propagation(
    candidate: DependencyPropagationCandidate,
    source_incident: SystemdIncidentTemporalEvidence,
    affected_incident: SystemdIncidentTemporalEvidence,
    *,
    analysis_window_usec: int,
) -> PairwiseFaultPropagationEvidence:
    """Classify pairwise temporal ordering without making a causal claim."""

    if not isinstance(candidate, DependencyPropagationCandidate):
        raise FaultPropagationEvidenceContractError(
            "candidate must be a DependencyPropagationCandidate"
        )
    if not isinstance(source_incident, SystemdIncidentTemporalEvidence):
        raise FaultPropagationEvidenceContractError(
            "source_incident must be SystemdIncidentTemporalEvidence"
        )
    if not isinstance(affected_incident, SystemdIncidentTemporalEvidence):
        raise FaultPropagationEvidenceContractError(
            "affected_incident must be SystemdIncidentTemporalEvidence"
        )
    _validate_analysis_window(analysis_window_usec)
    signed_offset_usec = affected_incident.temporal_usec - source_incident.temporal_usec
    finding, interpretation = _classify_pairwise_offset(
        signed_offset_usec,
        analysis_window_usec=analysis_window_usec,
    )
    evidence_id = _pairwise_evidence_id(
        candidate=candidate,
        source_incident=source_incident,
        affected_incident=affected_incident,
        analysis_window_usec=analysis_window_usec,
        signed_offset_usec=signed_offset_usec,
    )
    return PairwiseFaultPropagationEvidence(
        evidence_id=evidence_id,
        candidate=candidate,
        source_incident=source_incident,
        affected_incident=affected_incident,
        analysis_window_usec=analysis_window_usec,
        signed_offset_usec=signed_offset_usec,
        finding=finding,
        interpretation=interpretation,
    )


def _classify_pairwise_offset(
    signed_offset_usec: int,
    *,
    analysis_window_usec: int,
) -> tuple[PairwiseTemporalFinding, PairwiseTemporalInterpretation]:
    if signed_offset_usec < 0:
        return (
            PairwiseTemporalFinding.REVERSE_SEQUENCE,
            PairwiseTemporalInterpretation.COUNTEREVIDENCE_TO_CANDIDATE_DIRECTION,
        )
    if signed_offset_usec == 0:
        return (
            PairwiseTemporalFinding.SIMULTANEOUS,
            PairwiseTemporalInterpretation.AMBIGUOUS_SIMULTANEOUS,
        )
    if signed_offset_usec <= analysis_window_usec:
        return (
            PairwiseTemporalFinding.FORWARD_WITHIN_WINDOW,
            PairwiseTemporalInterpretation.CONSISTENT_WITH_CANDIDATE_DIRECTION,
        )
    return (
        PairwiseTemporalFinding.FORWARD_OUTSIDE_WINDOW,
        PairwiseTemporalInterpretation.OUTSIDE_ANALYSIS_WINDOW,
    )


def _assessment_evidence_id(
    *,
    assessment: SystemdServiceHealthAssessment,
    source_event_id: str,
    boot_id: str,
) -> str:
    payload: dict[str, object] = {
        "assessment_id": assessment.assessment_id,
        "source_event_id": source_event_id,
        "boot_id": boot_id,
        "target_unit": assessment.target_unit,
        "canonical_unit": assessment.canonical_unit,
        "source_observed_at": assessment.source_observed_at.isoformat(),
        "assessed_at": assessment.assessed_at.isoformat(),
        "assessed_monotonic_usec": assessment.assessed_monotonic_usec,
        "state_change_monotonic_usec": assessment.state_change_monotonic_usec,
        "load_state": assessment.load_state,
        "active_state": assessment.active_state,
        "sub_state": assessment.sub_state,
        "main_pid": assessment.main_pid,
        "result": assessment.result,
        "status": assessment.status.value,
        "anomaly_class": (
            None if assessment.anomaly_class is None else assessment.anomaly_class.value
        ),
        "severity": None if assessment.severity is None else assessment.severity.value,
        "basis": assessment.basis.value,
    }
    return "asmev-" + _sha256_payload(_ASSESSMENT_EVIDENCE_ID_DOMAIN, payload)


def _incident_evidence_id(
    *,
    incident_id: str,
    assessment_evidence_id: str,
) -> str:
    payload = {
        "incident_id": incident_id,
        "assessment_evidence_id": assessment_evidence_id,
    }
    return "inctev-" + _sha256_payload(_INCIDENT_EVIDENCE_ID_DOMAIN, payload)


def _propagation_candidate_id(
    *,
    topology_id: str,
    dependency_unit: str,
    dependent_unit: str,
    requirement_relation: DependencyRelation,
) -> str:
    payload = {
        "topology_id": topology_id,
        "dependency_unit": dependency_unit,
        "dependent_unit": dependent_unit,
        "requirement_relation": requirement_relation.value,
    }
    return "propcand-" + _sha256_payload(_PROPAGATION_CANDIDATE_ID_DOMAIN, payload)


def _pairwise_evidence_id(
    *,
    candidate: DependencyPropagationCandidate,
    source_incident: SystemdIncidentTemporalEvidence,
    affected_incident: SystemdIncidentTemporalEvidence,
    analysis_window_usec: int,
    signed_offset_usec: int,
) -> str:
    payload = {
        "candidate_id": candidate.candidate_id,
        "graph_version_id": candidate.graph_version_id,
        "source_incident_evidence_id": source_incident.evidence_id,
        "affected_incident_evidence_id": affected_incident.evidence_id,
        "analysis_window_usec": analysis_window_usec,
        "signed_offset_usec": signed_offset_usec,
    }
    return "propev-" + _sha256_payload(_PROPAGATION_EVIDENCE_ID_DOMAIN, payload)


def _sha256_payload(domain: bytes, payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(encoded)
    return digest.hexdigest()


def _required_text_attribute(attributes: Mapping[str, object], key: str) -> str:
    if key not in attributes:
        raise FaultPropagationEvidenceContractError(
            f"source_event is missing required attribute {key}"
        )
    value = attributes[key]
    if not isinstance(value, str) or not value:
        raise FaultPropagationEvidenceContractError(
            f"source_event attribute {key} must be non-empty text"
        )
    return value


def _require_attribute_equal(
    attributes: Mapping[str, object],
    key: str,
    expected: object,
) -> None:
    if key not in attributes:
        raise FaultPropagationEvidenceContractError(
            f"source_event is missing required attribute {key}"
        )
    if attributes[key] != expected:
        raise FaultPropagationEvidenceContractError(
            f"source_event attribute {key} does not match assessment"
        )


def _normalize_boot_id(value: object) -> str:
    try:
        return normalize_boot_id(value, field_name="boot_id")
    except SystemBootIdError as exc:
        raise FaultPropagationEvidenceContractError(str(exc)) from exc


def _validate_ordering_key(
    key: object,
    *,
    dependency_unit: str,
    dependent_unit: str,
) -> None:
    if not isinstance(key, tuple) or len(key) != 3:
        raise FaultPropagationEvidenceContractError(
            "ordering context entries must be three-part topology keys"
        )
    subject, relation, object_unit = key
    if not all(isinstance(value, str) and value for value in key):
        raise FaultPropagationEvidenceContractError(
            "ordering context topology keys must contain non-empty text"
        )
    if relation not in {
        DependencyRelation.AFTER.value,
        DependencyRelation.BEFORE.value,
    }:
        raise FaultPropagationEvidenceContractError(
            "ordering context may contain only After/Before edges"
        )
    if {subject, object_unit} != {dependency_unit, dependent_unit}:
        raise FaultPropagationEvidenceContractError(
            "ordering context must refer to the candidate unit pair"
        )


def _validate_analysis_window(value: object) -> None:
    _validate_positive_int(value, field_name="analysis_window_usec")


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise FaultPropagationEvidenceContractError(
            f"{field_name} must be non-empty text"
        )


def _validate_nonnegative_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FaultPropagationEvidenceContractError(
            f"{field_name} must be a non-negative integer"
        )


def _validate_positive_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FaultPropagationEvidenceContractError(
            f"{field_name} must be a positive integer"
        )


def _validate_aware_datetime(value: object, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise FaultPropagationEvidenceContractError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FaultPropagationEvidenceContractError(
            f"{field_name} must be timezone-aware"
        )


def _validate_signed_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FaultPropagationEvidenceContractError(f"{field_name} must be an integer")
