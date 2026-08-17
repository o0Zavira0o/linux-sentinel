"""Private deterministic Phase-5F adversarial corpus transformations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final, cast

from ._common import validate_case_id
from .corpus import (
    ADVERSARIAL_CASE_PLAN,
    CROSS_BOOT_INVALID_FAMILY,
    EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
    EMPIRICAL_EFFECT_FAMILY,
    INSUFFICIENT_COVERAGE_FAMILY,
    MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY,
    PHASE5F_TRANSFORMATION_VERSION,
    REVERSE_COUNTEREVIDENCE_FAMILY,
    TEMPORAL_DISTRACTOR_FAMILY,
    CorpusCase,
    _required_nonnegative_int,
    _required_positive_int,
    _required_text,
    _thaw_json,
)
from .gold import CaseGold
from .visible import CaseSource

_FULL_INVALIDATED_BY_TRANSFORM: Final[frozenset[str]] = frozenset(
    {
        "controlled_coverage",
        "pairwise_evidence",
        "controlled_record",
        "live_run",
        "protocol_execution",
        "synthesis",
    }
)


def derive_adversarial_case(
    parent: CorpusCase,
    *,
    case_id: str,
    scenario_family: str,
) -> CorpusCase:
    """Apply one frozen deterministic adversarial transformation."""

    validate_case_id(case_id)
    expected = {
        planned_case: (family, parent_id)
        for planned_case, family, parent_id in ADVERSARIAL_CASE_PLAN
    }.get(case_id)
    if expected is None:
        raise ValueError("case_id is not assigned to an adversarial corpus-v1 case")
    if expected != (scenario_family, parent.source.case_id):
        raise ValueError("adversarial case does not match the frozen corpus-v1 plan")
    if parent.gold.origin != "empirical":
        raise ValueError("adversarial parent must be empirical")

    if scenario_family == INSUFFICIENT_COVERAGE_FAMILY:
        return _derive_insufficient(parent, case_id)
    if scenario_family == REVERSE_COUNTEREVIDENCE_FAMILY:
        return _derive_reverse(parent, case_id)
    if scenario_family == TEMPORAL_DISTRACTOR_FAMILY:
        return _derive_temporal_distractor(parent, case_id)
    if scenario_family == CROSS_BOOT_INVALID_FAMILY:
        return _derive_cross_boot(parent, case_id)
    if scenario_family == MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY:
        return _derive_multiple_candidate(parent, case_id)
    raise AssertionError("unreachable adversarial family")


def _derive_insufficient(parent: CorpusCase, case_id: str) -> CorpusCase:
    if parent.scenario_family != EMPIRICAL_BOUNDED_NEGATIVE_FAMILY:
        raise ValueError(
            "insufficient-coverage parent must be empirical bounded-negative"
        )
    items, lineage = _mutable_case(parent.source, parent.lineage_by_ref)
    coverage = _single_payload_item(items, "coverage", "minimal")
    minimal = cast(dict[str, object], coverage["minimal"])
    full = cast(dict[str, object], coverage["full"])
    max_gap = _required_positive_int(minimal, "max_sample_gap_usec")
    forced_gap = max_gap + 1
    minimal["largest_gap_usec"] = forced_gap
    full["largest_gap_usec"] = forced_gap
    raw_item = _single_payload_item(items, "systemctl_samples", "raw")
    raw_payload = cast(dict[str, object], raw_item["raw"])
    raw_samples = raw_payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("systemctl_samples raw payload requires samples list")
    target_unit = _required_text(minimal, "unit")
    window_start = _required_nonnegative_int(minimal, "window_start_usec")
    window_end = _required_nonnegative_int(minimal, "window_end_usec")
    cut_start = window_start + (window_end - window_start) // 3
    cut_end = window_start + 2 * (window_end - window_start) // 3
    retained_samples: list[object] = []
    removed = 0
    for raw_sample in raw_samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("systemctl_samples entries must be mappings")
        sample_unit = raw_sample.get("unit")
        sample_usec = raw_sample.get("captured_monotonic_usec")
        if (
            sample_unit == target_unit
            and type(sample_usec) is int
            and cut_start <= sample_usec <= cut_end
        ):
            removed += 1
            continue
        retained_samples.append(raw_sample)
    raw_payload["samples"] = retained_samples
    raw_payload["derived_removed_sample_count"] = removed
    raw_payload["derived_gap_start_usec"] = cut_start
    raw_payload["derived_gap_end_usec"] = cut_end
    _drop_invalidated_full_records(items, lineage)
    source = _rebuild_source(parent.source, case_id, items)
    coverage_ref = cast(str, coverage["ref"])
    gold = _derived_gold(
        parent,
        case_id=case_id,
        classification="INSUFFICIENT",
        must_abstain=True,
        supporting=("REF-0001", "REF-0002", "REF-0003", coverage_ref, "REF-0010"),
        invalid=(),
        counter=(),
        maximum_causal_strength="none",
    )
    return _derived_case(
        source,
        gold,
        parent,
        family=INSUFFICIENT_COVERAGE_FAMILY,
        lineage=lineage,
        details={
            "operation": "force_coverage_gap_above_bound",
            "coverage_ref": coverage_ref,
            "forced_largest_gap_usec": forced_gap,
            "max_sample_gap_usec": max_gap,
            "raw_samples_removed_inside_middle_third": removed,
            "stale_full_derived_records_removed": True,
        },
    )


def _derive_reverse(parent: CorpusCase, case_id: str) -> CorpusCase:
    if parent.scenario_family != EMPIRICAL_EFFECT_FAMILY:
        raise ValueError("reverse parent must be empirical effect")
    items, lineage = _mutable_case(parent.source, parent.lineage_by_ref)
    source_item = _timeline_item(items, unit_role="source")
    target_item = _timeline_item(items, unit_role="target")
    source_time = _required_nonnegative_int(
        cast(dict[str, object], source_item["minimal"]),
        "transition_monotonic_usec",
    )
    reversed_time = max(0, source_time - 100_000)
    for view in ("minimal", "full"):
        payload = cast(dict[str, object], target_item[view])
        payload["transition_monotonic_usec"] = reversed_time
    raw_item = _single_payload_item(items, "systemctl_samples", "raw")
    raw_payload = cast(dict[str, object], raw_item["raw"])
    raw_samples = raw_payload.get("samples")
    if not isinstance(raw_samples, list):
        raise ValueError("systemctl_samples raw payload requires samples list")
    target_unit = _required_text(
        cast(dict[str, object], target_item["minimal"]), "unit"
    )
    filtered: list[object] = []
    template: dict[str, object] | None = None
    for raw_sample in raw_samples:
        if not isinstance(raw_sample, Mapping):
            raise ValueError("systemctl_samples entries must be mappings")
        stdout = raw_sample.get("stdout")
        is_target_anomaly = (
            raw_sample.get("unit") == target_unit
            and isinstance(stdout, str)
            and ("ActiveState=inactive" in stdout or "ActiveState=failed" in stdout)
        )
        if is_target_anomaly:
            if template is None:
                template = dict(raw_sample)
            continue
        filtered.append(raw_sample)
    if template is None:
        template = {
            "round": -1,
            "captured_at": "derived-adversarial",
            "unit": target_unit,
            "returncode": 0,
            "duration_usec": 0,
            "stdout": f"Id={target_unit}\nActiveState=inactive\nSubState=dead\n",
            "stderr": "",
        }
    template["captured_monotonic_usec"] = reversed_time
    template["derived_adversarial"] = True
    filtered.append(template)
    filtered.sort(
        key=lambda row: (
            row.get("captured_monotonic_usec", 0) if isinstance(row, Mapping) else 0,
            str(row.get("unit", "")) if isinstance(row, Mapping) else "",
        )
    )
    raw_payload["samples"] = filtered
    _drop_kind(items, lineage, "journal_excerpt")
    _drop_invalidated_full_records(items, lineage)
    source = _rebuild_source(parent.source, case_id, items)
    source_ref = cast(str, source_item["ref"])
    target_ref = cast(str, target_item["ref"])
    gold = _derived_gold(
        parent,
        case_id=case_id,
        classification="COUNTEREVIDENCE",
        must_abstain=False,
        supporting=("REF-0001", "REF-0002", source_ref, target_ref, "REF-0010"),
        invalid=(),
        counter=(source_ref, target_ref, "REF-0010"),
        maximum_causal_strength="none",
    )
    return _derived_case(
        source,
        gold,
        parent,
        family=REVERSE_COUNTEREVIDENCE_FAMILY,
        lineage=lineage,
        details={
            "operation": "move_target_anomaly_before_source_transition",
            "source_transition_usec": source_time,
            "derived_target_transition_usec": reversed_time,
            "raw_journal_removed_to_avoid_stale_forward_timing": True,
            "stale_full_derived_records_removed": True,
        },
    )


def _derive_temporal_distractor(parent: CorpusCase, case_id: str) -> CorpusCase:
    if parent.scenario_family != EMPIRICAL_EFFECT_FAMILY:
        raise ValueError("temporal-distractor parent must be empirical effect")
    items, lineage = _mutable_case(parent.source, parent.lineage_by_ref)
    source_item = _timeline_item(items, unit_role="source")
    source_payload = cast(dict[str, object], source_item["minimal"])
    source_time = _required_nonnegative_int(source_payload, "transition_monotonic_usec")
    boot_id = _required_text(source_payload, "boot_id")
    distractor_ref = _next_ref(items)
    distractor_unit = f"unrelated-{case_id.lower()}.service"
    distractor: dict[str, object] = {
        "ref": distractor_ref,
        "kind": "incident_timeline",
        "raw": {
            "unit": distractor_unit,
            "state": "failed",
            "captured_monotonic_usec": source_time + 50_000,
            "boot_id": boot_id,
            "raw_text": f"ActiveState=failed\\nSubState=failed\\nId={distractor_unit}",
        },
        "minimal": {
            "unit": distractor_unit,
            "state": "failed",
            "transition_monotonic_usec": source_time + 50_000,
            "boot_id": boot_id,
        },
        "full": {
            "unit": distractor_unit,
            "state": "failed",
            "transition_monotonic_usec": source_time + 50_000,
            "boot_id": boot_id,
        },
    }
    items.append(distractor)
    lineage[distractor_ref] = f"derived:{case_id}:temporal-distractor"
    source = _rebuild_source(parent.source, case_id, items)
    gold = _derived_gold(
        parent,
        case_id=case_id,
        classification="EFFECT_OBSERVED",
        must_abstain=False,
        supporting=parent.gold.supporting_evidence_refs,
        invalid=(distractor_ref,),
        counter=(),
        maximum_causal_strength="hypothesis",
    )
    return _derived_case(
        source,
        gold,
        parent,
        family=TEMPORAL_DISTRACTOR_FAMILY,
        lineage=lineage,
        details={
            "operation": "add_temporally_close_structurally_unrelated_anomaly",
            "distractor_ref": distractor_ref,
            "distractor_unit": distractor_unit,
            "offset_usec": 50_000,
            "parent_full_records_preserved": True,
        },
    )


def _derive_cross_boot(parent: CorpusCase, case_id: str) -> CorpusCase:
    if parent.scenario_family != EMPIRICAL_BOUNDED_NEGATIVE_FAMILY:
        raise ValueError("cross-boot parent must be empirical bounded-negative")
    items, lineage = _mutable_case(parent.source, parent.lineage_by_ref)
    scope_item = _single_payload_item(items, "scope", "minimal")
    scope = cast(dict[str, object], scope_item["minimal"])
    boot_id = _required_text(scope, "boot_id")
    wrong_boot = "f" * 32 if boot_id != "f" * 32 else "e" * 32
    target_unit = _required_text(scope, "target_unit")
    source_item = _timeline_item(items, unit_role="source")
    source_time = _required_nonnegative_int(
        cast(dict[str, object], source_item["minimal"]),
        "transition_monotonic_usec",
    )
    observation_ref = _next_ref(items)
    timeline_ref = _next_ref(items, after=observation_ref)
    items.extend(
        [
            {
                "ref": observation_ref,
                "kind": "observation",
                "raw": {
                    "unit": target_unit,
                    "status": "failed",
                    "boot_id": wrong_boot,
                    "raw_text": "ActiveState=failed\\nSubState=failed",
                },
                "minimal": {
                    "unit": target_unit,
                    "status": "failed",
                    "boot_id": wrong_boot,
                },
                "full": {
                    "unit": target_unit,
                    "status": "failed",
                    "boot_id": wrong_boot,
                },
            },
            {
                "ref": timeline_ref,
                "kind": "incident_timeline",
                "minimal": {
                    "unit": target_unit,
                    "state": "failed",
                    "transition_monotonic_usec": source_time + 50_000,
                    "boot_id": wrong_boot,
                },
                "full": {
                    "unit": target_unit,
                    "state": "failed",
                    "transition_monotonic_usec": source_time + 50_000,
                    "boot_id": wrong_boot,
                },
            },
        ]
    )
    lineage[observation_ref] = f"derived:{case_id}:wrong-boot-observation"
    lineage[timeline_ref] = f"derived:{case_id}:wrong-boot-timeline"
    source = _rebuild_source(parent.source, case_id, items)
    gold = _derived_gold(
        parent,
        case_id=case_id,
        classification="BOUNDED_NEGATIVE",
        must_abstain=False,
        supporting=parent.gold.supporting_evidence_refs,
        invalid=(observation_ref, timeline_ref),
        counter=(),
        maximum_causal_strength="none",
    )
    return _derived_case(
        source,
        gold,
        parent,
        family=CROSS_BOOT_INVALID_FAMILY,
        lineage=lineage,
        details={
            "operation": "add_other_boot_supportive_distractor",
            "wrong_boot_id": wrong_boot,
            "invalid_refs": [observation_ref, timeline_ref],
            "parent_full_records_preserved": True,
        },
    )


def _derive_multiple_candidate(parent: CorpusCase, case_id: str) -> CorpusCase:
    if parent.scenario_family != EMPIRICAL_EFFECT_FAMILY:
        raise ValueError("multiple-candidate parent must be empirical effect")
    items, lineage = _mutable_case(parent.source, parent.lineage_by_ref)
    scope_item = _single_payload_item(items, "scope", "minimal")
    source_item = _timeline_item(items, unit_role="source")
    source_time = _required_nonnegative_int(
        cast(dict[str, object], source_item["minimal"]),
        "transition_monotonic_usec",
    )
    for view in ("raw", "minimal", "full"):
        if view in scope_item:
            payload = cast(dict[str, object], scope_item[view])
            payload.pop("source_unit", None)
    original_topology = _single_payload_item(items, "topology_relation", "minimal")
    topology = cast(dict[str, object], original_topology["minimal"])
    target_unit = _required_text(topology, "subject_unit")
    boot_id = _required_text(topology, "boot_id")
    second_source = f"alternate-{case_id.lower()}.service"
    topology_ref = _next_ref(items)
    timeline_ref = _next_ref(items, after=topology_ref)
    items.extend(
        [
            {
                "ref": topology_ref,
                "kind": "topology_relation",
                "raw": {
                    "manager_property": "Wants",
                    "subject_unit": target_unit,
                    "object_unit": second_source,
                    "boot_id": boot_id,
                },
                "minimal": {
                    "subject_unit": target_unit,
                    "object_unit": second_source,
                    "relation": "wants",
                    "boot_id": boot_id,
                },
                "full": {
                    "subject_unit": target_unit,
                    "object_unit": second_source,
                    "relation": "wants",
                    "boot_id": boot_id,
                },
            },
            {
                "ref": timeline_ref,
                "kind": "incident_timeline",
                "raw": {
                    "unit": second_source,
                    "state": "inactive",
                    "captured_monotonic_usec": source_time + 10_000,
                    "boot_id": boot_id,
                    "raw_text": "ActiveState=inactive\\nSubState=dead",
                },
                "minimal": {
                    "unit": second_source,
                    "state": "inactive",
                    "transition_monotonic_usec": source_time + 10_000,
                    "boot_id": boot_id,
                },
                "full": {
                    "unit": second_source,
                    "state": "inactive",
                    "transition_monotonic_usec": source_time + 10_000,
                    "boot_id": boot_id,
                },
            },
        ]
    )
    lineage[topology_ref] = f"derived:{case_id}:alternate-topology"
    lineage[timeline_ref] = f"derived:{case_id}:alternate-source-transition"
    _drop_invalidated_full_records(items, lineage)
    source = _rebuild_source(parent.source, case_id, items)
    gold = _derived_gold(
        parent,
        case_id=case_id,
        classification="AMBIGUOUS",
        must_abstain=True,
        supporting=(
            "REF-0001",
            "REF-0002",
            topology_ref,
            "REF-0003",
            timeline_ref,
            "REF-0004",
            "REF-0005",
            "REF-0010",
        ),
        invalid=(),
        counter=(),
        maximum_causal_strength="none",
    )
    return _derived_case(
        source,
        gold,
        parent,
        family=MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY,
        lineage=lineage,
        details={
            "operation": "remove_explicit_source_and_add_equally_plausible_candidate",
            "alternate_source_unit": second_source,
            "alternate_topology_ref": topology_ref,
            "alternate_timeline_ref": timeline_ref,
            "stale_full_derived_records_removed": True,
        },
    )


def _derived_case(
    source: CaseSource,
    gold: CaseGold,
    parent: CorpusCase,
    *,
    family: str,
    lineage: Mapping[str, str],
    details: Mapping[str, object],
) -> CorpusCase:
    return CorpusCase(
        source=source,
        gold=gold,
        scenario_family=family,
        lineage_by_ref=lineage,
        parent_case_id=parent.source.case_id,
        transformation=family,
        transformation_version=PHASE5F_TRANSFORMATION_VERSION,
        transformation_details=details,
    )


def _derived_gold(
    parent: CorpusCase,
    *,
    case_id: str,
    classification: str,
    must_abstain: bool,
    supporting: Iterable[str],
    invalid: Iterable[str],
    counter: Iterable[str],
    maximum_causal_strength: str,
) -> CaseGold:
    return CaseGold(
        case_id=case_id,
        classification=classification,
        must_abstain=must_abstain,
        supporting_evidence_refs=tuple(sorted(set(supporting))),
        invalid_evidence_refs=tuple(sorted(set(invalid))),
        counterevidence_refs=tuple(sorted(set(counter))),
        maximum_allowed_causal_strength=maximum_causal_strength,
        origin="adversarial",
        source_run_group=parent.gold.source_run_group,
    )


def _mutable_case(
    source: CaseSource,
    lineage_by_ref: Mapping[str, str],
) -> tuple[list[dict[str, object]], dict[str, str]]:
    items = [_thaw_json(item) for item in source.evidence_items]
    if not all(isinstance(item, dict) for item in items):
        raise AssertionError("thawed evidence items must be mappings")
    return cast(list[dict[str, object]], items), dict(lineage_by_ref)


def _rebuild_source(
    parent: CaseSource,
    case_id: str,
    items: list[dict[str, object]],
) -> CaseSource:
    environment = _thaw_json(parent.environment)
    if not isinstance(environment, dict):
        raise AssertionError("thawed CaseSource environment must remain a mapping")
    return CaseSource(
        case_id=case_id,
        task=parent.task,
        environment=cast(dict[str, object], environment),
        evidence_items=tuple(items),
    )


def _single_payload_item(
    items: list[dict[str, object]],
    kind: str,
    view: str,
) -> dict[str, object]:
    matched = [item for item in items if item.get("kind") == kind and view in item]
    if len(matched) != 1:
        raise ValueError(f"expected exactly one {kind} item with {view} payload")
    return matched[0]


def _timeline_item(
    items: list[dict[str, object]], *, unit_role: str
) -> dict[str, object]:
    scope = _single_payload_item(items, "scope", "minimal")
    scope_payload = cast(dict[str, object], scope["minimal"])
    field = "source_unit" if unit_role == "source" else "target_unit"
    unit = _required_text(scope_payload, field)
    matched: list[dict[str, object]] = []
    for item in items:
        if item.get("kind") != "incident_timeline" or "minimal" not in item:
            continue
        payload = cast(dict[str, object], item["minimal"])
        if payload.get("unit") == unit:
            matched.append(item)
    if len(matched) != 1:
        raise ValueError(f"expected one {unit_role} incident_timeline item")
    return matched[0]


def _drop_invalidated_full_records(
    items: list[dict[str, object]],
    lineage: dict[str, str],
) -> None:
    retained: list[dict[str, object]] = []
    for item in items:
        if item.get("kind") in _FULL_INVALIDATED_BY_TRANSFORM:
            item.pop("full", None)
        if any(view in item for view in ("raw", "minimal", "full")):
            retained.append(item)
        else:
            ref = item.get("ref")
            if isinstance(ref, str):
                lineage.pop(ref, None)
    items[:] = retained


def _drop_kind(
    items: list[dict[str, object]], lineage: dict[str, str], kind: str
) -> None:
    retained: list[dict[str, object]] = []
    for item in items:
        if item.get("kind") == kind:
            ref = item.get("ref")
            if isinstance(ref, str):
                lineage.pop(ref, None)
            continue
        retained.append(item)
    items[:] = retained


def _next_ref(items: list[dict[str, object]], *, after: str | None = None) -> str:
    used = {
        int(cast(str, item["ref"])[4:])
        for item in items
        if isinstance(item.get("ref"), str)
    }
    start = 100 if after is None else int(after[4:]) + 1
    for value in range(start, 10_000):
        if value not in used:
            return f"REF-{value:04d}"
    raise ValueError("no opaque evidence reference available")
