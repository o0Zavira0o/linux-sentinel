"""Private Phase-5F.6 blinded execution-manifest and preflight boundary.

This module is provider-neutral and deliberately performs no provider call, scoring,
or hidden-gold access.  It freezes the score-bearing execution controls that must be
identical across B2/B3/B4 before any reasoner output exists: prompt/schema identity,
model/version/configuration identity, fresh stateless requests, retry policy, exact
36 x 3 x 3 planning, condition blinding, and explicit no-truncation token capacity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Final, cast

from ._common import MAX_CASE_TASK_CHARS, validate_bounded_text, validate_case_id

_EXECUTION_MANIFEST_SCHEMA: Final[str] = "sentinel-x.phase5f-execution-manifest.v1"
_EXECUTION_PLAN_SCHEMA: Final[str] = "sentinel-x.phase5f-execution-plan.v1"
_EVIDENCE_BUNDLE_SCHEMA: Final[str] = "sentinel-x.phase5f-evidence-bundle.v1"
_CONDITIONS: Final[tuple[str, ...]] = ("raw", "minimal", "full")
_REPEATS: Final[tuple[int, ...]] = (1, 2, 3)
_CASE_IDS: Final[tuple[str, ...]] = tuple(f"CASE-{index:04d}" for index in range(1, 37))
_CASE_ID_SET = frozenset(_CASE_IDS)
_EXECUTION_ORDER_SEED: Final[str] = "sentinel-x.phase5f6-execution-order.v1"
TRANSPORT_RETRY_LIMIT: Final[int] = 1

FROZEN_CORPUS_ARCHIVE_SHA256: Final[str] = (
    "a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18"
)
FROZEN_REASONER_OUTPUT_SHA256: Final[str] = (
    "86a145297726a41bc05cd08d64a6edaae359c4b2edd34597fac217dfdb54bf4c"
)
FROZEN_SCORER_SHA256: Final[str] = (
    "8ef2514805bc3c7b788a7f13f24e8183c8a3ddb3c472015797e8721368b3b22e"
)
FROZEN_FULL_REFERENCE_SHA: Final[str] = "1733485fdce630e4a3c32731c7dcbc62cbdebefb"
FROZEN_PREREGISTRATION_CHECKPOINT_SHA: Final[str] = (
    "f47b5cb764030ea8c78d53ff76ebe0859bb4ca29"
)

_OUTPUT_SCHEMA_TEXT: Final[
    str
] = """Return exactly one bare JSON object with these fields and no extras:
{
  "classification": "EFFECT_OBSERVED | BOUNDED_NEGATIVE | COUNTEREVIDENCE | INSUFFICIENT | AMBIGUOUS",
  "abstain": false,
  "claims": [
    {
      "claim_kind": "non-empty text",
      "causal_strength": "none | association | hypothesis | established_cause",
      "evidence_refs": ["REF-####"],
      "text": "non-empty text"
    }
  ],
  "unresolved": ["non-empty text"],
  "evidence_refs": ["REF-####"]
}
Use JSON boolean true/false. Evidence references must be copied exactly when cited.
Do not include markdown fences, commentary outside the JSON object, or chain-of-thought."""

_SYSTEM_PROMPT: Final[
    str
] = """Analyze one blinded operational evidence bundle and answer the task contained in it.
Use only the supplied evidence bundle. Do not use external tools, web access,
repository context, prior conversation, hidden labels, expected outcomes, or previous
runs. Preserve uncertainty, missingness, and counterevidence. Do not invent facts that
are absent from the bundle. Return only the required structured final output."""

_EVIDENCE_PLACEHOLDER: Final[str] = "{{EVIDENCE_BUNDLE_JSON}}"
_USER_PROMPT_TEMPLATE: Final[str] = (
    "Evidence bundle:\n"
    + _EVIDENCE_PLACEHOLDER
    + "\n\nRequired structured final output:\n"
    + _OUTPUT_SCHEMA_TEXT
)

PROMPT_TEMPLATE_SHA256: Final[str] = hashlib.sha256(
    (_SYSTEM_PROMPT + "\n---\n" + _USER_PROMPT_TEMPLATE).encode("utf-8")
).hexdigest()
OUTPUT_SCHEMA_SHA256: Final[str] = hashlib.sha256(
    _OUTPUT_SCHEMA_TEXT.encode("utf-8")
).hexdigest()

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "provider",
        "model_identifier",
        "model_version",
        "model_configuration",
        "tokenizer_identifier",
        "context_window_tokens",
        "max_output_tokens",
        "token_safety_margin",
        "transport_retry_limit",
        "fresh_context",
        "tools_enabled",
        "web_enabled",
        "prompt_template_sha256",
        "output_schema_sha256",
        "frozen_reasoner_output_sha256",
        "frozen_scorer_sha256",
        "frozen_corpus_archive_sha256",
        "frozen_full_reference_sha",
        "frozen_preregistration_checkpoint_sha",
    }
)
_BUNDLE_KEYS = frozenset(
    {"schema_version", "case_id", "task", "environment", "evidence"}
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
_MAX_IDENTITY_CHARS = 512
_MAX_CONFIGURATION_BYTES = 64 * 1024
_MAX_BUNDLE_BYTES = 4 * 1024 * 1024


def validate_execution_manifest(value: Mapping[str, object]) -> dict[str, object]:
    """Validate one provider-neutral, pre-score execution manifest.

    The manifest deliberately contains no API secret and does not select a provider on
    behalf of the operator.  A concrete manifest instance must name the exact provider,
    model identifier/version, configuration, tokenizer, and context limits before any
    score-bearing request is sent.
    """
    manifest = _exact_mapping(
        value, expected_keys=_MANIFEST_KEYS, field_name="manifest"
    )

    schema_version = _text(manifest["schema_version"], field_name="schema_version")
    if schema_version != _EXECUTION_MANIFEST_SCHEMA:
        raise ValueError("unexpected execution manifest schema_version")

    provider = _identity(manifest["provider"], field_name="provider")
    model_identifier = _identity(
        manifest["model_identifier"], field_name="model_identifier"
    )
    model_version = _identity(manifest["model_version"], field_name="model_version")
    tokenizer_identifier = _identity(
        manifest["tokenizer_identifier"], field_name="tokenizer_identifier"
    )
    configuration = _json_mapping_copy(
        manifest["model_configuration"], field_name="model_configuration"
    )
    if not configuration:
        raise ValueError("model_configuration must explicitly pin provider settings")
    configuration_bytes = _canonical_json_bytes(configuration)
    if len(configuration_bytes) > _MAX_CONFIGURATION_BYTES:
        raise ValueError("model_configuration exceeds bounded manifest size")

    context_window_tokens = _positive_int(
        manifest["context_window_tokens"], field_name="context_window_tokens"
    )
    max_output_tokens = _positive_int(
        manifest["max_output_tokens"], field_name="max_output_tokens"
    )
    token_safety_margin = _positive_int(
        manifest["token_safety_margin"], field_name="token_safety_margin"
    )
    if max_output_tokens + token_safety_margin >= context_window_tokens:
        raise ValueError("output budget plus safety margin must fit context window")

    retry_limit = _nonnegative_int(
        manifest["transport_retry_limit"], field_name="transport_retry_limit"
    )
    if retry_limit != TRANSPORT_RETRY_LIMIT:
        raise ValueError(
            "transport_retry_limit must equal frozen pre-score value "
            f"{TRANSPORT_RETRY_LIMIT}"
        )
    _required_bool(manifest["fresh_context"], field_name="fresh_context", expected=True)
    _required_bool(
        manifest["tools_enabled"], field_name="tools_enabled", expected=False
    )
    _required_bool(manifest["web_enabled"], field_name="web_enabled", expected=False)

    _required_digest(
        manifest["prompt_template_sha256"],
        field_name="prompt_template_sha256",
        expected=PROMPT_TEMPLATE_SHA256,
    )
    _required_digest(
        manifest["output_schema_sha256"],
        field_name="output_schema_sha256",
        expected=OUTPUT_SCHEMA_SHA256,
    )
    _required_digest(
        manifest["frozen_reasoner_output_sha256"],
        field_name="frozen_reasoner_output_sha256",
        expected=FROZEN_REASONER_OUTPUT_SHA256,
    )
    _required_digest(
        manifest["frozen_scorer_sha256"],
        field_name="frozen_scorer_sha256",
        expected=FROZEN_SCORER_SHA256,
    )
    _required_digest(
        manifest["frozen_corpus_archive_sha256"],
        field_name="frozen_corpus_archive_sha256",
        expected=FROZEN_CORPUS_ARCHIVE_SHA256,
    )
    frozen_full_reference = _identity(
        manifest["frozen_full_reference_sha"], field_name="frozen_full_reference_sha"
    )
    if frozen_full_reference != FROZEN_FULL_REFERENCE_SHA:
        raise ValueError("frozen_full_reference_sha does not match 5E.4 reference")
    preregistration_checkpoint = _identity(
        manifest["frozen_preregistration_checkpoint_sha"],
        field_name="frozen_preregistration_checkpoint_sha",
    )
    if preregistration_checkpoint != FROZEN_PREREGISTRATION_CHECKPOINT_SHA:
        raise ValueError(
            "frozen_preregistration_checkpoint_sha does not match frozen 5F.0"
        )

    return {
        "schema_version": schema_version,
        "provider": provider,
        "model_identifier": model_identifier,
        "model_version": model_version,
        "model_configuration": configuration,
        "tokenizer_identifier": tokenizer_identifier,
        "context_window_tokens": context_window_tokens,
        "max_output_tokens": max_output_tokens,
        "token_safety_margin": token_safety_margin,
        "transport_retry_limit": retry_limit,
        "fresh_context": True,
        "tools_enabled": False,
        "web_enabled": False,
        "prompt_template_sha256": PROMPT_TEMPLATE_SHA256,
        "output_schema_sha256": OUTPUT_SCHEMA_SHA256,
        "frozen_reasoner_output_sha256": FROZEN_REASONER_OUTPUT_SHA256,
        "frozen_scorer_sha256": FROZEN_SCORER_SHA256,
        "frozen_corpus_archive_sha256": FROZEN_CORPUS_ARCHIVE_SHA256,
        "frozen_full_reference_sha": FROZEN_FULL_REFERENCE_SHA,
        "frozen_preregistration_checkpoint_sha": (
            FROZEN_PREREGISTRATION_CHECKPOINT_SHA
        ),
    }


def execution_manifest_sha256(value: Mapping[str, object]) -> str:
    """Return the canonical digest of one validated execution manifest."""
    manifest = validate_execution_manifest(value)
    return hashlib.sha256(_canonical_json_bytes(manifest)).hexdigest()


def build_reasoner_request(
    evidence_bundle: Mapping[str, object],
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Build one stateless model request with no condition/repeat metadata."""
    validated_manifest = validate_execution_manifest(manifest)
    bundle = _validate_evidence_bundle(evidence_bundle)
    bundle_json = _canonical_json_bytes(bundle).decode("utf-8")
    user_prompt = _USER_PROMPT_TEMPLATE.replace(_EVIDENCE_PLACEHOLDER, bundle_json)
    return {
        "provider": validated_manifest["provider"],
        "model_identifier": validated_manifest["model_identifier"],
        "model_version": validated_manifest["model_version"],
        "model_configuration": _json_mapping_copy(
            validated_manifest["model_configuration"],
            field_name="model_configuration",
        ),
        "max_output_tokens": validated_manifest["max_output_tokens"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }


def build_execution_plan(
    raw_views: Sequence[Mapping[str, object]],
    minimal_views: Sequence[Mapping[str, object]],
    full_views: Sequence[Mapping[str, object]],
    *,
    manifest: Mapping[str, object],
    input_token_counts: Mapping[str, int],
) -> dict[str, object]:
    """Build and preflight the exact 324-attempt blinded B2/B3/B4 matrix.

    ``input_token_counts`` must contain one externally measured full-request token
    count per ``CASE-####:<condition>`` semantic request.  This module does not pretend
    to implement a provider tokenizer; it verifies that the measured counts fit the
    pinned context/output budget without truncation.
    """
    validated_manifest = validate_execution_manifest(manifest)
    views = {
        "raw": _index_views(raw_views, condition="raw"),
        "minimal": _index_views(minimal_views, condition="minimal"),
        "full": _index_views(full_views, condition="full"),
    }
    _validate_outer_lineage(views)
    counts = _validate_token_counts(input_token_counts)

    context_window = cast(int, validated_manifest["context_window_tokens"])
    max_output = cast(int, validated_manifest["max_output_tokens"])
    safety_margin = cast(int, validated_manifest["token_safety_margin"])
    manifest_digest = execution_manifest_sha256(validated_manifest)

    requests: dict[tuple[str, str], tuple[dict[str, object], str, int]] = {}
    for case_id in _CASE_IDS:
        for condition in _CONDITIONS:
            request = build_reasoner_request(
                views[condition][case_id], validated_manifest
            )
            request_digest = hashlib.sha256(_canonical_json_bytes(request)).hexdigest()
            token_count = counts[f"{case_id}:{condition}"]
            if token_count + max_output + safety_margin > context_window:
                raise ValueError(
                    "silent truncation risk: measured input plus output budget and "
                    f"safety margin exceed context window for {case_id}:{condition}"
                )
            requests[(case_id, condition)] = (request, request_digest, token_count)

    coordinates = [
        (case_id, condition, repeat_index)
        for case_id in _CASE_IDS
        for condition in _CONDITIONS
        for repeat_index in _REPEATS
    ]
    coordinates.sort(key=lambda item: _order_key(*item))

    attempts: list[dict[str, object]] = []
    seen_attempt_ids: set[str] = set()
    for case_id, condition, repeat_index in coordinates:
        request, request_digest, token_count = requests[(case_id, condition)]
        attempt_id = _attempt_id(
            manifest_digest=manifest_digest,
            case_id=case_id,
            condition=condition,
            repeat_index=repeat_index,
            request_digest=request_digest,
        )
        if attempt_id in seen_attempt_ids:
            raise AssertionError("execution attempt identity collision")
        seen_attempt_ids.add(attempt_id)
        attempts.append(
            {
                "attempt_id": attempt_id,
                "case_id": case_id,
                "condition": condition,
                "repeat_index": repeat_index,
                "input_token_count": token_count,
                "semantic_request_sha256": request_digest,
                "reasoner_request": _json_mapping_copy(
                    request, field_name="reasoner_request"
                ),
            }
        )

    if len(attempts) != 324:
        raise AssertionError("execution plan must contain exactly 324 attempts")
    plan: dict[str, object] = {
        "schema_version": _EXECUTION_PLAN_SCHEMA,
        "execution_manifest_sha256": manifest_digest,
        "execution_order_seed": _EXECUTION_ORDER_SEED,
        "base_request_count": 108,
        "attempt_count": 324,
        "case_count": 36,
        "conditions": list(_CONDITIONS),
        "repeats": list(_REPEATS),
        "transport_retry_limit": TRANSPORT_RETRY_LIMIT,
        "attempts": attempts,
    }
    plan_digest = hashlib.sha256(_canonical_json_bytes(plan)).hexdigest()
    return {**plan, "execution_plan_sha256": plan_digest}


def _validate_evidence_bundle(value: Mapping[str, object]) -> dict[str, object]:
    bundle = _exact_mapping(
        value, expected_keys=_BUNDLE_KEYS, field_name="evidence_bundle"
    )
    schema_version = _text(bundle["schema_version"], field_name="schema_version")
    if schema_version != _EVIDENCE_BUNDLE_SCHEMA:
        raise ValueError("unexpected evidence bundle schema_version")
    case_id = validate_case_id(bundle["case_id"])
    if case_id not in _CASE_ID_SET:
        raise ValueError("evidence bundle case_id is not in frozen corpus-v1")
    task = validate_bounded_text(
        bundle["task"], field_name="task", max_chars=MAX_CASE_TASK_CHARS
    )
    environment = _json_mapping_copy(bundle["environment"], field_name="environment")
    evidence_value = _json_copy(bundle["evidence"], field_name="evidence")
    if not isinstance(evidence_value, list) or not evidence_value:
        raise ValueError("evidence must be a non-empty JSON list")
    evidence: list[object] = list(cast(list[object], evidence_value))
    normalized: dict[str, object] = {
        "schema_version": schema_version,
        "case_id": case_id,
        "task": task,
        "environment": environment,
        "evidence": evidence,
    }
    _reject_hidden_gold_keys(normalized, path="evidence_bundle")
    if len(_canonical_json_bytes(normalized)) > _MAX_BUNDLE_BYTES:
        raise ValueError("evidence bundle exceeds frozen projection size bound")
    return normalized


def _index_views(
    values: Sequence[Mapping[str, object]], *, condition: str
) -> dict[str, dict[str, object]]:
    if len(values) != 36:
        raise ValueError(f"{condition} views must contain exactly 36 cases")
    indexed: dict[str, dict[str, object]] = {}
    for value in values:
        bundle = _validate_evidence_bundle(value)
        case_id = cast(str, bundle["case_id"])
        if case_id in indexed:
            raise ValueError(f"{condition} views contain duplicate case_id {case_id}")
        indexed[case_id] = bundle
    if set(indexed) != _CASE_ID_SET:
        raise ValueError(f"{condition} views do not match frozen corpus-v1 case IDs")
    return indexed


def _validate_outer_lineage(
    views: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> None:
    for case_id in _CASE_IDS:
        baseline = views["raw"][case_id]
        for condition in ("minimal", "full"):
            candidate = views[condition][case_id]
            for field in ("schema_version", "case_id", "task", "environment"):
                if candidate[field] != baseline[field]:
                    raise ValueError(
                        "RAW/MINIMAL/FULL outer lineage drift for "
                        f"{case_id} field {field}"
                    )


def _validate_token_counts(value: Mapping[str, int]) -> dict[str, int]:
    expected = {
        f"{case_id}:{condition}" for case_id in _CASE_IDS for condition in _CONDITIONS
    }
    if set(value) != expected:
        raise ValueError(
            "input_token_counts must cover exactly 108 case-condition requests"
        )
    result: dict[str, int] = {}
    for key, count in value.items():
        result[key] = _positive_int(count, field_name=f"input_token_counts[{key}]")
    return result


def _order_key(case_id: str, condition: str, repeat_index: int) -> str:
    material = f"{_EXECUTION_ORDER_SEED}:{case_id}:{condition}:{repeat_index}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _attempt_id(
    *,
    manifest_digest: str,
    case_id: str,
    condition: str,
    repeat_index: int,
    request_digest: str,
) -> str:
    material = ":".join(
        (manifest_digest, case_id, condition, str(repeat_index), request_digest)
    )
    return "EVAL-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _exact_mapping(
    value: object, *, expected_keys: frozenset[str], field_name: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    mapping = cast(Mapping[object, object], value)
    keys: set[str] = set()
    for key in mapping:
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be text")
        keys.add(key)
    if keys != expected_keys:
        raise ValueError(
            f"{field_name} keys mismatch; missing={sorted(expected_keys - keys)!r} "
            f"extra={sorted(keys - expected_keys)!r}"
        )
    return cast(Mapping[str, object], value)


def _identity(value: object, *, field_name: str) -> str:
    return validate_bounded_text(
        value, field_name=field_name, max_chars=_MAX_IDENTITY_CHARS
    )


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty text")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    return value


def _positive_int(value: object, *, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(value: object, *, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")
    return value


def _required_bool(value: object, *, field_name: str, expected: bool) -> None:
    if type(value) is not bool or value is not expected:
        raise ValueError(f"{field_name} must be {expected}")


def _required_digest(value: object, *, field_name: str, expected: str) -> None:
    text = _text(value, field_name=field_name)
    if text != expected:
        raise ValueError(f"{field_name} does not match frozen execution identity")


def _json_copy(value: object, *, field_name: str) -> object:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        decoded: object = json.loads(encoded)
        return decoded
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite JSON-compatible data") from exc


def _json_mapping_copy(value: object, *, field_name: str) -> dict[str, object]:
    copied = _json_copy(value, field_name=field_name)
    if not isinstance(copied, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    raw = cast(Mapping[object, object], copied)
    result: dict[str, object] = {}
    for key, child in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be text")
        result[key] = child
    return result


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be finite JSON-compatible data") from exc


def _reject_hidden_gold_keys(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, child in mapping.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} mapping keys must be text")
            if key in _FORBIDDEN_VISIBLE_KEYS:
                raise ValueError(f"{path} contains hidden-gold key {key!r}")
            _reject_hidden_gold_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(cast(list[object], value)):
            _reject_hidden_gold_keys(child, path=f"{path}[{index}]")
