"""Typed controlled paired-contrast synthesis for Sentinel-X Phase 5E.2.

This module compares exactly two *completed* controlled live propagation runs:
one ``Requires=`` arm and one ``Wants=`` arm.  The comparison is deliberately
narrow.  It preserves each arm's Phase 5E.1 evidence synthesis and verifies that
fixture identity, source fault semantics, sampling policy, boot identity, and
candidate context are comparable while the requirement relation remains the
single controlled pair-spec variable.

The result is descriptive evidence about these two laboratory runs only.  It
never estimates a causal effect, treatment effect, probability, confidence,
statistical significance, root cause, or universal systemd behavior.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.propagation import DependencyPropagationCandidate
from sentinel_x.dependency.propagation_experiment import (
    ControlledPropagationEvidenceClass,
    SystemdPropagationPairArtifact,
)
from sentinel_x.dependency.propagation_live import ControlledPropagationLiveRun
from sentinel_x.dependency.synthesis import (
    PropagationEvidenceSynthesis,
    PropagationEvidenceSynthesisInterpretation,
    PropagationEvidenceSynthesisScope,
    synthesize_propagation_evidence,
)
from sentinel_x.lab.injector import SystemdLabServiceState
from sentinel_x.lab.models import FaultMode

CONTROLLED_PROPAGATION_PAIRED_SYNTHESIS_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.controlled-propagation-paired-synthesis.v1"
)

_ARM_ID_DOMAIN: Final[bytes] = b"sentinel-x.controlled-propagation-paired-arm.v1\x00"
_CONTRAST_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-paired-synthesis.v1\x00"
)
_LIVE_FINGERPRINT_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-live-fingerprint.v1\x00"
)


class ControlledPropagationPairedSynthesisError(RuntimeError):
    """Base error for Phase 5E.2 controlled paired synthesis."""


class ControlledPropagationPairedSynthesisContractError(
    ControlledPropagationPairedSynthesisError
):
    """Raised when two live runs cannot be safely treated as a controlled pair."""


class ControlledPropagationPairedContrastProfile(StrEnum):
    """Descriptive profile of two arm-local evidence syntheses."""

    REQUIRES_ANOMALY_WANTS_BOUNDED_NEGATIVE = "requires_anomaly_wants_bounded_negative"
    REQUIRES_BOUNDED_NEGATIVE_WANTS_ANOMALY = "requires_bounded_negative_wants_anomaly"
    BOTH_ANOMALY_OBSERVED = "both_anomaly_observed"
    BOTH_BOUNDED_NEGATIVE = "both_bounded_negative"
    DIRECTIONAL_COUNTEREVIDENCE_PRESENT = "directional_counterevidence_present"
    CONFLICT_PRESENT = "conflict_present"
    INCONCLUSIVE_PRESENT = "inconclusive_present"
    OTHER_USABLE_EVIDENCE_PROFILE = "other_usable_evidence_profile"


@dataclass(frozen=True, slots=True)
class ControlledPropagationPairedArm:
    """One relation-specific live arm and its exact controlled synthesis."""

    arm_id: str
    live_run: ControlledPropagationLiveRun
    synthesis: PropagationEvidenceSynthesis
    live_run_fingerprint: str

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.arm_id, "proparm-"):
            raise ControlledPropagationPairedSynthesisContractError(
                "arm_id must be a proparm- prefixed SHA-256 identity"
            )
        if not isinstance(self.live_run, ControlledPropagationLiveRun):
            raise ControlledPropagationPairedSynthesisContractError(
                "live_run must be a ControlledPropagationLiveRun"
            )
        if not isinstance(self.synthesis, PropagationEvidenceSynthesis):
            raise ControlledPropagationPairedSynthesisContractError(
                "synthesis must be a PropagationEvidenceSynthesis"
            )
        if not _is_prefixed_digest(self.live_run_fingerprint, "proplivefp-"):
            raise ControlledPropagationPairedSynthesisContractError(
                "live_run_fingerprint must be a proplivefp- prefixed SHA-256 identity"
            )
        expected_fingerprint = _live_run_fingerprint(self.live_run)
        if self.live_run_fingerprint != expected_fingerprint:
            raise ControlledPropagationPairedSynthesisContractError(
                "live_run_fingerprint does not match live-run content"
            )
        if self.relation not in {
            DependencyRelation.REQUIRES,
            DependencyRelation.WANTS,
        }:
            raise ControlledPropagationPairedSynthesisContractError(
                "paired arms require Requires or Wants relation"
            )
        if self.synthesis.candidate != self.live_run.candidate:
            raise ControlledPropagationPairedSynthesisContractError(
                "arm synthesis candidate must exactly match live-run candidate"
            )
        if (
            self.synthesis.scope
            is not PropagationEvidenceSynthesisScope.CONTROLLED_EXPERIMENT
        ):
            raise ControlledPropagationPairedSynthesisContractError(
                "paired arm synthesis must have controlled-experiment scope"
            )
        if self.synthesis.max_input_evidence != 1:
            raise ControlledPropagationPairedSynthesisContractError(
                "paired arm synthesis must use exact single-record capacity"
            )
        if self.synthesis.input_evidence != (self.live_run.experiment_record,):
            raise ControlledPropagationPairedSynthesisContractError(
                "paired arm synthesis must contain exactly the live experiment record"
            )
        expected_arm_id = _arm_id(
            relation=self.relation,
            live_run_fingerprint=self.live_run_fingerprint,
            synthesis_id=self.synthesis.synthesis_id,
        )
        if self.arm_id != expected_arm_id:
            raise ControlledPropagationPairedSynthesisContractError(
                "arm identity does not match relation, live run, and synthesis"
            )

    @property
    def relation(self) -> DependencyRelation:
        return self.live_run.pair_artifact.spec.requirement_relation

    @property
    def evidence_class(self) -> ControlledPropagationEvidenceClass:
        return self.live_run.experiment_record.evidence_class

    @property
    def fault_duration_usec(self) -> int:
        duration = self.live_run.injection_outcome.ground_truth.duration_usec
        if duration is None:
            raise ControlledPropagationPairedSynthesisContractError(
                "completed live arm must expose closed fault duration"
            )
        return duration

    def to_dict(self) -> dict[str, object]:
        coverage = self.live_run.experiment_record.controlled_coverage_evidence
        return {
            "schema_version": CONTROLLED_PROPAGATION_PAIRED_SYNTHESIS_SCHEMA_VERSION,
            "arm_id": self.arm_id,
            "relation": self.relation.value,
            "live_run_fingerprint": self.live_run_fingerprint,
            "pair_id": self.live_run.pair_artifact.pair_id,
            "experiment_id": self.live_run.injection_outcome.plan.experiment_id,
            "scenario_id": self.live_run.injection_outcome.plan.scenario_id,
            "experiment_record_id": self.live_run.experiment_record.record_id,
            "candidate_id": self.live_run.candidate.candidate_id,
            "topology_id": self.live_run.candidate.topology_id,
            "graph_version_id": self.live_run.candidate.graph_version_id,
            "boot_id": self.live_run.boot_id_before,
            "synthesis_id": self.synthesis.synthesis_id,
            "synthesis_interpretation": self.synthesis.interpretation.value,
            "evidence_class": self.evidence_class.value,
            "affected_anomaly_observed_count": (
                self.synthesis.affected_anomaly_observed_count
            ),
            "forward_temporal_consistency_count": (
                self.synthesis.forward_temporal_consistency_count
            ),
            "directional_counterevidence_count": (
                self.synthesis.directional_counterevidence_count
            ),
            "bounded_negative_observation_count": (
                self.synthesis.bounded_negative_observation_count
            ),
            "insufficient_evidence_count": self.synthesis.insufficient_evidence_count,
            "conflicting_evidence_present": self.synthesis.conflicting_evidence_present,
            "fault_duration_usec": self.fault_duration_usec,
            "dependent_assessment_count": len(self.live_run.dependent_assessments),
            "in_window_assessment_count": len(coverage.in_window_assessments),
            "largest_observed_gap_usec": coverage.largest_observed_gap_usec,
            "sampling_coverage": coverage.sampling_coverage.value,
            "assessment_outcome": coverage.assessment_outcome.value,
            "post_recovery_verified": self.live_run.post_recovery_verified,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
            "scalar_score_assigned": False,
        }


@dataclass(frozen=True, slots=True)
class ControlledPropagationPairedSynthesis:
    """Typed comparison of one Requires and one Wants controlled live arm."""

    contrast_id: str
    requires_arm: ControlledPropagationPairedArm
    wants_arm: ControlledPropagationPairedArm

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.contrast_id, "propcontrast-"):
            raise ControlledPropagationPairedSynthesisContractError(
                "contrast_id must be a propcontrast- prefixed SHA-256 identity"
            )
        if not isinstance(self.requires_arm, ControlledPropagationPairedArm):
            raise ControlledPropagationPairedSynthesisContractError(
                "requires_arm must be a ControlledPropagationPairedArm"
            )
        if not isinstance(self.wants_arm, ControlledPropagationPairedArm):
            raise ControlledPropagationPairedSynthesisContractError(
                "wants_arm must be a ControlledPropagationPairedArm"
            )
        if self.requires_arm.relation is not DependencyRelation.REQUIRES:
            raise ControlledPropagationPairedSynthesisContractError(
                "requires_arm must carry Requires relation"
            )
        if self.wants_arm.relation is not DependencyRelation.WANTS:
            raise ControlledPropagationPairedSynthesisContractError(
                "wants_arm must carry Wants relation"
            )
        _validate_pair_comparability(
            self.requires_arm.live_run,
            self.wants_arm.live_run,
        )
        expected_id = _contrast_id(
            requires_arm=self.requires_arm,
            wants_arm=self.wants_arm,
        )
        if self.contrast_id != expected_id:
            raise ControlledPropagationPairedSynthesisContractError(
                "contrast identity does not match exact paired-arm content"
            )

    @property
    def profile(self) -> ControlledPropagationPairedContrastProfile:
        req_syn = self.requires_arm.synthesis
        want_syn = self.wants_arm.synthesis
        if (
            req_syn.conflicting_evidence_present
            or want_syn.conflicting_evidence_present
        ):
            return ControlledPropagationPairedContrastProfile.CONFLICT_PRESENT
        if (
            req_syn.directional_counterevidence_count > 0
            or want_syn.directional_counterevidence_count > 0
        ):
            return ControlledPropagationPairedContrastProfile.DIRECTIONAL_COUNTEREVIDENCE_PRESENT
        inconclusive_interpretations = {
            PropagationEvidenceSynthesisInterpretation.NO_EVIDENCE,
            PropagationEvidenceSynthesisInterpretation.INSUFFICIENT_ONLY,
        }
        if (
            req_syn.interpretation in inconclusive_interpretations
            or want_syn.interpretation in inconclusive_interpretations
            or self.requires_arm.evidence_class
            is ControlledPropagationEvidenceClass.INCONCLUSIVE
            or self.wants_arm.evidence_class
            is ControlledPropagationEvidenceClass.INCONCLUSIVE
        ):
            return ControlledPropagationPairedContrastProfile.INCONCLUSIVE_PRESENT
        req_class = self.requires_arm.evidence_class
        want_class = self.wants_arm.evidence_class
        if (
            req_class is ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED
            and want_class
            is ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE
        ):
            return ControlledPropagationPairedContrastProfile.REQUIRES_ANOMALY_WANTS_BOUNDED_NEGATIVE
        if (
            req_class is ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE
            and want_class
            is ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED
        ):
            return ControlledPropagationPairedContrastProfile.REQUIRES_BOUNDED_NEGATIVE_WANTS_ANOMALY
        if (
            req_class is ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED
            and want_class
            is ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED
        ):
            return ControlledPropagationPairedContrastProfile.BOTH_ANOMALY_OBSERVED
        if (
            req_class is ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE
            and want_class
            is ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE
        ):
            return ControlledPropagationPairedContrastProfile.BOTH_BOUNDED_NEGATIVE
        return ControlledPropagationPairedContrastProfile.OTHER_USABLE_EVIDENCE_PROFILE

    @property
    def same_boot(self) -> bool:
        return (
            self.requires_arm.live_run.boot_id_before
            == self.wants_arm.live_run.boot_id_before
        )

    @property
    def sampling_policy_equal(self) -> bool:
        return self.requires_arm.live_run.policy == self.wants_arm.live_run.policy

    @property
    def arm_evidence_profiles_differ(self) -> bool:
        return (
            self.requires_arm.synthesis.interpretation
            != self.wants_arm.synthesis.interpretation
            or self.requires_arm.evidence_class != self.wants_arm.evidence_class
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLED_PROPAGATION_PAIRED_SYNTHESIS_SCHEMA_VERSION,
            "contrast_id": self.contrast_id,
            "pair_spec_controlled_variable": "requirement_relation",
            "experiment_wide_single_variable_isolation_claim": False,
            "requires_arm": self.requires_arm.to_dict(),
            "wants_arm": self.wants_arm.to_dict(),
            "profile": self.profile.value,
            "arm_evidence_profiles_differ": self.arm_evidence_profiles_differ,
            "same_source_fixture_spec_required": True,
            "same_dependent_fixture_spec_required": True,
            "source_artifact_bytes_equal_required": True,
            "dependent_unit_text_exact_relation_only_difference_required": True,
            "candidate_nonrelation_context_equal_required": True,
            "sampling_policy_equal": self.sampling_policy_equal,
            "same_sampling_policy_required": True,
            "same_boot": self.same_boot,
            "same_boot_required": True,
            "source_fault_plan_semantics_equal_required": True,
            "source_state_category_semantics_equal_required": True,
            "distinct_experiment_identity_required": True,
            "distinct_scenario_identity_required": True,
            "execution_manifest_timing_policy_available_in_frozen_live_contract": False,
            "execution_manifest_timing_policy_compared": False,
            "fault_duration_equality_required": False,
            "cross_arm_temporal_latency_comparison_assigned": False,
            "arm_statistical_independence_assumed": False,
            "sample_statistical_independence_assumed": False,
            "replication_claim_assigned": False,
            "statistical_significance_assigned": False,
            "causal_effect_estimate_assigned": False,
            "treatment_effect_claim_assigned": False,
            "topology_temporal_applicability_claim": False,
            "generalization_beyond_controlled_pair_permitted": False,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "universal_systemd_behavior_claim": False,
            "probabilistic_confidence_assigned": False,
            "scalar_score_assigned": False,
        }


def build_controlled_propagation_paired_synthesis(
    requires_run: ControlledPropagationLiveRun,
    wants_run: ControlledPropagationLiveRun,
) -> ControlledPropagationPairedSynthesis:
    """Build one strict descriptive paired contrast from two completed live runs."""

    if not isinstance(requires_run, ControlledPropagationLiveRun):
        raise ControlledPropagationPairedSynthesisContractError(
            "requires_run must be a ControlledPropagationLiveRun"
        )
    if not isinstance(wants_run, ControlledPropagationLiveRun):
        raise ControlledPropagationPairedSynthesisContractError(
            "wants_run must be a ControlledPropagationLiveRun"
        )
    if (
        requires_run.pair_artifact.spec.requirement_relation
        is not DependencyRelation.REQUIRES
    ):
        raise ControlledPropagationPairedSynthesisContractError(
            "requires_run must carry Requires relation"
        )
    if (
        wants_run.pair_artifact.spec.requirement_relation
        is not DependencyRelation.WANTS
    ):
        raise ControlledPropagationPairedSynthesisContractError(
            "wants_run must carry Wants relation"
        )
    _validate_pair_comparability(requires_run, wants_run)
    requires_arm = _build_arm(requires_run)
    wants_arm = _build_arm(wants_run)
    contrast_id = _contrast_id(
        requires_arm=requires_arm,
        wants_arm=wants_arm,
    )
    return ControlledPropagationPairedSynthesis(
        contrast_id=contrast_id,
        requires_arm=requires_arm,
        wants_arm=wants_arm,
    )


def _build_arm(run: ControlledPropagationLiveRun) -> ControlledPropagationPairedArm:
    synthesis = synthesize_propagation_evidence(
        run.candidate,
        (run.experiment_record,),
        max_input_evidence=1,
    )
    fingerprint = _live_run_fingerprint(run)
    return ControlledPropagationPairedArm(
        arm_id=_arm_id(
            relation=run.pair_artifact.spec.requirement_relation,
            live_run_fingerprint=fingerprint,
            synthesis_id=synthesis.synthesis_id,
        ),
        live_run=run,
        synthesis=synthesis,
        live_run_fingerprint=fingerprint,
    )


def _validate_pair_comparability(
    requires_run: ControlledPropagationLiveRun,
    wants_run: ControlledPropagationLiveRun,
) -> None:
    req_pair = requires_run.pair_artifact
    want_pair = wants_run.pair_artifact
    if req_pair.spec.source != want_pair.spec.source:
        raise ControlledPropagationPairedSynthesisContractError(
            "paired runs must use the exact same source fixture spec"
        )
    if req_pair.spec.dependent != want_pair.spec.dependent:
        raise ControlledPropagationPairedSynthesisContractError(
            "paired runs must use the exact same dependent fixture spec"
        )
    if (
        req_pair.source_artifact.unit_text != want_pair.source_artifact.unit_text
        or req_pair.source_artifact.sha256 != want_pair.source_artifact.sha256
    ):
        raise ControlledPropagationPairedSynthesisContractError(
            "paired runs must use byte-identical source fixture artifacts"
        )
    _validate_dependent_relation_only_difference(req_pair, want_pair)
    if requires_run.policy != wants_run.policy:
        raise ControlledPropagationPairedSynthesisContractError(
            "paired runs must use the exact same live sampling policy"
        )
    if requires_run.boot_id_before != wants_run.boot_id_before:
        raise ControlledPropagationPairedSynthesisContractError(
            "paired runs must execute on the same boot"
        )
    _validate_candidate_nonrelation_context(requires_run.candidate, wants_run.candidate)
    _validate_source_fault_semantics(requires_run, wants_run)


def _validate_dependent_relation_only_difference(
    req_pair: SystemdPropagationPairArtifact,
    want_pair: SystemdPropagationPairArtifact,
) -> None:
    req_text = req_pair.dependent_unit_text
    want_text = want_pair.dependent_unit_text
    req_lines = req_text.splitlines()
    want_lines = want_text.splitlines()
    if len(req_lines) != len(want_lines):
        raise ControlledPropagationPairedSynthesisContractError(
            "dependent pair renderings differ beyond one requirement line"
        )
    differences = tuple(
        (left, right)
        for left, right in zip(req_lines, want_lines, strict=True)
        if left != right
    )
    expected = (
        (
            f"Requires={req_pair.source_unit}",
            f"Wants={want_pair.source_unit}",
        ),
    )
    if differences != expected:
        raise ControlledPropagationPairedSynthesisContractError(
            "dependent pair renderings must differ only by Requires versus Wants"
        )
    if req_pair.dependent_sha256 == want_pair.dependent_sha256:
        raise ControlledPropagationPairedSynthesisContractError(
            "relation-specific dependent artifacts must have distinct digests"
        )


def _validate_candidate_nonrelation_context(
    requires: DependencyPropagationCandidate,
    wants: DependencyPropagationCandidate,
) -> None:
    if requires.requirement_relation is not DependencyRelation.REQUIRES:
        raise ControlledPropagationPairedSynthesisContractError(
            "Requires candidate relation mismatch"
        )
    if wants.requirement_relation is not DependencyRelation.WANTS:
        raise ControlledPropagationPairedSynthesisContractError(
            "Wants candidate relation mismatch"
        )
    comparable_fields = (
        "boot_id",
        "dependency_unit",
        "dependent_unit",
        "dependency_node_observation",
        "dependent_node_observation",
        "ordering_context",
        "graph_complete_within_scope",
        "graph_truncated_by_depth",
        "graph_failure_count",
    )
    for field_name in comparable_fields:
        if getattr(requires, field_name) != getattr(wants, field_name):
            raise ControlledPropagationPairedSynthesisContractError(
                f"candidate non-relation context drift: {field_name}"
            )
    if requires.candidate_id == wants.candidate_id:
        raise ControlledPropagationPairedSynthesisContractError(
            "Requires and Wants candidates must have distinct candidate identities"
        )
    if requires.topology_id == wants.topology_id:
        raise ControlledPropagationPairedSynthesisContractError(
            "Requires and Wants controlled topologies must be relation-sensitive"
        )


def _validate_source_fault_semantics(
    requires_run: ControlledPropagationLiveRun,
    wants_run: ControlledPropagationLiveRun,
) -> None:
    req_outcome = requires_run.injection_outcome
    want_outcome = wants_run.injection_outcome
    req_plan = req_outcome.plan
    want_plan = want_outcome.plan
    if req_plan.experiment_id == want_plan.experiment_id:
        raise ControlledPropagationPairedSynthesisContractError(
            "paired runs require distinct experiment identities"
        )
    if req_plan.scenario_id == want_plan.scenario_id:
        raise ControlledPropagationPairedSynthesisContractError(
            "paired runs require distinct scenario identities"
        )
    if req_plan.target_unit != want_plan.target_unit:
        raise ControlledPropagationPairedSynthesisContractError(
            "paired fault plans must target the same source unit"
        )
    if (
        req_plan.fault_mode is not FaultMode.SERVICE_INACTIVE
        or want_plan.fault_mode is not FaultMode.SERVICE_INACTIVE
    ):
        raise ControlledPropagationPairedSynthesisContractError(
            "paired contrast requires SERVICE_INACTIVE in both arms"
        )
    plan_fields = (
        "fault_mode",
        "operation",
        "expected_fault_active_states",
        "recovery_operations",
        "artifact_sha256",
    )
    for field_name in plan_fields:
        if getattr(req_plan, field_name) != getattr(want_plan, field_name):
            raise ControlledPropagationPairedSynthesisContractError(
                f"source fault-plan semantic drift: {field_name}"
            )
    if req_plan.artifact_sha256 != requires_run.pair_artifact.source_artifact.sha256:
        raise ControlledPropagationPairedSynthesisContractError(
            "Requires fault plan source artifact drift"
        )
    if want_plan.artifact_sha256 != wants_run.pair_artifact.source_artifact.sha256:
        raise ControlledPropagationPairedSynthesisContractError(
            "Wants fault plan source artifact drift"
        )
    for label, req_state, want_state in (
        ("baseline", req_outcome.baseline_state, want_outcome.baseline_state),
        ("fault", req_outcome.fault_state, want_outcome.fault_state),
        ("recovered", req_outcome.recovered_state, want_outcome.recovered_state),
    ):
        if _service_state_semantic_key(req_state) != _service_state_semantic_key(
            want_state
        ):
            raise ControlledPropagationPairedSynthesisContractError(
                f"source {label} state semantic drift between paired arms"
            )


def _service_state_semantic_key(state: SystemdLabServiceState) -> tuple[str, ...]:
    return (
        state.unit_name,
        state.load_state,
        state.active_state,
        state.sub_state,
        state.result,
    )


def _live_run_fingerprint(run: ControlledPropagationLiveRun) -> str:
    return "proplivefp-" + _content_digest(_LIVE_FINGERPRINT_DOMAIN, run.to_dict())


def _arm_id(
    *,
    relation: DependencyRelation,
    live_run_fingerprint: str,
    synthesis_id: str,
) -> str:
    payload: dict[str, object] = {
        "relation": relation.value,
        "live_run_fingerprint": live_run_fingerprint,
        "synthesis_id": synthesis_id,
    }
    return "proparm-" + _content_digest(_ARM_ID_DOMAIN, payload)


def _contrast_id(
    *,
    requires_arm: ControlledPropagationPairedArm,
    wants_arm: ControlledPropagationPairedArm,
) -> str:
    payload: dict[str, object] = {
        "requires_arm_id": requires_arm.arm_id,
        "wants_arm_id": wants_arm.arm_id,
        "requires_live_run_fingerprint": requires_arm.live_run_fingerprint,
        "wants_live_run_fingerprint": wants_arm.live_run_fingerprint,
        "requires_synthesis_id": requires_arm.synthesis.synthesis_id,
        "wants_synthesis_id": wants_arm.synthesis.synthesis_id,
    }
    return "propcontrast-" + _content_digest(_CONTRAST_ID_DOMAIN, payload)


def _content_digest(domain: bytes, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(domain + encoded).hexdigest()


def _is_prefixed_digest(value: object, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value[len(prefix) :]
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )
