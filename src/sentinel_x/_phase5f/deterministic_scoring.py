"""Private scorer-side adapter for deterministic Phase-5F baselines.

The frozen 5F.5 scorer is intentionally left unchanged.  This adapter supplies the
preregistered B0/B1/B1S identity outside the scorer row while reusing the frozen
``score_attempt`` and ``summarize_primary_metrics`` primitives verbatim.

B0 and B1 consume the frozen MINIMAL projection; B1S consumes FULL.  Those actual
evidence projections are passed through the frozen scorer ``condition`` field.  The
single deterministic evaluation uses repeat index 1 only as a scorer transport
sentinel; M8 remains not applicable because no case/baseline has three LLM repeats.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Final, cast

from .corpus import (
    ADVERSARIAL_CASE_PLAN,
    CORPUS_V1_CASE_IDS,
    REQUIRES_EMPIRICAL_CASE_IDS,
    WANTS_EMPIRICAL_CASE_IDS,
)
from .gold import CaseGold
from .scoring import score_attempt, summarize_primary_metrics

_BASELINES: Final[tuple[str, ...]] = ("B0", "B1", "B1S")
_EVIDENCE_CONDITION_BY_BASELINE: Final[dict[str, str]] = {
    "B0": "minimal",
    "B1": "minimal",
    "B1S": "full",
}
_SCORER_REPEAT_SENTINEL: Final[int] = 1


def score_deterministic_baseline_output(
    gold: CaseGold,
    output: Mapping[str, object],
    *,
    baseline: str,
    scenario_family: str,
) -> dict[str, object]:
    """Score one already-produced deterministic baseline output.

    Baseline execution must happen on the visible side before this scorer-side helper
    receives hidden gold.  The returned wrapper keeps baseline identity distinct from
    the frozen scorer's evidence-condition field.
    """
    baseline = _baseline(baseline)
    if not isinstance(output, Mapping):
        raise ValueError("deterministic baseline output must be a mapping")

    evidence_condition = _EVIDENCE_CONDITION_BY_BASELINE[baseline]
    scored = score_attempt(
        gold,
        output,
        condition=evidence_condition,
        repeat_index=_SCORER_REPEAT_SENTINEL,
        scenario_family=scenario_family,
    )
    return {
        "baseline": baseline,
        "evidence_condition": evidence_condition,
        "scorer_repeat_sentinel": _SCORER_REPEAT_SENTINEL,
        "score": scored,
    }


def build_deterministic_baseline_analysis_report(
    golds: Iterable[CaseGold],
    outputs: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Score and aggregate the exact 36 x B0/B1/B1S deterministic matrix."""
    gold_by_case = _validate_golds(golds)
    records = tuple(_validated_output_record(item) for item in outputs)
    _validate_complete_output_matrix(records)

    rows_by_baseline: dict[str, list[dict[str, object]]] = {
        baseline: [] for baseline in _BASELINES
    }
    for record in records:
        case_id = cast(str, record["case_id"])
        baseline = cast(str, record["baseline"])
        wrapped = score_deterministic_baseline_output(
            gold_by_case[case_id],
            cast(Mapping[str, object], record["output"]),
            baseline=baseline,
            scenario_family=cast(str, record["scenario_family"]),
        )
        rows_by_baseline[baseline].append(cast(dict[str, object], wrapped["score"]))

    baseline_reports = {
        baseline: _baseline_report(rows_by_baseline[baseline])
        for baseline in _BASELINES
    }
    b1_analysis_sets = cast(
        Mapping[str, object], baseline_reports["B1"]["analysis_sets"]
    )
    b1s_analysis_sets = cast(
        Mapping[str, object], baseline_reports["B1S"]["analysis_sets"]
    )
    return {
        "schema_version": "sentinel-x.phase5f-deterministic-baseline-analysis.v1",
        "case_count": 36,
        "baseline_count": 3,
        "output_count": 108,
        "baselines": list(_BASELINES),
        "evidence_condition_by_baseline": dict(_EVIDENCE_CONDITION_BY_BASELINE),
        "scorer_repeat_sentinel": _SCORER_REPEAT_SENTINEL,
        "m8_applicability": "not_applicable_deterministic_single_run",
        "baseline_reports": baseline_reports,
        "hard_collapse_comparison_inputs": {
            "B1": b1_analysis_sets["hard"],
            "B1S": b1s_analysis_sets["hard"],
        },
    }


def _baseline_report(rows: list[dict[str, object]]) -> dict[str, object]:
    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_family[cast(str, row["scenario_family"])].append(row)

    analysis_sets: dict[str, list[dict[str, object]]] = {
        "all": rows,
        "hard": [row for row in rows if row["is_hard"] is True],
        "empirical": [row for row in rows if row["origin"] == "empirical"],
        "adversarial": [row for row in rows if row["origin"] == "adversarial"],
    }
    for family, family_rows in sorted(by_family.items()):
        analysis_sets[f"family:{family}"] = family_rows

    summaries = {
        name: summarize_primary_metrics(selected)
        for name, selected in analysis_sets.items()
    }
    for summary in summaries.values():
        m8 = cast(Mapping[str, object], summary["m8_run_consistency"])
        if m8["complete_case_condition_group_count"] != 0 or m8["rate"] is not None:
            raise AssertionError("deterministic baseline unexpectedly produced M8")

    return {
        "case_count": 36,
        "analysis_sets": summaries,
    }


def _validate_golds(golds: Iterable[CaseGold]) -> dict[str, CaseGold]:
    values = tuple(golds)
    if len(values) != 36:
        raise ValueError("deterministic baseline analysis requires exactly 36 golds")

    by_case: dict[str, CaseGold] = {}
    for gold in values:
        if not isinstance(gold, CaseGold):
            raise ValueError("golds must contain only CaseGold values")
        if gold.case_id in by_case:
            raise ValueError("duplicate deterministic baseline gold case_id")
        by_case[gold.case_id] = gold
    if set(by_case) != set(CORPUS_V1_CASE_IDS):
        raise ValueError("deterministic baseline gold case set is incomplete")

    empirical_ids = (*REQUIRES_EMPIRICAL_CASE_IDS, *WANTS_EMPIRICAL_CASE_IDS)
    empirical_groups: dict[str, str] = {}
    for case_id in empirical_ids:
        gold = by_case[case_id]
        if gold.origin != "empirical":
            raise ValueError("empirical deterministic baseline gold origin drift")
        empirical_groups[case_id] = gold.source_run_group
    if len(set(empirical_groups.values())) != 16:
        raise ValueError("empirical deterministic baseline golds require 16 groups")

    for case_id, _, parent_case_id in ADVERSARIAL_CASE_PLAN:
        gold = by_case[case_id]
        if gold.origin != "adversarial":
            raise ValueError("adversarial deterministic baseline gold origin drift")
        if gold.source_run_group != empirical_groups[parent_case_id]:
            raise ValueError("adversarial deterministic baseline source group drift")
    return by_case


def _validated_output_record(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("deterministic baseline output record must be a mapping")
    record = dict(value)
    if set(record) != {"baseline", "case_id", "scenario_family", "output"}:
        raise ValueError("deterministic baseline output record keys are invalid")
    record["baseline"] = _baseline(record["baseline"])
    case_id = record["case_id"]
    if not isinstance(case_id, str) or case_id not in CORPUS_V1_CASE_IDS:
        raise ValueError("deterministic baseline output case_id is invalid")
    family = record["scenario_family"]
    if not isinstance(family, str) or not family:
        raise ValueError("deterministic baseline scenario_family is invalid")
    if not isinstance(record["output"], Mapping):
        raise ValueError("deterministic baseline output must be a mapping")
    record["output"] = dict(cast(Mapping[str, object], record["output"]))
    return record


def _validate_complete_output_matrix(records: tuple[dict[str, object], ...]) -> None:
    if len(records) != 108:
        raise ValueError("deterministic baseline analysis requires exactly 108 outputs")
    keys: set[tuple[str, str]] = set()
    for record in records:
        key = (cast(str, record["baseline"]), cast(str, record["case_id"]))
        if key in keys:
            raise ValueError("duplicate deterministic baseline/case output")
        keys.add(key)
    expected = {
        (baseline, case_id) for baseline in _BASELINES for case_id in CORPUS_V1_CASE_IDS
    }
    if keys != expected:
        raise ValueError("deterministic baseline output matrix is incomplete")


def _baseline(value: object) -> str:
    if not isinstance(value, str) or value not in _BASELINES:
        raise ValueError("baseline must be B0, B1, or B1S")
    return value
