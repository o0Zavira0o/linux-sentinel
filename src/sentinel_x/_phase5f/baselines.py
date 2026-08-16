"""Private deterministic Phase-5F benchmark baselines.

These baselines consume the frozen Phase-5F visible boundary without importing hidden
benchmark gold.  B0 is intentionally naive, B1 is a small factual graph/time heuristic,
and B1S projects the existing frozen propagation synthesis into the common benchmark
labels without strengthening its epistemic claims.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import cast

from sentinel_x.dependency.synthesis import (
    PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
)

from .visible import CaseSource, project_case_evidence

_CLASSIFICATIONS = frozenset(
    {
        "EFFECT_OBSERVED",
        "BOUNDED_NEGATIVE",
        "COUNTEREVIDENCE",
        "INSUFFICIENT",
        "AMBIGUOUS",
    }
)
_ANOMALY_STATES = frozenset({"inactive", "failed"})
_REQUIREMENT_RELATIONS = frozenset({"requires", "wants"})


def run_b0_state_rule(source: CaseSource) -> dict[str, object]:
    """Run the preregistered naive dependent-state baseline on MINIMAL evidence.

    B0 deliberately ignores topology, timing, boot provenance, and coverage.  Mapping an
    all-healthy observation set to ``BOUNDED_NEGATIVE`` is therefore a benchmark label
    mapping, not a claim that B0 established bounded sampling coverage.
    """

    minimal = project_case_evidence(source, "minimal")
    scope = _single_item(minimal, "scope", required=False)
    if scope is None:
        return _result(
            "INSUFFICIENT",
            (),
            unresolved=("missing_case_scope",),
        )
    target_unit = _required_text(_payload(scope), "target_unit")

    observations = _items(minimal, "observation")
    used_refs: set[str] = {_item_ref(scope)}

    statuses: list[str] = []
    for item in observations:
        payload = _payload(item)
        unit = _required_text(payload, "unit")
        status = _required_text(payload, "status")
        if target_unit is not None and unit != target_unit:
            continue
        if status not in {"healthy", "inactive", "failed", "unassessed"}:
            raise ValueError(f"unsupported observation status {status!r}")
        statuses.append(status)
        used_refs.add(_item_ref(item))

    if any(status in _ANOMALY_STATES for status in statuses):
        return _result("EFFECT_OBSERVED", used_refs)
    if statuses and all(status == "healthy" for status in statuses):
        return _result(
            "BOUNDED_NEGATIVE",
            used_refs,
            unresolved=("b0_does_not_evaluate_sampling_coverage",),
        )
    return _result(
        "INSUFFICIENT",
        used_refs,
        unresolved=("no_usable_dependent_state",),
    )


def run_b1_graph_time(source: CaseSource) -> dict[str, object]:
    """Run the preregistered small Graph+Time heuristic on MINIMAL evidence."""

    minimal = project_case_evidence(source, "minimal")
    scope = _single_item(minimal, "scope", required=False)
    if scope is None:
        return _result(
            "INSUFFICIENT",
            (),
            unresolved=("missing_case_scope",),
        )

    scope_ref = _item_ref(scope)
    scope_payload = _payload(scope)
    target_unit = _required_text(scope_payload, "target_unit")
    scope_boot = _required_text(scope_payload, "boot_id")
    analysis_window_usec = _required_positive_int(
        scope_payload,
        "analysis_window_usec",
    )
    explicit_source = _optional_text_field(scope_payload, "source_unit")

    environment = _mapping(minimal, "environment")
    environment_boot = environment.get("boot_id")
    if environment_boot is not None:
        if not isinstance(environment_boot, str):
            raise ValueError("environment.boot_id must be text when present")
        if environment_boot != scope_boot:
            return _result(
                "INSUFFICIENT",
                (scope_ref,),
                unresolved=("scope_boot_mismatch",),
            )

    topology = _valid_boot_items(minimal, "topology_relation", scope_boot)
    timeline = _valid_boot_items(minimal, "incident_timeline", scope_boot)
    coverage = _valid_boot_items(minimal, "coverage", scope_boot)

    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in topology:
        payload = _payload(item)
        subject = _required_text(payload, "subject_unit")
        object_unit = _required_text(payload, "object_unit")
        relation = _required_text(payload, "relation")
        if relation not in _REQUIREMENT_RELATIONS:
            continue
        graph[object_unit].append((subject, _item_ref(item)))

    source_transitions: list[tuple[str, int, str]] = []
    target_anomalies: list[tuple[int, str]] = []
    for item in timeline:
        payload = _payload(item)
        unit = _required_text(payload, "unit")
        state = _required_text(payload, "state")
        when = _required_nonnegative_int(payload, "transition_monotonic_usec")
        ref = _item_ref(item)
        if state not in {"healthy", "inactive", "failed", "unassessed"}:
            raise ValueError(f"unsupported incident_timeline state {state!r}")
        if unit == target_unit and state in _ANOMALY_STATES:
            target_anomalies.append((when, ref))
        if state in _ANOMALY_STATES and unit != target_unit:
            source_transitions.append((unit, when, ref))

    candidates: list[tuple[str, int, str, tuple[str, ...]]] = []
    for source_unit, source_time, transition_ref in source_transitions:
        if explicit_source is not None and source_unit != explicit_source:
            continue
        path_refs = _find_requirement_path(graph, source_unit, target_unit)
        if path_refs is None:
            continue
        candidates.append((source_unit, source_time, transition_ref, path_refs))

    if explicit_source is not None and not candidates:
        return _result(
            "INSUFFICIENT",
            (scope_ref,),
            unresolved=("no_compatible_candidate_or_source_transition",),
        )
    if explicit_source is None and not candidates:
        return _result(
            "INSUFFICIENT",
            (scope_ref,),
            unresolved=("no_compatible_candidate_scope",),
        )

    if target_anomalies:
        target_time, target_ref = min(target_anomalies, key=lambda item: item[0])
        forward: list[tuple[str, int, str, tuple[str, ...]]] = []
        reverse: list[tuple[str, int, str, tuple[str, ...]]] = []
        for candidate in candidates:
            _, source_time, _, _ = candidate
            delta = target_time - source_time
            if 0 < delta <= analysis_window_usec:
                forward.append(candidate)
            elif delta < 0:
                reverse.append(candidate)

        if len({candidate[0] for candidate in forward}) > 1:
            refs = _candidate_refs(scope_ref, target_ref, forward)
            return _result(
                "AMBIGUOUS",
                refs,
                unresolved=("multiple_time_compatible_sources",),
            )
        if forward:
            refs = _candidate_refs(scope_ref, target_ref, forward)
            return _result("EFFECT_OBSERVED", refs)
        if reverse:
            refs = _candidate_refs(scope_ref, target_ref, reverse)
            return _result("COUNTEREVIDENCE", refs)
        return _result(
            "INSUFFICIENT",
            (scope_ref, target_ref),
            unresolved=("target_anomaly_not_time_compatible",),
        )

    distinct_sources = {candidate[0] for candidate in candidates}
    if explicit_source is None and len(distinct_sources) > 1:
        refs = _candidate_refs(scope_ref, None, candidates)
        return _result(
            "AMBIGUOUS",
            refs,
            unresolved=("multiple_structurally_plausible_sources",),
        )

    candidate = min(candidates, key=lambda item: item[1])
    _, source_time, transition_ref, path_refs = candidate
    expected_end = source_time + analysis_window_usec

    sufficient_coverage: list[str] = []
    inspected_coverage: list[str] = []
    for item in coverage:
        payload = _payload(item)
        if _required_text(payload, "unit") != target_unit:
            continue
        ref = _item_ref(item)
        inspected_coverage.append(ref)
        if _coverage_is_bounded_healthy(
            payload,
            source_time=source_time,
            expected_end=expected_end,
        ):
            sufficient_coverage.append(ref)

    base_refs = {scope_ref, transition_ref, *path_refs, *inspected_coverage}
    if sufficient_coverage:
        return _result(
            "BOUNDED_NEGATIVE",
            {scope_ref, transition_ref, *path_refs, *sufficient_coverage},
        )
    return _result(
        "INSUFFICIENT",
        base_refs,
        unresolved=("insufficient_bounded_healthy_coverage",),
    )


def run_b1s_current_synthesis(source: CaseSource) -> dict[str, object]:
    """Project one existing frozen synthesis record into benchmark labels.

    Forward temporal consistency alone is intentionally not mapped to an observed
    effect. The projection only emits ``EFFECT_OBSERVED`` when the existing synthesis
    contains an affected-anomaly observation and no counter/negative conflict.
    """

    full = project_case_evidence(source, "full")
    syntheses = _items(full, "synthesis")
    if not syntheses:
        return _result(
            "INSUFFICIENT",
            (),
            unresolved=("no_current_synthesis_record",),
        )
    if len(syntheses) != 1:
        raise ValueError("B1S requires exactly one synthesis evidence item")

    item = syntheses[0]
    ref = _item_ref(item)
    payload = _payload(item)
    if payload.get("schema_version") != PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION:
        raise ValueError("B1S synthesis schema_version mismatch")

    affected = _required_nonnegative_int(payload, "affected_anomaly_observed_count")
    forward = _required_nonnegative_int(payload, "forward_temporal_consistency_count")
    counter = _required_nonnegative_int(payload, "directional_counterevidence_count")
    negative = _required_nonnegative_int(payload, "bounded_negative_observation_count")
    insufficient = _required_nonnegative_int(payload, "insufficient_evidence_count")
    supportive = _required_nonnegative_int(payload, "supportive_signal_count")
    conflict = payload.get("conflicting_evidence_present")
    if type(conflict) is not bool:
        raise ValueError("conflicting_evidence_present must be a boolean")
    if supportive != affected + forward:
        raise ValueError("supportive_signal_count does not match synthesis counts")
    expected_conflict = supportive > 0 and (counter > 0 or negative > 0)
    if conflict is not expected_conflict:
        raise ValueError("conflicting_evidence_present does not match synthesis counts")

    interpretation = _required_text(payload, "interpretation")
    expected_interpretation = _synthesis_interpretation(
        supportive=supportive,
        counter=counter,
        negative=negative,
        insufficient=insufficient,
    )
    if interpretation != expected_interpretation:
        raise ValueError("synthesis interpretation does not match synthesis counts")
    if payload.get("causal_claim") is not False:
        raise ValueError("B1S requires causal_claim=false")
    if payload.get("propagation_claim_assigned") is not False:
        raise ValueError("B1S requires propagation_claim_assigned=false")

    if conflict:
        return _result(
            "AMBIGUOUS",
            (ref,),
            unresolved=("current_synthesis_contains_conflicting_evidence",),
        )
    if counter > 0:
        return _result("COUNTEREVIDENCE", (ref,))
    if affected > 0:
        return _result("EFFECT_OBSERVED", (ref,))
    if negative > 0:
        return _result("BOUNDED_NEGATIVE", (ref,))
    if forward > 0:
        return _result(
            "INSUFFICIENT",
            (ref,),
            unresolved=("forward_temporal_consistency_without_observed_effect",),
        )
    if insufficient > 0:
        return _result(
            "INSUFFICIENT",
            (ref,),
            unresolved=("current_synthesis_insufficient_only",),
        )
    return _result(
        "INSUFFICIENT",
        (ref,),
        unresolved=("current_synthesis_contains_no_decisive_signal",),
    )


def _synthesis_interpretation(
    *,
    supportive: int,
    counter: int,
    negative: int,
    insufficient: int,
) -> str:
    if supportive == 0 and counter == 0 and negative == 0:
        return "insufficient_only" if insufficient > 0 else "no_evidence"
    if supportive > 0 and (counter > 0 or negative > 0):
        return "conflicting_evidence"
    if supportive > 0:
        return "supportive_without_counter_or_negative"
    if counter > 0 and negative > 0:
        return "counter_and_bounded_negative_without_support"
    if counter > 0:
        return "counterevidence_without_support_or_negative"
    return "bounded_negative_without_support_or_counter"


def _result(
    classification: str,
    evidence_refs: Iterable[str],
    *,
    unresolved: tuple[str, ...] = (),
) -> dict[str, object]:
    if classification not in _CLASSIFICATIONS:
        raise AssertionError("invalid internal baseline classification")
    canonical_refs = tuple(sorted(set(evidence_refs)))
    return {
        "classification": classification,
        "abstain": classification in {"INSUFFICIENT", "AMBIGUOUS"},
        "claims": [],
        "unresolved": list(unresolved),
        "evidence_refs": list(canonical_refs),
    }


def _items(bundle: Mapping[str, object], kind: str) -> list[dict[str, object]]:
    raw_evidence = bundle.get("evidence")
    if not isinstance(raw_evidence, list):
        raise ValueError("evidence bundle must contain a list named evidence")
    matched: list[dict[str, object]] = []
    for raw_item in raw_evidence:
        if not isinstance(raw_item, dict):
            raise ValueError("evidence bundle entries must be mappings")
        item = cast(dict[str, object], raw_item)
        if item.get("kind") == kind:
            matched.append(item)
    return matched


def _single_item(
    bundle: Mapping[str, object],
    kind: str,
    *,
    required: bool,
) -> dict[str, object] | None:
    items = _items(bundle, kind)
    if not items:
        if required:
            raise ValueError(f"expected one {kind} evidence item")
        return None
    if len(items) != 1:
        raise ValueError(f"expected at most one {kind} evidence item")
    return items[0]


def _valid_boot_items(
    bundle: Mapping[str, object],
    kind: str,
    scope_boot: str,
) -> list[dict[str, object]]:
    valid: list[dict[str, object]] = []
    for item in _items(bundle, kind):
        payload = _payload(item)
        boot_id = _required_text(payload, "boot_id")
        if boot_id == scope_boot:
            valid.append(item)
    return valid


def _payload(item: Mapping[str, object]) -> Mapping[str, object]:
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("evidence item payload must be a mapping")
    return cast(Mapping[str, object], payload)


def _item_ref(item: Mapping[str, object]) -> str:
    ref = item.get("ref")
    if not isinstance(ref, str):
        raise ValueError("evidence item ref must be text")
    return ref


def _mapping(bundle: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = bundle.get(field)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return cast(Mapping[str, object], value)


def _required_text(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")
    return value


def _optional_text_field(payload: Mapping[str, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text when present")
    return value


def _required_nonnegative_int(payload: Mapping[str, object], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _required_positive_int(payload: Mapping[str, object], field: str) -> int:
    value = _required_nonnegative_int(payload, field)
    if value == 0:
        raise ValueError(f"{field} must be positive")
    return value


def _find_requirement_path(
    graph: Mapping[str, list[tuple[str, str]]],
    source_unit: str,
    target_unit: str,
) -> tuple[str, ...] | None:
    if source_unit == target_unit:
        return ()
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(source_unit, ())])
    seen = {source_unit}
    while queue:
        unit, refs = queue.popleft()
        for dependent, edge_ref in graph.get(unit, []):
            if dependent in seen:
                continue
            next_refs = (*refs, edge_ref)
            if dependent == target_unit:
                return next_refs
            seen.add(dependent)
            queue.append((dependent, next_refs))
    return None


def _candidate_refs(
    scope_ref: str,
    target_ref: str | None,
    candidates: list[tuple[str, int, str, tuple[str, ...]]],
) -> set[str]:
    refs = {scope_ref}
    if target_ref is not None:
        refs.add(target_ref)
    for _, _, transition_ref, path_refs in candidates:
        refs.add(transition_ref)
        refs.update(path_refs)
    return refs


def _coverage_is_bounded_healthy(
    payload: Mapping[str, object],
    *,
    source_time: int,
    expected_end: int,
) -> bool:
    window_start = _required_nonnegative_int(payload, "window_start_usec")
    window_end = _required_nonnegative_int(payload, "window_end_usec")
    largest_gap = _required_nonnegative_int(payload, "largest_gap_usec")
    max_gap = _required_positive_int(payload, "max_sample_gap_usec")
    healthy = _required_nonnegative_int(payload, "healthy_count")
    inactive = _required_nonnegative_int(payload, "inactive_count")
    failed = _required_nonnegative_int(payload, "failed_count")
    unassessed = _required_nonnegative_int(payload, "unassessed_count")

    if window_end < window_start:
        raise ValueError("coverage window_end_usec precedes window_start_usec")
    return (
        window_start <= source_time
        and window_end >= expected_end
        and largest_gap <= max_gap
        and healthy > 0
        and inactive == 0
        and failed == 0
        and unassessed == 0
    )
