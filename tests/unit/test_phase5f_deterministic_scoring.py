from __future__ import annotations

import hashlib
import inspect
import unittest
from pathlib import Path

from sentinel_x._phase5f import scoring
from sentinel_x._phase5f.corpus import (
    ADVERSARIAL_CASE_PLAN,
    CORPUS_V1_CASE_IDS,
    EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
    EMPIRICAL_EFFECT_FAMILY,
    REQUIRES_EMPIRICAL_CASE_IDS,
    WANTS_EMPIRICAL_CASE_IDS,
)
from sentinel_x._phase5f.deterministic_scoring import (
    build_deterministic_baseline_analysis_report,
    score_deterministic_baseline_output,
)
from sentinel_x._phase5f.gold import CaseGold
from sentinel_x._phase5f.scoring import score_attempt

_FROZEN_SCORER_SHA256 = (
    "8ef2514805bc3c7b788a7f13f24e8183c8a3ddb3c472015797e8721368b3b22e"
)
_LABELS = (
    "EFFECT_OBSERVED",
    "BOUNDED_NEGATIVE",
    "COUNTEREVIDENCE",
    "INSUFFICIENT",
    "AMBIGUOUS",
)
_ADVERSARIAL_FAMILY = {case_id: family for case_id, family, _ in ADVERSARIAL_CASE_PLAN}
_ADVERSARIAL_PARENT = {case_id: parent for case_id, _, parent in ADVERSARIAL_CASE_PLAN}


class Phase5FDeterministicScoringTests(unittest.TestCase):
    def test_adapter_reuses_frozen_scorer_without_modifying_metric_semantics(
        self,
    ) -> None:
        scorer_path = Path(inspect.getsourcefile(scoring) or "")
        self.assertEqual(
            hashlib.sha256(scorer_path.read_bytes()).hexdigest(),
            _FROZEN_SCORER_SHA256,
        )
        gold = _gold("CASE-0001")
        output = _output(gold.classification)
        wrapped = score_deterministic_baseline_output(
            gold,
            output,
            baseline="B1",
            scenario_family=_family("CASE-0001"),
        )
        direct = score_attempt(
            gold,
            output,
            condition="minimal",
            repeat_index=1,
            scenario_family=_family("CASE-0001"),
        )
        self.assertEqual(wrapped["score"], direct)

    def test_baseline_identity_is_separate_from_actual_evidence_condition(self) -> None:
        gold = _gold("CASE-0001")
        cases = (("B0", "minimal"), ("B1", "minimal"), ("B1S", "full"))
        for baseline, expected in cases:
            with self.subTest(baseline=baseline):
                wrapped = score_deterministic_baseline_output(
                    gold,
                    _output(gold.classification),
                    baseline=baseline,
                    scenario_family=_family(gold.case_id),
                )
                self.assertEqual(wrapped["baseline"], baseline)
                self.assertEqual(wrapped["evidence_condition"], expected)
                self.assertEqual(wrapped["scorer_repeat_sentinel"], 1)

    def test_unknown_baseline_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "baseline must be B0, B1, or B1S"):
            score_deterministic_baseline_output(
                _gold("CASE-0001"),
                _output("EFFECT_OBSERVED"),
                baseline="B2",
                scenario_family=_family("CASE-0001"),
            )

    def test_malformed_output_still_uses_common_reasoner_boundary(self) -> None:
        malformed = _output("EFFECT_OBSERVED")
        malformed["extra"] = True
        with self.assertRaises(ValueError):
            score_deterministic_baseline_output(
                _gold("CASE-0001"),
                malformed,
                baseline="B0",
                scenario_family=_family("CASE-0001"),
            )

    def test_complete_report_requires_exact_108_baseline_case_matrix(self) -> None:
        golds = _golds()
        records = _records(golds)
        report = build_deterministic_baseline_analysis_report(golds, records)
        self.assertEqual(report["case_count"], 36)
        self.assertEqual(report["baseline_count"], 3)
        self.assertEqual(report["output_count"], 108)
        self.assertEqual(report["baselines"], ["B0", "B1", "B1S"])
        self.assertEqual(
            report["evidence_condition_by_baseline"],
            {"B0": "minimal", "B1": "minimal", "B1S": "full"},
        )

    def test_hard_collapse_inputs_are_exposed_for_b1_vs_b1s(self) -> None:
        golds = _golds()
        report = build_deterministic_baseline_analysis_report(golds, _records(golds))
        collapse = report["hard_collapse_comparison_inputs"]
        self.assertEqual(set(collapse), {"B1", "B1S"})
        self.assertEqual(collapse["B1"]["case_count"], 28)
        self.assertEqual(collapse["B1S"]["case_count"], 28)

    def test_deterministic_report_marks_m8_not_applicable(self) -> None:
        golds = _golds()
        report = build_deterministic_baseline_analysis_report(golds, _records(golds))
        self.assertEqual(
            report["m8_applicability"],
            "not_applicable_deterministic_single_run",
        )
        for baseline_report in report["baseline_reports"].values():
            for summary in baseline_report["analysis_sets"].values():
                m8 = summary["m8_run_consistency"]
                self.assertEqual(m8["complete_case_condition_group_count"], 0)
                self.assertIsNone(m8["rate"])
                self.assertEqual(m8["groups"], {})

    def test_missing_or_duplicate_output_record_is_rejected(self) -> None:
        golds = _golds()
        records = _records(golds)
        with self.assertRaisesRegex(ValueError, "exactly 108 outputs"):
            build_deterministic_baseline_analysis_report(golds, records[:-1])

        duplicate = [dict(item) for item in records]
        duplicate[-1] = dict(duplicate[0])
        with self.assertRaisesRegex(
            ValueError,
            "duplicate deterministic baseline/case",
        ):
            build_deterministic_baseline_analysis_report(golds, duplicate)

    def test_wrong_output_record_shape_is_rejected(self) -> None:
        golds = _golds()
        records = _records(golds)
        records[0]["repeat"] = 1
        with self.assertRaisesRegex(ValueError, "record keys are invalid"):
            build_deterministic_baseline_analysis_report(golds, records)

    def test_scenario_family_drift_is_rejected_by_frozen_scorer(self) -> None:
        golds = _golds()
        records = _records(golds)
        records[0]["scenario_family"] = "wrong-family"
        with self.assertRaisesRegex(ValueError, "scenario_family does not match"):
            build_deterministic_baseline_analysis_report(golds, records)

    def test_empirical_source_groups_must_be_unique(self) -> None:
        golds = _golds()
        first = golds[0]
        second = golds[1]
        golds[1] = CaseGold(
            case_id=second.case_id,
            classification=second.classification,
            must_abstain=second.must_abstain,
            supporting_evidence_refs=second.supporting_evidence_refs,
            invalid_evidence_refs=second.invalid_evidence_refs,
            counterevidence_refs=second.counterevidence_refs,
            maximum_allowed_causal_strength=second.maximum_allowed_causal_strength,
            origin=second.origin,
            source_run_group=first.source_run_group,
        )
        with self.assertRaisesRegex(ValueError, "require 16 groups"):
            build_deterministic_baseline_analysis_report(golds, _records(_golds()))

    def test_adversarial_source_group_must_match_empirical_parent(self) -> None:
        golds = _golds()
        index = CORPUS_V1_CASE_IDS.index("CASE-0017")
        item = golds[index]
        golds[index] = CaseGold(
            case_id=item.case_id,
            classification=item.classification,
            must_abstain=item.must_abstain,
            supporting_evidence_refs=item.supporting_evidence_refs,
            invalid_evidence_refs=item.invalid_evidence_refs,
            counterevidence_refs=item.counterevidence_refs,
            maximum_allowed_causal_strength=item.maximum_allowed_causal_strength,
            origin=item.origin,
            source_run_group="wrong-parent-group",
        )
        with self.assertRaisesRegex(ValueError, "source group drift"):
            build_deterministic_baseline_analysis_report(golds, _records(_golds()))


def _family(case_id: str) -> str:
    if case_id in REQUIRES_EMPIRICAL_CASE_IDS:
        return EMPIRICAL_EFFECT_FAMILY
    if case_id in WANTS_EMPIRICAL_CASE_IDS:
        return EMPIRICAL_BOUNDED_NEGATIVE_FAMILY
    return _ADVERSARIAL_FAMILY[case_id]


def _gold(case_id: str) -> CaseGold:
    index = CORPUS_V1_CASE_IDS.index(case_id)
    classification = _LABELS[index % len(_LABELS)]
    if case_id in REQUIRES_EMPIRICAL_CASE_IDS or case_id in WANTS_EMPIRICAL_CASE_IDS:
        origin = "empirical"
        source_group = f"run-{case_id}"
    else:
        origin = "adversarial"
        source_group = f"run-{_ADVERSARIAL_PARENT[case_id]}"
    return CaseGold(
        case_id=case_id,
        classification=classification,
        must_abstain=classification in {"INSUFFICIENT", "AMBIGUOUS"},
        supporting_evidence_refs=(),
        invalid_evidence_refs=(),
        counterevidence_refs=(),
        maximum_allowed_causal_strength="none",
        origin=origin,
        source_run_group=source_group,
    )


def _golds() -> list[CaseGold]:
    return [_gold(case_id) for case_id in CORPUS_V1_CASE_IDS]


def _output(classification: str) -> dict[str, object]:
    return {
        "classification": classification,
        "abstain": classification in {"INSUFFICIENT", "AMBIGUOUS"},
        "claims": [],
        "unresolved": [],
        "evidence_refs": [],
    }


def _records(golds: list[CaseGold]) -> list[dict[str, object]]:
    by_case = {gold.case_id: gold for gold in golds}
    return [
        {
            "baseline": baseline,
            "case_id": case_id,
            "scenario_family": _family(case_id),
            "output": _output(by_case[case_id].classification),
        }
        for baseline in ("B0", "B1", "B1S")
        for case_id in CORPUS_V1_CASE_IDS
    ]


if __name__ == "__main__":
    unittest.main()
