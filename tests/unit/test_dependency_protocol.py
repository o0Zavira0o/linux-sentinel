from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.propagation_experiment import (
    SystemdPropagationPairSpec,
    build_systemd_propagation_pair,
)
from sentinel_x.dependency.propagation_live import ControlledPropagationLivePolicy
from sentinel_x.dependency.protocol import (
    CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION,
    ControlledPropagationProtocolCapture,
    ControlledPropagationProtocolContractError,
    ControlledPropagationProtocolControlledVariable,
    ControlledPropagationProtocolScope,
    capture_controlled_propagation_protocol,
    compare_controlled_propagation_protocols,
)
from sentinel_x.lab.fixture import SystemdLabFixtureSpec
from sentinel_x.lab.models import FaultExperimentManifest, FaultMode, FaultScenario

_SOURCE_ID = "p53-src"
_DEPENDENT_ID = "p53-dep"
_SOURCE = f"sentinel-x-lab-{_SOURCE_ID}.service"
_CREATED = datetime(2026, 8, 16, 6, 0, tzinfo=timezone.utc)


def _artifact(
    relation: DependencyRelation,
    *,
    source_runtime: int = 120,
    dependent_runtime: int = 120,
):
    return build_systemd_propagation_pair(
        SystemdPropagationPairSpec(
            source=SystemdLabFixtureSpec(
                _SOURCE_ID,
                runtime_max_seconds=source_runtime,
            ),
            dependent=SystemdLabFixtureSpec(
                _DEPENDENT_ID,
                runtime_max_seconds=dependent_runtime,
            ),
            requirement_relation=relation,
        )
    )


def _manifest(
    relation: DependencyRelation,
    *,
    digit: str,
    scenario_id: str | None = None,
    description: str | None = None,
    target_unit: str = _SOURCE,
    fault_mode: FaultMode = FaultMode.SERVICE_INACTIVE,
    baseline_timeout_seconds: float = 5.0,
    fault_timeout_seconds: float = 5.0,
    recovery_timeout_seconds: float = 10.0,
    evidence_grace_seconds: float = 2.0,
    created_at: datetime = _CREATED,
) -> FaultExperimentManifest:
    label = "requires" if relation is DependencyRelation.REQUIRES else "wants"
    return FaultExperimentManifest(
        scenario=FaultScenario(
            scenario_id=scenario_id or f"p53-{label}-protocol",
            description=description or f"Protocol capture for {label} arm.",
            target_unit=target_unit,
            fault_mode=fault_mode,
            baseline_timeout_seconds=baseline_timeout_seconds,
            fault_timeout_seconds=fault_timeout_seconds,
            recovery_timeout_seconds=recovery_timeout_seconds,
            evidence_grace_seconds=evidence_grace_seconds,
        ),
        experiment_id="exp-" + digit * 32,
        created_at=created_at,
    )


def _policy(
    *,
    max_sample_gap_usec: int = 250_000,
    sample_interval_seconds: float = 0.1,
    max_samples: int = 256,
    capture_join_timeout_seconds: float = 5.0,
) -> ControlledPropagationLivePolicy:
    return ControlledPropagationLivePolicy(
        max_sample_gap_usec=max_sample_gap_usec,
        sample_interval_seconds=sample_interval_seconds,
        max_samples=max_samples,
        capture_join_timeout_seconds=capture_join_timeout_seconds,
    )


def _capture(
    relation: DependencyRelation,
    *,
    digit: str,
    artifact=None,
    manifest=None,
    policy=None,
) -> ControlledPropagationProtocolCapture:
    selected_artifact = artifact or _artifact(relation)
    selected_manifest = manifest or _manifest(relation, digit=digit)
    selected_policy = policy or _policy()
    return capture_controlled_propagation_protocol(
        selected_artifact,
        selected_manifest,
        selected_policy,
    )


def _canonical_pair():
    requires = _capture(DependencyRelation.REQUIRES, digit="1")
    wants = _capture(DependencyRelation.WANTS, digit="2")
    return requires, wants


class ControlledPropagationProtocolTests(unittest.TestCase):
    def test_capture_is_typed_pre_execution_input_scope(self) -> None:
        capture = _capture(DependencyRelation.REQUIRES, digit="1")
        payload = capture.to_dict()

        self.assertEqual(
            payload["schema_version"],
            CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION,
        )
        self.assertEqual(
            payload["protocol_scope"],
            ControlledPropagationProtocolScope.TYPED_PRE_EXECUTION_INPUTS.value,
        )
        self.assertEqual(
            payload["controlled_variable_dimension"],
            ControlledPropagationProtocolControlledVariable.REQUIREMENT_RELATION.value,
        )

    def test_protocol_identity_is_deterministic_across_run_labels(self) -> None:
        artifact = _artifact(DependencyRelation.REQUIRES)
        first = _capture(
            DependencyRelation.REQUIRES,
            digit="1",
            artifact=artifact,
            manifest=_manifest(
                DependencyRelation.REQUIRES,
                digit="1",
                scenario_id="p53-requires-a",
                description="First run label.",
                created_at=_CREATED,
            ),
        )
        second = _capture(
            DependencyRelation.REQUIRES,
            digit="2",
            artifact=artifact,
            manifest=_manifest(
                DependencyRelation.REQUIRES,
                digit="2",
                scenario_id="p53-requires-b",
                description="Second run label.",
                created_at=_CREATED + timedelta(seconds=1),
            ),
        )

        self.assertEqual(first.protocol_id, second.protocol_id)
        self.assertEqual(first.contrast_context_id, second.contrast_context_id)
        self.assertNotEqual(first.manifest_fingerprint, second.manifest_fingerprint)

    def test_protocol_identity_changes_with_requirement_relation(self) -> None:
        requires, wants = _canonical_pair()
        self.assertNotEqual(requires.protocol_id, wants.protocol_id)

    def test_protocol_identity_ignores_artifact_creation_timestamps(self) -> None:
        first_artifact = _artifact(DependencyRelation.REQUIRES)
        second_artifact = _artifact(DependencyRelation.REQUIRES)
        manifest = _manifest(DependencyRelation.REQUIRES, digit="1")
        first = _capture(
            DependencyRelation.REQUIRES,
            digit="1",
            artifact=first_artifact,
            manifest=manifest,
        )
        second = _capture(
            DependencyRelation.REQUIRES,
            digit="1",
            artifact=second_artifact,
            manifest=manifest,
        )

        self.assertEqual(first.protocol_id, second.protocol_id)
        self.assertEqual(first.contrast_context_id, second.contrast_context_id)

    def test_contrast_context_excludes_only_relation_derived_protocol_fields(
        self,
    ) -> None:
        requires, wants = _canonical_pair()
        self.assertEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_source_fixture_drift_changes_contrast_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            artifact=_artifact(DependencyRelation.WANTS, source_runtime=121),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_dependent_fixture_drift_changes_contrast_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            artifact=_artifact(DependencyRelation.WANTS, dependent_runtime=121),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_fault_timing_drift_changes_protocol_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            manifest=_manifest(
                DependencyRelation.WANTS,
                digit="2",
                fault_timeout_seconds=6.0,
            ),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_every_scenario_timeout_participates_in_protocol_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        variants = (
            {"baseline_timeout_seconds": 6.0},
            {"fault_timeout_seconds": 6.0},
            {"recovery_timeout_seconds": 11.0},
            {"evidence_grace_seconds": 3.0},
        )
        for overrides in variants:
            with self.subTest(overrides=overrides):
                wants = _capture(
                    DependencyRelation.WANTS,
                    digit="2",
                    manifest=_manifest(
                        DependencyRelation.WANTS,
                        digit="2",
                        **overrides,
                    ),
                )
                self.assertNotEqual(
                    requires.contrast_context_id,
                    wants.contrast_context_id,
                )

    def test_evidence_grace_drift_changes_protocol_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            manifest=_manifest(
                DependencyRelation.WANTS,
                digit="2",
                evidence_grace_seconds=3.0,
            ),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_sampling_gap_policy_drift_changes_protocol_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            policy=_policy(max_sample_gap_usec=300_000),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_sampling_interval_policy_drift_changes_protocol_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            policy=_policy(sample_interval_seconds=0.2),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_sampling_capacity_policy_drift_changes_protocol_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            policy=_policy(max_samples=512),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_capture_join_policy_drift_changes_protocol_context(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            policy=_policy(capture_join_timeout_seconds=6.0),
        )
        self.assertNotEqual(requires.contrast_context_id, wants.contrast_context_id)

    def test_capture_rejects_wrong_target(self) -> None:
        artifact = _artifact(DependencyRelation.REQUIRES)
        manifest = _manifest(
            DependencyRelation.REQUIRES,
            digit="1",
            target_unit="sentinel-x-lab-other.service",
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            capture_controlled_propagation_protocol(artifact, manifest, _policy())

    def test_capture_rejects_unsupported_fault_mode(self) -> None:
        artifact = _artifact(DependencyRelation.REQUIRES)
        manifest = _manifest(
            DependencyRelation.REQUIRES,
            digit="1",
            fault_mode=FaultMode.SERVICE_FAILED,
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            capture_controlled_propagation_protocol(artifact, manifest, _policy())

    def test_capture_rejects_untyped_inputs(self) -> None:
        artifact = _artifact(DependencyRelation.REQUIRES)
        manifest = _manifest(DependencyRelation.REQUIRES, digit="1")
        policy = _policy()
        with self.assertRaises(ControlledPropagationProtocolContractError):
            capture_controlled_propagation_protocol(object(), manifest, policy)  # type: ignore[arg-type]
        with self.assertRaises(ControlledPropagationProtocolContractError):
            capture_controlled_propagation_protocol(artifact, object(), policy)  # type: ignore[arg-type]
        with self.assertRaises(ControlledPropagationProtocolContractError):
            capture_controlled_propagation_protocol(artifact, manifest, object())  # type: ignore[arg-type]

    def test_capture_model_rejects_identity_drift(self) -> None:
        capture = _capture(DependencyRelation.REQUIRES, digit="1")
        for field_name, value in (
            ("capture_id", "protocap-" + "0" * 64),
            ("protocol_id", "protoprot-" + "0" * 64),
            ("contrast_context_id", "protoctx-" + "0" * 64),
            ("manifest_fingerprint", "manifestfp-" + "0" * 64),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ControlledPropagationProtocolContractError):
                    replace(capture, **{field_name: value})

    def test_capture_model_rejects_invalid_monotonic_time(self) -> None:
        capture = _capture(DependencyRelation.REQUIRES, digit="1")
        for value in (True, -1, 1.5, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ControlledPropagationProtocolContractError):
                    replace(capture, captured_monotonic_usec=value)  # type: ignore[arg-type]

    def test_capture_model_requires_timezone_aware_wall_clock(self) -> None:
        capture = _capture(DependencyRelation.REQUIRES, digit="1")
        with self.assertRaises(ControlledPropagationProtocolContractError):
            replace(capture, captured_at=capture.captured_at.replace(tzinfo=None))

    def test_capture_model_rejects_time_before_input_creation(self) -> None:
        capture = _capture(DependencyRelation.REQUIRES, digit="1")
        too_early = min(
            capture.pair_artifact.source_artifact.created_at,
            capture.pair_artifact.created_at,
            capture.manifest.created_at,
        ) - timedelta(microseconds=1)
        with self.assertRaises(ControlledPropagationProtocolContractError):
            replace(capture, captured_at=too_early)

    def test_serialization_omits_raw_unit_bodies(self) -> None:
        payload = _capture(DependencyRelation.REQUIRES, digit="1").to_dict()
        self.assertNotIn("unit_text", payload)
        self.assertNotIn("dependent_unit_text", payload)
        self.assertIn("source_artifact_sha256", payload)
        self.assertIn("dependent_artifact_sha256", payload)

    def test_serialization_keeps_full_manifest_labels_as_provenance(self) -> None:
        capture = _capture(
            DependencyRelation.REQUIRES,
            digit="1",
            manifest=_manifest(
                DependencyRelation.REQUIRES,
                digit="1",
                scenario_id="p53-label-proof",
                description="Human-readable provenance label.",
            ),
        )
        payload = capture.to_dict()
        self.assertEqual(payload["scenario_id"], "p53-label-proof")
        self.assertEqual(
            payload["scenario_description"],
            "Human-readable provenance label.",
        )
        self.assertFalse(payload["scenario_labels_included_in_protocol_identity"])

    def test_serialization_does_not_invent_backend_completeness_or_mutation_order(
        self,
    ) -> None:
        payload = _capture(DependencyRelation.REQUIRES, digit="1").to_dict()
        self.assertFalse(payload["execution_backend_configuration_fully_captured"])
        self.assertFalse(payload["execution_backend_binding_assigned"])
        self.assertFalse(payload["capture_before_live_runner_invocation_claim"])
        self.assertFalse(payload["capture_before_any_mutation_claim"])
        self.assertFalse(payload["capture_before_fault_ground_truth_claim"])
        self.assertFalse(payload["same_boot_claim_assigned"])

    def test_serialization_preserves_no_inference_claim_boundaries(self) -> None:
        payload = _capture(DependencyRelation.REQUIRES, digit="1").to_dict()
        for key in (
            "replication_claim_assigned",
            "statistical_significance_assigned",
            "causal_effect_estimate_assigned",
            "treatment_effect_claim_assigned",
            "experiment_wide_single_variable_isolation_claim",
            "generalization_beyond_captured_protocol_permitted",
            "causal_claim",
            "propagation_claim_assigned",
            "root_cause_claim_assigned",
            "universal_systemd_behavior_claim",
            "probabilistic_confidence_assigned",
            "scalar_score_assigned",
        ):
            self.assertFalse(payload[key], key)

    def test_canonical_requires_wants_protocols_are_comparable(self) -> None:
        requires, wants = _canonical_pair()
        comparison = compare_controlled_propagation_protocols(requires, wants)
        self.assertEqual(
            comparison.requires_capture.contrast_context_id,
            comparison.wants_capture.contrast_context_id,
        )
        self.assertNotEqual(
            comparison.requires_capture.protocol_id,
            comparison.wants_capture.protocol_id,
        )

    def test_comparison_rejects_reversed_arm_assignment(self) -> None:
        requires, wants = _canonical_pair()
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(wants, requires)

    def test_comparison_rejects_same_relation(self) -> None:
        first = _capture(DependencyRelation.REQUIRES, digit="1")
        second = _capture(
            DependencyRelation.REQUIRES,
            digit="2",
            manifest=_manifest(
                DependencyRelation.REQUIRES,
                digit="2",
                scenario_id="p53-requires-second",
            ),
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(first, second)

    def test_comparison_rejects_fixture_drift(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            artifact=_artifact(DependencyRelation.WANTS, dependent_runtime=121),
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(requires, wants)

    def test_comparison_rejects_fault_timing_drift(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            manifest=_manifest(
                DependencyRelation.WANTS,
                digit="2",
                recovery_timeout_seconds=11.0,
            ),
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(requires, wants)

    def test_comparison_rejects_sampling_policy_drift(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            policy=_policy(max_samples=512),
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(requires, wants)

    def test_comparison_rejects_duplicate_experiment_identity(self) -> None:
        requires = _capture(DependencyRelation.REQUIRES, digit="1")
        wants = _capture(
            DependencyRelation.WANTS,
            digit="1",
            manifest=_manifest(DependencyRelation.WANTS, digit="1"),
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(requires, wants)

    def test_comparison_rejects_duplicate_scenario_identity(self) -> None:
        requires = _capture(
            DependencyRelation.REQUIRES,
            digit="1",
            manifest=_manifest(
                DependencyRelation.REQUIRES,
                digit="1",
                scenario_id="p53-shared-scenario",
            ),
        )
        wants = _capture(
            DependencyRelation.WANTS,
            digit="2",
            manifest=_manifest(
                DependencyRelation.WANTS,
                digit="2",
                scenario_id="p53-shared-scenario",
            ),
        )
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(requires, wants)

    def test_comparison_identity_is_deterministic_for_same_captures(self) -> None:
        requires, wants = _canonical_pair()
        first = compare_controlled_propagation_protocols(requires, wants)
        second = compare_controlled_propagation_protocols(requires, wants)
        self.assertEqual(first.comparison_id, second.comparison_id)

    def test_comparison_model_rejects_identity_drift(self) -> None:
        requires, wants = _canonical_pair()
        comparison = compare_controlled_propagation_protocols(requires, wants)
        with self.assertRaises(ControlledPropagationProtocolContractError):
            replace(comparison, comparison_id="protocomp-" + "0" * 64)

    def test_comparison_rejects_untyped_inputs(self) -> None:
        requires, wants = _canonical_pair()
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(object(), wants)  # type: ignore[arg-type]
        with self.assertRaises(ControlledPropagationProtocolContractError):
            compare_controlled_propagation_protocols(requires, object())  # type: ignore[arg-type]

    def test_comparison_serialization_is_input_protocol_only(self) -> None:
        requires, wants = _canonical_pair()
        payload = compare_controlled_propagation_protocols(requires, wants).to_dict()
        self.assertTrue(payload["captured_input_protocol_relation_only_comparable"])
        self.assertFalse(payload["execution_backend_configuration_fully_captured"])
        self.assertFalse(payload["execution_backend_binding_assigned"])
        self.assertFalse(payload["capture_order_relative_to_mutation_proven"])
        self.assertFalse(payload["same_boot_available_at_pre_execution_capture"])
        self.assertFalse(payload["execution_outcome_compared"])

    def test_comparison_serialization_preserves_no_replication_or_causal_claims(
        self,
    ) -> None:
        requires, wants = _canonical_pair()
        payload = compare_controlled_propagation_protocols(requires, wants).to_dict()
        for key in (
            "arm_statistical_independence_assumed",
            "sample_statistical_independence_assumed",
            "replication_claim_assigned",
            "statistical_significance_assigned",
            "causal_effect_estimate_assigned",
            "treatment_effect_claim_assigned",
            "experiment_wide_single_variable_isolation_claim",
            "generalization_beyond_captured_protocol_permitted",
            "causal_claim",
            "propagation_claim_assigned",
            "root_cause_claim_assigned",
            "universal_systemd_behavior_claim",
            "probabilistic_confidence_assigned",
            "scalar_score_assigned",
        ):
            self.assertFalse(payload[key], key)

    def test_fixed_controlled_variable_has_no_arbitrary_caller_dimension(self) -> None:
        self.assertEqual(
            list(ControlledPropagationProtocolControlledVariable),
            [ControlledPropagationProtocolControlledVariable.REQUIREMENT_RELATION],
        )


if __name__ == "__main__":
    unittest.main()
