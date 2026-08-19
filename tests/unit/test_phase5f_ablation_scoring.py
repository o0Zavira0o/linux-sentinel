from __future__ import annotations

import hashlib
import inspect
import unittest
from pathlib import Path

import sentinel_x._phase5f as phase5f
from sentinel_x._phase5f import scoring
from sentinel_x._phase5f.ablation import (
    ablation_mapping_base_condition,
    ablation_mapping_ids,
)
from sentinel_x._phase5f.ablation_scoring import (
    ABLATION_SCORE_EXECUTION_SEMANTICS_SHA256,
    build_ablation_analysis_report,
    score_ablation_output,
)
from sentinel_x._phase5f.corpus import (
    ADVERSARIAL_CASE_PLAN,
    CORPUS_V1_CASE_IDS,
    EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
    EMPIRICAL_EFFECT_FAMILY,
    REQUIRES_EMPIRICAL_CASE_IDS,
    WANTS_EMPIRICAL_CASE_IDS,
)
from sentinel_x._phase5f.gold import CaseGold
from sentinel_x._phase5f.scoring import score_attempt

_FROZEN_SCORER_SHA256 = (
    "8ef2514805bc3c7b788a7f13f24e8183c8a3ddb3c472015797e8721368b3b22e"
)
_FROZEN_EXECUTION_SEMANTICS_SHA256 = (
    "d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d"
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


class Phase5FAblationScoringTests(unittest.TestCase):
    def test_adapter_pins_execution_semantics_and_reuses_frozen_scorer(
        self,
    ) -> None:
        self.assertEqual(
            ABLATION_SCORE_EXECUTION_SEMANTICS_SHA256,
            _FROZEN_EXECUTION_SEMANTICS_SHA256,
        )

        scorer_path = Path(inspect.getsourcefile(scoring) or "")

        self.assertEqual(
            hashlib.sha256(scorer_path.read_bytes()).hexdigest(),
            _FROZEN_SCORER_SHA256,
        )

        gold = _gold("CASE-0001")
        mapping_id = "minimal-minus-topology"
        output = _output(gold.classification)

        wrapped = score_ablation_output(
            gold,
            output,
            mapping_id=mapping_id,
            repeat_index=2,
            scenario_family=_family(gold.case_id),
        )

        direct = score_attempt(
            gold,
            output,
            condition="minimal",
            repeat_index=2,
            scenario_family=_family(gold.case_id),
        )

        self.assertEqual(wrapped["score"], direct)

    def test_mapping_identity_is_separate_from_scorer_condition(self) -> None:
        gold = _gold("CASE-0001")

        for mapping_id in ablation_mapping_ids():
            with self.subTest(mapping_id=mapping_id):
                wrapped = score_ablation_output(
                    gold,
                    _output(gold.classification),
                    mapping_id=mapping_id,
                    repeat_index=1,
                    scenario_family=_family(gold.case_id),
                )

                self.assertEqual(
                    wrapped["mapping_id"],
                    mapping_id,
                )
                self.assertEqual(
                    wrapped["evidence_condition"],
                    ablation_mapping_base_condition(mapping_id),
                )
                self.assertNotIn(
                    "mapping_id",
                    wrapped["score"],
                )

    def test_full_derived_mapping_uses_full_scorer_condition(self) -> None:
        gold = _gold("CASE-0001")
        wrapped = score_ablation_output(
            gold,
            _output(gold.classification),
            mapping_id="full-minus-current-derived-synthesis-interpretation",
            repeat_index=3,
            scenario_family=_family(gold.case_id),
        )

        self.assertEqual(
            wrapped["evidence_condition"],
            "full",
        )
        self.assertEqual(
            wrapped["score"]["condition"],
            "full",
        )

    def test_parse_failure_passes_through_frozen_scorer(self) -> None:
        gold = _gold("CASE-0001")
        wrapped = score_ablation_output(
            gold,
            None,
            mapping_id="minimal-minus-coverage",
            repeat_index=1,
            scenario_family=_family(gold.case_id),
        )

        self.assertTrue(
            wrapped["score"]["parse_failed"],
        )
        self.assertIsNone(
            wrapped["score"]["classification"],
        )

    def test_unknown_mapping_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "frozen mandatory ablation mapping",
        ):
            score_ablation_output(
                _gold("CASE-0001"),
                _output("EFFECT_OBSERVED"),
                mapping_id="unknown",
                repeat_index=1,
                scenario_family=_family("CASE-0001"),
            )

    def test_invalid_repeat_is_rejected_by_frozen_boundary(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "repeat_index",
        ):
            score_ablation_output(
                _gold("CASE-0001"),
                _output("EFFECT_OBSERVED"),
                mapping_id="minimal-minus-coverage",
                repeat_index=4,
                scenario_family=_family("CASE-0001"),
            )

    def test_complete_report_requires_exact_756_matrix(self) -> None:
        golds = _golds()
        report = build_ablation_analysis_report(
            golds,
            _records(golds),
        )

        self.assertEqual(report["case_count"], 36)
        self.assertEqual(report["mapping_count"], 7)
        self.assertEqual(report["repeat_count"], 3)
        self.assertEqual(report["output_count"], 756)
        self.assertEqual(
            report["execution_semantics_sha256"],
            _FROZEN_EXECUTION_SEMANTICS_SHA256,
        )
        self.assertEqual(
            report["m8_grouping"],
            "independent_per_mapping",
        )

    def test_report_pins_six_minimal_and_one_full_mapping(self) -> None:
        report = build_ablation_analysis_report(
            _golds(),
            _records(_golds()),
        )
        conditions = report["evidence_condition_by_mapping"]

        self.assertEqual(
            list(conditions.values()).count("minimal"),
            6,
        )
        self.assertEqual(
            list(conditions.values()).count("full"),
            1,
        )

    def test_each_mapping_gets_independent_complete_m8_groups(self) -> None:
        golds = _golds()
        report = build_ablation_analysis_report(
            golds,
            _records(golds),
        )

        for mapping_id, mapping_report in report["mapping_reports"].items():
            with self.subTest(mapping_id=mapping_id):
                m8 = mapping_report["analysis_sets"]["all"]["m8_run_consistency"]

                self.assertEqual(
                    m8["complete_case_condition_group_count"],
                    36,
                )
                self.assertEqual(
                    m8["rate"],
                    1.0,
                )

    def test_repeat_summaries_do_not_invent_m8_completeness(self) -> None:
        report = build_ablation_analysis_report(
            _golds(),
            _records(_golds()),
        )

        for mapping_report in report["mapping_reports"].values():
            for repeat_summary in mapping_report["repeat_summaries"].values():
                m8 = repeat_summary["all"]["m8_run_consistency"]

                self.assertEqual(
                    m8["complete_case_condition_group_count"],
                    0,
                )
                self.assertIsNone(m8["rate"])

    def test_hard_empirical_and_adversarial_slices_are_preserved(self) -> None:
        report = build_ablation_analysis_report(
            _golds(),
            _records(_golds()),
        )
        first = report["mapping_reports"][ablation_mapping_ids()[0]]["analysis_sets"]

        self.assertEqual(
            first["hard"]["case_count"],
            28,
        )
        self.assertEqual(
            first["empirical"]["case_count"],
            16,
        )
        self.assertEqual(
            first["adversarial"]["case_count"],
            20,
        )

    def test_per_family_slices_are_exposed(self) -> None:
        report = build_ablation_analysis_report(
            _golds(),
            _records(_golds()),
        )
        analysis_sets = report["mapping_reports"][ablation_mapping_ids()[0]][
            "analysis_sets"
        ]

        self.assertEqual(
            len([name for name in analysis_sets if name.startswith("family:")]),
            7,
        )

    def test_missing_output_is_rejected(self) -> None:
        golds = _golds()
        records = _records(golds)

        with self.assertRaisesRegex(
            ValueError,
            "exactly 756 outputs",
        ):
            build_ablation_analysis_report(
                golds,
                records[:-1],
            )

    def test_duplicate_output_is_rejected(self) -> None:
        golds = _golds()
        records = _records(golds)
        records[-1] = dict(records[0])

        with self.assertRaisesRegex(
            ValueError,
            "duplicate ablation mapping/case/repeat",
        ):
            build_ablation_analysis_report(
                golds,
                records,
            )

    def test_wrong_output_record_shape_is_rejected(self) -> None:
        records = _records(_golds())
        records[0]["condition"] = "minimal"

        with self.assertRaisesRegex(
            ValueError,
            "record keys are invalid",
        ):
            build_ablation_analysis_report(
                _golds(),
                records,
            )

    def test_output_record_accepts_none_for_parse_failure(self) -> None:
        golds = _golds()
        records = _records(golds)
        records[0]["output"] = None

        report = build_ablation_analysis_report(
            golds,
            records,
        )

        mapping_id = ablation_mapping_ids()[0]
        summary = report["mapping_reports"][mapping_id]["analysis_sets"]["all"]

        self.assertEqual(
            summary["parse_failure_count"],
            1,
        )

    def test_scenario_family_drift_is_rejected_by_frozen_scorer(self) -> None:
        golds = _golds()
        records = _records(golds)
        records[0]["scenario_family"] = "wrong-family"

        with self.assertRaisesRegex(
            ValueError,
            "scenario_family does not match",
        ):
            build_ablation_analysis_report(
                golds,
                records,
            )

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
            maximum_allowed_causal_strength=(second.maximum_allowed_causal_strength),
            origin=second.origin,
            source_run_group=first.source_run_group,
        )

        with self.assertRaisesRegex(
            ValueError,
            "require 16 groups",
        ):
            build_ablation_analysis_report(
                golds,
                _records(_golds()),
            )

    def test_adversarial_source_group_must_match_empirical_parent(
        self,
    ) -> None:
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
            maximum_allowed_causal_strength=(item.maximum_allowed_causal_strength),
            origin=item.origin,
            source_run_group="wrong-parent-group",
        )

        with self.assertRaisesRegex(
            ValueError,
            "source group drift",
        ):
            build_ablation_analysis_report(
                golds,
                _records(_golds()),
            )

    def test_adapter_is_not_reexported_from_visible_package_root(self) -> None:
        self.assertFalse(
            hasattr(
                phase5f,
                "score_ablation_output",
            )
        )
        self.assertFalse(
            hasattr(
                phase5f,
                "build_ablation_analysis_report",
            )
        )

    def test_mapping_reports_keep_mapping_identity_outside_score_rows(
        self,
    ) -> None:
        report = build_ablation_analysis_report(
            _golds(),
            _records(_golds()),
        )

        for mapping_id, mapping_report in report["mapping_reports"].items():
            with self.subTest(mapping_id=mapping_id):
                self.assertEqual(
                    mapping_report["evidence_condition"],
                    ablation_mapping_base_condition(mapping_id),
                )
                self.assertNotIn(
                    "mapping_id",
                    mapping_report["analysis_sets"]["all"],
                )


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
        must_abstain=classification
        in {
            "INSUFFICIENT",
            "AMBIGUOUS",
        },
        supporting_evidence_refs=(),
        invalid_evidence_refs=(),
        counterevidence_refs=(),
        maximum_allowed_causal_strength="none",
        origin=origin,
        source_run_group=source_group,
    )


def _golds() -> list[CaseGold]:
    return [_gold(case_id) for case_id in CORPUS_V1_CASE_IDS]


def _output(
    classification: str,
) -> dict[str, object]:
    return {
        "classification": classification,
        "abstain": classification
        in {
            "INSUFFICIENT",
            "AMBIGUOUS",
        },
        "claims": [],
        "unresolved": [],
        "evidence_refs": [],
    }


def _records(
    golds: list[CaseGold],
) -> list[dict[str, object]]:
    by_case = {gold.case_id: gold for gold in golds}

    return [
        {
            "mapping_id": mapping_id,
            "case_id": case_id,
            "repeat_index": repeat_index,
            "scenario_family": _family(case_id),
            "output": _output(by_case[case_id].classification),
        }
        for mapping_id in ablation_mapping_ids()
        for case_id in CORPUS_V1_CASE_IDS
        for repeat_index in (1, 2, 3)
    ]


if __name__ == "__main__":
    unittest.main()
