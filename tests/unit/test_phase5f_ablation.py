from __future__ import annotations

import copy
import json
import unittest

from sentinel_x._phase5f.ablation import (
    ABLATION_FIELD_MAPPING_SHA256,
    ablation_mapping_base_condition,
    ablation_mapping_ids,
    apply_ablation_mapping,
)

BOOT = "a" * 32
OTHER_BOOT = "b" * 32


class Phase5FAblationTests(unittest.TestCase):
    def _item(
        self,
        ref: str,
        kind: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return {
            "ref": ref,
            "kind": kind,
            "payload": payload,
        }

    def _minimal_bundle(self) -> dict[str, object]:
        return {
            "schema_version": "sentinel-x.phase5f-evidence-bundle.v1",
            "case_id": "CASE-0001",
            "task": "Assess the operational evidence.",
            "environment": {
                "boot_id": BOOT,
                "host_alias": "host-a",
            },
            "evidence": [
                self._item(
                    "REF-0001",
                    "scope",
                    {
                        "source_unit": "source.service",
                        "target_unit": "target.service",
                        "boot_id": BOOT,
                        "analysis_window_usec": 5_000_000,
                    },
                ),
                self._item(
                    "REF-0002",
                    "topology_relation",
                    {
                        "subject_unit": "target.service",
                        "object_unit": "source.service",
                        "relation": "requires",
                        "boot_id": BOOT,
                    },
                ),
                self._item(
                    "REF-0003",
                    "incident_timeline",
                    {
                        "unit": "source.service",
                        "state": "inactive",
                        "transition_monotonic_usec": 1_000_000,
                        "boot_id": BOOT,
                    },
                ),
                self._item(
                    "REF-0004",
                    "incident_timeline",
                    {
                        "unit": "target.service",
                        "state": "failed",
                        "transition_monotonic_usec": 900_000,
                        "boot_id": BOOT,
                    },
                ),
                self._item(
                    "REF-0005",
                    "observation",
                    {
                        "unit": "target.service",
                        "status": "failed",
                        "boot_id": BOOT,
                    },
                ),
                self._item(
                    "REF-0006",
                    "coverage",
                    {
                        "unit": "target.service",
                        "boot_id": BOOT,
                        "window_start_usec": 1_000_000,
                        "window_end_usec": 6_000_000,
                        "largest_gap_usec": 100_000,
                        "max_sample_gap_usec": 250_000,
                        "healthy_count": 1,
                        "failed_count": 19,
                    },
                ),
                self._item(
                    "REF-0007",
                    "provenance",
                    {
                        "boot_id": BOOT,
                        "candidate_id": "candidate-1",
                        "graph_version_id": "graph-1",
                        "topology_id": "topology-1",
                        "requirement_observed_at": "2026-08-19T00:00:00Z",
                        "cross_boot_temporal_comparison_permitted": False,
                    },
                ),
                self._item(
                    "REF-0008",
                    "intervention_context",
                    {
                        "experiment_id": "exp-1",
                        "scenario_id": "scenario-1",
                        "fault_mode": "service_inactive",
                        "boot_id": BOOT,
                        "started_monotonic_usec": 800_000,
                        "ended_monotonic_usec": 1_200_000,
                    },
                ),
            ],
        }

    def _full_bundle(self) -> dict[str, object]:
        bundle = self._minimal_bundle()
        evidence = bundle["evidence"]
        assert isinstance(evidence, list)
        evidence.append(
            self._item(
                "REF-0020",
                "candidate",
                {
                    "candidate_id": "candidate-1",
                    "causal_claim": False,
                    "topology_temporal_applicability_claim": False,
                    "nested": {
                        "evidence_class": "affected_anomaly_observed",
                        "fact": "retain-me",
                    },
                },
            )
        )
        evidence.append(
            self._item(
                "REF-0021",
                "controlled_record",
                {
                    "root_cause_claim_assigned": False,
                    "universal_systemd_behavior_claim": False,
                    "nested": [
                        {
                            "propagation_claim_assigned": False,
                            "fact": 7,
                        }
                    ],
                },
            )
        )
        evidence.append(
            self._item(
                "REF-0026",
                "synthesis",
                {
                    "interpretation": "supportive_without_counter_or_negative",
                    "causal_claim": False,
                    "propagation_claim_assigned": False,
                    "factual_count": 3,
                },
            )
        )
        return bundle

    def _evidence_by_ref(
        self,
        bundle: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        evidence = bundle["evidence"]
        assert isinstance(evidence, list)
        return {
            item["ref"]: item
            for item in evidence
            if isinstance(item, dict) and isinstance(item.get("ref"), str)
        }

    def test_frozen_mapping_identity_and_base_conditions(self) -> None:
        self.assertEqual(
            ABLATION_FIELD_MAPPING_SHA256,
            "5a4a7188742788954c2d7b1d11085057ab56594327c8306305286d9ba1b41844",
        )
        self.assertEqual(len(ablation_mapping_ids()), 7)
        self.assertEqual(len(set(ablation_mapping_ids())), 7)
        for mapping_id in ablation_mapping_ids()[:6]:
            self.assertEqual(ablation_mapping_base_condition(mapping_id), "minimal")
        self.assertEqual(
            ablation_mapping_base_condition(ablation_mapping_ids()[6]),
            "full",
        )

    def test_unknown_mapping_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown Phase-5F ablation"):
            apply_ablation_mapping(self._minimal_bundle(), "unknown")

    def test_transform_is_deterministic_and_does_not_mutate_source(self) -> None:
        bundle = self._minimal_bundle()
        before = copy.deepcopy(bundle)
        first = apply_ablation_mapping(bundle, "minimal-minus-topology")
        second = apply_ablation_mapping(bundle, "minimal-minus-topology")
        self.assertEqual(bundle, before)
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )

    def test_topology_mapping_drops_kind_and_only_preregistered_payload_keys(
        self,
    ) -> None:
        result = apply_ablation_mapping(
            self._minimal_bundle(),
            "minimal-minus-topology",
        )
        evidence = result["evidence"]
        assert isinstance(evidence, list)
        self.assertNotIn("topology_relation", {item["kind"] for item in evidence})
        provenance = self._evidence_by_ref(result)["REF-0007"]["payload"]
        assert isinstance(provenance, dict)
        self.assertEqual(
            provenance,
            {
                "boot_id": BOOT,
                "cross_boot_temporal_comparison_permitted": False,
            },
        )
        self.assertIn("boot_id", self._evidence_by_ref(result)["REF-0001"]["payload"])

    def test_coverage_mapping_drops_only_coverage_item(self) -> None:
        bundle = self._minimal_bundle()
        result = apply_ablation_mapping(bundle, "minimal-minus-coverage")
        before_refs = [item["ref"] for item in bundle["evidence"]]
        after_refs = [item["ref"] for item in result["evidence"]]
        self.assertEqual(
            after_refs,
            [ref for ref in before_refs if ref != "REF-0006"],
        )
        self.assertEqual(result["environment"], bundle["environment"])

    def test_boot_provenance_mapping_removes_boot_everywhere_and_provenance(
        self,
    ) -> None:
        result = apply_ablation_mapping(
            self._minimal_bundle(),
            "minimal-minus-boot-provenance",
        )
        environment = result["environment"]
        assert isinstance(environment, dict)
        self.assertEqual(environment, {"host_alias": "host-a"})
        evidence = result["evidence"]
        assert isinstance(evidence, list)
        self.assertNotIn("provenance", {item["kind"] for item in evidence})
        encoded = json.dumps(evidence, sort_keys=True)
        self.assertNotIn('"boot_id"', encoded)
        self.assertIn("experiment_id", encoded)

    def test_intervention_mapping_drops_only_intervention_context(self) -> None:
        bundle = self._minimal_bundle()
        result = apply_ablation_mapping(
            bundle,
            "minimal-minus-intervention-metadata",
        )
        before_refs = [item["ref"] for item in bundle["evidence"]]
        after_refs = [item["ref"] for item in result["evidence"]]
        self.assertEqual(after_refs, [ref for ref in before_refs if ref != "REF-0008"])

    def test_counterevidence_mapping_drops_only_reverse_target_timeline(self) -> None:
        result = apply_ablation_mapping(
            self._minimal_bundle(),
            "minimal-minus-counterevidence",
        )
        refs = [item["ref"] for item in result["evidence"]]
        self.assertNotIn("REF-0004", refs)
        self.assertIn("REF-0003", refs)
        self.assertIn("REF-0005", refs)
        self.assertEqual(len(refs), 7)

    def test_counterevidence_mapping_is_noop_for_forward_order(self) -> None:
        bundle = self._minimal_bundle()
        target = self._evidence_by_ref(bundle)["REF-0004"]["payload"]
        assert isinstance(target, dict)
        target["transition_monotonic_usec"] = 1_100_000
        self.assertEqual(
            apply_ablation_mapping(bundle, "minimal-minus-counterevidence"),
            bundle,
        )

    def test_counterevidence_mapping_is_noop_for_different_boot(self) -> None:
        bundle = self._minimal_bundle()
        target = self._evidence_by_ref(bundle)["REF-0004"]["payload"]
        assert isinstance(target, dict)
        target["boot_id"] = OTHER_BOOT
        self.assertEqual(
            apply_ablation_mapping(bundle, "minimal-minus-counterevidence"),
            bundle,
        )

    def test_counterevidence_mapping_is_noop_when_target_timeline_missing(self) -> None:
        bundle = self._minimal_bundle()
        bundle["evidence"] = [
            item for item in bundle["evidence"] if item["ref"] != "REF-0004"
        ]
        self.assertEqual(
            apply_ablation_mapping(bundle, "minimal-minus-counterevidence"),
            bundle,
        )

    def test_counterevidence_mapping_rejects_multiple_scope_items(self) -> None:
        bundle = self._minimal_bundle()
        bundle["evidence"].append(
            self._item(
                "REF-0009",
                "scope",
                {
                    "source_unit": "source.service",
                    "target_unit": "target.service",
                    "boot_id": BOOT,
                },
            )
        )
        with self.assertRaisesRegex(ValueError, "exactly one scope"):
            apply_ablation_mapping(bundle, "minimal-minus-counterevidence")

    def test_counterevidence_mapping_rejects_multiple_matching_timelines(self) -> None:
        bundle = self._minimal_bundle()
        bundle["evidence"].append(
            self._item(
                "REF-0009",
                "incident_timeline",
                {
                    "unit": "target.service",
                    "state": "failed",
                    "transition_monotonic_usec": 850_000,
                    "boot_id": BOOT,
                },
            )
        )
        with self.assertRaisesRegex(ValueError, "multiple matching timelines"):
            apply_ablation_mapping(bundle, "minimal-minus-counterevidence")

    def test_counterevidence_mapping_rejects_noninteger_matched_time(self) -> None:
        bundle = self._minimal_bundle()
        target = self._evidence_by_ref(bundle)["REF-0004"]["payload"]
        assert isinstance(target, dict)
        target["transition_monotonic_usec"] = "900000"
        with self.assertRaisesRegex(ValueError, "must be int"):
            apply_ablation_mapping(bundle, "minimal-minus-counterevidence")

    def test_timestamp_mapping_removes_exact_points_but_preserves_thresholds(
        self,
    ) -> None:
        result = apply_ablation_mapping(
            self._minimal_bundle(),
            "minimal-minus-exact-timestamp-basis",
        )
        encoded = json.dumps(result, sort_keys=True)
        for key in (
            "transition_monotonic_usec",
            "started_monotonic_usec",
            "ended_monotonic_usec",
            "window_start_usec",
            "window_end_usec",
            "requirement_observed_at",
        ):
            self.assertNotIn(f'"{key}"', encoded)
        for key in (
            "analysis_window_usec",
            "largest_gap_usec",
            "max_sample_gap_usec",
        ):
            self.assertIn(f'"{key}"', encoded)

    def test_full_derived_mapping_drops_synthesis_and_claim_fields_recursively(
        self,
    ) -> None:
        result = apply_ablation_mapping(
            self._full_bundle(),
            "full-minus-current-derived-synthesis-interpretation",
        )
        evidence = result["evidence"]
        assert isinstance(evidence, list)
        self.assertNotIn("synthesis", {item["kind"] for item in evidence})
        encoded = json.dumps(result, sort_keys=True)
        for key in (
            "causal_claim",
            "evidence_class",
            "interpretation",
            "propagation_claim_assigned",
            "root_cause_claim_assigned",
            "topology_temporal_applicability_claim",
            "universal_systemd_behavior_claim",
        ):
            self.assertNotIn(f'"{key}"', encoded)
        self.assertIn("retain-me", encoded)
        self.assertIn('"fact": 7', encoded)

    def test_retained_evidence_order_and_refs_are_preserved(self) -> None:
        bundle = self._minimal_bundle()
        result = apply_ablation_mapping(bundle, "minimal-minus-coverage")
        expected = [
            item["ref"] for item in bundle["evidence"] if item["ref"] != "REF-0006"
        ]
        self.assertEqual([item["ref"] for item in result["evidence"]], expected)

    def test_no_condition_or_mapping_label_is_added(self) -> None:
        result = apply_ablation_mapping(
            self._minimal_bundle(),
            "minimal-minus-topology",
        )
        self.assertEqual(
            set(result),
            {"schema_version", "case_id", "task", "environment", "evidence"},
        )
        self.assertNotIn("condition", result)
        self.assertNotIn("mapping_id", result)

    def test_hidden_gold_keys_are_rejected_recursively(self) -> None:
        bundle = self._minimal_bundle()
        observation = self._evidence_by_ref(bundle)["REF-0005"]["payload"]
        assert isinstance(observation, dict)
        observation["nested"] = {"counterevidence_refs": ["REF-0004"]}
        with self.assertRaisesRegex(ValueError, "hidden-gold key"):
            apply_ablation_mapping(bundle, "minimal-minus-topology")

    def test_outer_schema_drift_is_rejected(self) -> None:
        bundle = self._minimal_bundle()
        bundle["condition"] = "minimal"
        with self.assertRaisesRegex(ValueError, "outer schema drift"):
            apply_ablation_mapping(bundle, "minimal-minus-topology")


if __name__ == "__main__":
    unittest.main()
