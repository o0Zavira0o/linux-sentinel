from __future__ import annotations

import copy
import unittest

from sentinel_x._phase5f.corpus import (
    ADVERSARIAL_CASE_PLAN,
    EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
    EMPIRICAL_EFFECT_FAMILY,
)
from sentinel_x._phase5f.gold import CaseGold
from sentinel_x._phase5f.scoring import (
    build_corpus_v1_analysis_report,
    score_attempt,
    summarize_primary_metrics,
)


class Phase5FPrimaryScoringTests(unittest.TestCase):
    def _gold(
        self,
        *,
        case_id: str = "CASE-0001",
        classification: str = "EFFECT_OBSERVED",
        must_abstain: bool = False,
        supporting: tuple[str, ...] = ("REF-0001", "REF-0002"),
        invalid: tuple[str, ...] = ("REF-0004",),
        counter: tuple[str, ...] = (),
        max_strength: str = "hypothesis",
        origin: str = "empirical",
        group: str = "run-0001",
    ) -> CaseGold:
        return CaseGold(
            case_id=case_id,
            classification=classification,
            must_abstain=must_abstain,
            supporting_evidence_refs=supporting,
            invalid_evidence_refs=invalid,
            counterevidence_refs=counter,
            maximum_allowed_causal_strength=max_strength,
            origin=origin,
            source_run_group=group,
        )

    def _output(
        self,
        *,
        classification: str = "EFFECT_OBSERVED",
        abstain: bool = False,
        strength: str = "hypothesis",
        claim_refs: list[str] | None = None,
        top_refs: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "classification": classification,
            "abstain": abstain,
            "claims": [
                {
                    "claim_kind": "downstream_effect",
                    "causal_strength": strength,
                    "evidence_refs": (
                        ["REF-0001"] if claim_refs is None else list(claim_refs)
                    ),
                    "text": "A bounded claim grounded in the supplied evidence.",
                }
            ],
            "unresolved": [],
            "evidence_refs": (["REF-0001"] if top_refs is None else list(top_refs)),
        }

    def _score(
        self,
        gold: CaseGold,
        output: dict[str, object] | None,
        *,
        condition: str = "minimal",
        repeat_index: int = 1,
        family: str = EMPIRICAL_EFFECT_FAMILY,
    ) -> dict[str, object]:
        return score_attempt(
            gold,
            output,
            condition=condition,
            repeat_index=repeat_index,
            scenario_family=family,
        )

    def test_attempt_scores_correctness_claim_strength_citations_and_provenance(
        self,
    ) -> None:
        gold = self._gold(max_strength="association")
        output = self._output(
            strength="hypothesis",
            claim_refs=["REF-0001", "REF-0003", "REF-0004"],
        )
        scored = self._score(gold, output)
        self.assertTrue(scored["classification_correct"])
        self.assertEqual(scored["causal_claim_count"], 1)
        self.assertEqual(scored["unsupported_causal_claim_count"], 1)
        self.assertEqual(scored["claim_evidence_ref_count"], 3)
        self.assertEqual(scored["supporting_claim_ref_count"], 1)
        self.assertEqual(scored["invalid_claim_ref_count"], 1)

    def test_none_strength_is_not_a_causal_claim_and_zero_denominator_stays_missing(
        self,
    ) -> None:
        gold = self._gold(max_strength="none")
        scored = self._score(
            gold,
            self._output(strength="none", claim_refs=[]),
        )
        summary = summarize_primary_metrics([scored])
        self.assertEqual(summary["m3_uccr"]["denominator"], 0)
        self.assertIsNone(summary["m3_uccr"]["rate"])
        self.assertEqual(summary["m5_evidence_citation_precision"]["denominator"], 0)
        self.assertIsNone(summary["m5_evidence_citation_precision"]["rate"])
        self.assertIsNone(summary["m6_provenance_violation_rate"]["rate"])

    def test_citation_precision_counts_reference_use_per_claim(self) -> None:
        gold = self._gold(supporting=("REF-0001",), invalid=())
        output = self._output(claim_refs=["REF-0001"])
        claims = output["claims"]
        assert isinstance(claims, list)
        claims.append(
            {
                "claim_kind": "second_claim",
                "causal_strength": "none",
                "evidence_refs": ["REF-0001", "REF-0003"],
                "text": (
                    "A second claim reuses one reference and adds one irrelevant ref."
                ),
            }
        )
        scored = self._score(gold, output)
        summary = summarize_primary_metrics([scored])
        metric = summary["m5_evidence_citation_precision"]
        self.assertEqual(metric["numerator"], 2)
        self.assertEqual(metric["denominator"], 3)
        self.assertAlmostEqual(metric["rate"], 2 / 3)

    def test_top_level_refs_do_not_enter_m5_or_m6_claim_denominators(self) -> None:
        gold = self._gold(supporting=("REF-0001",), invalid=("REF-0004",))
        scored = self._score(
            gold,
            self._output(
                strength="none",
                claim_refs=[],
                top_refs=["REF-0001", "REF-0004"],
            ),
        )
        summary = summarize_primary_metrics([scored])
        citation = summary["m5_evidence_citation_precision"]
        provenance = summary["m6_provenance_violation_rate"]
        self.assertEqual(citation["numerator"], 0)
        self.assertEqual(citation["denominator"], 0)
        self.assertIsNone(citation["rate"])
        self.assertEqual(provenance["numerator"], 0)
        self.assertEqual(provenance["denominator"], 0)
        self.assertIsNone(provenance["rate"])

    def test_counterevidence_is_preserved_by_explicit_ref_or_exact_compatible_class(
        self,
    ) -> None:
        gold = self._gold(
            case_id="CASE-0021",
            classification="COUNTEREVIDENCE",
            supporting=("REF-0001", "REF-0002"),
            invalid=(),
            counter=("REF-0002",),
            max_strength="none",
            origin="adversarial",
            group="run-0001",
        )
        explicit = self._score(
            gold,
            self._output(
                classification="AMBIGUOUS",
                strength="none",
                claim_refs=[],
                top_refs=["REF-0002"],
            ),
            family="reverse_counterevidence",
        )
        compatible = self._score(
            gold,
            self._output(
                classification="COUNTEREVIDENCE",
                strength="none",
                claim_refs=[],
                top_refs=[],
            ),
            repeat_index=2,
            family="reverse_counterevidence",
        )
        suppressed = self._score(
            gold,
            self._output(
                classification="EFFECT_OBSERVED",
                strength="none",
                claim_refs=[],
                top_refs=[],
            ),
            repeat_index=3,
            family="reverse_counterevidence",
        )
        self.assertTrue(explicit["counterevidence_preserved"])
        self.assertTrue(compatible["counterevidence_preserved"])
        self.assertFalse(suppressed["counterevidence_preserved"])
        summary = summarize_primary_metrics([explicit, compatible, suppressed])
        self.assertEqual(summary["m7_counterevidence_preservation_rate"]["rate"], 2 / 3)

    def test_parse_failure_is_not_semantically_repaired_or_given_fake_claims(
        self,
    ) -> None:
        gold = self._gold(
            case_id="CASE-0021",
            classification="COUNTEREVIDENCE",
            counter=("REF-0002",),
            max_strength="none",
            origin="adversarial",
        )
        scored = self._score(
            gold,
            None,
            family="reverse_counterevidence",
        )
        self.assertTrue(scored["parse_failed"])
        self.assertIsNone(scored["classification"])
        self.assertFalse(scored["classification_correct"])
        self.assertIsNone(scored["abstain"])
        self.assertEqual(scored["causal_claim_count"], 0)
        self.assertEqual(scored["claim_evidence_ref_count"], 0)
        self.assertFalse(scored["counterevidence_preserved"])

    def test_task_accuracy_and_macro_f1_count_missing_prediction_as_false_negative(
        self,
    ) -> None:
        correct = self._score(self._gold(), self._output())
        missing = self._score(
            self._gold(
                case_id="CASE-0009",
                classification="BOUNDED_NEGATIVE",
                max_strength="none",
                group="run-0009",
            ),
            None,
            family=EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
        )
        summary = summarize_primary_metrics([correct, missing])
        self.assertEqual(summary["m1_task_correctness"]["rate"], 0.5)
        per_class = summary["m2_macro_f1"]["per_class"]
        self.assertEqual(per_class["EFFECT_OBSERVED"]["f1"], 1.0)
        self.assertEqual(per_class["BOUNDED_NEGATIVE"]["f1"], 0.0)
        self.assertIsNone(per_class["COUNTEREVIDENCE"]["f1"])
        self.assertEqual(summary["m2_macro_f1"]["defined_class_count"], 2)
        self.assertEqual(summary["m2_macro_f1"]["rate"], 0.5)

    def test_correct_abstention_f1_treats_parse_failure_as_no_positive_prediction(
        self,
    ) -> None:
        positive = self._score(
            self._gold(
                case_id="CASE-0017",
                classification="INSUFFICIENT",
                must_abstain=True,
                max_strength="none",
                origin="adversarial",
            ),
            self._output(
                classification="INSUFFICIENT",
                abstain=True,
                strength="none",
                claim_refs=[],
            ),
            family="insufficient_coverage",
        )
        missed = self._score(
            self._gold(
                case_id="CASE-0018",
                classification="INSUFFICIENT",
                must_abstain=True,
                max_strength="none",
                origin="adversarial",
            ),
            None,
            family="insufficient_coverage",
        )
        summary = summarize_primary_metrics([positive, missed])
        metric = summary["m4_correct_abstention_f1"]
        self.assertEqual(metric, {"tp": 1, "fp": 0, "fn": 1, "rate": 2 / 3})

    def test_run_consistency_uses_real_classifications_in_three_run_denominator(
        self,
    ) -> None:
        gold = self._gold()
        rows = [
            self._score(gold, self._output(), repeat_index=1),
            self._score(gold, self._output(), repeat_index=2),
            self._score(gold, None, repeat_index=3),
        ]
        summary = summarize_primary_metrics(rows)
        m8 = summary["m8_run_consistency"]
        self.assertEqual(m8["complete_case_condition_group_count"], 1)
        self.assertEqual(m8["rate"], 2 / 3)

        all_failed = [
            self._score(gold, None, repeat_index=index) for index in (1, 2, 3)
        ]
        self.assertEqual(
            summarize_primary_metrics(all_failed)["m8_run_consistency"]["rate"],
            0.0,
        )

    def test_m8_is_missing_for_single_repeat_slice(self) -> None:
        row = self._score(self._gold(), self._output())
        self.assertIsNone(
            summarize_primary_metrics([row])["m8_run_consistency"]["rate"]
        )

    def test_score_rejects_condition_repeat_and_output_contract_drift(self) -> None:
        with self.assertRaisesRegex(ValueError, "condition"):
            self._score(self._gold(), self._output(), condition="B3")
        with self.assertRaisesRegex(ValueError, "repeat_index"):
            self._score(self._gold(), self._output(), repeat_index=0)
        invalid = self._output()
        invalid["extra"] = True
        with self.assertRaisesRegex(ValueError, "keys mismatch"):
            self._score(self._gold(), invalid)

    def test_full_corpus_report_requires_exact_324_case_condition_repeat_matrix(
        self,
    ) -> None:
        rows = self._complete_matrix()
        report = build_corpus_v1_analysis_report(rows)
        self.assertEqual(report["attempt_count"], 324)
        self.assertEqual(report["case_count"], 36)
        self.assertEqual(report["conditions"], ["raw", "minimal", "full"])
        self.assertEqual(report["repeats"], [1, 2, 3])
        sets = report["analysis_sets"]
        self.assertEqual(sets["all"]["case_count"], 36)
        self.assertEqual(sets["hard"]["case_count"], 28)
        self.assertEqual(sets["empirical"]["case_count"], 16)
        self.assertEqual(sets["adversarial"]["case_count"], 20)
        self.assertEqual(sets["family:reverse_counterevidence"]["case_count"], 4)
        self.assertEqual(
            sets["all"]["condition_summaries"]["minimal"]["attempt_count"],
            108,
        )
        self.assertEqual(
            sets["all"]["repeat_summaries"]["1"]["minimal"]["attempt_count"],
            36,
        )

    def test_full_corpus_report_rejects_duplicate_missing_or_metadata_drift(
        self,
    ) -> None:
        rows = self._complete_matrix()
        with self.assertRaisesRegex(ValueError, "exactly 324"):
            build_corpus_v1_analysis_report(rows[:-1])

        duplicate = rows[:-1] + [copy.deepcopy(rows[0])]
        with self.assertRaisesRegex(ValueError, "duplicate|incomplete"):
            build_corpus_v1_analysis_report(duplicate)

        drift = copy.deepcopy(rows)
        drift[1]["source_run_group"] = "different-group"
        with self.assertRaisesRegex(ValueError, "metadata drift"):
            build_corpus_v1_analysis_report(drift)

    def test_full_corpus_report_rejects_adversarial_parent_group_drift(self) -> None:
        rows = self._complete_matrix()
        for row in rows:
            if row["case_id"] == "CASE-0017":
                row["source_run_group"] = "run-0016"
        with self.assertRaisesRegex(ValueError, "empirical parent"):
            build_corpus_v1_analysis_report(rows)

    def test_full_corpus_report_preserves_source_run_group_pseudoreplication_signal(
        self,
    ) -> None:
        report = build_corpus_v1_analysis_report(self._complete_matrix())
        sets = report["analysis_sets"]
        self.assertEqual(sets["all"]["source_run_group_count"], 16)
        self.assertEqual(sets["empirical"]["source_run_group_count"], 16)
        self.assertLess(
            sets["adversarial"]["source_run_group_count"],
            sets["adversarial"]["case_count"],
        )

    def _complete_matrix(self) -> list[dict[str, object]]:
        family_by_case = {
            **{f"CASE-{index:04d}": EMPIRICAL_EFFECT_FAMILY for index in range(1, 9)},
            **{
                f"CASE-{index:04d}": EMPIRICAL_BOUNDED_NEGATIVE_FAMILY
                for index in range(9, 17)
            },
            **{case_id: family for case_id, family, _ in ADVERSARIAL_CASE_PLAN},
        }
        parent_by_case = {
            case_id: parent for case_id, _, parent in ADVERSARIAL_CASE_PLAN
        }
        classification_by_case = {
            **{f"CASE-{index:04d}": "EFFECT_OBSERVED" for index in range(1, 9)},
            **{f"CASE-{index:04d}": "BOUNDED_NEGATIVE" for index in range(9, 17)},
            **{f"CASE-{index:04d}": "INSUFFICIENT" for index in range(17, 21)},
            **{f"CASE-{index:04d}": "COUNTEREVIDENCE" for index in range(21, 25)},
            **{f"CASE-{index:04d}": "EFFECT_OBSERVED" for index in range(25, 29)},
            **{f"CASE-{index:04d}": "BOUNDED_NEGATIVE" for index in range(29, 33)},
            **{f"CASE-{index:04d}": "AMBIGUOUS" for index in range(33, 37)},
        }
        rows: list[dict[str, object]] = []
        for index in range(1, 37):
            case_id = f"CASE-{index:04d}"
            origin = "empirical" if index <= 16 else "adversarial"
            parent = parent_by_case.get(case_id, case_id)
            group = f"run-{int(parent.split('-')[1]):04d}"
            classification = classification_by_case[case_id]
            counter = ("REF-0002",) if classification == "COUNTEREVIDENCE" else ()
            gold = self._gold(
                case_id=case_id,
                classification=classification,
                must_abstain=classification in {"INSUFFICIENT", "AMBIGUOUS"},
                supporting=("REF-0001", "REF-0002"),
                invalid=(),
                counter=counter,
                max_strength=(
                    "hypothesis" if classification == "EFFECT_OBSERVED" else "none"
                ),
                origin=origin,
                group=group,
            )
            for condition in ("raw", "minimal", "full"):
                for repeat_index in (1, 2, 3):
                    rows.append(
                        self._score(
                            gold,
                            self._output(
                                classification=classification,
                                abstain=gold.must_abstain,
                                strength="none",
                                claim_refs=["REF-0001"],
                                top_refs=list(counter) or ["REF-0001"],
                            ),
                            condition=condition,
                            repeat_index=repeat_index,
                            family=family_by_case[case_id],
                        )
                    )
        return rows


if __name__ == "__main__":
    unittest.main()
