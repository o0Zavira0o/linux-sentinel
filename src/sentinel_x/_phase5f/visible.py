"""Reasoner-visible Phase-5F case projection boundary.

The module deliberately has no import from ``gold``.  A visible case is assembled
once and projected into RAW, MINIMAL, or FULL without serializing the condition
name into the bundle.  Hidden scoring truth is not accepted by this boundary.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from ._common import (
    MAX_CASE_TASK_CHARS,
    validate_bounded_text,
    validate_case_id,
    validate_evidence_ref,
    validate_kind,
)

_EVIDENCE_BUNDLE_SCHEMA_VERSION = "sentinel-x.phase5f-evidence-bundle.v1"
_VIEW_NAMES = ("raw", "minimal", "full")
_MINIMAL_KINDS = frozenset(
    {
        "scope",
        "observation",
        "topology_relation",
        "incident_timeline",
        "coverage",
        "missingness",
        "provenance",
        "intervention_context",
    }
)
_FORBIDDEN_VISIBLE_KEYS = frozenset(
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
_MAX_EVIDENCE_ITEMS = 4096
_MAX_JSON_DEPTH = 32
_MAX_CONTAINER_ITEMS = 8192
_MAX_TEXT_CHARS = 262_144
_MAX_PROJECTED_JSON_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CaseSource:
    """One gold-free visible case source shared by all evidence conditions.

    ``evidence_items`` entries use stable opaque references and may carry one or
    more condition-specific payloads under ``raw``, ``minimal``, and ``full``.
    All three projections therefore originate from the same immutable case object.
    """

    case_id: str
    task: str
    environment: Mapping[str, object]
    evidence_items: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        validate_case_id(self.case_id)
        validate_bounded_text(
            self.task,
            field_name="task",
            max_chars=MAX_CASE_TASK_CHARS,
        )
        if not isinstance(self.environment, Mapping):
            raise ValueError("environment must be a mapping")
        if not isinstance(self.evidence_items, tuple):
            raise ValueError("evidence_items must be a tuple")
        if not self.evidence_items:
            raise ValueError("evidence_items must not be empty")
        if len(self.evidence_items) > _MAX_EVIDENCE_ITEMS:
            raise ValueError(f"evidence_items exceeds {_MAX_EVIDENCE_ITEMS} entries")

        frozen_environment = _freeze_json_mapping(
            self.environment,
            path="environment",
        )
        _reject_hidden_gold_keys(frozen_environment, path="environment")

        frozen_items = tuple(
            _freeze_evidence_item(item, index=index)
            for index, item in enumerate(self.evidence_items)
        )
        frozen_items = tuple(sorted(frozen_items, key=_item_ref))

        refs = tuple(_item_ref(item) for item in frozen_items)
        if len(set(refs)) != len(refs):
            raise ValueError("evidence_items must have unique references")

        for view_name in _VIEW_NAMES:
            if not any(view_name in item for item in frozen_items):
                raise ValueError(
                    f"evidence_items must provide at least one {view_name} payload"
                )

        object.__setattr__(self, "environment", frozen_environment)
        object.__setattr__(self, "evidence_items", frozen_items)

        for view_name in _VIEW_NAMES:
            _build_projection(self, view_name, enforce_size=True)


def project_case_evidence(source: CaseSource, condition: str) -> dict[str, object]:
    """Project one blinded evidence bundle without exposing the condition label."""

    if not isinstance(source, CaseSource):
        raise ValueError("source must be a CaseSource")
    if condition not in _VIEW_NAMES:
        raise ValueError("condition must be one of: raw, minimal, full")
    return _build_projection(source, condition, enforce_size=True)


def visible_evidence_refs(source: CaseSource, condition: str) -> frozenset[str]:
    """Return the opaque evidence references actually visible in one projection."""

    projection = project_case_evidence(source, condition)
    evidence = cast(list[dict[str, object]], projection["evidence"])
    refs: set[str] = set()
    for item in evidence:
        ref = item["ref"]
        if not isinstance(ref, str):
            raise AssertionError("projected evidence reference must be text")
        refs.add(ref)
    return frozenset(refs)


def _freeze_evidence_item(
    item: Mapping[str, object],
    *,
    index: int,
) -> Mapping[str, object]:
    if not isinstance(item, Mapping):
        raise ValueError(f"evidence_items[{index}] must be a mapping")

    raw_mapping = cast(Mapping[object, object], item)
    keys: set[str] = set()
    for key in raw_mapping:
        if not isinstance(key, str):
            raise ValueError(f"evidence_items[{index}] keys must be text")
        keys.add(key)

    allowed_keys = {"ref", "kind", *_VIEW_NAMES}
    if not {"ref", "kind"}.issubset(keys):
        raise ValueError(f"evidence_items[{index}] requires ref and kind")
    unknown = keys - allowed_keys
    if unknown:
        raise ValueError(
            f"evidence_items[{index}] has unsupported keys: {sorted(unknown)}"
        )
    if not any(view_name in keys for view_name in _VIEW_NAMES):
        raise ValueError(
            f"evidence_items[{index}] must contain raw, minimal, or full payload"
        )

    ref = validate_evidence_ref(raw_mapping["ref"])
    kind = validate_kind(raw_mapping["kind"])
    if "minimal" in keys and kind not in _MINIMAL_KINDS:
        raise ValueError(
            f"minimal payload kind {kind!r} is not a preregistered factual category"
        )

    frozen: dict[str, object] = {"ref": ref, "kind": kind}
    for view_name in _VIEW_NAMES:
        if view_name not in keys:
            continue
        payload = _freeze_json(
            raw_mapping[view_name],
            path=f"evidence_items[{index}].{view_name}",
            depth=0,
        )
        _reject_hidden_gold_keys(
            payload,
            path=f"evidence_items[{index}].{view_name}",
        )
        frozen[view_name] = payload
    return MappingProxyType(frozen)


def _freeze_json_mapping(
    value: Mapping[str, object],
    *,
    path: str,
) -> Mapping[str, object]:
    frozen = _freeze_json(value, path=path, depth=0)
    if not isinstance(frozen, Mapping):
        raise AssertionError("mapping freezer must return a mapping")
    return cast(Mapping[str, object], frozen)


def _freeze_json(value: object, *, path: str, depth: int) -> object:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{path} exceeds maximum JSON nesting depth")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_TEXT_CHARS:
            raise ValueError(f"{path} contains oversized text")
        if "\x00" in value:
            raise ValueError(f"{path} contains NUL")
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if len(mapping) > _MAX_CONTAINER_ITEMS:
            raise ValueError(f"{path} contains too many mapping entries")
        frozen_mapping: dict[str, object] = {}
        for key, child in mapping.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} mapping keys must be text")
            frozen_mapping[key] = _freeze_json(
                child,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return MappingProxyType(frozen_mapping)
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        if len(sequence) > _MAX_CONTAINER_ITEMS:
            raise ValueError(f"{path} contains too many sequence entries")
        return tuple(
            _freeze_json(
                child,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            for index, child in enumerate(sequence)
        )
    raise ValueError(f"{path} contains non-JSON value {type(value).__name__}")


def _reject_hidden_gold_keys(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, child in mapping.items():
            if not isinstance(key, str):
                raise AssertionError("frozen JSON mapping keys must be text")
            if key in _FORBIDDEN_VISIBLE_KEYS:
                raise ValueError(f"{path} contains hidden-gold key {key!r}")
            _reject_hidden_gold_keys(child, path=f"{path}.{key}")
    elif isinstance(value, tuple):
        for index, child in enumerate(value):
            _reject_hidden_gold_keys(child, path=f"{path}[{index}]")


def _build_projection(
    source: CaseSource,
    condition: str,
    *,
    enforce_size: bool,
) -> dict[str, object]:
    evidence: list[dict[str, object]] = []
    for item in source.evidence_items:
        if condition not in item:
            continue
        evidence.append(
            {
                "ref": _item_ref(item),
                "kind": item["kind"],
                "payload": _thaw_json(item[condition]),
            }
        )

    projection: dict[str, object] = {
        "schema_version": _EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "case_id": source.case_id,
        "task": source.task,
        "environment": _thaw_json(source.environment),
        "evidence": evidence,
    }
    if enforce_size:
        encoded = json.dumps(
            projection,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > _MAX_PROJECTED_JSON_BYTES:
            raise ValueError(
                f"{condition} projection exceeds {_MAX_PROJECTED_JSON_BYTES} bytes"
            )
    return projection


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        thawed: dict[str, object] = {}
        for key, child in mapping.items():
            if not isinstance(key, str):
                raise AssertionError("frozen JSON mapping keys must be text")
            thawed[key] = _thaw_json(child)
        return thawed
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _item_ref(item: Mapping[str, object]) -> str:
    ref = item["ref"]
    if not isinstance(ref, str):
        raise AssertionError("frozen evidence ref must be text")
    return ref
