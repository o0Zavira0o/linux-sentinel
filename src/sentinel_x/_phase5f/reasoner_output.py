"""Private Phase-5F common reasoner final-output boundary.

The frozen evaluation protocol scores only structured final output.  This module owns
strict JSON parsing and shape validation for that output without importing hidden gold,
scoring policy, provider integrations, or chain-of-thought.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import NoReturn

from ._common import validate_evidence_ref

_CLASSIFICATIONS = frozenset(
    {
        "EFFECT_OBSERVED",
        "BOUNDED_NEGATIVE",
        "COUNTEREVIDENCE",
        "INSUFFICIENT",
        "AMBIGUOUS",
    }
)
_CAUSAL_STRENGTHS = frozenset(
    {
        "none",
        "association",
        "hypothesis",
        "established_cause",
    }
)
_OUTPUT_KEYS = frozenset(
    {
        "classification",
        "abstain",
        "claims",
        "unresolved",
        "evidence_refs",
    }
)
_CLAIM_KEYS = frozenset(
    {
        "claim_kind",
        "causal_strength",
        "evidence_refs",
        "text",
    }
)


def parse_reasoner_output(raw: str) -> dict[str, object]:
    """Parse one bare JSON reasoner response and validate the frozen 5F.4 shape.

    Markdown fences, prose around JSON, duplicate object keys, and non-standard JSON
    constants are rejected rather than repaired.  The returned value contains only the
    preregistered final-output fields.
    """

    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("reasoner output must be non-empty JSON text")
    try:
        parsed: object = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("reasoner output must be one bare JSON object") from exc
    return validate_reasoner_output(parsed)


def validate_reasoner_output(value: object) -> dict[str, object]:
    """Validate one already-decoded common Phase-5F final output.

    This validator deliberately does not infer abstention from classification, require
    evidence for a claim, or check references against hidden gold.  Those properties are
    independently scored later and must remain observable rather than being repaired or
    collapsed into parse failure.
    """

    output = _exact_object(value, expected_keys=_OUTPUT_KEYS, field_name="output")

    classification = output["classification"]
    if not isinstance(classification, str) or classification not in _CLASSIFICATIONS:
        raise ValueError("classification is not a Phase-5F benchmark label")

    abstain = output["abstain"]
    if type(abstain) is not bool:
        raise ValueError("abstain must be a boolean")

    claims_value = output["claims"]
    if not isinstance(claims_value, list):
        raise ValueError("claims must be a list")
    claims = [
        _validate_claim(claim, index=index) for index, claim in enumerate(claims_value)
    ]

    unresolved = _text_list(output["unresolved"], field_name="unresolved")
    evidence_refs = _evidence_ref_list(
        output["evidence_refs"],
        field_name="evidence_refs",
    )

    return {
        "classification": classification,
        "abstain": abstain,
        "claims": claims,
        "unresolved": unresolved,
        "evidence_refs": evidence_refs,
    }


def _validate_claim(value: object, *, index: int) -> dict[str, object]:
    field_name = f"claims[{index}]"
    claim = _exact_object(value, expected_keys=_CLAIM_KEYS, field_name=field_name)

    claim_kind = _nonempty_text(
        claim["claim_kind"],
        field_name=f"{field_name}.claim_kind",
    )

    causal_strength = claim["causal_strength"]
    if not isinstance(causal_strength, str) or causal_strength not in _CAUSAL_STRENGTHS:
        raise ValueError(f"{field_name}.causal_strength is invalid")

    evidence_refs = _evidence_ref_list(
        claim["evidence_refs"],
        field_name=f"{field_name}.evidence_refs",
    )
    text = _nonempty_text(claim["text"], field_name=f"{field_name}.text")

    return {
        "claim_kind": claim_kind,
        "causal_strength": causal_strength,
        "evidence_refs": evidence_refs,
        "text": text,
    }


def _exact_object(
    value: object,
    *,
    expected_keys: frozenset[str],
    field_name: str,
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} keys must be text")
    actual_keys = frozenset(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(
            f"{field_name} keys mismatch; missing={missing!r} extra={extra!r}"
        )
    return value


def _text_list(value: object, *, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [
        _nonempty_text(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(value)
    ]


def _evidence_ref_list(value: object, *, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    refs = [validate_evidence_ref(item) for item in value]
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field_name} must not contain duplicate references")
    return refs


def _nonempty_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant {value!r} is not allowed")
