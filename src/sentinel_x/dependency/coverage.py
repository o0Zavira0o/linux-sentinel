"""Bounded sampling coverage and negative propagation evidence for Phase 5D.2.

This module does not prove that propagation did or did not occur continuously.
It evaluates only what the frozen detector actually sampled after one source
incident anchor, while keeping topology, boot identity, sampling gaps, detector
UNASSESSED outcomes, and source timing quality explicit.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Iterable

from sentinel_x.dependency.propagation import (
    DependencyPropagationCandidate,
    SystemdAssessmentEvidence,
    SystemdIncidentTemporalEvidence,
    TemporalEvidenceBasis,
)
from sentinel_x.detection.models import SystemdServiceHealthStatus
from sentinel_x.systemd.models import SystemdUnitNameError, validate_service_unit_name

PROPAGATION_COVERAGE_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.propagation-coverage-evidence.v1"
)
DEFAULT_MAX_COVERAGE_INPUT_ASSESSMENTS: Final[int] = 4096
MAX_COVERAGE_INPUT_ASSESSMENTS: Final[int] = 65536

_COVERAGE_EVIDENCE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.propagation-coverage-evidence.v1\x00"
)
_COVERAGE_EVIDENCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"propcov-[0-9a-f]{64}"
)


class PropagationCoverageError(RuntimeError):
    """Base error for Phase 5D.2 coverage evidence."""


class PropagationCoverageContractError(PropagationCoverageError):
    """Raised when coverage inputs violate typed cross-phase contracts."""


class PropagationCoverageCapacityError(PropagationCoverageError):
    """Raised when bounded coverage input capacity is exceeded."""


class PropagationEndpointObservability(StrEnum):
    """Whether current Phase 4 detector contracts can observe both endpoints."""

    SERVICE_TO_SERVICE_SUPPORTED = "service_to_service_supported"
    CURRENT_DETECTOR_SCOPE_UNSUPPORTED = "current_detector_scope_unsupported"


class SamplingCoverageStatus(StrEnum):
    """Observed sampling density inside the declared analysis window."""

    NO_IN_WINDOW_SAMPLES = "no_in_window_samples"
    BOUNDED = "bounded"
    GAP_BOUND_EXCEEDED = "gap_bound_exceeded"


class AssessmentCoverageOutcome(StrEnum):
    """Detector outcomes actually sampled for the dependent unit."""

    NO_IN_WINDOW_SAMPLES = "no_in_window_samples"
    ALL_HEALTHY = "all_healthy"
    UNASSESSED_PRESENT = "unassessed_present"
    ANOMALY_OBSERVED = "anomaly_observed"


class PropagationCoverageInterpretation(StrEnum):
    """Non-causal interpretation of bounded sampled evidence."""

    NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING = (
        "negative_evidence_within_bounded_sampling"
    )
    AFFECTED_ANOMALY_OBSERVED_IN_WINDOW = "affected_anomaly_observed_in_window"
    INSUFFICIENT_SAMPLING_COVERAGE = "insufficient_sampling_coverage"
    UNASSESSED_COVERAGE = "unassessed_coverage"
    SOURCE_TRANSITION_TIMING_LIMITED = "source_transition_timing_limited"
    CURRENT_DETECTOR_SCOPE_UNSUPPORTED = "current_detector_scope_unsupported"


@dataclass(frozen=True, slots=True)
class PropagationCoverageEvidence:
    """Sample-bounded evidence for one dependency candidate after a source incident.

    Assessment sample time always uses ``assessed_monotonic_usec`` because this
    structure describes observation coverage, not transition timing.  A healthy
    sample therefore means only that the detector assessed HEALTHY at that sample
    instant; continuous health is never inferred between samples.
    """

    evidence_id: str
    candidate: DependencyPropagationCandidate
    source_incident: SystemdIncidentTemporalEvidence
    analysis_window_usec: int
    max_sample_gap_usec: int
    max_input_assessments: int
    input_assessment_count: int
    in_window_assessments: tuple[SystemdAssessmentEvidence, ...]
    sampling_coverage: SamplingCoverageStatus
    assessment_outcome: AssessmentCoverageOutcome
    interpretation: PropagationCoverageInterpretation
    largest_observed_gap_usec: int | None
    healthy_count: int
    inactive_count: int
    failed_count: int
    unassessed_count: int

    def __post_init__(self) -> None:
        if _COVERAGE_EVIDENCE_ID_PATTERN.fullmatch(self.evidence_id) is None:
            raise PropagationCoverageContractError(
                "evidence_id must be a propcov- prefixed SHA-256 identity"
            )
        if not isinstance(self.candidate, DependencyPropagationCandidate):
            raise PropagationCoverageContractError(
                "candidate must be a DependencyPropagationCandidate"
            )
        if not isinstance(self.source_incident, SystemdIncidentTemporalEvidence):
            raise PropagationCoverageContractError(
                "source_incident must be SystemdIncidentTemporalEvidence"
            )
        _validate_positive_int(
            self.analysis_window_usec, field_name="analysis_window_usec"
        )
        _validate_positive_int(
            self.max_sample_gap_usec, field_name="max_sample_gap_usec"
        )
        _validate_capacity(self.max_input_assessments)
        _validate_nonnegative_int(
            self.input_assessment_count,
            field_name="input_assessment_count",
        )
        if self.input_assessment_count > self.max_input_assessments:
            raise PropagationCoverageContractError(
                "input_assessment_count exceeds max_input_assessments"
            )
        if not isinstance(self.in_window_assessments, tuple):
            raise PropagationCoverageContractError(
                "in_window_assessments must be a tuple"
            )
        if len(self.in_window_assessments) > self.input_assessment_count:
            raise PropagationCoverageContractError(
                "in-window assessment count cannot exceed input count"
            )
        if not isinstance(self.sampling_coverage, SamplingCoverageStatus):
            raise PropagationCoverageContractError("sampling_coverage must be typed")
        if not isinstance(self.assessment_outcome, AssessmentCoverageOutcome):
            raise PropagationCoverageContractError("assessment_outcome must be typed")
        if not isinstance(self.interpretation, PropagationCoverageInterpretation):
            raise PropagationCoverageContractError("interpretation must be typed")
        if self.largest_observed_gap_usec is not None:
            _validate_nonnegative_int(
                self.largest_observed_gap_usec,
                field_name="largest_observed_gap_usec",
            )
        for field_name, value in (
            ("healthy_count", self.healthy_count),
            ("inactive_count", self.inactive_count),
            ("failed_count", self.failed_count),
            ("unassessed_count", self.unassessed_count),
        ):
            _validate_nonnegative_int(value, field_name=field_name)
        self._validate_cross_contract()
        self._validate_derived_fields()
        expected_id = _coverage_evidence_id(
            candidate=self.candidate,
            source_incident=self.source_incident,
            analysis_window_usec=self.analysis_window_usec,
            max_sample_gap_usec=self.max_sample_gap_usec,
            max_input_assessments=self.max_input_assessments,
            input_assessment_count=self.input_assessment_count,
            in_window_assessments=self.in_window_assessments,
        )
        if self.evidence_id != expected_id:
            raise PropagationCoverageContractError(
                "coverage evidence identity does not match its content"
            )

    @property
    def endpoint_observability(self) -> PropagationEndpointObservability:
        return propagation_candidate_observability(self.candidate)

    @property
    def window_start_usec(self) -> int:
        return self.source_incident.temporal_usec

    @property
    def window_end_usec(self) -> int:
        return self.window_start_usec + self.analysis_window_usec

    @property
    def in_window_assessment_count(self) -> int:
        return len(self.in_window_assessments)

    @property
    def out_of_window_assessment_count(self) -> int:
        return self.input_assessment_count - self.in_window_assessment_count

    @property
    def anomalous_count(self) -> int:
        return self.inactive_count + self.failed_count

    @property
    def negative_evidence_assigned(self) -> bool:
        return (
            self.interpretation
            is PropagationCoverageInterpretation.NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING
        )

    def _validate_cross_contract(self) -> None:
        if self.source_incident.boot_id != self.candidate.boot_id:
            raise PropagationCoverageContractError(
                "source incident boot does not match candidate graph boot"
            )
        if self.source_incident.canonical_unit != self.candidate.dependency_unit:
            raise PropagationCoverageContractError(
                "source incident unit does not match candidate dependency unit"
            )
        seen_ids: set[str] = set()
        previous_key: tuple[int, str] | None = None
        for evidence in self.in_window_assessments:
            if not isinstance(evidence, SystemdAssessmentEvidence):
                raise PropagationCoverageContractError(
                    "in-window assessments must be SystemdAssessmentEvidence"
                )
            if evidence.evidence_id in seen_ids:
                raise PropagationCoverageContractError(
                    "duplicate assessment evidence is not allowed"
                )
            seen_ids.add(evidence.evidence_id)
            if evidence.boot_id != self.candidate.boot_id:
                raise PropagationCoverageContractError(
                    "assessment boot does not match candidate graph boot"
                )
            if evidence.canonical_unit != self.candidate.dependent_unit:
                raise PropagationCoverageContractError(
                    "assessment unit does not match candidate dependent unit"
                )
            sample_usec = evidence.assessment.assessed_monotonic_usec
            if (
                sample_usec < self.window_start_usec
                or sample_usec > self.window_end_usec
            ):
                raise PropagationCoverageContractError(
                    "stored assessment lies outside declared analysis window"
                )
            key = (sample_usec, evidence.evidence_id)
            if previous_key is not None and key < previous_key:
                raise PropagationCoverageContractError(
                    "in-window assessments must be canonically sorted"
                )
            previous_key = key

    def _validate_derived_fields(self) -> None:
        expected_counts = _status_counts(self.in_window_assessments)
        actual_counts = (
            self.healthy_count,
            self.inactive_count,
            self.failed_count,
            self.unassessed_count,
        )
        if actual_counts != expected_counts:
            raise PropagationCoverageContractError(
                "coverage status counts do not match assessment evidence"
            )
        expected_sampling, expected_largest_gap = _sampling_coverage(
            self.in_window_assessments,
            window_start_usec=self.window_start_usec,
            window_end_usec=self.window_end_usec,
            max_sample_gap_usec=self.max_sample_gap_usec,
        )
        if self.sampling_coverage is not expected_sampling:
            raise PropagationCoverageContractError(
                "sampling_coverage does not match observed sample gaps"
            )
        if self.largest_observed_gap_usec != expected_largest_gap:
            raise PropagationCoverageContractError(
                "largest_observed_gap_usec does not match sample gaps"
            )
        expected_outcome = _assessment_outcome(
            healthy_count=self.healthy_count,
            inactive_count=self.inactive_count,
            failed_count=self.failed_count,
            unassessed_count=self.unassessed_count,
        )
        if self.assessment_outcome is not expected_outcome:
            raise PropagationCoverageContractError(
                "assessment_outcome does not match sampled statuses"
            )
        expected_interpretation = _coverage_interpretation(
            endpoint_observability=self.endpoint_observability,
            source_temporal_basis=self.source_incident.temporal_basis,
            sampling_coverage=self.sampling_coverage,
            assessment_outcome=self.assessment_outcome,
        )
        if self.interpretation is not expected_interpretation:
            raise PropagationCoverageContractError(
                "coverage interpretation does not match evidence quality"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PROPAGATION_COVERAGE_SCHEMA_VERSION,
            "evidence_id": self.evidence_id,
            "candidate_id": self.candidate.candidate_id,
            "graph_version_id": self.candidate.graph_version_id,
            "topology_id": self.candidate.topology_id,
            "boot_id": self.candidate.boot_id,
            "dependency_unit": self.candidate.dependency_unit,
            "dependent_unit": self.candidate.dependent_unit,
            "requirement_relation": self.candidate.requirement_relation.value,
            "requirement_observed_at": self.candidate.requirement_observed_at.isoformat(),
            "graph_complete_within_scope": self.candidate.graph_complete_within_scope,
            "graph_truncated_by_depth": self.candidate.graph_truncated_by_depth,
            "graph_failure_count": self.candidate.graph_failure_count,
            "source_incident_id": self.source_incident.incident.incident_id,
            "source_temporal_basis": self.source_incident.temporal_basis.value,
            "window_start_usec": self.window_start_usec,
            "window_end_usec": self.window_end_usec,
            "analysis_window_usec": self.analysis_window_usec,
            "analysis_window_declared_by_caller": True,
            "sampling_time_basis": "assessment_monotonic",
            "sampling_evidence_scope": "sampled_detector_assessments_only",
            "max_sample_gap_usec": self.max_sample_gap_usec,
            "max_sample_gap_declared_by_caller": True,
            "max_input_assessments": self.max_input_assessments,
            "input_assessment_count": self.input_assessment_count,
            "in_window_assessment_count": self.in_window_assessment_count,
            "out_of_window_assessment_count": self.out_of_window_assessment_count,
            "in_window_assessment_evidence_ids": [
                evidence.evidence_id for evidence in self.in_window_assessments
            ],
            "in_window_assessed_monotonic_usec": [
                evidence.assessment.assessed_monotonic_usec
                for evidence in self.in_window_assessments
            ],
            "endpoint_observability": self.endpoint_observability.value,
            "sampling_coverage": self.sampling_coverage.value,
            "largest_observed_gap_usec": self.largest_observed_gap_usec,
            "assessment_outcome": self.assessment_outcome.value,
            "healthy_count": self.healthy_count,
            "inactive_count": self.inactive_count,
            "failed_count": self.failed_count,
            "unassessed_count": self.unassessed_count,
            "anomalous_count": self.anomalous_count,
            "interpretation": self.interpretation.value,
            "negative_evidence_assigned": self.negative_evidence_assigned,
            "continuous_health_claim_assigned": False,
            "non_propagation_claim_assigned": False,
            "causal_claim": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
            "topology_temporal_applicability_claim": False,
        }


def propagation_candidate_observability(
    candidate: DependencyPropagationCandidate,
) -> PropagationEndpointObservability:
    """Classify whether frozen Phase 4 service evidence can cover both endpoints."""

    if not isinstance(candidate, DependencyPropagationCandidate):
        raise PropagationCoverageContractError(
            "candidate must be a DependencyPropagationCandidate"
        )
    if _is_service_unit(candidate.dependency_unit) and _is_service_unit(
        candidate.dependent_unit
    ):
        return PropagationEndpointObservability.SERVICE_TO_SERVICE_SUPPORTED
    return PropagationEndpointObservability.CURRENT_DETECTOR_SCOPE_UNSUPPORTED


def evaluate_propagation_sampling_coverage(
    candidate: DependencyPropagationCandidate,
    source_incident: SystemdIncidentTemporalEvidence,
    assessments: Iterable[SystemdAssessmentEvidence],
    *,
    analysis_window_usec: int,
    max_sample_gap_usec: int,
    max_input_assessments: int = DEFAULT_MAX_COVERAGE_INPUT_ASSESSMENTS,
) -> PropagationCoverageEvidence:
    """Evaluate bounded sampled evidence after a source incident anchor.

    The caller declares both the analysis window and the largest acceptable
    observation gap.  Sentinel-X does not invent either threshold.  Assessments
    outside the window remain counted as input but are not silently treated as
    post-source evidence.
    """

    if not isinstance(candidate, DependencyPropagationCandidate):
        raise PropagationCoverageContractError(
            "candidate must be a DependencyPropagationCandidate"
        )
    if not isinstance(source_incident, SystemdIncidentTemporalEvidence):
        raise PropagationCoverageContractError(
            "source_incident must be SystemdIncidentTemporalEvidence"
        )
    _validate_positive_int(analysis_window_usec, field_name="analysis_window_usec")
    _validate_positive_int(max_sample_gap_usec, field_name="max_sample_gap_usec")
    _validate_capacity(max_input_assessments)
    materialized = _materialize_bounded_assessments(
        assessments,
        max_input_assessments=max_input_assessments,
    )
    seen_ids: set[str] = set()
    for evidence in materialized:
        if not isinstance(evidence, SystemdAssessmentEvidence):
            raise PropagationCoverageContractError(
                "assessments must contain SystemdAssessmentEvidence"
            )
        if evidence.evidence_id in seen_ids:
            raise PropagationCoverageContractError(
                "duplicate assessment evidence is not allowed"
            )
        seen_ids.add(evidence.evidence_id)
        if evidence.boot_id != candidate.boot_id:
            raise PropagationCoverageContractError(
                "assessment boot does not match candidate graph boot"
            )
        if evidence.canonical_unit != candidate.dependent_unit:
            raise PropagationCoverageContractError(
                "assessment unit does not match candidate dependent unit"
            )
    if source_incident.boot_id != candidate.boot_id:
        raise PropagationCoverageContractError(
            "source incident boot does not match candidate graph boot"
        )
    if source_incident.canonical_unit != candidate.dependency_unit:
        raise PropagationCoverageContractError(
            "source incident unit does not match candidate dependency unit"
        )

    window_start_usec = source_incident.temporal_usec
    window_end_usec = window_start_usec + analysis_window_usec
    in_window = tuple(
        sorted(
            (
                evidence
                for evidence in materialized
                if window_start_usec
                <= evidence.assessment.assessed_monotonic_usec
                <= window_end_usec
            ),
            key=lambda evidence: (
                evidence.assessment.assessed_monotonic_usec,
                evidence.evidence_id,
            ),
        )
    )
    healthy_count, inactive_count, failed_count, unassessed_count = _status_counts(
        in_window
    )
    sampling_coverage, largest_gap = _sampling_coverage(
        in_window,
        window_start_usec=window_start_usec,
        window_end_usec=window_end_usec,
        max_sample_gap_usec=max_sample_gap_usec,
    )
    outcome = _assessment_outcome(
        healthy_count=healthy_count,
        inactive_count=inactive_count,
        failed_count=failed_count,
        unassessed_count=unassessed_count,
    )
    endpoint_observability = propagation_candidate_observability(candidate)
    interpretation = _coverage_interpretation(
        endpoint_observability=endpoint_observability,
        source_temporal_basis=source_incident.temporal_basis,
        sampling_coverage=sampling_coverage,
        assessment_outcome=outcome,
    )
    evidence_id = _coverage_evidence_id(
        candidate=candidate,
        source_incident=source_incident,
        analysis_window_usec=analysis_window_usec,
        max_sample_gap_usec=max_sample_gap_usec,
        max_input_assessments=max_input_assessments,
        input_assessment_count=len(materialized),
        in_window_assessments=in_window,
    )
    return PropagationCoverageEvidence(
        evidence_id=evidence_id,
        candidate=candidate,
        source_incident=source_incident,
        analysis_window_usec=analysis_window_usec,
        max_sample_gap_usec=max_sample_gap_usec,
        max_input_assessments=max_input_assessments,
        input_assessment_count=len(materialized),
        in_window_assessments=in_window,
        sampling_coverage=sampling_coverage,
        assessment_outcome=outcome,
        interpretation=interpretation,
        largest_observed_gap_usec=largest_gap,
        healthy_count=healthy_count,
        inactive_count=inactive_count,
        failed_count=failed_count,
        unassessed_count=unassessed_count,
    )


def _sampling_coverage(
    assessments: tuple[SystemdAssessmentEvidence, ...],
    *,
    window_start_usec: int,
    window_end_usec: int,
    max_sample_gap_usec: int,
) -> tuple[SamplingCoverageStatus, int | None]:
    if not assessments:
        return SamplingCoverageStatus.NO_IN_WINDOW_SAMPLES, None
    sample_times = tuple(
        evidence.assessment.assessed_monotonic_usec for evidence in assessments
    )
    gaps = [sample_times[0] - window_start_usec]
    gaps.extend(
        later - earlier
        for earlier, later in zip(sample_times, sample_times[1:], strict=False)
    )
    gaps.append(window_end_usec - sample_times[-1])
    largest_gap = max(gaps)
    if largest_gap <= max_sample_gap_usec:
        return SamplingCoverageStatus.BOUNDED, largest_gap
    return SamplingCoverageStatus.GAP_BOUND_EXCEEDED, largest_gap


def _status_counts(
    assessments: tuple[SystemdAssessmentEvidence, ...],
) -> tuple[int, int, int, int]:
    healthy = inactive = failed = unassessed = 0
    for evidence in assessments:
        status = evidence.assessment.status
        if status is SystemdServiceHealthStatus.HEALTHY:
            healthy += 1
        elif status is SystemdServiceHealthStatus.INACTIVE:
            inactive += 1
        elif status is SystemdServiceHealthStatus.FAILED:
            failed += 1
        elif status is SystemdServiceHealthStatus.UNASSESSED:
            unassessed += 1
        else:  # pragma: no cover - frozen enum contract is exhaustive
            raise PropagationCoverageContractError(
                f"unsupported detector status: {status!r}"
            )
    return healthy, inactive, failed, unassessed


def _assessment_outcome(
    *,
    healthy_count: int,
    inactive_count: int,
    failed_count: int,
    unassessed_count: int,
) -> AssessmentCoverageOutcome:
    total = healthy_count + inactive_count + failed_count + unassessed_count
    if total == 0:
        return AssessmentCoverageOutcome.NO_IN_WINDOW_SAMPLES
    if inactive_count or failed_count:
        return AssessmentCoverageOutcome.ANOMALY_OBSERVED
    if unassessed_count:
        return AssessmentCoverageOutcome.UNASSESSED_PRESENT
    return AssessmentCoverageOutcome.ALL_HEALTHY


def _coverage_interpretation(
    *,
    endpoint_observability: PropagationEndpointObservability,
    source_temporal_basis: TemporalEvidenceBasis,
    sampling_coverage: SamplingCoverageStatus,
    assessment_outcome: AssessmentCoverageOutcome,
) -> PropagationCoverageInterpretation:
    if (
        endpoint_observability
        is PropagationEndpointObservability.CURRENT_DETECTOR_SCOPE_UNSUPPORTED
    ):
        return PropagationCoverageInterpretation.CURRENT_DETECTOR_SCOPE_UNSUPPORTED
    if assessment_outcome is AssessmentCoverageOutcome.ANOMALY_OBSERVED:
        return PropagationCoverageInterpretation.AFFECTED_ANOMALY_OBSERVED_IN_WINDOW
    if source_temporal_basis is not TemporalEvidenceBasis.STATE_CHANGE_MONOTONIC:
        return PropagationCoverageInterpretation.SOURCE_TRANSITION_TIMING_LIMITED
    if assessment_outcome is AssessmentCoverageOutcome.UNASSESSED_PRESENT:
        return PropagationCoverageInterpretation.UNASSESSED_COVERAGE
    if sampling_coverage is not SamplingCoverageStatus.BOUNDED:
        return PropagationCoverageInterpretation.INSUFFICIENT_SAMPLING_COVERAGE
    if assessment_outcome is not AssessmentCoverageOutcome.ALL_HEALTHY:
        return PropagationCoverageInterpretation.INSUFFICIENT_SAMPLING_COVERAGE
    return PropagationCoverageInterpretation.NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING


def _coverage_evidence_id(
    *,
    candidate: DependencyPropagationCandidate,
    source_incident: SystemdIncidentTemporalEvidence,
    analysis_window_usec: int,
    max_sample_gap_usec: int,
    max_input_assessments: int,
    input_assessment_count: int,
    in_window_assessments: tuple[SystemdAssessmentEvidence, ...],
) -> str:
    payload: dict[str, object] = {
        "candidate_id": candidate.candidate_id,
        "graph_version_id": candidate.graph_version_id,
        "source_incident_evidence_id": source_incident.evidence_id,
        "analysis_window_usec": analysis_window_usec,
        "max_sample_gap_usec": max_sample_gap_usec,
        "max_input_assessments": max_input_assessments,
        "input_assessment_count": input_assessment_count,
        "in_window_assessment_evidence_ids": [
            evidence.evidence_id for evidence in in_window_assessments
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(_COVERAGE_EVIDENCE_ID_DOMAIN)
    digest.update(encoded)
    return "propcov-" + digest.hexdigest()


def _is_service_unit(unit: str) -> bool:
    try:
        validate_service_unit_name(unit, field_name="unit")
    except SystemdUnitNameError:
        return False
    return True


def _materialize_bounded_assessments(
    assessments: Iterable[SystemdAssessmentEvidence],
    *,
    max_input_assessments: int,
) -> tuple[SystemdAssessmentEvidence, ...]:
    _reject_text_assessment_iterable(assessments)
    try:
        iterator = iter(assessments)
    except TypeError as exc:
        raise PropagationCoverageContractError("assessments must be iterable") from exc

    materialized: list[SystemdAssessmentEvidence] = []
    for index, evidence in enumerate(iterator):
        if index >= max_input_assessments:
            raise PropagationCoverageCapacityError(
                "coverage assessment input exceeds max_input_assessments"
            )
        if not isinstance(evidence, SystemdAssessmentEvidence):
            raise PropagationCoverageContractError(
                "assessments must contain SystemdAssessmentEvidence"
            )
        materialized.append(evidence)
    return tuple(materialized)


def _reject_text_assessment_iterable(value: object) -> None:
    if isinstance(value, (str, bytes)):
        raise PropagationCoverageContractError(
            "assessments must be an iterable of SystemdAssessmentEvidence"
        )


def _validate_capacity(value: int) -> None:
    _validate_positive_int(value, field_name="max_input_assessments")
    if value > MAX_COVERAGE_INPUT_ASSESSMENTS:
        raise PropagationCoverageContractError(
            f"max_input_assessments must be <= {MAX_COVERAGE_INPUT_ASSESSMENTS}"
        )


def _validate_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PropagationCoverageContractError(
            f"{field_name} must be a positive integer"
        )


def _validate_nonnegative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PropagationCoverageContractError(
            f"{field_name} must be a non-negative integer"
        )
