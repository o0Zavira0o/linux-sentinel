"""Private Phase-5F primary-metric scoring and aggregation boundary.

This module is scorer-side only. It may consume hidden ``CaseGold`` plus validated
reasoner output, but it never participates in visible projection or reasoner input.
The frozen preregistration owns metric meaning; this module pins deterministic
pre-score implementation conventions needed to make M1-M8 machine-computable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Final, cast

from .corpus import (
    ADVERSARIAL_CASE_PLAN,
    CORPUS_V1_CASE_IDS,
    EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
    EMPIRICAL_EFFECT_FAMILY,
    HARD_CASE_IDS,
    REQUIRES_EMPIRICAL_CASE_IDS,
    WANTS_EMPIRICAL_CASE_IDS,
)
from .gold import CaseGold
from .reasoner_output import validate_reasoner_output

_CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "EFFECT_OBSERVED",
    "BOUNDED_NEGATIVE",
    "COUNTEREVIDENCE",
    "INSUFFICIENT",
    "AMBIGUOUS",
)
_CLASSIFICATION_SET = frozenset(_CLASSIFICATIONS)
_CAUSAL_STRENGTH_RANK: Final[dict[str, int]] = {
    "none": 0,
    "association": 1,
    "hypothesis": 2,
    "established_cause": 3,
}
_LLM_CONDITIONS: Final[tuple[str, ...]] = ("raw", "minimal", "full")
_LLM_REPEATS: Final[tuple[int, ...]] = (1, 2, 3)
_ADVERSARIAL_FAMILY_BY_CASE: Final[dict[str, str]] = {
    case_id: family for case_id, family, _ in ADVERSARIAL_CASE_PLAN
}
_FAMILY_BY_CASE: Final[dict[str, str]] = {
    **{case_id: EMPIRICAL_EFFECT_FAMILY for case_id in REQUIRES_EMPIRICAL_CASE_IDS},
    **{
        case_id: EMPIRICAL_BOUNDED_NEGATIVE_FAMILY
        for case_id in WANTS_EMPIRICAL_CASE_IDS
    },
    **_ADVERSARIAL_FAMILY_BY_CASE,
}


def score_attempt(
    gold: CaseGold,
    output: Mapping[str, object] | None,
    *,
    condition: str,
    repeat_index: int,
    scenario_family: str,
) -> dict[str, object]:
    """Score one final output without semantic repair.

    ``output=None`` is the frozen scorer representation of a parse failure. It does
    not fabricate claims, citations, or an abstention value. Classification metrics
    count the missing prediction as incorrect; claim/reference denominators remain
    zero because no valid structured claims existed.
    """
    if not isinstance(gold, CaseGold):
        raise ValueError("gold must be a CaseGold")
    condition = _condition(condition)
    repeat_index = _repeat_index(repeat_index)
    scenario_family = _scenario_family(scenario_family)
    if scenario_family != _FAMILY_BY_CASE[gold.case_id]:
        raise ValueError("scenario_family does not match frozen corpus-v1 case plan")

    if output is None:
        return _score_parse_failure(
            gold,
            condition=condition,
            repeat_index=repeat_index,
            scenario_family=scenario_family,
        )

    validated = validate_reasoner_output(dict(output))
    classification = cast(str, validated["classification"])
    abstain = cast(bool, validated["abstain"])
    claims = cast(list[dict[str, object]], validated["claims"])
    top_level_refs = cast(list[str], validated["evidence_refs"])

    max_rank = _CAUSAL_STRENGTH_RANK[gold.maximum_allowed_causal_strength]
    causal_claim_count = 0
    unsupported_causal_claim_count = 0
    claim_ref_count = 0
    supporting_claim_ref_count = 0
    invalid_claim_ref_count = 0
    all_referenced: set[str] = set(top_level_refs)
    supporting = set(gold.supporting_evidence_refs)
    invalid = set(gold.invalid_evidence_refs)

    for claim in claims:
        strength = cast(str, claim["causal_strength"])
        if strength != "none":
            causal_claim_count += 1
            if _CAUSAL_STRENGTH_RANK[strength] > max_rank:
                unsupported_causal_claim_count += 1
        refs = cast(list[str], claim["evidence_refs"])
        for ref in refs:
            claim_ref_count += 1
            if ref in supporting:
                supporting_claim_ref_count += 1
            if ref in invalid:
                invalid_claim_ref_count += 1
            all_referenced.add(ref)

    counter_refs = set(gold.counterevidence_refs)
    counter_eligible = bool(counter_refs)
    counter_preserved: bool | None = None
    if counter_eligible:
        counter_preserved = bool(all_referenced & counter_refs) or (
            classification == gold.classification
        )

    return {
        "case_id": gold.case_id,
        "condition": condition,
        "repeat_index": repeat_index,
        "scenario_family": scenario_family,
        "origin": gold.origin,
        "source_run_group": gold.source_run_group,
        "is_hard": gold.case_id in HARD_CASE_IDS,
        "parse_failed": False,
        "gold_classification": gold.classification,
        "classification": classification,
        "classification_correct": classification == gold.classification,
        "must_abstain": gold.must_abstain,
        "abstain": abstain,
        "causal_claim_count": causal_claim_count,
        "unsupported_causal_claim_count": unsupported_causal_claim_count,
        "claim_evidence_ref_count": claim_ref_count,
        "supporting_claim_ref_count": supporting_claim_ref_count,
        "invalid_claim_ref_count": invalid_claim_ref_count,
        "counterevidence_eligible": counter_eligible,
        "counterevidence_preserved": counter_preserved,
    }


def summarize_primary_metrics(
    attempts: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Aggregate M1-M8 over an already selected analysis slice.

    Metric rates with an empty natural denominator remain ``None``. M2 uses the
    fixed five preregistered labels; a label with neither truth nor prediction is
    reported as undefined and omitted from the secondary-slice macro mean. The
    primary HARD/full-corpus sets contain all five labels, so their M2 is the exact
    five-class macro-F1 required by the frozen metric contract.
    """
    rows = tuple(_validated_score_row(item) for item in attempts)
    if not rows:
        raise ValueError("metric summary requires at least one scored attempt")

    m1_numerator = sum(bool(row["classification_correct"]) for row in rows)
    m1 = _rate(m1_numerator, len(rows))

    per_class: dict[str, dict[str, object]] = {}
    defined_f1: list[float] = []
    for label in _CLASSIFICATIONS:
        tp = 0
        fp = 0
        fn = 0
        for row in rows:
            truth = row["gold_classification"]
            prediction = row["classification"]
            if truth == label and prediction == label:
                tp += 1
            elif truth != label and prediction == label:
                fp += 1
            elif truth == label and prediction != label:
                fn += 1
        denominator = 2 * tp + fp + fn
        f1 = None if denominator == 0 else (2 * tp) / denominator
        if f1 is not None:
            defined_f1.append(f1)
        per_class[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "f1": f1,
        }
    macro_f1 = sum(defined_f1) / len(defined_f1) if defined_f1 else None

    causal_denominator = sum(cast(int, row["causal_claim_count"]) for row in rows)
    unsupported_numerator = sum(
        cast(int, row["unsupported_causal_claim_count"]) for row in rows
    )

    abstain_tp = 0
    abstain_fp = 0
    abstain_fn = 0
    for row in rows:
        truth = cast(bool, row["must_abstain"])
        prediction = row["abstain"]
        if prediction is True and truth:
            abstain_tp += 1
        elif prediction is True and not truth:
            abstain_fp += 1
        elif truth and prediction is not True:
            abstain_fn += 1
    abstain_denominator = 2 * abstain_tp + abstain_fp + abstain_fn
    abstain_f1 = (
        None if abstain_denominator == 0 else (2 * abstain_tp) / abstain_denominator
    )

    citation_denominator = sum(
        cast(int, row["claim_evidence_ref_count"]) for row in rows
    )
    citation_numerator = sum(
        cast(int, row["supporting_claim_ref_count"]) for row in rows
    )
    provenance_numerator = sum(
        cast(int, row["invalid_claim_ref_count"]) for row in rows
    )

    counter_rows = [row for row in rows if row["counterevidence_eligible"] is True]
    counter_numerator = sum(
        row["counterevidence_preserved"] is True for row in counter_rows
    )

    m8 = _run_consistency(rows)
    return {
        "attempt_count": len(rows),
        "case_count": len({cast(str, row["case_id"]) for row in rows}),
        "source_run_group_count": len(
            {cast(str, row["source_run_group"]) for row in rows}
        ),
        "parse_failure_count": sum(bool(row["parse_failed"]) for row in rows),
        "m1_task_correctness": {
            "numerator": m1_numerator,
            "denominator": len(rows),
            "rate": m1,
        },
        "m2_macro_f1": {
            "rate": macro_f1,
            "defined_class_count": len(defined_f1),
            "per_class": per_class,
        },
        "m3_uccr": {
            "numerator": unsupported_numerator,
            "denominator": causal_denominator,
            "rate": _rate_or_none(unsupported_numerator, causal_denominator),
        },
        "m4_correct_abstention_f1": {
            "tp": abstain_tp,
            "fp": abstain_fp,
            "fn": abstain_fn,
            "rate": abstain_f1,
        },
        "m5_evidence_citation_precision": {
            "numerator": citation_numerator,
            "denominator": citation_denominator,
            "rate": _rate_or_none(citation_numerator, citation_denominator),
        },
        "m6_provenance_violation_rate": {
            "numerator": provenance_numerator,
            "denominator": citation_denominator,
            "rate": _rate_or_none(provenance_numerator, citation_denominator),
        },
        "m7_counterevidence_preservation_rate": {
            "numerator": counter_numerator,
            "denominator": len(counter_rows),
            "rate": _rate_or_none(counter_numerator, len(counter_rows)),
        },
        "m8_run_consistency": m8,
    }


def build_corpus_v1_analysis_report(
    attempts: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Build the frozen corpus-v1 analysis slices from exactly 324 LLM attempts."""
    rows = tuple(_validated_score_row(item) for item in attempts)
    _validate_complete_llm_matrix(rows)

    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_family[cast(str, row["scenario_family"])].append(row)

    all_rows = list(rows)
    hard_rows = [row for row in rows if row["is_hard"] is True]
    empirical_rows = [row for row in rows if row["origin"] == "empirical"]
    adversarial_rows = [row for row in rows if row["origin"] == "adversarial"]

    analysis_sets: dict[str, list[dict[str, object]]] = {
        "all": all_rows,
        "hard": hard_rows,
        "empirical": empirical_rows,
        "adversarial": adversarial_rows,
    }
    for family, family_rows in sorted(by_family.items()):
        analysis_sets[f"family:{family}"] = family_rows

    return {
        "schema_version": "sentinel-x.phase5f-primary-metrics.v1",
        "attempt_count": len(rows),
        "case_count": 36,
        "conditions": list(_LLM_CONDITIONS),
        "repeats": list(_LLM_REPEATS),
        "analysis_sets": {
            name: _analysis_set_summary(set_rows)
            for name, set_rows in analysis_sets.items()
        },
    }


def _score_parse_failure(
    gold: CaseGold,
    *,
    condition: str,
    repeat_index: int,
    scenario_family: str,
) -> dict[str, object]:
    return {
        "case_id": gold.case_id,
        "condition": condition,
        "repeat_index": repeat_index,
        "scenario_family": scenario_family,
        "origin": gold.origin,
        "source_run_group": gold.source_run_group,
        "is_hard": gold.case_id in HARD_CASE_IDS,
        "parse_failed": True,
        "gold_classification": gold.classification,
        "classification": None,
        "classification_correct": False,
        "must_abstain": gold.must_abstain,
        "abstain": None,
        "causal_claim_count": 0,
        "unsupported_causal_claim_count": 0,
        "claim_evidence_ref_count": 0,
        "supporting_claim_ref_count": 0,
        "invalid_claim_ref_count": 0,
        "counterevidence_eligible": bool(gold.counterevidence_refs),
        "counterevidence_preserved": (False if gold.counterevidence_refs else None),
    }


def _analysis_set_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    by_condition: dict[str, dict[str, object]] = {}
    by_repeat: dict[str, dict[str, object]] = {}
    for condition in _LLM_CONDITIONS:
        selected = [row for row in rows if row["condition"] == condition]
        by_condition[condition] = summarize_primary_metrics(selected)
    for repeat_index in _LLM_REPEATS:
        repeat_summary: dict[str, object] = {}
        for condition in _LLM_CONDITIONS:
            selected = [
                row
                for row in rows
                if row["condition"] == condition and row["repeat_index"] == repeat_index
            ]
            repeat_summary[condition] = summarize_primary_metrics(selected)
        by_repeat[str(repeat_index)] = repeat_summary
    return {
        "case_count": len({cast(str, row["case_id"]) for row in rows}),
        "source_run_group_count": len(
            {cast(str, row["source_run_group"]) for row in rows}
        ),
        "condition_summaries": by_condition,
        "repeat_summaries": by_repeat,
    }


def _run_consistency(rows: tuple[dict[str, object], ...]) -> dict[str, object]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(cast(str, row["case_id"]), cast(str, row["condition"]))].append(row)

    complete_groups = 0
    agreement_total = 0.0
    group_details: dict[str, object] = {}
    for (case_id, condition), group in sorted(grouped.items()):
        repeats = {cast(int, item["repeat_index"]) for item in group}
        if len(group) != 3 or repeats != set(_LLM_REPEATS):
            continue
        complete_groups += 1
        parsed = [
            item["classification"]
            for item in group
            if isinstance(item["classification"], str)
        ]
        if parsed:
            modal_count = max(Counter(parsed).values())
            rate = modal_count / 3
        else:
            modal_count = 0
            rate = 0.0
        agreement_total += rate
        group_details[f"{case_id}:{condition}"] = {
            "modal_count": modal_count,
            "denominator": 3,
            "rate": rate,
        }
    return {
        "complete_case_condition_group_count": complete_groups,
        "rate": (None if complete_groups == 0 else agreement_total / complete_groups),
        "groups": group_details,
    }


def _validate_complete_llm_matrix(rows: tuple[dict[str, object], ...]) -> None:
    if len(rows) != 324:
        raise ValueError("corpus-v1 LLM analysis requires exactly 324 attempts")
    keys: set[tuple[str, str, int]] = set()
    case_metadata: dict[str, tuple[str, str, str, bool]] = {}
    for row in rows:
        case_id = cast(str, row["case_id"])
        condition = cast(str, row["condition"])
        repeat_index = cast(int, row["repeat_index"])
        key = (case_id, condition, repeat_index)
        if key in keys:
            raise ValueError("duplicate case/condition/repeat scored attempt")
        keys.add(key)
        family = cast(str, row["scenario_family"])
        origin = cast(str, row["origin"])
        if family != _FAMILY_BY_CASE[case_id]:
            raise ValueError("scenario-family drift from frozen corpus-v1 plan")
        expected_origin = (
            "empirical"
            if case_id in REQUIRES_EMPIRICAL_CASE_IDS
            or case_id in WANTS_EMPIRICAL_CASE_IDS
            else "adversarial"
        )
        if origin != expected_origin:
            raise ValueError("origin drift from frozen corpus-v1 plan")
        metadata = (
            family,
            origin,
            cast(str, row["source_run_group"]),
            cast(bool, row["is_hard"]),
        )
        previous = case_metadata.setdefault(case_id, metadata)
        if previous != metadata:
            raise ValueError("case metadata drift across conditions/repeats")
    empirical_groups = {
        case_id: case_metadata[case_id][2]
        for case_id in (*REQUIRES_EMPIRICAL_CASE_IDS, *WANTS_EMPIRICAL_CASE_IDS)
    }
    if len(set(empirical_groups.values())) != 16:
        raise ValueError("empirical corpus-v1 cases require 16 source-run groups")
    for case_id, _, parent_case_id in ADVERSARIAL_CASE_PLAN:
        if case_metadata[case_id][2] != empirical_groups[parent_case_id]:
            raise ValueError("adversarial source-run group drift from empirical parent")
    expected = {
        (case_id, condition, repeat_index)
        for case_id in CORPUS_V1_CASE_IDS
        for condition in _LLM_CONDITIONS
        for repeat_index in _LLM_REPEATS
    }
    if keys != expected:
        raise ValueError("corpus-v1 scored matrix is incomplete or unexpected")


def _validated_score_row(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("scored attempt must be a mapping")
    row = dict(value)
    required = {
        "case_id",
        "condition",
        "repeat_index",
        "scenario_family",
        "origin",
        "source_run_group",
        "is_hard",
        "parse_failed",
        "gold_classification",
        "classification",
        "classification_correct",
        "must_abstain",
        "abstain",
        "causal_claim_count",
        "unsupported_causal_claim_count",
        "claim_evidence_ref_count",
        "supporting_claim_ref_count",
        "invalid_claim_ref_count",
        "counterevidence_eligible",
        "counterevidence_preserved",
    }
    if set(row) != required:
        raise ValueError("scored attempt keys do not match scorer contract")
    case_id = row["case_id"]
    if not isinstance(case_id, str) or case_id not in CORPUS_V1_CASE_IDS:
        raise ValueError("scored attempt case_id is not corpus-v1")
    row["condition"] = _condition(row["condition"])
    row["repeat_index"] = _repeat_index(row["repeat_index"])
    row["scenario_family"] = _scenario_family(row["scenario_family"])
    if row["origin"] not in {"empirical", "adversarial"}:
        raise ValueError("scored attempt origin is invalid")
    if not isinstance(row["source_run_group"], str) or not row["source_run_group"]:
        raise ValueError("scored attempt source_run_group is invalid")
    for field in (
        "is_hard",
        "parse_failed",
        "classification_correct",
        "must_abstain",
        "counterevidence_eligible",
    ):
        if type(row[field]) is not bool:
            raise ValueError(f"scored attempt {field} must be boolean")
    if row["is_hard"] != (case_id in HARD_CASE_IDS):
        raise ValueError("scored attempt HARD membership drift")
    truth = row["gold_classification"]
    if not isinstance(truth, str) or truth not in _CLASSIFICATION_SET:
        raise ValueError("scored attempt gold classification is invalid")
    prediction = row["classification"]
    if prediction is not None and (
        not isinstance(prediction, str) or prediction not in _CLASSIFICATION_SET
    ):
        raise ValueError("scored attempt classification is invalid")
    abstain = row["abstain"]
    if abstain is not None and type(abstain) is not bool:
        raise ValueError("scored attempt abstain is invalid")
    if bool(row["parse_failed"]) != (prediction is None and abstain is None):
        raise ValueError("parse_failed must exactly match missing semantic output")
    if bool(row["classification_correct"]) != (prediction == truth):
        raise ValueError("classification_correct drift")
    for field in (
        "causal_claim_count",
        "unsupported_causal_claim_count",
        "claim_evidence_ref_count",
        "supporting_claim_ref_count",
        "invalid_claim_ref_count",
    ):
        item = row[field]
        if type(item) is not int or item < 0:
            raise ValueError(f"scored attempt {field} must be nonnegative integer")
    if cast(int, row["unsupported_causal_claim_count"]) > cast(
        int, row["causal_claim_count"]
    ):
        raise ValueError("unsupported causal claim count exceeds causal claims")
    if cast(int, row["supporting_claim_ref_count"]) > cast(
        int, row["claim_evidence_ref_count"]
    ):
        raise ValueError("supporting reference count exceeds claim references")
    if cast(int, row["invalid_claim_ref_count"]) > cast(
        int, row["claim_evidence_ref_count"]
    ):
        raise ValueError("invalid reference count exceeds claim references")
    preserved = row["counterevidence_preserved"]
    eligible = cast(bool, row["counterevidence_eligible"])
    if eligible and type(preserved) is not bool:
        raise ValueError("eligible counterevidence requires boolean preservation")
    if not eligible and preserved is not None:
        raise ValueError("ineligible counterevidence preservation must be missing")
    return row


def _condition(value: object) -> str:
    if not isinstance(value, str) or value not in _LLM_CONDITIONS:
        raise ValueError("condition must be raw, minimal, or full")
    return value


def _repeat_index(value: object) -> int:
    if type(value) is not int or value not in _LLM_REPEATS:
        raise ValueError("repeat_index must be 1, 2, or 3")
    return value


def _scenario_family(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("scenario_family must be non-empty text")
    return value


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise ValueError("rate denominator must be positive")
    return numerator / denominator


def _rate_or_none(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return _rate(numerator, denominator)
