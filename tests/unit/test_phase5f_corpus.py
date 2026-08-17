from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sentinel_x._phase5f.baselines import (
    run_b0_state_rule,
    run_b1_graph_time,
    run_b1s_current_synthesis,
)
from sentinel_x._phase5f.corpus import (
    ADVERSARIAL_CASE_PLAN,
    CORPUS_V1_CASE_IDS,
    CROSS_BOOT_INVALID_FAMILY,
    EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
    EMPIRICAL_EFFECT_FAMILY,
    HARD_CASE_IDS,
    INSUFFICIENT_COVERAGE_FAMILY,
    MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY,
    PHASE5F_TRANSFORMATION_VERSION,
    REVERSE_COUNTEREVIDENCE_FAMILY,
    TEMPORAL_DISTRACTOR_FAMILY,
    CorpusCase,
    audit_corpus_v1,
    empirical_relation_for_case,
    write_corpus_v1,
)
from sentinel_x._phase5f.corpus_transformations import derive_adversarial_case
from sentinel_x._phase5f.gold import CaseGold
from sentinel_x._phase5f.visible import CaseSource, project_case_evidence
from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.synthesis import (
    PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
)

BOOT = "a" * 32


class Phase5FCorpusTests(unittest.TestCase):
    def _item(
        self,
        ref: str,
        kind: str,
        *,
        raw: object | None = None,
        minimal: object | None = None,
        full: object | None = None,
    ) -> dict[str, object]:
        item: dict[str, object] = {"ref": ref, "kind": kind}
        if raw is not None:
            item["raw"] = raw
        if minimal is not None:
            item["minimal"] = minimal
        if full is not None:
            item["full"] = full
        return item

    def _synthesis(self, *, affected: int = 0, negative: int = 0) -> dict[str, object]:
        supportive = affected
        interpretation = (
            "supportive_without_counter_or_negative"
            if affected
            else "bounded_negative_without_support_or_counter"
        )
        return {
            "schema_version": PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
            "affected_anomaly_observed_count": affected,
            "forward_temporal_consistency_count": 0,
            "directional_counterevidence_count": 0,
            "bounded_negative_observation_count": negative,
            "insufficient_evidence_count": 0,
            "supportive_signal_count": supportive,
            "conflicting_evidence_present": False,
            "interpretation": interpretation,
            "causal_claim": False,
            "propagation_claim_assigned": False,
        }

    def _empirical(self, case_id: str, *, effect: bool) -> CorpusCase:
        source_unit = "source.service"
        target_unit = "dependent.service"
        source_time = 1_000_000
        items = [
            self._item(
                "REF-0001",
                "scope",
                raw={
                    "source_unit": source_unit,
                    "target_unit": target_unit,
                    "boot_id": BOOT,
                    "capture_started_monotonic_usec": 900_000,
                    "capture_ended_monotonic_usec": 6_100_000,
                },
                minimal={
                    "source_unit": source_unit,
                    "target_unit": target_unit,
                    "boot_id": BOOT,
                    "analysis_window_usec": 5_000_000,
                },
                full={
                    "source_unit": source_unit,
                    "target_unit": target_unit,
                    "boot_id": BOOT,
                    "analysis_window_usec": 5_000_000,
                },
            ),
            self._item(
                "REF-0002",
                "topology_relation",
                raw={"manager_property": "Requires" if effect else "Wants"},
                minimal={
                    "subject_unit": target_unit,
                    "object_unit": source_unit,
                    "relation": "requires" if effect else "wants",
                    "boot_id": BOOT,
                },
                full={
                    "subject_unit": target_unit,
                    "object_unit": source_unit,
                    "relation": "requires" if effect else "wants",
                    "boot_id": BOOT,
                },
            ),
            self._item(
                "REF-0003",
                "incident_timeline",
                minimal={
                    "unit": source_unit,
                    "state": "inactive",
                    "transition_monotonic_usec": source_time,
                    "boot_id": BOOT,
                },
                full={
                    "unit": source_unit,
                    "state": "inactive",
                    "transition_monotonic_usec": source_time,
                    "boot_id": BOOT,
                },
            ),
        ]
        if effect:
            items.append(
                self._item(
                    "REF-0004",
                    "incident_timeline",
                    minimal={
                        "unit": target_unit,
                        "state": "inactive",
                        "transition_monotonic_usec": 1_100_000,
                        "boot_id": BOOT,
                    },
                    full={
                        "unit": target_unit,
                        "state": "inactive",
                        "transition_monotonic_usec": 1_100_000,
                        "boot_id": BOOT,
                    },
                )
            )
        items.extend(
            [
                self._item(
                    "REF-0005",
                    "observation",
                    minimal={
                        "unit": target_unit,
                        "status": "inactive" if effect else "healthy",
                        "boot_id": BOOT,
                    },
                    full={
                        "unit": target_unit,
                        "status": "inactive" if effect else "healthy",
                        "boot_id": BOOT,
                    },
                ),
                self._item(
                    "REF-0006",
                    "coverage",
                    minimal={
                        "unit": target_unit,
                        "boot_id": BOOT,
                        "window_start_usec": source_time,
                        "window_end_usec": 6_000_000,
                        "largest_gap_usec": 100_000,
                        "max_sample_gap_usec": 250_000,
                        "healthy_count": 20 if not effect else 1,
                        "inactive_count": 0 if not effect else 19,
                        "failed_count": 0,
                        "unassessed_count": 0,
                    },
                    full={
                        "unit": target_unit,
                        "boot_id": BOOT,
                        "window_start_usec": source_time,
                        "window_end_usec": 6_000_000,
                        "largest_gap_usec": 100_000,
                        "max_sample_gap_usec": 250_000,
                        "healthy_count": 20 if not effect else 1,
                        "inactive_count": 0 if not effect else 19,
                        "failed_count": 0,
                        "unassessed_count": 0,
                    },
                ),
                self._item(
                    "REF-0007",
                    "provenance",
                    minimal={"boot_id": BOOT, "candidate_id": "candidate"},
                    full={"boot_id": BOOT, "candidate_id": "candidate"},
                ),
                self._item(
                    "REF-0008",
                    "intervention_context",
                    minimal={
                        "experiment_id": "exp-test",
                        "target_unit": source_unit,
                        "fault_mode": "service_inactive",
                        "boot_id": BOOT,
                    },
                    full={
                        "experiment_id": "exp-test",
                        "target_unit": source_unit,
                        "fault_mode": "service_inactive",
                        "boot_id": BOOT,
                    },
                ),
                self._item(
                    "REF-0010",
                    "systemctl_samples",
                    raw={
                        "samples": [
                            {"captured_monotonic_usec": 900_000, "unit": target_unit},
                            {"captured_monotonic_usec": 6_100_000, "unit": target_unit},
                        ]
                    },
                ),
                self._item(
                    "REF-0011",
                    "journal_excerpt",
                    raw={"stdout": "journal", "bounded": True},
                ),
                self._item(
                    "REF-0020",
                    "candidate",
                    full={"candidate_id": "candidate", "boot_id": BOOT},
                ),
                self._item(
                    "REF-0026",
                    "synthesis",
                    full=self._synthesis(
                        affected=1 if effect else 0, negative=0 if effect else 1
                    ),
                ),
            ]
        )
        source = CaseSource(
            case_id=case_id,
            task="Assess the operational evidence.",
            environment={"boot_id": BOOT},
            evidence_items=tuple(items),
        )
        refs = {item["ref"]: f"fixture:{case_id}:{item['ref']}" for item in items}
        supporting = ["REF-0001", "REF-0002", "REF-0003", "REF-0005", "REF-0006"]
        if effect:
            supporting.append("REF-0004")
        gold = CaseGold(
            case_id=case_id,
            classification="EFFECT_OBSERVED" if effect else "BOUNDED_NEGATIVE",
            must_abstain=False,
            supporting_evidence_refs=tuple(supporting),
            invalid_evidence_refs=(),
            counterevidence_refs=(),
            maximum_allowed_causal_strength="hypothesis" if effect else "none",
            origin="empirical",
            source_run_group=f"RUN-{int(case_id[5:]):04d}",
        )
        return CorpusCase(
            source=source,
            gold=gold,
            scenario_family=(
                EMPIRICAL_EFFECT_FAMILY if effect else EMPIRICAL_BOUNDED_NEGATIVE_FAMILY
            ),
            lineage_by_ref=refs,
        )

    def _corpus(self) -> list[CorpusCase]:
        empirical = [
            self._empirical(case_id, effect=True) for case_id in CORPUS_V1_CASE_IDS[:8]
        ] + [
            self._empirical(case_id, effect=False)
            for case_id in CORPUS_V1_CASE_IDS[8:16]
        ]
        by_id = {case.source.case_id: case for case in empirical}
        adversarial = [
            derive_adversarial_case(
                by_id[parent_id],
                case_id=case_id,
                scenario_family=family,
            )
            for case_id, family, parent_id in ADVERSARIAL_CASE_PLAN
        ]
        return [*empirical, *adversarial]

    def test_empirical_relation_plan_is_frozen_and_opaque(self) -> None:
        self.assertIs(
            empirical_relation_for_case("CASE-0001"), DependencyRelation.REQUIRES
        )
        self.assertIs(
            empirical_relation_for_case("CASE-0008"), DependencyRelation.REQUIRES
        )
        self.assertIs(
            empirical_relation_for_case("CASE-0009"), DependencyRelation.WANTS
        )
        self.assertIs(
            empirical_relation_for_case("CASE-0016"), DependencyRelation.WANTS
        )
        with self.assertRaisesRegex(ValueError, "not an empirical"):
            empirical_relation_for_case("CASE-0017")

    def test_corpus_case_rejects_visible_gold_mismatch(self) -> None:
        parent = self._empirical("CASE-0001", effect=True)
        wrong_gold = CaseGold(
            case_id="CASE-0002",
            classification="EFFECT_OBSERVED",
            must_abstain=False,
            supporting_evidence_refs=("REF-0001",),
            invalid_evidence_refs=(),
            counterevidence_refs=(),
            maximum_allowed_causal_strength="hypothesis",
            origin="empirical",
            source_run_group="RUN-0001",
        )
        with self.assertRaisesRegex(ValueError, "case_id"):
            CorpusCase(
                source=parent.source,
                gold=wrong_gold,
                scenario_family=EMPIRICAL_EFFECT_FAMILY,
                lineage_by_ref=parent.lineage_by_ref,
            )

    def test_corpus_case_requires_exact_lineage_coverage(self) -> None:
        parent = self._empirical("CASE-0001", effect=True)
        bad_lineage = dict(parent.lineage_by_ref)
        bad_lineage.pop("REF-0001")
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            CorpusCase(
                source=parent.source,
                gold=parent.gold,
                scenario_family=parent.scenario_family,
                lineage_by_ref=bad_lineage,
            )

    def test_adversarial_case_requires_parent_and_frozen_transformation(self) -> None:
        parent = self._empirical("CASE-0009", effect=False)
        gold = CaseGold(
            case_id="CASE-0017",
            classification="INSUFFICIENT",
            must_abstain=True,
            supporting_evidence_refs=("REF-0001",),
            invalid_evidence_refs=(),
            counterevidence_refs=(),
            maximum_allowed_causal_strength="none",
            origin="adversarial",
            source_run_group=parent.gold.source_run_group,
        )
        source = CaseSource(
            case_id="CASE-0017",
            task=parent.source.task,
            environment=parent.source.environment,
            evidence_items=parent.source.evidence_items,
        )
        with self.assertRaisesRegex(ValueError, "parent_case_id"):
            CorpusCase(
                source=source,
                gold=gold,
                scenario_family=INSUFFICIENT_COVERAGE_FAMILY,
                lineage_by_ref=parent.lineage_by_ref,
            )

    def test_insufficient_transform_forces_gap_and_drops_stale_synthesis(self) -> None:
        parent = self._empirical("CASE-0009", effect=False)
        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0017",
            scenario_family=INSUFFICIENT_COVERAGE_FAMILY,
        )
        self.assertEqual(
            run_b1_graph_time(derived.source)["classification"], "INSUFFICIENT"
        )
        self.assertEqual(
            run_b0_state_rule(derived.source)["classification"], "BOUNDED_NEGATIVE"
        )
        self.assertEqual(
            run_b1s_current_synthesis(derived.source)["classification"], "INSUFFICIENT"
        )
        full = project_case_evidence(derived.source, "full")
        self.assertFalse(any(item["kind"] == "synthesis" for item in full["evidence"]))
        self.assertTrue(derived.gold.must_abstain)

    def test_reverse_transform_yields_counterevidence_and_preserves_hidden_counter_refs(
        self,
    ) -> None:
        parent = self._empirical("CASE-0001", effect=True)
        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0021",
            scenario_family=REVERSE_COUNTEREVIDENCE_FAMILY,
        )
        self.assertEqual(
            run_b1_graph_time(derived.source)["classification"], "COUNTEREVIDENCE"
        )
        self.assertEqual(
            run_b0_state_rule(derived.source)["classification"], "EFFECT_OBSERVED"
        )
        self.assertEqual(
            set(derived.gold.counterevidence_refs),
            {"REF-0003", "REF-0004", "REF-0010"},
        )
        self.assertNotIn("REF-0011", derived.lineage_by_ref)

    def test_reverse_transform_synthesizes_target_timeline_when_pairwise_is_absent(
        self,
    ) -> None:
        original = self._empirical("CASE-0001", effect=True)
        source = CaseSource(
            case_id=original.source.case_id,
            task=original.source.task,
            environment=original.source.environment,
            evidence_items=tuple(
                item
                for item in original.source.evidence_items
                if item["ref"] != "REF-0004"
            ),
        )
        gold = CaseGold(
            case_id=original.gold.case_id,
            classification=original.gold.classification,
            must_abstain=original.gold.must_abstain,
            supporting_evidence_refs=tuple(
                ref
                for ref in original.gold.supporting_evidence_refs
                if ref != "REF-0004"
            ),
            invalid_evidence_refs=original.gold.invalid_evidence_refs,
            counterevidence_refs=original.gold.counterevidence_refs,
            maximum_allowed_causal_strength=(
                original.gold.maximum_allowed_causal_strength
            ),
            origin=original.gold.origin,
            source_run_group=original.gold.source_run_group,
        )
        parent = CorpusCase(
            source=source,
            gold=gold,
            scenario_family=original.scenario_family,
            lineage_by_ref={
                ref: token
                for ref, token in original.lineage_by_ref.items()
                if ref != "REF-0004"
            },
        )

        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0021",
            scenario_family=REVERSE_COUNTEREVIDENCE_FAMILY,
        )

        self.assertEqual(
            run_b1_graph_time(derived.source)["classification"],
            "COUNTEREVIDENCE",
        )
        minimal = project_case_evidence(derived.source, "minimal")
        target_timelines = [
            item
            for item in minimal["evidence"]
            if item["kind"] == "incident_timeline"
            and item["payload"].get("unit") == "dependent.service"
        ]
        self.assertEqual(len(target_timelines), 1)
        target_ref = target_timelines[0]["ref"]
        self.assertIn(target_ref, derived.gold.counterevidence_refs)
        self.assertEqual(
            derived.lineage_by_ref[target_ref],
            "derived:CASE-0021:reverse-target-timeline",
        )
        self.assertTrue(
            derived.provenance_dict()["transformation_details"][
                "target_timeline_synthesized_from_observed_anomaly"
            ]
        )

    def test_temporal_distractor_preserves_true_effect_but_marks_distractor_invalid(
        self,
    ) -> None:
        parent = self._empirical("CASE-0005", effect=True)
        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0025",
            scenario_family=TEMPORAL_DISTRACTOR_FAMILY,
        )
        self.assertEqual(
            run_b1_graph_time(derived.source)["classification"], "EFFECT_OBSERVED"
        )
        self.assertEqual(
            run_b1s_current_synthesis(derived.source)["classification"],
            "EFFECT_OBSERVED",
        )
        self.assertEqual(len(derived.gold.invalid_evidence_refs), 1)

    def test_cross_boot_distractor_fools_naive_state_but_not_graph_time(self) -> None:
        parent = self._empirical("CASE-0013", effect=False)
        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0029",
            scenario_family=CROSS_BOOT_INVALID_FAMILY,
        )
        self.assertEqual(
            run_b0_state_rule(derived.source)["classification"], "EFFECT_OBSERVED"
        )
        self.assertEqual(
            run_b1_graph_time(derived.source)["classification"], "BOUNDED_NEGATIVE"
        )
        self.assertEqual(len(derived.gold.invalid_evidence_refs), 2)

    def test_multiple_candidate_transform_is_ambiguous_and_strips_stale_synthesis(
        self,
    ) -> None:
        parent = self._empirical("CASE-0001", effect=True)
        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0033",
            scenario_family=MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY,
        )
        result = run_b1_graph_time(derived.source)
        self.assertEqual(result["classification"], "AMBIGUOUS")
        self.assertEqual(
            set(result["evidence_refs"]),
            set(derived.gold.supporting_evidence_refs),
        )
        self.assertTrue(derived.gold.must_abstain)
        self.assertEqual(
            run_b1s_current_synthesis(derived.source)["classification"], "INSUFFICIENT"
        )

    def test_multiple_candidate_transform_supports_live_shaped_effect_without_target_timeline(
        self,
    ) -> None:
        original = self._empirical("CASE-0001", effect=True)
        source = CaseSource(
            case_id=original.source.case_id,
            task=original.source.task,
            environment=original.source.environment,
            evidence_items=tuple(
                item
                for item in original.source.evidence_items
                if item["ref"] != "REF-0004"
            ),
        )
        gold = CaseGold(
            case_id=original.gold.case_id,
            classification=original.gold.classification,
            must_abstain=original.gold.must_abstain,
            supporting_evidence_refs=tuple(
                ref
                for ref in original.gold.supporting_evidence_refs
                if ref != "REF-0004"
            ),
            invalid_evidence_refs=original.gold.invalid_evidence_refs,
            counterevidence_refs=original.gold.counterevidence_refs,
            maximum_allowed_causal_strength=(
                original.gold.maximum_allowed_causal_strength
            ),
            origin=original.gold.origin,
            source_run_group=original.gold.source_run_group,
        )
        parent = CorpusCase(
            source=source,
            gold=gold,
            scenario_family=original.scenario_family,
            lineage_by_ref={
                ref: token
                for ref, token in original.lineage_by_ref.items()
                if ref != "REF-0004"
            },
        )

        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0033",
            scenario_family=MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY,
        )

        result = run_b1_graph_time(derived.source)
        self.assertEqual(result["classification"], "AMBIGUOUS")
        self.assertEqual(
            set(result["evidence_refs"]),
            set(derived.gold.supporting_evidence_refs),
        )
        self.assertNotIn("REF-0004", derived.gold.supporting_evidence_refs)
        self.assertTrue(derived.gold.must_abstain)

    def test_transformation_plan_rejects_wrong_parent_or_family(self) -> None:
        parent = self._empirical("CASE-0009", effect=False)
        with self.assertRaisesRegex(ValueError, "frozen corpus-v1 plan"):
            derive_adversarial_case(
                parent,
                case_id="CASE-0017",
                scenario_family=REVERSE_COUNTEREVIDENCE_FAMILY,
            )

    def test_adversarial_provenance_is_hidden_and_versioned(self) -> None:
        parent = self._empirical("CASE-0009", effect=False)
        derived = derive_adversarial_case(
            parent,
            case_id="CASE-0017",
            scenario_family=INSUFFICIENT_COVERAGE_FAMILY,
        )
        provenance = derived.provenance_dict()
        self.assertEqual(provenance["parent_case_id"], "CASE-0009")
        self.assertEqual(
            provenance["transformation_version"], PHASE5F_TRANSFORMATION_VERSION
        )
        for condition in ("raw", "minimal", "full"):
            projection = project_case_evidence(derived.source, condition)
            encoded = json.dumps(projection)
            self.assertNotIn("parent_case_id", encoded)
            self.assertNotIn("transformation_version", encoded)

    def test_audit_requires_exact_preregistered_counts_and_hard_subset(self) -> None:
        corpus = self._corpus()
        summary = audit_corpus_v1(corpus)
        self.assertEqual(summary["case_count"], 36)
        self.assertEqual(summary["empirical_case_count"], 16)
        self.assertEqual(summary["adversarial_case_count"], 20)
        self.assertEqual(summary["hard_case_count"], 28)
        self.assertEqual(len(HARD_CASE_IDS), 28)
        with self.assertRaisesRegex(ValueError, "exactly 36"):
            audit_corpus_v1(corpus[:-1])

    def test_audit_rejects_pseudoreplication_group_drift(self) -> None:
        corpus = self._corpus()
        parent = corpus[0]
        duplicate_group_gold = CaseGold(
            case_id="CASE-0002",
            classification="EFFECT_OBSERVED",
            must_abstain=False,
            supporting_evidence_refs=corpus[1].gold.supporting_evidence_refs,
            invalid_evidence_refs=(),
            counterevidence_refs=(),
            maximum_allowed_causal_strength="hypothesis",
            origin="empirical",
            source_run_group=parent.gold.source_run_group,
        )
        corpus[1] = CorpusCase(
            source=corpus[1].source,
            gold=duplicate_group_gold,
            scenario_family=corpus[1].scenario_family,
            lineage_by_ref=corpus[1].lineage_by_ref,
        )
        with self.assertRaisesRegex(ValueError, "unique source_run_group"):
            audit_corpus_v1(corpus)

    def test_raw_minimal_full_outer_lineage_remains_identical(self) -> None:
        case = derive_adversarial_case(
            self._empirical("CASE-0013", effect=False),
            case_id="CASE-0029",
            scenario_family=CROSS_BOOT_INVALID_FAMILY,
        )
        views = [
            project_case_evidence(case.source, name)
            for name in ("raw", "minimal", "full")
        ]
        for field in ("schema_version", "case_id", "task", "environment"):
            self.assertEqual(views[0][field], views[1][field])
            self.assertEqual(views[0][field], views[2][field])

    def test_write_corpus_separates_visible_gold_provenance_and_hashes(self) -> None:
        corpus = self._corpus()
        pilot = {
            "schema_version": "sentinel-x.phase5f-observer-pilot.v1",
            "material_observer_effect_detected": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "corpus-v1"
            summary = write_corpus_v1(corpus, output, observer_pilot=pilot)
            self.assertEqual(summary["case_count"], 36)
            self.assertTrue((output / "visible/raw.jsonl").is_file())
            self.assertTrue((output / "visible/minimal.jsonl").is_file())
            self.assertTrue((output / "visible/full.jsonl").is_file())
            self.assertTrue((output / "hidden/gold.jsonl").is_file())
            self.assertTrue((output / "hidden/provenance.jsonl").is_file())
            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "SHA256SUMS").is_file())
            raw_lines = (output / "visible/raw.jsonl").read_text().splitlines()
            gold_lines = (output / "hidden/gold.jsonl").read_text().splitlines()
            self.assertEqual(len(raw_lines), 36)
            self.assertEqual(len(gold_lines), 36)
            self.assertNotIn('"classification"', raw_lines[0])
            self.assertIn('"classification"', gold_lines[0])
            self.assertEqual(
                (output / "hidden/gold.jsonl").stat().st_mode & 0o777, 0o600
            )

    def test_write_corpus_requires_observer_pilot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "observer pilot"):
                write_corpus_v1(
                    self._corpus(),
                    Path(temporary) / "corpus-v1",
                    observer_pilot={"material_observer_effect_detected": True},
                )

    def test_write_corpus_refuses_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "corpus-v1"
            output.mkdir()
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                write_corpus_v1(
                    self._corpus(),
                    output,
                    observer_pilot={"material_observer_effect_detected": False},
                )

    def test_no_reasoner_or_scoring_api_is_added_by_corpus_module(self) -> None:
        import sentinel_x._phase5f as phase5f

        self.assertFalse(hasattr(phase5f, "CorpusCase"))
        self.assertFalse(hasattr(phase5f, "audit_corpus_v1"))
        self.assertFalse(hasattr(phase5f, "write_corpus_v1"))
        self.assertEqual(
            phase5f.__all__,
            [
                "CaseSource",
                "project_case_evidence",
                "run_b0_state_rule",
                "run_b1_graph_time",
                "run_b1s_current_synthesis",
                "visible_evidence_refs",
            ],
        )


if __name__ == "__main__":
    unittest.main()
