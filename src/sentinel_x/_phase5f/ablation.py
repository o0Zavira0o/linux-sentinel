"""Private deterministic Phase-5F mandatory ablation transforms."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final, cast

from ._common import validate_case_id, validate_evidence_ref, validate_kind

ABLATION_FIELD_MAPPING_SHA256: Final[str] = (
    "5a4a7188742788954c2d7b1d11085057ab56594327c8306305286d9ba1b41844"
)

_EVIDENCE_BUNDLE_SCHEMA_VERSION: Final[str] = "sentinel-x.phase5f-evidence-bundle.v1"

_MINIMAL_MINUS_TOPOLOGY: Final[str] = "minimal-minus-topology"
_MINIMAL_MINUS_COVERAGE: Final[str] = "minimal-minus-coverage"
_MINIMAL_MINUS_BOOT_PROVENANCE: Final[str] = "minimal-minus-boot-provenance"
_MINIMAL_MINUS_INTERVENTION: Final[str] = "minimal-minus-intervention-metadata"
_MINIMAL_MINUS_COUNTEREVIDENCE: Final[str] = "minimal-minus-counterevidence"
_MINIMAL_MINUS_TIMESTAMP: Final[str] = "minimal-minus-exact-timestamp-basis"
_FULL_MINUS_DERIVED: Final[str] = "full-minus-current-derived-synthesis-interpretation"

_ABLATION_MAPPING_IDS: Final[tuple[str, ...]] = (
    _MINIMAL_MINUS_TOPOLOGY,
    _MINIMAL_MINUS_COVERAGE,
    _MINIMAL_MINUS_BOOT_PROVENANCE,
    _MINIMAL_MINUS_INTERVENTION,
    _MINIMAL_MINUS_COUNTEREVIDENCE,
    _MINIMAL_MINUS_TIMESTAMP,
    _FULL_MINUS_DERIVED,
)

_BASE_CONDITION_BY_MAPPING: Final[dict[str, str]] = {
    _MINIMAL_MINUS_TOPOLOGY: "minimal",
    _MINIMAL_MINUS_COVERAGE: "minimal",
    _MINIMAL_MINUS_BOOT_PROVENANCE: "minimal",
    _MINIMAL_MINUS_INTERVENTION: "minimal",
    _MINIMAL_MINUS_COUNTEREVIDENCE: "minimal",
    _MINIMAL_MINUS_TIMESTAMP: "minimal",
    _FULL_MINUS_DERIVED: "full",
}

_TOPOLOGY_DROP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "candidate_id",
        "graph_version_id",
        "requirement_observed_at",
        "topology_id",
    }
)

_TIMESTAMP_DROP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "ended_monotonic_usec",
        "requirement_observed_at",
        "started_monotonic_usec",
        "transition_monotonic_usec",
        "window_end_usec",
        "window_start_usec",
    }
)

_DERIVED_DROP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "causal_claim",
        "continuous_health_claim_assigned",
        "evidence_class",
        "experiment_wide_single_variable_isolation_claim",
        "interpretation",
        "least_privilege_claim_assigned",
        "non_propagation_claim_assigned",
        "propagation_claim_assigned",
        "replication_claim_assigned",
        "root_cause_claim_assigned",
        "topology_temporal_applicability_claim",
        "treatment_effect_claim_assigned",
        "universal_systemd_behavior_claim",
    }
)

_FORBIDDEN_VISIBLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "gold_label",
        "gold_classification",
        "classification",
        "expected_classification",
        "expected_outcome",
        "reference_answer",
        "must_abstain",
        "maximum_allowed_causal_strength",
        "supporting_evidence_refs",
        "invalid_evidence_refs",
        "counterevidence_refs",
        "source_run_group",
    }
)


def ablation_mapping_ids() -> tuple[str, ...]:
    """Return the exact preregistered private mapping identifiers."""
    return _ABLATION_MAPPING_IDS


def ablation_mapping_base_condition(mapping_id: str) -> str:
    """Return the frozen base condition for one preregistered mapping."""
    try:
        return _BASE_CONDITION_BY_MAPPING[mapping_id]
    except KeyError as exc:
        raise ValueError("unknown Phase-5F ablation mapping_id") from exc


def apply_ablation_mapping(
    bundle: Mapping[str, object],
    mapping_id: str,
) -> dict[str, object]:
    """Apply one frozen deletion-only mapping to one visible projected bundle."""
    ablation_mapping_base_condition(mapping_id)
    output = _clone_and_validate_bundle(bundle)

    environment = cast(dict[str, object], output["environment"])
    evidence = cast(list[dict[str, object]], output["evidence"])

    if mapping_id == _MINIMAL_MINUS_TOPOLOGY:
        _drop_evidence_kinds(evidence, frozenset({"topology_relation"}))
        _drop_payload_keys(evidence, _TOPOLOGY_DROP_KEYS)
    elif mapping_id == _MINIMAL_MINUS_COVERAGE:
        _drop_evidence_kinds(evidence, frozenset({"coverage"}))
    elif mapping_id == _MINIMAL_MINUS_BOOT_PROVENANCE:
        environment.pop("boot_id", None)
        _drop_evidence_kinds(evidence, frozenset({"provenance"}))
        _drop_payload_keys(evidence, frozenset({"boot_id"}))
    elif mapping_id == _MINIMAL_MINUS_INTERVENTION:
        _drop_evidence_kinds(evidence, frozenset({"intervention_context"}))
    elif mapping_id == _MINIMAL_MINUS_COUNTEREVIDENCE:
        _drop_visible_reverse_target_timeline(environment, evidence)
    elif mapping_id == _MINIMAL_MINUS_TIMESTAMP:
        _drop_payload_keys(evidence, _TIMESTAMP_DROP_KEYS)
    elif mapping_id == _FULL_MINUS_DERIVED:
        _drop_evidence_kinds(evidence, frozenset({"synthesis"}))
        _drop_payload_keys(evidence, _DERIVED_DROP_KEYS)
    else:
        raise AssertionError("validated ablation mapping_id must be reachable")

    _validate_transformed_bundle(output)
    return output


def _clone_and_validate_bundle(bundle: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(bundle, Mapping):
        raise ValueError("bundle must be a mapping")

    cloned = _clone_json(bundle)
    if not isinstance(cloned, dict):
        raise AssertionError("cloned visible bundle must remain a mapping")

    _validate_transformed_bundle(cloned)
    return cloned


def _validate_transformed_bundle(bundle: dict[str, object]) -> None:
    expected_outer = {
        "schema_version",
        "case_id",
        "task",
        "environment",
        "evidence",
    }
    if set(bundle) != expected_outer:
        raise ValueError("visible bundle outer schema drift")
    if bundle["schema_version"] != _EVIDENCE_BUNDLE_SCHEMA_VERSION:
        raise ValueError("visible bundle schema_version drift")

    validate_case_id(bundle["case_id"])
    task = bundle["task"]
    if not isinstance(task, str) or not task:
        raise ValueError("visible bundle task must be non-empty text")

    environment = bundle["environment"]
    if not isinstance(environment, dict):
        raise ValueError("visible bundle environment must be an object")

    evidence = bundle["evidence"]
    if not isinstance(evidence, list):
        raise ValueError("visible bundle evidence must be a list")
    if not evidence:
        raise ValueError("ablated visible bundle must retain evidence")

    refs: set[str] = set()
    for index, raw_item in enumerate(evidence):
        if not isinstance(raw_item, dict):
            raise ValueError(f"visible evidence[{index}] must be an object")
        if set(raw_item) != {"ref", "kind", "payload"}:
            raise ValueError(f"visible evidence[{index}] schema drift")
        ref = validate_evidence_ref(raw_item["ref"])
        if ref in refs:
            raise ValueError("visible evidence references must remain unique")
        refs.add(ref)
        validate_kind(raw_item["kind"])
        if not isinstance(raw_item["payload"], dict):
            raise ValueError(f"visible evidence[{index}].payload must be an object")

    _reject_hidden_gold_keys(bundle, path="bundle")
    try:
        json.dumps(
            bundle,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("visible bundle must remain plain finite JSON") from exc


def _clone_json(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Mapping):
        cloned: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("visible JSON mapping keys must be text")
            cloned[key] = _clone_json(child)
        return cloned
    if isinstance(value, list):
        return [_clone_json(child) for child in value]
    if isinstance(value, tuple):
        return [_clone_json(child) for child in value]
    raise ValueError(f"visible bundle contains non-JSON value {type(value).__name__}")


def _reject_hidden_gold_keys(value: object, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN_VISIBLE_KEYS:
                raise ValueError(f"{path} contains hidden-gold key {key!r}")
            _reject_hidden_gold_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_hidden_gold_keys(child, path=f"{path}[{index}]")


def _drop_evidence_kinds(
    evidence: list[dict[str, object]],
    kinds: frozenset[str],
) -> None:
    evidence[:] = [item for item in evidence if item["kind"] not in kinds]


def _drop_payload_keys(
    evidence: list[dict[str, object]],
    keys: frozenset[str],
) -> None:
    for item in evidence:
        _drop_recursive_keys(item["payload"], keys)


def _drop_recursive_keys(value: object, keys: frozenset[str]) -> None:
    if isinstance(value, dict):
        for key in tuple(value):
            if key in keys:
                del value[key]
                continue
            _drop_recursive_keys(value[key], keys)
    elif isinstance(value, list):
        for child in value:
            _drop_recursive_keys(child, keys)


def _drop_visible_reverse_target_timeline(
    environment: dict[str, object],
    evidence: list[dict[str, object]],
) -> None:
    scopes = [item for item in evidence if item["kind"] == "scope"]
    if len(scopes) != 1:
        raise ValueError("counterevidence ablation requires exactly one scope item")

    scope = cast(dict[str, object], scopes[0]["payload"])
    source_unit = scope.get("source_unit")
    target_unit = scope.get("target_unit")
    if source_unit is None or target_unit is None:
        return
    if not isinstance(source_unit, str) or not isinstance(target_unit, str):
        raise ValueError("scope source_unit and target_unit must be text when present")

    source_matches = _matching_timelines(evidence, source_unit)
    target_matches = _matching_timelines(evidence, target_unit)
    if len(source_matches) > 1 or len(target_matches) > 1:
        raise ValueError("counterevidence ablation found multiple matching timelines")
    if not source_matches or not target_matches:
        return

    source_item = source_matches[0]
    target_item = target_matches[0]
    source_payload = cast(dict[str, object], source_item["payload"])
    target_payload = cast(dict[str, object], target_item["payload"])

    environment_boot = environment.get("boot_id")
    source_boot = source_payload.get("boot_id")
    target_boot = target_payload.get("boot_id")
    boot_values = (environment_boot, source_boot, target_boot)
    if not all(isinstance(value, str) for value in boot_values):
        return
    if not (environment_boot == source_boot == target_boot):
        return

    source_time = source_payload.get("transition_monotonic_usec")
    target_time = target_payload.get("transition_monotonic_usec")
    if type(source_time) is not int or type(target_time) is not int:
        raise ValueError("matched timeline transition_monotonic_usec must be int")

    if target_time < source_time:
        evidence.remove(target_item)


def _matching_timelines(
    evidence: list[dict[str, object]],
    unit: str,
) -> list[dict[str, object]]:
    matched: list[dict[str, object]] = []
    for item in evidence:
        if item["kind"] != "incident_timeline":
            continue
        payload = cast(dict[str, object], item["payload"])
        if payload.get("unit") == unit:
            matched.append(item)
    return matched
