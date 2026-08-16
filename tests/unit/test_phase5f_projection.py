from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

import sentinel_x._phase5f as phase5f
from sentinel_x._phase5f.gold import CaseGold
from sentinel_x._phase5f.visible import CaseSource, project_case_evidence
from sentinel_x.dependency.models import (
    DependencyConfigurationOrigin,
    DependencyEndpoint,
    DependencyEntityKind,
    DependencyEvidence,
    DependencyEvidenceOrigin,
    DependencyRelation,
)


class Phase5FProjectionTests(unittest.TestCase):
    def _source(self) -> CaseSource:
        return CaseSource(
            case_id="CASE-0001",
            task="Assess the operational evidence without exceeding what it supports.",
            environment={
                "host_alias": "host-a",
                "boot_id": "a" * 32,
            },
            evidence_items=(
                {
                    "ref": "REF-0003",
                    "kind": "provenance",
                    "minimal": {
                        "evidence_boot_id": "a" * 32,
                        "claim_scope_boot_id": "a" * 32,
                    },
                    "full": {
                        "schema_version": "sentinel-x.test-full.v1",
                        "boot_id": "a" * 32,
                        "causal_claim": False,
                    },
                },
                {
                    "ref": "REF-0001",
                    "kind": "observation",
                    "raw": {
                        "source": "systemctl show",
                        "stdout": "ActiveState=failed\nSubState=failed\n",
                    },
                    "minimal": {
                        "unit": "dependent.service",
                        "active_state": "failed",
                    },
                    "full": {
                        "assessment_id": "asmt-" + "1" * 64,
                        "status": "failed",
                    },
                },
                {
                    "ref": "REF-0002",
                    "kind": "topology_relation",
                    "raw": {
                        "source": "systemctl show",
                        "stdout": "Requires=source.service\n",
                    },
                    "minimal": {
                        "subject": "dependent.service",
                        "relation": "requires",
                        "object": "source.service",
                    },
                    "full": {
                        "evidence_id": "depev-" + "2" * 64,
                        "relation": "requires",
                        "causal_claim": False,
                    },
                },
                {
                    "ref": "REF-0004",
                    "kind": "derived_evidence",
                    "full": {
                        "interpretation": "supportive_evidence_present",
                        "causal_claim": False,
                    },
                },
            ),
        )

    def test_three_views_share_outer_contract_and_do_not_expose_condition_label(
        self,
    ) -> None:
        source = self._source()

        projections = {
            name: project_case_evidence(source, name)
            for name in ("raw", "minimal", "full")
        }

        for projection in projections.values():
            self.assertEqual(
                set(projection),
                {"schema_version", "case_id", "task", "environment", "evidence"},
            )
            self.assertEqual(projection["case_id"], "CASE-0001")
            self.assertNotIn("condition", projection)
            self.assertNotIn("view", projection)

        self.assertEqual(
            [item["ref"] for item in projections["raw"]["evidence"]],
            ["REF-0001", "REF-0002"],
        )
        self.assertEqual(
            [item["ref"] for item in projections["minimal"]["evidence"]],
            ["REF-0001", "REF-0002", "REF-0003"],
        )
        self.assertEqual(
            [item["ref"] for item in projections["full"]["evidence"]],
            ["REF-0001", "REF-0002", "REF-0003", "REF-0004"],
        )

    def test_visible_source_is_deeply_copied_and_caller_mutation_cannot_change_view(
        self,
    ) -> None:
        environment = {"boot_id": "a" * 32}
        raw_payload = {"stdout": "ActiveState=failed\n"}
        source = CaseSource(
            case_id="CASE-0002",
            task="Assess evidence.",
            environment=environment,
            evidence_items=(
                {
                    "ref": "REF-0001",
                    "kind": "observation",
                    "raw": raw_payload,
                    "minimal": {"active_state": "failed"},
                    "full": {"status": "failed"},
                },
            ),
        )

        environment["boot_id"] = "b" * 32
        raw_payload["stdout"] = "ActiveState=active\n"

        projection = project_case_evidence(source, "raw")
        self.assertEqual(projection["environment"], {"boot_id": "a" * 32})
        self.assertEqual(
            projection["evidence"][0]["payload"],
            {"stdout": "ActiveState=failed\n"},
        )

    def test_visible_projection_rejects_recursive_hidden_gold_leakage(self) -> None:
        with self.assertRaisesRegex(ValueError, "hidden-gold key"):
            CaseSource(
                case_id="CASE-0003",
                task="Assess evidence.",
                environment={"boot_id": "a" * 32},
                evidence_items=(
                    {
                        "ref": "REF-0001",
                        "kind": "observation",
                        "raw": {"nested": {"must_abstain": True}},
                        "minimal": {"active_state": "failed"},
                        "full": {"status": "failed"},
                    },
                ),
            )

    def test_actual_case_gold_serialization_cannot_enter_visible_payload(self) -> None:
        gold = CaseGold(
            case_id="CASE-0011",
            classification="INSUFFICIENT",
            must_abstain=True,
            supporting_evidence_refs=(),
            invalid_evidence_refs=("REF-0002",),
            counterevidence_refs=(),
            maximum_allowed_causal_strength="none",
            origin="adversarial",
            source_run_group="source-run-B",
        )

        with self.assertRaisesRegex(ValueError, "hidden-gold key"):
            CaseSource(
                case_id="CASE-0011",
                task="Assess evidence.",
                environment={},
                evidence_items=(
                    {
                        "ref": "REF-0001",
                        "kind": "observation",
                        "raw": gold.to_dict(),
                        "minimal": {},
                        "full": {},
                    },
                ),
            )

    def test_visible_projection_rejects_direct_gold_classification_leakage(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "hidden-gold key"):
            CaseSource(
                case_id="CASE-0010",
                task="Assess evidence.",
                environment={},
                evidence_items=(
                    {
                        "ref": "REF-0001",
                        "kind": "observation",
                        "raw": {"classification": "EFFECT_OBSERVED"},
                        "minimal": {"active_state": "failed"},
                        "full": {"status": "failed"},
                    },
                ),
            )

    def test_minimal_projection_rejects_nonfactual_category(self) -> None:
        with self.assertRaisesRegex(ValueError, "preregistered factual category"):
            CaseSource(
                case_id="CASE-0004",
                task="Assess evidence.",
                environment={"boot_id": "a" * 32},
                evidence_items=(
                    {
                        "ref": "REF-0001",
                        "kind": "derived_evidence",
                        "raw": {"text": "raw"},
                        "minimal": {"interpretation": "likely cause"},
                        "full": {"interpretation": "likely cause"},
                    },
                ),
            )

    def test_full_only_derived_evidence_is_allowed_without_polluting_minimal(
        self,
    ) -> None:
        source = self._source()

        minimal = project_case_evidence(source, "minimal")
        full = project_case_evidence(source, "full")

        self.assertNotIn(
            "REF-0004",
            {item["ref"] for item in minimal["evidence"]},
        )
        self.assertIn(
            "REF-0004",
            {item["ref"] for item in full["evidence"]},
        )

    def test_each_condition_must_have_visible_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one full payload"):
            CaseSource(
                case_id="CASE-0005",
                task="Assess evidence.",
                environment={},
                evidence_items=(
                    {
                        "ref": "REF-0001",
                        "kind": "observation",
                        "raw": {"text": "raw"},
                        "minimal": {"state": "failed"},
                    },
                ),
            )

    def test_duplicate_or_descriptive_evidence_references_are_rejected(self) -> None:
        duplicate = (
            {
                "ref": "REF-0001",
                "kind": "observation",
                "raw": {},
                "minimal": {},
                "full": {},
            },
            {
                "ref": "REF-0001",
                "kind": "provenance",
                "full": {},
            },
        )
        with self.assertRaisesRegex(ValueError, "unique references"):
            CaseSource(
                case_id="CASE-0006",
                task="Assess evidence.",
                environment={},
                evidence_items=duplicate,
            )

        with self.assertRaisesRegex(ValueError, "REF-####"):
            CaseSource(
                case_id="CASE-0006",
                task="Assess evidence.",
                environment={},
                evidence_items=(
                    {
                        "ref": "requires-positive",
                        "kind": "observation",
                        "raw": {},
                        "minimal": {},
                        "full": {},
                    },
                ),
            )

    def test_non_json_and_nonfinite_payloads_are_rejected(self) -> None:
        for payload in ({"bad": object()}, {"bad": float("nan")}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    CaseSource(
                        case_id="CASE-0007",
                        task="Assess evidence.",
                        environment={},
                        evidence_items=(
                            {
                                "ref": "REF-0001",
                                "kind": "observation",
                                "raw": payload,
                                "minimal": {},
                                "full": {},
                            },
                        ),
                    )

    def test_unknown_projection_condition_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw, minimal, full"):
            project_case_evidence(self._source(), "gold")

    def test_full_projection_accepts_serialized_frozen_dependency_evidence(
        self,
    ) -> None:
        evidence = DependencyEvidence(
            evidence_id="depev-" + "1" * 64,
            source_event_id="evt-1",
            observed_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
            boot_id="a" * 32,
            subject=DependencyEndpoint(
                kind=DependencyEntityKind.SYSTEMD_UNIT,
                identity="dependent.service",
            ),
            relation=DependencyRelation.REQUIRES,
            object=DependencyEndpoint(
                kind=DependencyEntityKind.SYSTEMD_UNIT,
                identity="source.service",
            ),
            origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
            configuration_origin=(
                DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED
            ),
            source_property="Requires",
        )
        source = CaseSource(
            case_id="CASE-0012",
            task="Assess evidence.",
            environment={"boot_id": "a" * 32},
            evidence_items=(
                {
                    "ref": "REF-0001",
                    "kind": "topology_relation",
                    "raw": {"stdout": "Requires=source.service\n"},
                    "minimal": {"relation": "requires"},
                    "full": evidence.to_dict(),
                },
            ),
        )

        full = project_case_evidence(source, "full")
        payload = full["evidence"][0]["payload"]
        self.assertEqual(payload["evidence_id"], evidence.evidence_id)
        self.assertFalse(payload["causal_claim"])

    def test_projected_bundle_is_plain_json_serializable(self) -> None:
        for condition in ("raw", "minimal", "full"):
            encoded = json.dumps(
                project_case_evidence(self._source(), condition),
                allow_nan=False,
                sort_keys=True,
            )
            self.assertIn('"CASE-0001"', encoded)

    def test_visible_reference_query_matches_condition_specific_projection(
        self,
    ) -> None:
        source = self._source()
        self.assertEqual(
            phase5f.visible_evidence_refs(source, "raw"),
            frozenset({"REF-0001", "REF-0002"}),
        )
        self.assertEqual(
            phase5f.visible_evidence_refs(source, "full"),
            frozenset({"REF-0001", "REF-0002", "REF-0003", "REF-0004"}),
        )

    def test_hidden_gold_is_not_reexported_from_visible_package_root(self) -> None:
        self.assertNotIn("CaseGold", phase5f.__all__)
        self.assertFalse(hasattr(phase5f, "CaseGold"))

    def test_case_gold_preserves_hidden_scoring_and_source_grouping(self) -> None:
        gold = CaseGold(
            case_id="CASE-0008",
            classification="COUNTEREVIDENCE",
            must_abstain=False,
            supporting_evidence_refs=("REF-0002", "REF-0001"),
            invalid_evidence_refs=("REF-0004",),
            counterevidence_refs=("REF-0002",),
            maximum_allowed_causal_strength="hypothesis",
            origin="adversarial",
            source_run_group="source-run-A",
        )

        serialized = gold.to_dict()
        self.assertEqual(
            serialized["supporting_evidence_refs"],
            ["REF-0001", "REF-0002"],
        )
        self.assertEqual(serialized["source_run_group"], "source-run-A")
        self.assertEqual(serialized["origin"], "adversarial")

    def test_case_gold_rejects_invalid_ref_overlap(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be disjoint"):
            CaseGold(
                case_id="CASE-0009",
                classification="INSUFFICIENT",
                must_abstain=True,
                supporting_evidence_refs=("REF-0001",),
                invalid_evidence_refs=("REF-0001",),
                counterevidence_refs=(),
                maximum_allowed_causal_strength="none",
                origin="adversarial",
                source_run_group="source-run-A",
            )


if __name__ == "__main__":
    unittest.main()
