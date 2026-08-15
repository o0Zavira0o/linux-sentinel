"""Controlled paired systemd propagation experiments for Phase 5D.3.

This module builds deterministic, hardened ``sentinel-x-lab-*`` fixture pairs
that differ only in their requirement relation (``Requires=`` or ``Wants=``),
and binds already-established Phase 3/4/5D evidence into one audit record.

It deliberately does not install units, invoke systemctl, assign a causal claim,
or treat one controlled observation as a universal systemd guarantee.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Final, Iterable

from sentinel_x.dependency.coverage import (
    DEFAULT_MAX_COVERAGE_INPUT_ASSESSMENTS,
    MAX_COVERAGE_INPUT_ASSESSMENTS,
    AssessmentCoverageOutcome,
    PropagationCoverageInterpretation,
    SamplingCoverageStatus,
)
from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.propagation import (
    DependencyPropagationCandidate,
    PairwiseFaultPropagationEvidence,
    PairwiseTemporalInterpretation,
    SystemdAssessmentEvidence,
    SystemdIncidentTemporalEvidence,
    TemporalEvidenceBasis,
)
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    SystemdServiceHealthStatus,
)
from sentinel_x.lab.fixture import (
    SYSTEMD_LAB_RUNTIME_ROOT,
    FaultLabFixtureError,
    SystemdLabFixtureArtifact,
    SystemdLabFixtureSpec,
    build_systemd_lab_fixture,
)
from sentinel_x.lab.injector import FaultInjectionOutcome, verify_installed_lab_fixture
from sentinel_x.lab.models import FaultGroundTruthWindow, FaultMode
from sentinel_x.systemd.boot import SystemBootIdError, normalize_boot_id

CONTROLLED_PROPAGATION_EXPERIMENT_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.controlled-propagation-experiment.v1"
)

_PAIR_ID_DOMAIN: Final[bytes] = b"sentinel-x.controlled-propagation-pair.v1\x00"
_RECORD_ID_DOMAIN: Final[bytes] = b"sentinel-x.controlled-propagation-record.v1\x00"
_GROUND_TRUTH_COVERAGE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-ground-truth-coverage.v1\x00"
)
_INSTALLED_UNIT_MODE: Final[int] = 0o644
_STAGING_UNIT_MODE: Final[int] = 0o600


class ControlledPropagationExperimentError(RuntimeError):
    """Base error for controlled Phase 5D.3 propagation experiments."""


class ControlledPropagationExperimentContractError(
    ControlledPropagationExperimentError
):
    """Raised when controlled experiment evidence violates its contract."""


class ControlledPropagationExperimentPreconditionError(
    ControlledPropagationExperimentError
):
    """Raised when a staged or installed controlled fixture is not trustworthy."""


class ControlledPropagationExperimentCapacityError(
    ControlledPropagationExperimentError
):
    """Raised when bounded controlled evidence input capacity is exceeded."""


class ControlledPropagationEvidenceClass(StrEnum):
    """Descriptive evidence class for one completed controlled experiment."""

    AFFECTED_ANOMALY_OBSERVED = "affected_anomaly_observed"
    BOUNDED_NEGATIVE_EVIDENCE = "bounded_negative_evidence"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class SystemdPropagationPairSpec:
    """Immutable controlled pair specification.

    ``dependent`` carries exactly one requirement relation toward ``source`` and
    always carries ``After=source`` so strong/weak experiments differ only in
    the requirement relation under study.  The ordering edge remains context,
    not a propagation or causal claim.
    """

    source: SystemdLabFixtureSpec
    dependent: SystemdLabFixtureSpec
    requirement_relation: DependencyRelation

    def __post_init__(self) -> None:
        if not isinstance(self.source, SystemdLabFixtureSpec):
            raise ControlledPropagationExperimentContractError(
                "source must be a SystemdLabFixtureSpec"
            )
        if not isinstance(self.dependent, SystemdLabFixtureSpec):
            raise ControlledPropagationExperimentContractError(
                "dependent must be a SystemdLabFixtureSpec"
            )
        if self.source.unit_name == self.dependent.unit_name:
            raise ControlledPropagationExperimentContractError(
                "source and dependent fixture units must be distinct"
            )
        if self.requirement_relation not in {
            DependencyRelation.REQUIRES,
            DependencyRelation.WANTS,
        }:
            raise ControlledPropagationExperimentContractError(
                "controlled pairs support only Requires or Wants"
            )

    @property
    def pair_id(self) -> str:
        payload: dict[str, object] = {
            "source": self.source.to_dict(),
            "dependent": self.dependent.to_dict(),
            "requirement_relation": self.requirement_relation.value,
        }
        return "proppair-" + _content_digest(_PAIR_ID_DOMAIN, payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "pair_id": self.pair_id,
            "source": self.source.to_dict(),
            "dependent": self.dependent.to_dict(),
            "requirement_relation": self.requirement_relation.value,
            "ordering_relation": DependencyRelation.AFTER.value,
            "causal_claim": False,
            "propagation_expectation_assigned": False,
        }


@dataclass(frozen=True, slots=True)
class SystemdPropagationPairArtifact:
    """Deterministic source/dependent unit artifacts for one controlled pair."""

    spec: SystemdPropagationPairSpec
    source_artifact: SystemdLabFixtureArtifact
    dependent_unit_text: str
    dependent_sha256: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.spec, SystemdPropagationPairSpec):
            raise ControlledPropagationExperimentContractError(
                "spec must be a SystemdPropagationPairSpec"
            )
        if not isinstance(self.source_artifact, SystemdLabFixtureArtifact):
            raise ControlledPropagationExperimentContractError(
                "source_artifact must be a SystemdLabFixtureArtifact"
            )
        if self.source_artifact.spec != self.spec.source:
            raise ControlledPropagationExperimentContractError(
                "source_artifact spec must match the pair source spec"
            )
        expected_text = _render_dependent_unit(self.spec)
        if self.dependent_unit_text != expected_text:
            raise ControlledPropagationExperimentContractError(
                "dependent_unit_text is not the canonical pair rendering"
            )
        expected_sha = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        if self.dependent_sha256 != expected_sha:
            raise ControlledPropagationExperimentContractError(
                "dependent_sha256 does not match dependent_unit_text"
            )
        _validate_aware_datetime(self.created_at, field_name="created_at")

    @property
    def pair_id(self) -> str:
        return self.spec.pair_id

    @property
    def source_unit(self) -> str:
        return self.spec.source.unit_name

    @property
    def dependent_unit(self) -> str:
        return self.spec.dependent.unit_name

    @property
    def dependent_runtime_install_path(self) -> Path:
        return SYSTEMD_LAB_RUNTIME_ROOT / self.dependent_unit

    def write_private_copies(self, directory: str | Path) -> tuple[Path, Path]:
        """Write an all-or-cleaned-up 0600 staging pair outside systemd trees."""

        try:
            source_path = self.source_artifact.write_private_copy(directory)
        except FaultLabFixtureError as exc:
            raise ControlledPropagationExperimentPreconditionError(
                "cannot create source staging artifact"
            ) from exc
        dependent_path = source_path.parent / self.dependent_unit
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            descriptor = os.open(dependent_path, flags, _STAGING_UNIT_MODE)
        except OSError as exc:
            _best_effort_unlink(source_path)
            raise ControlledPropagationExperimentPreconditionError(
                f"cannot create dependent staging artifact: {exc}"
            ) from exc

        try:
            payload = self.dependent_unit_text.encode("utf-8")
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            _best_effort_unlink(dependent_path)
            _best_effort_unlink(source_path)
            raise ControlledPropagationExperimentPreconditionError(
                f"cannot write dependent staging artifact: {exc}"
            ) from exc
        return source_path, dependent_path

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLED_PROPAGATION_EXPERIMENT_SCHEMA_VERSION,
            "pair_id": self.pair_id,
            "spec": self.spec.to_dict(),
            "source_unit": self.source_unit,
            "dependent_unit": self.dependent_unit,
            "source_sha256": self.source_artifact.sha256,
            "dependent_sha256": self.dependent_sha256,
            "source_runtime_install_path": os.fspath(
                self.source_artifact.runtime_install_path
            ),
            "dependent_runtime_install_path": os.fspath(
                self.dependent_runtime_install_path
            ),
            "created_at": self.created_at.isoformat(),
            "source_unit_text_bytes": len(
                self.source_artifact.unit_text.encode("utf-8")
            ),
            "dependent_unit_text_bytes": len(self.dependent_unit_text.encode("utf-8")),
            "causal_claim": False,
            "propagation_expectation_assigned": False,
        }


def build_systemd_propagation_pair(
    spec: SystemdPropagationPairSpec,
) -> SystemdPropagationPairArtifact:
    """Build one deterministic controlled pair without installing or starting it."""

    if not isinstance(spec, SystemdPropagationPairSpec):
        raise ControlledPropagationExperimentContractError(
            "spec must be a SystemdPropagationPairSpec"
        )
    source_artifact = build_systemd_lab_fixture(spec.source)
    dependent_text = _render_dependent_unit(spec)
    return SystemdPropagationPairArtifact(
        spec=spec,
        source_artifact=source_artifact,
        dependent_unit_text=dependent_text,
        dependent_sha256=hashlib.sha256(dependent_text.encode("utf-8")).hexdigest(),
    )


def verify_installed_systemd_propagation_pair(
    artifact: SystemdPropagationPairArtifact,
) -> None:
    """Verify exact root-owned 0644 runtime bytes for both controlled units."""

    if not isinstance(artifact, SystemdPropagationPairArtifact):
        raise ControlledPropagationExperimentPreconditionError(
            "artifact must be a SystemdPropagationPairArtifact"
        )
    try:
        verify_installed_lab_fixture(artifact.source_artifact)
    except Exception as exc:
        raise ControlledPropagationExperimentPreconditionError(
            "source fixture verification failed"
        ) from exc
    _verify_installed_unit(
        artifact.dependent_runtime_install_path,
        expected_text=artifact.dependent_unit_text,
        expected_sha256=artifact.dependent_sha256,
    )


@dataclass(frozen=True, slots=True)
class ControlledGroundTruthCoverageEvidence:
    """Sampling evidence anchored to authoritative controlled fault ground truth.

    This contract is lab-only.  It does not weaken the production 5D.2 rule
    that observational negative evidence requires a trustworthy source
    transition anchor.
    """

    evidence_id: str
    candidate: DependencyPropagationCandidate
    ground_truth: FaultGroundTruthWindow
    ground_truth_boot_id: str
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
        if not self.evidence_id.startswith("propgtcov-") or len(self.evidence_id) != 74:
            raise ControlledPropagationExperimentContractError(
                "evidence_id must be a propgtcov- prefixed SHA-256 identity"
            )
        if not isinstance(self.candidate, DependencyPropagationCandidate):
            raise ControlledPropagationExperimentContractError(
                "candidate must be a DependencyPropagationCandidate"
            )
        if not isinstance(self.ground_truth, FaultGroundTruthWindow):
            raise ControlledPropagationExperimentContractError(
                "ground_truth must be a FaultGroundTruthWindow"
            )
        ground_truth_boot_id = _normalize_controlled_boot_id(
            self.ground_truth_boot_id, field_name="ground_truth_boot_id"
        )
        if ground_truth_boot_id != self.ground_truth_boot_id:
            raise ControlledPropagationExperimentContractError(
                "ground_truth_boot_id must already be normalized"
            )
        if self.ground_truth_boot_id != self.candidate.boot_id:
            raise ControlledPropagationExperimentContractError(
                "ground truth boot must match candidate graph boot"
            )
        _validate_positive_int(
            self.analysis_window_usec, field_name="analysis_window_usec"
        )
        _validate_positive_int(
            self.max_sample_gap_usec, field_name="max_sample_gap_usec"
        )
        _validate_coverage_capacity(self.max_input_assessments)
        _validate_nonnegative_int(
            self.input_assessment_count, field_name="input_assessment_count"
        )
        if self.input_assessment_count > self.max_input_assessments:
            raise ControlledPropagationExperimentContractError(
                "input_assessment_count exceeds max_input_assessments"
            )
        ground_truth_end_usec = self.ground_truth.ended_monotonic_usec
        if self.ground_truth.is_open or ground_truth_end_usec is None:
            raise ControlledPropagationExperimentContractError(
                "controlled coverage requires closed ground truth"
            )
        if self.ground_truth.target_unit != self.candidate.dependency_unit:
            raise ControlledPropagationExperimentContractError(
                "ground truth target must match candidate dependency unit"
            )
        if self.ground_truth.fault_mode is not FaultMode.SERVICE_INACTIVE:
            raise ControlledPropagationExperimentContractError(
                "Phase 5D.3 controlled coverage requires source deactivation ground truth"
            )
        if self.candidate.requirement_observed_at >= self.ground_truth.started_at:
            raise ControlledPropagationExperimentContractError(
                "candidate topology evidence must strictly precede controlled fault start"
            )
        if self.window_end_usec > ground_truth_end_usec:
            raise ControlledPropagationExperimentContractError(
                "analysis window must stay within the controlled fault ground truth"
            )
        if not isinstance(self.in_window_assessments, tuple):
            raise ControlledPropagationExperimentContractError(
                "in_window_assessments must be a tuple"
            )
        if len(self.in_window_assessments) > self.input_assessment_count:
            raise ControlledPropagationExperimentContractError(
                "in-window assessment count cannot exceed input count"
            )
        seen_ids: set[str] = set()
        previous_key: tuple[int, str] | None = None
        for evidence in self.in_window_assessments:
            if not isinstance(evidence, SystemdAssessmentEvidence):
                raise ControlledPropagationExperimentContractError(
                    "in-window assessments must be SystemdAssessmentEvidence"
                )
            if evidence.evidence_id in seen_ids:
                raise ControlledPropagationExperimentContractError(
                    "duplicate assessment evidence is not allowed"
                )
            seen_ids.add(evidence.evidence_id)
            if evidence.boot_id != self.candidate.boot_id:
                raise ControlledPropagationExperimentContractError(
                    "assessment boot does not match candidate graph boot"
                )
            if evidence.canonical_unit != self.candidate.dependent_unit:
                raise ControlledPropagationExperimentContractError(
                    "assessment unit does not match candidate dependent unit"
                )
            sample_usec = evidence.assessment.assessed_monotonic_usec
            if (
                sample_usec < self.window_start_usec
                or sample_usec > self.window_end_usec
            ):
                raise ControlledPropagationExperimentContractError(
                    "in-window assessment lies outside the controlled window"
                )
            key = (sample_usec, evidence.evidence_id)
            if previous_key is not None and key < previous_key:
                raise ControlledPropagationExperimentContractError(
                    "in-window assessments must be canonically sorted"
                )
            previous_key = key
        if not isinstance(self.sampling_coverage, SamplingCoverageStatus):
            raise ControlledPropagationExperimentContractError(
                "sampling_coverage must be typed"
            )
        if not isinstance(self.assessment_outcome, AssessmentCoverageOutcome):
            raise ControlledPropagationExperimentContractError(
                "assessment_outcome must be typed"
            )
        if not isinstance(self.interpretation, PropagationCoverageInterpretation):
            raise ControlledPropagationExperimentContractError(
                "interpretation must be typed"
            )
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
        expected_counts = _controlled_status_counts(self.in_window_assessments)
        if expected_counts != (
            self.healthy_count,
            self.inactive_count,
            self.failed_count,
            self.unassessed_count,
        ):
            raise ControlledPropagationExperimentContractError(
                "controlled coverage status counts do not match assessments"
            )
        expected_sampling, expected_gap = _controlled_sampling_coverage(
            self.in_window_assessments,
            window_start_usec=self.window_start_usec,
            window_end_usec=self.window_end_usec,
            max_sample_gap_usec=self.max_sample_gap_usec,
        )
        if self.sampling_coverage is not expected_sampling:
            raise ControlledPropagationExperimentContractError(
                "controlled sampling coverage does not match assessments"
            )
        if self.largest_observed_gap_usec != expected_gap:
            raise ControlledPropagationExperimentContractError(
                "largest_observed_gap_usec does not match assessments"
            )
        expected_outcome = _controlled_assessment_outcome(
            healthy_count=self.healthy_count,
            inactive_count=self.inactive_count,
            failed_count=self.failed_count,
            unassessed_count=self.unassessed_count,
        )
        if self.assessment_outcome is not expected_outcome:
            raise ControlledPropagationExperimentContractError(
                "controlled assessment_outcome does not match assessments"
            )
        expected_interpretation = _controlled_coverage_interpretation(
            sampling_coverage=self.sampling_coverage,
            assessment_outcome=self.assessment_outcome,
        )
        if self.interpretation is not expected_interpretation:
            raise ControlledPropagationExperimentContractError(
                "controlled interpretation does not match sampled evidence"
            )
        expected_id = _controlled_ground_truth_coverage_id(
            candidate=self.candidate,
            ground_truth=self.ground_truth,
            ground_truth_boot_id=self.ground_truth_boot_id,
            analysis_window_usec=self.analysis_window_usec,
            max_sample_gap_usec=self.max_sample_gap_usec,
            max_input_assessments=self.max_input_assessments,
            input_assessment_count=self.input_assessment_count,
            in_window_assessments=self.in_window_assessments,
        )
        if self.evidence_id != expected_id:
            raise ControlledPropagationExperimentContractError(
                "controlled coverage evidence identity does not match its content"
            )

    @property
    def window_start_usec(self) -> int:
        return self.ground_truth.started_monotonic_usec

    @property
    def window_end_usec(self) -> int:
        return self.window_start_usec + self.analysis_window_usec

    @property
    def negative_evidence_assigned(self) -> bool:
        return (
            self.interpretation
            is PropagationCoverageInterpretation.NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLED_PROPAGATION_EXPERIMENT_SCHEMA_VERSION,
            "evidence_id": self.evidence_id,
            "candidate_id": self.candidate.candidate_id,
            "graph_version_id": self.candidate.graph_version_id,
            "topology_id": self.candidate.topology_id,
            "boot_id": self.candidate.boot_id,
            "ground_truth": self.ground_truth.to_dict(),
            "ground_truth_boot_id": self.ground_truth_boot_id,
            "temporal_anchor_basis": "controlled_fault_ground_truth",
            "cross_boot_temporal_comparison_permitted": False,
            "analysis_window_usec": self.analysis_window_usec,
            "max_sample_gap_usec": self.max_sample_gap_usec,
            "max_input_assessments": self.max_input_assessments,
            "input_assessment_count": self.input_assessment_count,
            "in_window_assessment_evidence_ids": [
                evidence.evidence_id for evidence in self.in_window_assessments
            ],
            "sampling_coverage": self.sampling_coverage.value,
            "assessment_outcome": self.assessment_outcome.value,
            "interpretation": self.interpretation.value,
            "largest_observed_gap_usec": self.largest_observed_gap_usec,
            "healthy_count": self.healthy_count,
            "inactive_count": self.inactive_count,
            "failed_count": self.failed_count,
            "unassessed_count": self.unassessed_count,
            "negative_evidence_assigned": self.negative_evidence_assigned,
            "continuous_health_claim_assigned": False,
            "non_propagation_claim_assigned": False,
            "topology_temporal_applicability_claim": False,
            "causal_claim": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
        }


def evaluate_controlled_ground_truth_coverage(
    candidate: DependencyPropagationCandidate,
    ground_truth: FaultGroundTruthWindow,
    ground_truth_boot_id: str,
    assessments: Iterable[SystemdAssessmentEvidence],
    *,
    analysis_window_usec: int,
    max_sample_gap_usec: int,
    max_input_assessments: int = DEFAULT_MAX_COVERAGE_INPUT_ASSESSMENTS,
) -> ControlledGroundTruthCoverageEvidence:
    """Evaluate lab-only sampled evidence from authoritative injected ground truth."""

    if not isinstance(candidate, DependencyPropagationCandidate):
        raise ControlledPropagationExperimentContractError(
            "candidate must be a DependencyPropagationCandidate"
        )
    if not isinstance(ground_truth, FaultGroundTruthWindow):
        raise ControlledPropagationExperimentContractError(
            "ground_truth must be a FaultGroundTruthWindow"
        )
    normalized_ground_truth_boot_id = _normalize_controlled_boot_id(
        ground_truth_boot_id, field_name="ground_truth_boot_id"
    )
    if normalized_ground_truth_boot_id != ground_truth_boot_id:
        raise ControlledPropagationExperimentContractError(
            "ground_truth_boot_id must already be normalized"
        )
    if ground_truth_boot_id != candidate.boot_id:
        raise ControlledPropagationExperimentContractError(
            "ground truth boot must match candidate graph boot"
        )
    _validate_positive_int(analysis_window_usec, field_name="analysis_window_usec")
    _validate_positive_int(max_sample_gap_usec, field_name="max_sample_gap_usec")
    _validate_coverage_capacity(max_input_assessments)
    ground_truth_end_usec = ground_truth.ended_monotonic_usec
    if ground_truth.is_open or ground_truth_end_usec is None:
        raise ControlledPropagationExperimentContractError(
            "controlled coverage requires closed ground truth"
        )
    if ground_truth.target_unit != candidate.dependency_unit:
        raise ControlledPropagationExperimentContractError(
            "ground truth target must match candidate dependency unit"
        )
    if ground_truth.fault_mode is not FaultMode.SERVICE_INACTIVE:
        raise ControlledPropagationExperimentContractError(
            "controlled coverage requires SERVICE_INACTIVE ground truth"
        )
    if candidate.requirement_observed_at >= ground_truth.started_at:
        raise ControlledPropagationExperimentContractError(
            "candidate topology evidence must strictly precede controlled fault start"
        )
    window_start_usec = ground_truth.started_monotonic_usec
    window_end_usec = window_start_usec + analysis_window_usec
    if window_end_usec > ground_truth_end_usec:
        raise ControlledPropagationExperimentContractError(
            "analysis window must stay within the controlled fault ground truth"
        )
    materialized = _materialize_controlled_assessments(
        assessments, max_input_assessments=max_input_assessments
    )
    seen_ids: set[str] = set()
    for evidence in materialized:
        if evidence.evidence_id in seen_ids:
            raise ControlledPropagationExperimentContractError(
                "duplicate assessment evidence is not allowed"
            )
        seen_ids.add(evidence.evidence_id)
        if evidence.boot_id != candidate.boot_id:
            raise ControlledPropagationExperimentContractError(
                "assessment boot does not match candidate graph boot"
            )
        if evidence.canonical_unit != candidate.dependent_unit:
            raise ControlledPropagationExperimentContractError(
                "assessment unit does not match candidate dependent unit"
            )
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
    counts = _controlled_status_counts(in_window)
    sampling_coverage, largest_gap = _controlled_sampling_coverage(
        in_window,
        window_start_usec=window_start_usec,
        window_end_usec=window_end_usec,
        max_sample_gap_usec=max_sample_gap_usec,
    )
    outcome = _controlled_assessment_outcome(
        healthy_count=counts[0],
        inactive_count=counts[1],
        failed_count=counts[2],
        unassessed_count=counts[3],
    )
    interpretation = _controlled_coverage_interpretation(
        sampling_coverage=sampling_coverage,
        assessment_outcome=outcome,
    )
    evidence_id = _controlled_ground_truth_coverage_id(
        candidate=candidate,
        ground_truth=ground_truth,
        ground_truth_boot_id=ground_truth_boot_id,
        analysis_window_usec=analysis_window_usec,
        max_sample_gap_usec=max_sample_gap_usec,
        max_input_assessments=max_input_assessments,
        input_assessment_count=len(materialized),
        in_window_assessments=in_window,
    )
    return ControlledGroundTruthCoverageEvidence(
        evidence_id=evidence_id,
        candidate=candidate,
        ground_truth=ground_truth,
        ground_truth_boot_id=ground_truth_boot_id,
        analysis_window_usec=analysis_window_usec,
        max_sample_gap_usec=max_sample_gap_usec,
        max_input_assessments=max_input_assessments,
        input_assessment_count=len(materialized),
        in_window_assessments=in_window,
        sampling_coverage=sampling_coverage,
        assessment_outcome=outcome,
        interpretation=interpretation,
        largest_observed_gap_usec=largest_gap,
        healthy_count=counts[0],
        inactive_count=counts[1],
        failed_count=counts[2],
        unassessed_count=counts[3],
    )


@dataclass(frozen=True, slots=True)
class ControlledPropagationExperimentRecord:
    """One controlled manipulation joined to graph and temporal evidence.

    The record preserves what was observed in the controlled lab.  It does not
    convert a systemd requirement relation or one experiment into a causal,
    probabilistic, or universal propagation claim.
    """

    record_id: str
    pair_artifact: SystemdPropagationPairArtifact
    injection_outcome: FaultInjectionOutcome
    candidate: DependencyPropagationCandidate
    controlled_coverage_evidence: ControlledGroundTruthCoverageEvidence
    source_incident: SystemdIncidentTemporalEvidence | None = None
    pairwise_evidence: PairwiseFaultPropagationEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.pair_artifact, SystemdPropagationPairArtifact):
            raise ControlledPropagationExperimentContractError(
                "pair_artifact has an invalid type"
            )
        if not isinstance(self.injection_outcome, FaultInjectionOutcome):
            raise ControlledPropagationExperimentContractError(
                "injection_outcome has an invalid type"
            )
        if not isinstance(self.candidate, DependencyPropagationCandidate):
            raise ControlledPropagationExperimentContractError(
                "candidate has an invalid type"
            )
        if not isinstance(
            self.controlled_coverage_evidence, ControlledGroundTruthCoverageEvidence
        ):
            raise ControlledPropagationExperimentContractError(
                "controlled_coverage_evidence has an invalid type"
            )
        if self.source_incident is not None and not isinstance(
            self.source_incident, SystemdIncidentTemporalEvidence
        ):
            raise ControlledPropagationExperimentContractError(
                "source_incident has an invalid type"
            )
        if self.pairwise_evidence is not None and not isinstance(
            self.pairwise_evidence,
            PairwiseFaultPropagationEvidence,
        ):
            raise ControlledPropagationExperimentContractError(
                "pairwise_evidence must be PairwiseFaultPropagationEvidence or None"
            )
        self._validate_cross_contract()
        expected_id = _record_id(
            pair_artifact=self.pair_artifact,
            injection_outcome=self.injection_outcome,
            candidate=self.candidate,
            controlled_coverage_evidence=self.controlled_coverage_evidence,
            source_incident=self.source_incident,
            pairwise_evidence=self.pairwise_evidence,
        )
        if self.record_id != expected_id:
            raise ControlledPropagationExperimentContractError(
                "record_id does not match controlled experiment content"
            )

    @property
    def evidence_class(self) -> ControlledPropagationEvidenceClass:
        if self.pairwise_evidence is not None:
            return ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED
        if (
            self.controlled_coverage_evidence.interpretation
            is PropagationCoverageInterpretation.AFFECTED_ANOMALY_OBSERVED_IN_WINDOW
        ):
            return ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED
        if self.controlled_coverage_evidence.negative_evidence_assigned:
            return ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE
        return ControlledPropagationEvidenceClass.INCONCLUSIVE

    @property
    def forward_temporal_consistency_observed(self) -> bool:
        return (
            self.pairwise_evidence is not None
            and self.pairwise_evidence.interpretation
            is PairwiseTemporalInterpretation.CONSISTENT_WITH_CANDIDATE_DIRECTION
        )

    @property
    def sampling_incident_evidence_conflict(self) -> bool:
        return (
            self.pairwise_evidence is not None
            and self.controlled_coverage_evidence.negative_evidence_assigned
        )

    @property
    def ground_truth_confirmation_offset_usec(self) -> int | None:
        if (
            self.source_incident is None
            or self.source_incident.temporal_basis
            is not TemporalEvidenceBasis.STATE_CHANGE_MONOTONIC
        ):
            return None
        return (
            self.injection_outcome.ground_truth.started_monotonic_usec
            - self.source_incident.temporal_usec
        )

    def _validate_cross_contract(self) -> None:
        pair = self.pair_artifact
        outcome = self.injection_outcome
        if outcome.plan.target_unit != pair.source_unit:
            raise ControlledPropagationExperimentContractError(
                "injection outcome must target the controlled pair source"
            )
        if outcome.plan.artifact_sha256 != pair.source_artifact.sha256:
            raise ControlledPropagationExperimentContractError(
                "injection outcome source artifact SHA-256 mismatch"
            )
        if outcome.plan.fault_mode is not FaultMode.SERVICE_INACTIVE:
            raise ControlledPropagationExperimentContractError(
                "Phase 5D.3 paired contrast requires explicit source deactivation"
            )
        if outcome.ground_truth.is_open:
            raise ControlledPropagationExperimentContractError(
                "controlled experiment ground truth must be closed"
            )
        if outcome.ground_truth.scenario_id != outcome.plan.scenario_id:
            raise ControlledPropagationExperimentContractError(
                "ground truth scenario must match the injection plan"
            )
        if outcome.ground_truth.fault_mode is not outcome.plan.fault_mode:
            raise ControlledPropagationExperimentContractError(
                "ground truth fault mode must match the injection plan"
            )
        if self.candidate.dependency_unit != pair.source_unit:
            raise ControlledPropagationExperimentContractError(
                "candidate dependency unit must match pair source"
            )
        if self.candidate.dependent_unit != pair.dependent_unit:
            raise ControlledPropagationExperimentContractError(
                "candidate dependent unit must match pair dependent"
            )
        if self.candidate.requirement_relation is not pair.spec.requirement_relation:
            raise ControlledPropagationExperimentContractError(
                "candidate relation must match controlled pair relation"
            )
        controlled_coverage = self.controlled_coverage_evidence
        if controlled_coverage.candidate.candidate_id != self.candidate.candidate_id:
            raise ControlledPropagationExperimentContractError(
                "controlled coverage candidate must match the controlled candidate"
            )
        if controlled_coverage.ground_truth != outcome.ground_truth:
            raise ControlledPropagationExperimentContractError(
                "controlled coverage ground truth must match the injection outcome"
            )
        if self.source_incident is not None:
            if self.source_incident.boot_id != controlled_coverage.ground_truth_boot_id:
                raise ControlledPropagationExperimentContractError(
                    "source incident boot must match controlled ground truth boot"
                )
            if self.source_incident.canonical_unit != pair.source_unit:
                raise ControlledPropagationExperimentContractError(
                    "source incident unit must match pair source"
                )
            if (
                self.source_incident.incident.anomaly_class
                is not DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
            ):
                raise ControlledPropagationExperimentContractError(
                    "source incident must represent the controlled inactive fault"
                )
        if self.pairwise_evidence is not None:
            if self.source_incident is None:
                raise ControlledPropagationExperimentContractError(
                    "pairwise evidence requires source_incident evidence"
                )
            if (
                self.pairwise_evidence.candidate.candidate_id
                != self.candidate.candidate_id
            ):
                raise ControlledPropagationExperimentContractError(
                    "pairwise candidate must match the controlled candidate"
                )
            if (
                self.pairwise_evidence.source_incident.evidence_id
                != self.source_incident.evidence_id
            ):
                raise ControlledPropagationExperimentContractError(
                    "pairwise source incident must match the controlled source incident"
                )
            if (
                self.pairwise_evidence.analysis_window_usec
                != controlled_coverage.analysis_window_usec
            ):
                raise ControlledPropagationExperimentContractError(
                    "pairwise and controlled coverage analysis windows must match"
                )

    def to_dict(self) -> dict[str, object]:
        pairwise = (
            None if self.pairwise_evidence is None else self.pairwise_evidence.to_dict()
        )
        return {
            "schema_version": CONTROLLED_PROPAGATION_EXPERIMENT_SCHEMA_VERSION,
            "record_id": self.record_id,
            "pair": self.pair_artifact.to_dict(),
            "injection_outcome": self.injection_outcome.to_dict(),
            "candidate": self.candidate.to_dict(),
            "source_incident": (
                None if self.source_incident is None else self.source_incident.to_dict()
            ),
            "controlled_coverage_evidence": self.controlled_coverage_evidence.to_dict(),
            "pairwise_evidence": pairwise,
            "evidence_class": self.evidence_class.value,
            "forward_temporal_consistency_observed": (
                self.forward_temporal_consistency_observed
            ),
            "sampling_incident_evidence_conflict": (
                self.sampling_incident_evidence_conflict
            ),
            "ground_truth_confirmation_offset_usec": (
                self.ground_truth_confirmation_offset_usec
            ),
            "source_incident_temporal_basis": (
                None
                if self.source_incident is None
                else self.source_incident.temporal_basis.value
            ),
            "topology_temporal_applicability_claim": False,
            "controlled_lab_observation_only": True,
            "universal_systemd_behavior_claim": False,
            "causal_claim": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
        }


def build_controlled_propagation_experiment_record(
    pair_artifact: SystemdPropagationPairArtifact,
    injection_outcome: FaultInjectionOutcome,
    candidate: DependencyPropagationCandidate,
    controlled_coverage_evidence: ControlledGroundTruthCoverageEvidence,
    *,
    source_incident: SystemdIncidentTemporalEvidence | None = None,
    pairwise_evidence: PairwiseFaultPropagationEvidence | None = None,
) -> ControlledPropagationExperimentRecord:
    """Bind one controlled source deactivation to already-derived Phase 5D evidence."""

    record_id = _record_id(
        pair_artifact=pair_artifact,
        injection_outcome=injection_outcome,
        candidate=candidate,
        controlled_coverage_evidence=controlled_coverage_evidence,
        source_incident=source_incident,
        pairwise_evidence=pairwise_evidence,
    )
    return ControlledPropagationExperimentRecord(
        record_id=record_id,
        pair_artifact=pair_artifact,
        injection_outcome=injection_outcome,
        candidate=candidate,
        controlled_coverage_evidence=controlled_coverage_evidence,
        source_incident=source_incident,
        pairwise_evidence=pairwise_evidence,
    )


def _render_dependent_unit(spec: SystemdPropagationPairSpec) -> str:
    relation_line = (
        f"Requires={spec.source.unit_name}"
        if spec.requirement_relation is DependencyRelation.REQUIRES
        else f"Wants={spec.source.unit_name}"
    )
    fixture_id = spec.dependent.fixture_id
    return "\n".join(
        (
            "[Unit]",
            f"Description=Sentinel-X controlled propagation dependent {fixture_id}",
            relation_line,
            f"After={spec.source.unit_name}",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStartPre=/usr/bin/echo SENTINEL_X_PROPAGATION_DEPENDENT_READY fixture_id={fixture_id}",
            "ExecStart=/usr/bin/sleep infinity",
            f"ExecStopPost=/usr/bin/echo SENTINEL_X_PROPAGATION_DEPENDENT_STOPPED fixture_id={fixture_id}",
            "Restart=no",
            "KillMode=control-group",
            "TimeoutStopSec=5s",
            f"RuntimeMaxSec={spec.dependent.runtime_max_seconds}s",
            "DynamicUser=yes",
            "NoNewPrivileges=yes",
            "PrivateTmp=yes",
            "PrivateDevices=yes",
            "PrivateNetwork=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "ProtectKernelTunables=yes",
            "ProtectKernelModules=yes",
            "ProtectControlGroups=yes",
            "RestrictSUIDSGID=yes",
            "RestrictNamespaces=yes",
            "LockPersonality=yes",
            "MemoryDenyWriteExecute=yes",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "UMask=0077",
            "StandardOutput=journal",
            "StandardError=journal",
            "",
        )
    )


def _verify_installed_unit(
    path: Path,
    *,
    expected_text: str,
    expected_sha256: str,
) -> None:
    if path.parent != SYSTEMD_LAB_RUNTIME_ROOT:
        raise ControlledPropagationExperimentPreconditionError(
            "dependent runtime path is not canonical"
        )
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ControlledPropagationExperimentPreconditionError(
            f"installed dependent cannot be inspected: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ControlledPropagationExperimentPreconditionError(
            "installed dependent must be a regular non-symlink file"
        )
    if metadata.st_uid != 0 or metadata.st_gid != 0:
        raise ControlledPropagationExperimentPreconditionError(
            "installed dependent must be owned by root:root"
        )
    if stat.S_IMODE(metadata.st_mode) != _INSTALLED_UNIT_MODE:
        raise ControlledPropagationExperimentPreconditionError(
            "installed dependent mode must be 0644"
        )
    expected_bytes = expected_text.encode("utf-8")
    if metadata.st_size != len(expected_bytes):
        raise ControlledPropagationExperimentPreconditionError(
            "installed dependent size mismatch"
        )
    try:
        installed_bytes = path.read_bytes()
    except OSError as exc:
        raise ControlledPropagationExperimentPreconditionError(
            f"installed dependent cannot be read: {exc}"
        ) from exc
    if installed_bytes != expected_bytes:
        raise ControlledPropagationExperimentPreconditionError(
            "installed dependent bytes are not canonical"
        )
    if hashlib.sha256(installed_bytes).hexdigest() != expected_sha256:
        raise ControlledPropagationExperimentPreconditionError(
            "installed dependent SHA-256 mismatch"
        )


def _controlled_sampling_coverage(
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


def _controlled_status_counts(
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
            raise ControlledPropagationExperimentContractError(
                f"unsupported detector status: {status!r}"
            )
    return healthy, inactive, failed, unassessed


def _controlled_assessment_outcome(
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


def _controlled_coverage_interpretation(
    *,
    sampling_coverage: SamplingCoverageStatus,
    assessment_outcome: AssessmentCoverageOutcome,
) -> PropagationCoverageInterpretation:
    if assessment_outcome is AssessmentCoverageOutcome.ANOMALY_OBSERVED:
        return PropagationCoverageInterpretation.AFFECTED_ANOMALY_OBSERVED_IN_WINDOW
    if assessment_outcome is AssessmentCoverageOutcome.UNASSESSED_PRESENT:
        return PropagationCoverageInterpretation.UNASSESSED_COVERAGE
    if sampling_coverage is not SamplingCoverageStatus.BOUNDED:
        return PropagationCoverageInterpretation.INSUFFICIENT_SAMPLING_COVERAGE
    if assessment_outcome is not AssessmentCoverageOutcome.ALL_HEALTHY:
        return PropagationCoverageInterpretation.INSUFFICIENT_SAMPLING_COVERAGE
    return PropagationCoverageInterpretation.NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING


def _materialize_controlled_assessments(
    assessments: Iterable[SystemdAssessmentEvidence],
    *,
    max_input_assessments: int,
) -> tuple[SystemdAssessmentEvidence, ...]:
    _reject_text_controlled_assessment_iterable(assessments)
    try:
        iterator = iter(assessments)
    except TypeError as exc:
        raise ControlledPropagationExperimentContractError(
            "assessments must be iterable"
        ) from exc
    materialized: list[SystemdAssessmentEvidence] = []
    for index, evidence in enumerate(iterator):
        if index >= max_input_assessments:
            raise ControlledPropagationExperimentCapacityError(
                "controlled coverage assessment capacity exceeded"
            )
        if not isinstance(evidence, SystemdAssessmentEvidence):
            raise ControlledPropagationExperimentContractError(
                "assessments must contain SystemdAssessmentEvidence"
            )
        materialized.append(evidence)
    return tuple(materialized)


def _reject_text_controlled_assessment_iterable(value: object) -> None:
    if isinstance(value, (str, bytes)):
        raise ControlledPropagationExperimentContractError(
            "assessments must be an iterable of SystemdAssessmentEvidence"
        )


def _controlled_ground_truth_coverage_id(
    *,
    candidate: DependencyPropagationCandidate,
    ground_truth: FaultGroundTruthWindow,
    ground_truth_boot_id: str,
    analysis_window_usec: int,
    max_sample_gap_usec: int,
    max_input_assessments: int,
    input_assessment_count: int,
    in_window_assessments: tuple[SystemdAssessmentEvidence, ...],
) -> str:
    payload: dict[str, object] = {
        "candidate_id": candidate.candidate_id,
        "graph_version_id": candidate.graph_version_id,
        "ground_truth": ground_truth.to_dict(),
        "ground_truth_boot_id": ground_truth_boot_id,
        "analysis_window_usec": analysis_window_usec,
        "max_sample_gap_usec": max_sample_gap_usec,
        "max_input_assessments": max_input_assessments,
        "input_assessment_count": input_assessment_count,
        "in_window_assessment_evidence_ids": [
            evidence.evidence_id for evidence in in_window_assessments
        ],
    }
    return "propgtcov-" + _content_digest(_GROUND_TRUTH_COVERAGE_ID_DOMAIN, payload)


def _normalize_controlled_boot_id(value: object, *, field_name: str) -> str:
    try:
        return normalize_boot_id(value, field_name=field_name)
    except SystemBootIdError as exc:
        raise ControlledPropagationExperimentContractError(str(exc)) from exc


def _validate_coverage_capacity(value: int) -> None:
    _validate_positive_int(value, field_name="max_input_assessments")
    if value > MAX_COVERAGE_INPUT_ASSESSMENTS:
        raise ControlledPropagationExperimentContractError(
            f"max_input_assessments must be <= {MAX_COVERAGE_INPUT_ASSESSMENTS}"
        )


def _validate_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ControlledPropagationExperimentContractError(
            f"{field_name} must be a positive integer"
        )


def _validate_nonnegative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ControlledPropagationExperimentContractError(
            f"{field_name} must be a non-negative integer"
        )


def _record_id(
    *,
    pair_artifact: SystemdPropagationPairArtifact,
    injection_outcome: FaultInjectionOutcome,
    candidate: DependencyPropagationCandidate,
    controlled_coverage_evidence: ControlledGroundTruthCoverageEvidence,
    source_incident: SystemdIncidentTemporalEvidence | None,
    pairwise_evidence: PairwiseFaultPropagationEvidence | None,
) -> str:
    payload: dict[str, object] = {
        "pair_id": pair_artifact.pair_id,
        "injection_outcome": injection_outcome.to_dict(),
        "candidate_id": candidate.candidate_id,
        "source_incident_evidence_id": (
            None if source_incident is None else source_incident.evidence_id
        ),
        "controlled_coverage_evidence_id": controlled_coverage_evidence.evidence_id,
        "pairwise_evidence_id": (
            None if pairwise_evidence is None else pairwise_evidence.evidence_id
        ),
    }
    return "propexp-" + _content_digest(_RECORD_ID_DOMAIN, payload)


def _content_digest(domain: bytes, payload: dict[str, object]) -> str:
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


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ControlledPropagationExperimentContractError(
            f"{field_name} must be a datetime"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ControlledPropagationExperimentContractError(
            f"{field_name} must be timezone-aware"
        )


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
