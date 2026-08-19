"""Private scorer-side adapter for mandatory Phase-5F ablations.

The frozen 5F.5 scorer is intentionally left unchanged.  This adapter keeps
mandatory-ablation identity outside the frozen scorer row while passing the
actual evidence condition (MINIMAL for six mappings, FULL for one mapping)
through ``score_attempt``.  Each mapping is aggregated independently so M8
never mixes different ablation transforms that share the same scorer condition.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Final, cast

from .ablation import ablation_mapping_base_condition, ablation_mapping_ids
from .corpus import (
    ADVERSARIAL_CASE_PLAN,
    CORPUS_V1_CASE_IDS,
    REQUIRES_EMPIRICAL_CASE_IDS,
    WANTS_EMPIRICAL_CASE_IDS,
)
from .gold import CaseGold
from .scoring import score_attempt, summarize_primary_metrics

ABLATION_SCORE_EXECUTION_SEMANTICS_SHA256: Final[str] = (
    "d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d"
)

_REPEATS: Final[tuple[int, ...]] = (1, 2, 3)


def score_ablation_output(
    gold: CaseGold,
    output: Mapping[str, object] | None,
    *,
    mapping_id: str,
    repeat_index: int,
    scenario_family: str,
) -> dict[str, object]:
    """Score one already-captured mandatory-ablation output.

    Mapping identity remains outside the frozen scorer row.  ``output=None`` is
    passed through unchanged as the frozen representation of a parse failure.
    """

    mapping_id = _mapping_id(mapping_id)
    evidence_condition = ablation_mapping_base_condition(mapping_id)

    if output is not None and not isinstance(output, Mapping):
        raise ValueError("ablation output must be a mapping or None")

    scored = score_attempt(
        gold,
        output,
        condition=evidence_condition,
        repeat_index=repeat_index,
        scenario_family=scenario_family,
    )

    return {
        "mapping_id": mapping_id,
        "evidence_condition": evidence_condition,
        "score": scored,
    }


def build_ablation_analysis_report(
    golds: Iterable[CaseGold],
    outputs: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Score and aggregate the exact 7 x 36 x 3 mandatory-ablation matrix."""

    gold_by_case = _validate_golds(golds)
    records = tuple(_validated_output_record(item) for item in outputs)
    _validate_complete_output_matrix(records)

    mapping_ids = ablation_mapping_ids()
    rows_by_mapping: dict[str, list[dict[str, object]]] = {
        mapping_id: [] for mapping_id in mapping_ids
    }

    for record in records:
        case_id = cast(str, record["case_id"])
        mapping_id = cast(str, record["mapping_id"])
        wrapped = score_ablation_output(
            gold_by_case[case_id],
            cast(Mapping[str, object] | None, record["output"]),
            mapping_id=mapping_id,
            repeat_index=cast(int, record["repeat_index"]),
            scenario_family=cast(str, record["scenario_family"]),
        )
        rows_by_mapping[mapping_id].append(cast(dict[str, object], wrapped["score"]))

    mapping_reports = {
        mapping_id: _mapping_report(
            rows_by_mapping[mapping_id],
            evidence_condition=ablation_mapping_base_condition(mapping_id),
        )
        for mapping_id in mapping_ids
    }

    return {
        "schema_version": "sentinel-x.phase5f-ablation-analysis.v1",
        "execution_semantics_sha256": (ABLATION_SCORE_EXECUTION_SEMANTICS_SHA256),
        "case_count": 36,
        "mapping_count": len(mapping_ids),
        "repeat_count": len(_REPEATS),
        "output_count": len(records),
        "mappings": list(mapping_ids),
        "evidence_condition_by_mapping": {
            mapping_id: ablation_mapping_base_condition(mapping_id)
            for mapping_id in mapping_ids
        },
        "m8_grouping": "independent_per_mapping",
        "mapping_reports": mapping_reports,
    }


def _mapping_report(
    rows: list[dict[str, object]],
    *,
    evidence_condition: str,
) -> dict[str, object]:
    if len(rows) != 108:
        raise AssertionError("each ablation mapping must have exactly 108 scored rows")

    if any(row["condition"] != evidence_condition for row in rows):
        raise AssertionError("ablation scorer condition drift")

    analysis_sets = _analysis_sets(rows)
    summaries = {
        name: summarize_primary_metrics(selected)
        for name, selected in analysis_sets.items()
    }

    m8 = cast(Mapping[str, object], summaries["all"]["m8_run_consistency"])
    if m8["complete_case_condition_group_count"] != 36:
        raise AssertionError("ablation M8 did not receive 36 complete case groups")

    repeat_summaries: dict[str, dict[str, object]] = {}
    for repeat_index in _REPEATS:
        repeat_rows = [row for row in rows if row["repeat_index"] == repeat_index]
        if len(repeat_rows) != 36:
            raise AssertionError("ablation repeat slice must contain exactly 36 rows")

        repeat_sets = _analysis_sets(repeat_rows)
        repeat_summaries[str(repeat_index)] = {
            name: summarize_primary_metrics(selected)
            for name, selected in repeat_sets.items()
        }

    return {
        "case_count": 36,
        "attempt_count": 108,
        "evidence_condition": evidence_condition,
        "analysis_sets": summaries,
        "repeat_summaries": repeat_summaries,
    }


def _analysis_sets(
    rows: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
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

    return analysis_sets


def _validate_golds(
    golds: Iterable[CaseGold],
) -> dict[str, CaseGold]:
    values = tuple(golds)

    if len(values) != 36:
        raise ValueError("ablation analysis requires exactly 36 golds")

    by_case: dict[str, CaseGold] = {}

    for gold in values:
        if not isinstance(gold, CaseGold):
            raise ValueError("golds must contain only CaseGold values")

        if gold.case_id in by_case:
            raise ValueError("duplicate ablation gold case_id")

        by_case[gold.case_id] = gold

    if set(by_case) != set(CORPUS_V1_CASE_IDS):
        raise ValueError("ablation gold case set is incomplete")

    empirical_ids = (
        *REQUIRES_EMPIRICAL_CASE_IDS,
        *WANTS_EMPIRICAL_CASE_IDS,
    )
    empirical_groups: dict[str, str] = {}

    for case_id in empirical_ids:
        gold = by_case[case_id]

        if gold.origin != "empirical":
            raise ValueError("empirical ablation gold origin drift")

        empirical_groups[case_id] = gold.source_run_group

    if len(set(empirical_groups.values())) != 16:
        raise ValueError("empirical ablation golds require 16 groups")

    for case_id, _, parent_case_id in ADVERSARIAL_CASE_PLAN:
        gold = by_case[case_id]

        if gold.origin != "adversarial":
            raise ValueError("adversarial ablation gold origin drift")

        if gold.source_run_group != empirical_groups[parent_case_id]:
            raise ValueError("adversarial ablation source group drift")

    return by_case


def _validated_output_record(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("ablation output record must be a mapping")

    record = dict(value)

    if set(record) != {
        "mapping_id",
        "case_id",
        "repeat_index",
        "scenario_family",
        "output",
    }:
        raise ValueError("ablation output record keys are invalid")

    record["mapping_id"] = _mapping_id(record["mapping_id"])

    case_id = record["case_id"]

    if not isinstance(case_id, str) or case_id not in CORPUS_V1_CASE_IDS:
        raise ValueError("ablation output case_id is invalid")

    repeat_index = record["repeat_index"]

    if type(repeat_index) is not int or repeat_index not in _REPEATS:
        raise ValueError("ablation repeat_index must be 1, 2, or 3")

    family = record["scenario_family"]

    if not isinstance(family, str) or not family:
        raise ValueError("ablation scenario_family is invalid")

    output = record["output"]

    if output is not None:
        if not isinstance(output, Mapping):
            raise ValueError("ablation output must be a mapping or None")

        record["output"] = dict(cast(Mapping[str, object], output))

    return record


def _validate_complete_output_matrix(
    records: tuple[dict[str, object], ...],
) -> None:
    if len(records) != 756:
        raise ValueError("ablation analysis requires exactly 756 outputs")

    keys: set[tuple[str, str, int]] = set()

    for record in records:
        key = (
            cast(str, record["mapping_id"]),
            cast(str, record["case_id"]),
            cast(int, record["repeat_index"]),
        )

        if key in keys:
            raise ValueError("duplicate ablation mapping/case/repeat output")

        keys.add(key)

    expected = {
        (mapping_id, case_id, repeat_index)
        for mapping_id in ablation_mapping_ids()
        for case_id in CORPUS_V1_CASE_IDS
        for repeat_index in _REPEATS
    }

    if keys != expected:
        raise ValueError("ablation output matrix is incomplete")


def _mapping_id(value: object) -> str:
    mapping_ids = ablation_mapping_ids()

    if not isinstance(value, str) or value not in mapping_ids:
        raise ValueError("mapping_id is not a frozen mandatory ablation mapping")

    return value
