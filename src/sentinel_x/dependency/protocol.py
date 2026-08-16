"""Typed pre-execution protocol provenance for controlled propagation experiments.

Phase 5E.3 captures the immutable inputs that are actually available before a
controlled live run: fixture/pair semantics, exact artifact digests, fault
scenario timing, and live sampling policy.  It deliberately does not pretend
that the frozen live contract exposes every execution-backend setting, does
not prove that a capture happened before mutation merely because it exists,
and does not assign replication, causal, probabilistic, or statistical claims.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Final

from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.propagation_experiment import (
    CONTROLLED_PROPAGATION_EXPERIMENT_SCHEMA_VERSION,
    SystemdPropagationPairArtifact,
)
from sentinel_x.dependency.propagation_live import (
    CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION,
    ControlledPropagationLivePolicy,
)
from sentinel_x.lab.models import FaultExperimentManifest, FaultMode

CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.controlled-propagation-protocol.v1"
)

_PROTOCOL_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-input-protocol.v1\x00"
)
_CONTRAST_CONTEXT_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-contrast-context.v1\x00"
)
_MANIFEST_FINGERPRINT_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-manifest-fingerprint.v1\x00"
)
_CAPTURE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-protocol-capture.v1\x00"
)
_COMPARISON_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-protocol-comparison.v1\x00"
)


class ControlledPropagationProtocolError(RuntimeError):
    """Base error for Phase 5E.3 protocol provenance."""


class ControlledPropagationProtocolContractError(ControlledPropagationProtocolError):
    """Raised when protocol provenance or comparison violates its contract."""


class ControlledPropagationProtocolControlledVariable(StrEnum):
    """Fixed controlled dimension supported by the current paired protocol."""

    REQUIREMENT_RELATION = "requirement_relation"


class ControlledPropagationProtocolScope(StrEnum):
    """Scope of what Phase 5E.3 actually captures."""

    TYPED_PRE_EXECUTION_INPUTS = "typed_pre_execution_inputs"


@dataclass(frozen=True, slots=True)
class ControlledPropagationProtocolCapture:
    """One immutable capture of typed pre-execution controlled-run inputs.

    ``protocol_id`` identifies execution-relevant input semantics while
    intentionally excluding run labels/identities and capture timestamps.
    ``contrast_context_id`` excludes the fixed controlled variable and all
    relation-derived artifact identities so Requires/Wants arms can prove that
    their *captured input protocol* matches outside that dimension.

    This model alone does not prove ordering relative to the first mutation.
    A later protocol-bound execution layer must establish that fact.
    """

    capture_id: str
    protocol_id: str
    contrast_context_id: str
    manifest_fingerprint: str
    pair_artifact: SystemdPropagationPairArtifact
    manifest: FaultExperimentManifest
    policy: ControlledPropagationLivePolicy
    captured_at: datetime
    captured_monotonic_usec: int

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.capture_id, "protocap-"):
            raise ControlledPropagationProtocolContractError(
                "capture_id must be a protocap- prefixed SHA-256 identity"
            )
        if not _is_prefixed_digest(self.protocol_id, "protoprot-"):
            raise ControlledPropagationProtocolContractError(
                "protocol_id must be a protoprot- prefixed SHA-256 identity"
            )
        if not _is_prefixed_digest(self.contrast_context_id, "protoctx-"):
            raise ControlledPropagationProtocolContractError(
                "contrast_context_id must be a protoctx- prefixed SHA-256 identity"
            )
        if not _is_prefixed_digest(self.manifest_fingerprint, "manifestfp-"):
            raise ControlledPropagationProtocolContractError(
                "manifest_fingerprint must be a manifestfp- prefixed SHA-256 identity"
            )
        if not isinstance(self.pair_artifact, SystemdPropagationPairArtifact):
            raise ControlledPropagationProtocolContractError(
                "pair_artifact must be a SystemdPropagationPairArtifact"
            )
        if not isinstance(self.manifest, FaultExperimentManifest):
            raise ControlledPropagationProtocolContractError(
                "manifest must be a FaultExperimentManifest"
            )
        if not isinstance(self.policy, ControlledPropagationLivePolicy):
            raise ControlledPropagationProtocolContractError(
                "policy must be a ControlledPropagationLivePolicy"
            )
        _validate_protocol_inputs(self.pair_artifact, self.manifest)
        _validate_aware_datetime(self.captured_at, field_name="captured_at")
        if (
            isinstance(self.captured_monotonic_usec, bool)
            or not isinstance(self.captured_monotonic_usec, int)
            or self.captured_monotonic_usec < 0
        ):
            raise ControlledPropagationProtocolContractError(
                "captured_monotonic_usec must be a non-negative integer"
            )
        if self.pair_artifact.source_artifact.created_at > self.captured_at:
            raise ControlledPropagationProtocolContractError(
                "protocol capture cannot precede source artifact creation"
            )
        if self.pair_artifact.created_at > self.captured_at:
            raise ControlledPropagationProtocolContractError(
                "protocol capture cannot precede pair artifact creation"
            )
        if self.manifest.created_at > self.captured_at:
            raise ControlledPropagationProtocolContractError(
                "protocol capture cannot precede manifest creation"
            )

        expected_manifest_fingerprint = _manifest_fingerprint(self.manifest)
        if self.manifest_fingerprint != expected_manifest_fingerprint:
            raise ControlledPropagationProtocolContractError(
                "manifest_fingerprint does not match full manifest provenance"
            )
        expected_protocol_id = _protocol_id(
            self.pair_artifact,
            self.manifest,
            self.policy,
        )
        if self.protocol_id != expected_protocol_id:
            raise ControlledPropagationProtocolContractError(
                "protocol_id does not match captured protocol semantics"
            )
        expected_context_id = _contrast_context_id(
            self.pair_artifact,
            self.manifest,
            self.policy,
        )
        if self.contrast_context_id != expected_context_id:
            raise ControlledPropagationProtocolContractError(
                "contrast_context_id does not match relation-excluded semantics"
            )
        expected_capture_id = _capture_id(
            protocol_id=self.protocol_id,
            contrast_context_id=self.contrast_context_id,
            manifest_fingerprint=self.manifest_fingerprint,
            pair_artifact=self.pair_artifact,
            captured_at=self.captured_at,
            captured_monotonic_usec=self.captured_monotonic_usec,
        )
        if self.capture_id != expected_capture_id:
            raise ControlledPropagationProtocolContractError(
                "capture_id does not match exact capture provenance"
            )

    @property
    def relation(self) -> DependencyRelation:
        return self.pair_artifact.spec.requirement_relation

    @property
    def source_unit(self) -> str:
        return self.pair_artifact.source_unit

    @property
    def dependent_unit(self) -> str:
        return self.pair_artifact.dependent_unit

    def to_dict(self) -> dict[str, object]:
        scenario = self.manifest.scenario
        return {
            "schema_version": CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION,
            "protocol_scope": ControlledPropagationProtocolScope.TYPED_PRE_EXECUTION_INPUTS.value,
            "capture_id": self.capture_id,
            "protocol_id": self.protocol_id,
            "contrast_context_id": self.contrast_context_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "controlled_variable_dimension": (
                ControlledPropagationProtocolControlledVariable.REQUIREMENT_RELATION.value
            ),
            "captured_at": self.captured_at.isoformat(),
            "captured_monotonic_usec": self.captured_monotonic_usec,
            "pair_id": self.pair_artifact.pair_id,
            "relation": self.relation.value,
            "ordering_relation": DependencyRelation.AFTER.value,
            "source_unit": self.source_unit,
            "dependent_unit": self.dependent_unit,
            "source_fixture": self.pair_artifact.spec.source.to_dict(),
            "dependent_fixture": self.pair_artifact.spec.dependent.to_dict(),
            "source_artifact_sha256": self.pair_artifact.source_artifact.sha256,
            "dependent_artifact_sha256": self.pair_artifact.dependent_sha256,
            "experiment_id": self.manifest.experiment_id,
            "manifest_created_at": self.manifest.created_at.isoformat(),
            "scenario_id": scenario.scenario_id,
            "scenario_description": scenario.description,
            "fault_mode": scenario.fault_mode.value,
            "target_unit": scenario.target_unit,
            "expected_fault_active_states": list(scenario.expected_fault_active_states),
            "baseline_timeout_seconds": scenario.baseline_timeout_seconds,
            "fault_timeout_seconds": scenario.fault_timeout_seconds,
            "recovery_timeout_seconds": scenario.recovery_timeout_seconds,
            "evidence_grace_seconds": scenario.evidence_grace_seconds,
            "live_policy": self.policy.to_dict(),
            "controlled_propagation_live_schema_version": (
                CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION
            ),
            "controlled_propagation_experiment_schema_version": (
                CONTROLLED_PROPAGATION_EXPERIMENT_SCHEMA_VERSION
            ),
            "scenario_labels_included_in_protocol_identity": False,
            "experiment_identity_included_in_protocol_identity": False,
            "manifest_creation_time_included_in_protocol_identity": False,
            "capture_time_included_in_protocol_identity": False,
            "execution_backend_configuration_fully_captured": False,
            "execution_backend_binding_assigned": False,
            "capture_before_live_runner_invocation_claim": False,
            "capture_before_any_mutation_claim": False,
            "capture_before_fault_ground_truth_claim": False,
            "same_boot_claim_assigned": False,
            "replication_claim_assigned": False,
            "statistical_significance_assigned": False,
            "causal_effect_estimate_assigned": False,
            "treatment_effect_claim_assigned": False,
            "experiment_wide_single_variable_isolation_claim": False,
            "generalization_beyond_captured_protocol_permitted": False,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "universal_systemd_behavior_claim": False,
            "probabilistic_confidence_assigned": False,
            "scalar_score_assigned": False,
        }


@dataclass(frozen=True, slots=True)
class ControlledPropagationProtocolComparison:
    """Relation-only comparison of two captured pre-execution input protocols."""

    comparison_id: str
    requires_capture: ControlledPropagationProtocolCapture
    wants_capture: ControlledPropagationProtocolCapture

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.comparison_id, "protocomp-"):
            raise ControlledPropagationProtocolContractError(
                "comparison_id must be a protocomp- prefixed SHA-256 identity"
            )
        if not isinstance(
            self.requires_capture,
            ControlledPropagationProtocolCapture,
        ):
            raise ControlledPropagationProtocolContractError(
                "requires_capture must be a ControlledPropagationProtocolCapture"
            )
        if not isinstance(
            self.wants_capture,
            ControlledPropagationProtocolCapture,
        ):
            raise ControlledPropagationProtocolContractError(
                "wants_capture must be a ControlledPropagationProtocolCapture"
            )
        _validate_protocol_comparability(
            self.requires_capture,
            self.wants_capture,
        )
        expected = _comparison_id(self.requires_capture, self.wants_capture)
        if self.comparison_id != expected:
            raise ControlledPropagationProtocolContractError(
                "comparison_id does not match exact protocol captures"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION,
            "comparison_id": self.comparison_id,
            "protocol_scope": ControlledPropagationProtocolScope.TYPED_PRE_EXECUTION_INPUTS.value,
            "controlled_variable_dimension": (
                ControlledPropagationProtocolControlledVariable.REQUIREMENT_RELATION.value
            ),
            "requires_capture_id": self.requires_capture.capture_id,
            "wants_capture_id": self.wants_capture.capture_id,
            "requires_protocol_id": self.requires_capture.protocol_id,
            "wants_protocol_id": self.wants_capture.protocol_id,
            "shared_contrast_context_id": self.requires_capture.contrast_context_id,
            "requires_relation": self.requires_capture.relation.value,
            "wants_relation": self.wants_capture.relation.value,
            "captured_input_protocol_relation_only_comparable": True,
            "same_source_fixture_spec_required": True,
            "same_dependent_fixture_spec_required": True,
            "same_source_artifact_bytes_required": True,
            "same_fault_scenario_execution_semantics_required": True,
            "same_live_sampling_policy_required": True,
            "dependent_unit_relation_only_rendering_required": True,
            "distinct_protocol_identity_required": True,
            "distinct_capture_identity_required": True,
            "distinct_experiment_identity_required": True,
            "distinct_scenario_identity_required": True,
            "execution_backend_configuration_fully_captured": False,
            "execution_backend_binding_assigned": False,
            "capture_order_relative_to_mutation_proven": False,
            "same_boot_available_at_pre_execution_capture": False,
            "execution_outcome_compared": False,
            "arm_statistical_independence_assumed": False,
            "sample_statistical_independence_assumed": False,
            "replication_claim_assigned": False,
            "statistical_significance_assigned": False,
            "causal_effect_estimate_assigned": False,
            "treatment_effect_claim_assigned": False,
            "experiment_wide_single_variable_isolation_claim": False,
            "generalization_beyond_captured_protocol_permitted": False,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "universal_systemd_behavior_claim": False,
            "probabilistic_confidence_assigned": False,
            "scalar_score_assigned": False,
        }


def capture_controlled_propagation_protocol(
    pair_artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
    policy: ControlledPropagationLivePolicy,
) -> ControlledPropagationProtocolCapture:
    """Capture typed pre-execution inputs without mutating system state."""

    if not isinstance(pair_artifact, SystemdPropagationPairArtifact):
        raise ControlledPropagationProtocolContractError(
            "pair_artifact must be a SystemdPropagationPairArtifact"
        )
    if not isinstance(manifest, FaultExperimentManifest):
        raise ControlledPropagationProtocolContractError(
            "manifest must be a FaultExperimentManifest"
        )
    if not isinstance(policy, ControlledPropagationLivePolicy):
        raise ControlledPropagationProtocolContractError(
            "policy must be a ControlledPropagationLivePolicy"
        )
    _validate_protocol_inputs(pair_artifact, manifest)

    captured_at = datetime.now(timezone.utc)
    captured_monotonic_usec = time.monotonic_ns() // 1_000
    manifest_fingerprint = _manifest_fingerprint(manifest)
    protocol_id = _protocol_id(pair_artifact, manifest, policy)
    context_id = _contrast_context_id(pair_artifact, manifest, policy)
    capture_id = _capture_id(
        protocol_id=protocol_id,
        contrast_context_id=context_id,
        manifest_fingerprint=manifest_fingerprint,
        pair_artifact=pair_artifact,
        captured_at=captured_at,
        captured_monotonic_usec=captured_monotonic_usec,
    )
    return ControlledPropagationProtocolCapture(
        capture_id=capture_id,
        protocol_id=protocol_id,
        contrast_context_id=context_id,
        manifest_fingerprint=manifest_fingerprint,
        pair_artifact=pair_artifact,
        manifest=manifest,
        policy=policy,
        captured_at=captured_at,
        captured_monotonic_usec=captured_monotonic_usec,
    )


def compare_controlled_propagation_protocols(
    requires_capture: ControlledPropagationProtocolCapture,
    wants_capture: ControlledPropagationProtocolCapture,
) -> ControlledPropagationProtocolComparison:
    """Compare one Requires and one Wants captured input protocol conservatively."""

    if not isinstance(requires_capture, ControlledPropagationProtocolCapture):
        raise ControlledPropagationProtocolContractError(
            "requires_capture must be a ControlledPropagationProtocolCapture"
        )
    if not isinstance(wants_capture, ControlledPropagationProtocolCapture):
        raise ControlledPropagationProtocolContractError(
            "wants_capture must be a ControlledPropagationProtocolCapture"
        )
    _validate_protocol_comparability(requires_capture, wants_capture)
    return ControlledPropagationProtocolComparison(
        comparison_id=_comparison_id(requires_capture, wants_capture),
        requires_capture=requires_capture,
        wants_capture=wants_capture,
    )


def _validate_protocol_inputs(
    pair_artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
) -> None:
    if pair_artifact.spec.requirement_relation not in {
        DependencyRelation.REQUIRES,
        DependencyRelation.WANTS,
    }:
        raise ControlledPropagationProtocolContractError(
            "protocol supports only Requires or Wants controlled pairs"
        )
    if manifest.scenario.target_unit != pair_artifact.source_unit:
        raise ControlledPropagationProtocolContractError(
            "manifest must target the controlled pair source"
        )
    if manifest.scenario.fault_mode is not FaultMode.SERVICE_INACTIVE:
        raise ControlledPropagationProtocolContractError(
            "current controlled propagation protocol requires SERVICE_INACTIVE"
        )


def _validate_protocol_comparability(
    requires_capture: ControlledPropagationProtocolCapture,
    wants_capture: ControlledPropagationProtocolCapture,
) -> None:
    if requires_capture.relation is not DependencyRelation.REQUIRES:
        raise ControlledPropagationProtocolContractError(
            "requires_capture must carry Requires relation"
        )
    if wants_capture.relation is not DependencyRelation.WANTS:
        raise ControlledPropagationProtocolContractError(
            "wants_capture must carry Wants relation"
        )
    if requires_capture.capture_id == wants_capture.capture_id:
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures must have distinct capture identities"
        )
    if requires_capture.protocol_id == wants_capture.protocol_id:
        raise ControlledPropagationProtocolContractError(
            "Requires and Wants input protocols must have distinct protocol identities"
        )
    if requires_capture.manifest.experiment_id == wants_capture.manifest.experiment_id:
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures require distinct experiment identities"
        )
    if (
        requires_capture.manifest.scenario.scenario_id
        == wants_capture.manifest.scenario.scenario_id
    ):
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures require distinct scenario identities"
        )
    if requires_capture.contrast_context_id != wants_capture.contrast_context_id:
        raise ControlledPropagationProtocolContractError(
            "captured protocols differ outside the requirement relation"
        )

    req_pair = requires_capture.pair_artifact
    want_pair = wants_capture.pair_artifact
    if req_pair.spec.source != want_pair.spec.source:
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures must use the same source fixture spec"
        )
    if req_pair.spec.dependent != want_pair.spec.dependent:
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures must use the same dependent fixture spec"
        )
    if (
        req_pair.source_artifact.unit_text != want_pair.source_artifact.unit_text
        or req_pair.source_artifact.sha256 != want_pair.source_artifact.sha256
    ):
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures must use byte-identical source artifacts"
        )
    _validate_dependent_relation_only_difference(req_pair, want_pair)
    if requires_capture.policy != wants_capture.policy:
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures must use the same live sampling policy"
        )
    if _scenario_semantic_payload(
        requires_capture.manifest
    ) != _scenario_semantic_payload(wants_capture.manifest):
        raise ControlledPropagationProtocolContractError(
            "paired protocol captures must use identical fault execution semantics"
        )


def _validate_dependent_relation_only_difference(
    requires_pair: SystemdPropagationPairArtifact,
    wants_pair: SystemdPropagationPairArtifact,
) -> None:
    req_lines = requires_pair.dependent_unit_text.splitlines()
    want_lines = wants_pair.dependent_unit_text.splitlines()
    if len(req_lines) != len(want_lines):
        raise ControlledPropagationProtocolContractError(
            "dependent artifacts differ beyond one requirement relation line"
        )
    differences = tuple(
        (left, right)
        for left, right in zip(req_lines, want_lines, strict=True)
        if left != right
    )
    expected = (
        (
            f"Requires={requires_pair.source_unit}",
            f"Wants={wants_pair.source_unit}",
        ),
    )
    if differences != expected:
        raise ControlledPropagationProtocolContractError(
            "dependent artifacts must differ only by Requires versus Wants"
        )
    if requires_pair.dependent_sha256 == wants_pair.dependent_sha256:
        raise ControlledPropagationProtocolContractError(
            "relation-specific dependent artifacts must have distinct digests"
        )


def _protocol_id(
    pair_artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
    policy: ControlledPropagationLivePolicy,
) -> str:
    return "protoprot-" + _content_digest(
        _PROTOCOL_ID_DOMAIN,
        _protocol_semantic_payload(pair_artifact, manifest, policy),
    )


def _contrast_context_id(
    pair_artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
    policy: ControlledPropagationLivePolicy,
) -> str:
    return "protoctx-" + _content_digest(
        _CONTRAST_CONTEXT_ID_DOMAIN,
        _contrast_context_payload(pair_artifact, manifest, policy),
    )


def _manifest_fingerprint(manifest: FaultExperimentManifest) -> str:
    return "manifestfp-" + _content_digest(
        _MANIFEST_FINGERPRINT_DOMAIN,
        manifest.to_dict(),
    )


def _capture_id(
    *,
    protocol_id: str,
    contrast_context_id: str,
    manifest_fingerprint: str,
    pair_artifact: SystemdPropagationPairArtifact,
    captured_at: datetime,
    captured_monotonic_usec: int,
) -> str:
    payload: dict[str, object] = {
        "protocol_id": protocol_id,
        "contrast_context_id": contrast_context_id,
        "manifest_fingerprint": manifest_fingerprint,
        "pair_id": pair_artifact.pair_id,
        "source_artifact_created_at": pair_artifact.source_artifact.created_at.isoformat(),
        "pair_artifact_created_at": pair_artifact.created_at.isoformat(),
        "captured_at": captured_at.isoformat(),
        "captured_monotonic_usec": captured_monotonic_usec,
    }
    return "protocap-" + _content_digest(_CAPTURE_ID_DOMAIN, payload)


def _comparison_id(
    requires_capture: ControlledPropagationProtocolCapture,
    wants_capture: ControlledPropagationProtocolCapture,
) -> str:
    payload: dict[str, object] = {
        "controlled_variable_dimension": (
            ControlledPropagationProtocolControlledVariable.REQUIREMENT_RELATION.value
        ),
        "shared_contrast_context_id": requires_capture.contrast_context_id,
        "requires_capture_id": requires_capture.capture_id,
        "wants_capture_id": wants_capture.capture_id,
        "requires_protocol_id": requires_capture.protocol_id,
        "wants_protocol_id": wants_capture.protocol_id,
    }
    return "protocomp-" + _content_digest(_COMPARISON_ID_DOMAIN, payload)


def _protocol_semantic_payload(
    pair_artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
    policy: ControlledPropagationLivePolicy,
) -> dict[str, object]:
    payload = _contrast_context_payload(pair_artifact, manifest, policy)
    payload.update(
        {
            "requirement_relation": pair_artifact.spec.requirement_relation.value,
            "pair_id": pair_artifact.pair_id,
            "dependent_artifact_sha256": pair_artifact.dependent_sha256,
        }
    )
    return payload


def _contrast_context_payload(
    pair_artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
    policy: ControlledPropagationLivePolicy,
) -> dict[str, object]:
    return {
        "protocol_schema_version": CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION,
        "protocol_scope": ControlledPropagationProtocolScope.TYPED_PRE_EXECUTION_INPUTS.value,
        "controlled_variable_dimension": (
            ControlledPropagationProtocolControlledVariable.REQUIREMENT_RELATION.value
        ),
        "source_fixture": pair_artifact.spec.source.to_dict(),
        "dependent_fixture": pair_artifact.spec.dependent.to_dict(),
        "ordering_relation": DependencyRelation.AFTER.value,
        "source_artifact_sha256": pair_artifact.source_artifact.sha256,
        "scenario_execution_semantics": _scenario_semantic_payload(manifest),
        "live_sampling_policy": _policy_semantic_payload(policy),
        "controlled_propagation_live_schema_version": (
            CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION
        ),
        "controlled_propagation_experiment_schema_version": (
            CONTROLLED_PROPAGATION_EXPERIMENT_SCHEMA_VERSION
        ),
        "execution_backend_configuration_fully_captured": False,
    }


def _scenario_semantic_payload(manifest: FaultExperimentManifest) -> dict[str, object]:
    scenario = manifest.scenario
    return {
        "target_unit": scenario.target_unit,
        "fault_mode": scenario.fault_mode.value,
        "expected_fault_active_states": list(scenario.expected_fault_active_states),
        "baseline_timeout_seconds": scenario.baseline_timeout_seconds,
        "fault_timeout_seconds": scenario.fault_timeout_seconds,
        "recovery_timeout_seconds": scenario.recovery_timeout_seconds,
        "evidence_grace_seconds": scenario.evidence_grace_seconds,
    }


def _policy_semantic_payload(
    policy: ControlledPropagationLivePolicy,
) -> dict[str, object]:
    return {
        "max_sample_gap_usec": policy.max_sample_gap_usec,
        "sample_interval_seconds": policy.sample_interval_seconds,
        "max_samples": policy.max_samples,
        "capture_join_timeout_seconds": policy.capture_join_timeout_seconds,
    }


def _content_digest(domain: bytes, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(domain + encoded).hexdigest()


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ControlledPropagationProtocolContractError(
            f"{field_name} must be a datetime"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ControlledPropagationProtocolContractError(
            f"{field_name} must be timezone-aware"
        )


def _is_prefixed_digest(value: object, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value[len(prefix) :]
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )
