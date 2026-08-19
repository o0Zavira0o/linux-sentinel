"""Private Phase-5F.6 score-bearing mandatory-ablation execution boundary.

This module operationalizes the already-frozen operator-local ablation execution
preregistration.  It does not build mappings, score outputs, inspect hidden gold, or
change model semantics.  It validates the frozen preregistration bytes, reuses the
frozen Ollama payload builder, sends only exact loopback ``/api/chat`` payloads when
an explicit caller authorization gate is true, retries only transport/provider
failures once with the exact same payload, and captures raw provider responses
without parsing or repairing the model's structured final answer.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, TextIO, cast

from .ollama_execution import (
    _ConnectionFactory,
    build_ollama_chat_payload,
    fetch_ollama_runtime_identity,
    ollama_payload_sha256,
    repeat_seed,
)

ABLATION_EXECUTION_SEMANTICS_SHA256: Final[str] = (
    "d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d"
)
ABLATION_EXECUTION_PLAN_SHA256: Final[str] = (
    "c9793baba485dabfa96bc914c7ccc4ab77f9b1a578b94df0a650bb1496afaf7b"
)
ABLATION_REQUEST_CATALOG_SHA256: Final[str] = (
    "9a77a49e87a757590e26e124d662e957a48c797a573e73e11f15aff236ff8439"
)
ABLATION_EXECUTION_SEMANTICS_FILE_SHA256: Final[str] = (
    "d0fe1114f1fcaf11606e780179699336020350d239cd3ee965c3392e9bf708b1"
)
ABLATION_EXECUTION_PLAN_FILE_SHA256: Final[str] = (
    "1667ea51b36bf3f66769880e499ee9b5e6360a86d1343d4ee77c45e1ed9e70a8"
)
ABLATION_RECEIPT_FILE_SHA256: Final[str] = (
    "c5d56f48f776a3614aa92d1e6cd651a9b5f33790ed8853552f14f617f9c3c00e"
)

_EXECUTION_SEMANTICS_SCHEMA: Final[str] = (
    "sentinel-x.phase5f6-ablation-score-execution-semantics.v1"
)
_EXECUTION_PLAN_SCHEMA: Final[str] = (
    "sentinel-x.phase5f6-ablation-score-execution-plan.v1"
)
_REQUEST_SCHEMA: Final[str] = "sentinel-x.phase5f6-ablation-score-request.v1"
_CAPTURE_SCHEMA: Final[str] = "sentinel-x.phase5f6-ablation-score-capture.v1"
_CAPTURE_MANIFEST_SCHEMA: Final[str] = (
    "sentinel-x.phase5f6-ablation-score-capture-manifest.v1"
)
_FAILURE_SCHEMA: Final[str] = "sentinel-x.phase5f6-ablation-score-capture-failure.v1"
_EXECUTOR_SEMANTICS_SCHEMA: Final[str] = (
    "sentinel-x.phase5f6-ablation-external-executor-semantics.v1"
)

_EXECUTION_ORDER_SEED: Final[str] = (
    "sentinel-x.phase5f6-ablation-score-execution-order.v1"
)
_EXECUTION_MANIFEST_SHA256: Final[str] = (
    "e0da42efc5e67fc3d86b434a99e3e46dda257ba1a8b049c2adbb408e79582ee6"
)
_FROZEN_BASE_PLAN_FILE_SHA256: Final[str] = (
    "61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80"
)
_FROZEN_BASE_PLAN_SEMANTIC_SHA256: Final[str] = (
    "0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542"
)

_CASE_IDS: Final[tuple[str, ...]] = tuple(f"CASE-{index:04d}" for index in range(1, 37))
_CASE_ID_SET = frozenset(_CASE_IDS)
_REPEATS: Final[tuple[int, ...]] = (1, 2, 3)
_MAPPING_BASE_CONDITIONS: Final[dict[str, str]] = {
    "minimal-minus-boot-provenance": "minimal",
    "minimal-minus-counterevidence": "minimal",
    "minimal-minus-coverage": "minimal",
    "minimal-minus-exact-timestamp-basis": "minimal",
    "minimal-minus-intervention-metadata": "minimal",
    "minimal-minus-topology": "minimal",
    "full-minus-current-derived-synthesis-interpretation": "full",
}
_MAPPING_IDS = frozenset(_MAPPING_BASE_CONDITIONS)
_REPEAT_SEEDS: Final[dict[int, int]] = {1: 1729, 2: 3253, 3: 7919}

_OLLAMA_HOST: Final[str] = "127.0.0.1"
_OLLAMA_PORT: Final[int] = 11_434
_CHAT_PATH: Final[str] = "/api/chat"
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 300.0
_MAX_CHAT_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024
_TRANSPORT_RETRY_LIMIT: Final[int] = 1
_EXPECTED_ATTEMPT_COUNT: Final[int] = 756
_EXPECTED_REQUEST_COUNT: Final[int] = 252

_EXECUTION_SEMANTICS_FILENAME: Final[str] = "EXECUTION_SEMANTICS.json"
_REQUESTS_FILENAME: Final[str] = "REQUESTS.jsonl"
_PLAN_FILENAME: Final[str] = "PLAN.json"
_RECEIPT_FILENAME: Final[str] = "RECEIPT.txt"
_RESPONSES_FILENAME: Final[str] = "RESPONSES.jsonl"
_CAPTURE_MANIFEST_FILENAME: Final[str] = "CAPTURE_MANIFEST.json"
_FAILURE_FILENAME: Final[str] = "FAILURE.json"
_SHA256SUMS_FILENAME: Final[str] = "SHA256SUMS"

_REQUEST_KEYS = frozenset(
    {
        "schema",
        "case_id",
        "mapping_id",
        "base_condition",
        "transformed_bundle_sha256",
        "semantic_request_sha256",
        "input_token_count",
        "reasoner_request",
    }
)
_ATTEMPT_KEYS = frozenset(
    {
        "ordinal",
        "attempt_id",
        "case_id",
        "mapping_id",
        "base_condition",
        "repeat_index",
        "seed",
        "input_token_count",
        "semantic_request_sha256",
        "chat_payload_sha256",
    }
)
_PLAN_KEYS = frozenset(
    {
        "schema",
        "execution_semantics_sha256",
        "request_catalog_sha256",
        "execution_manifest_sha256",
        "execution_order_seed",
        "repeat_seeds",
        "mapping_count",
        "case_count",
        "repeats",
        "transport_retry_limit",
        "attempt_count",
        "attempts",
        "execution_plan_sha256",
    }
)


class _TransportFailure(RuntimeError):
    """Retry-eligible local transport/provider-envelope failure only."""


def external_executor_semantics() -> dict[str, object]:
    """Return the exact pre-score executor/capture contract for formal freezing."""
    return {
        "schema": _EXECUTOR_SEMANTICS_SCHEMA,
        "execution_semantics_sha256": ABLATION_EXECUTION_SEMANTICS_SHA256,
        "execution_plan_sha256": ABLATION_EXECUTION_PLAN_SHA256,
        "request_catalog_sha256": ABLATION_REQUEST_CATALOG_SHA256,
        "attempt_count": _EXPECTED_ATTEMPT_COUNT,
        "request_count": _EXPECTED_REQUEST_COUNT,
        "transport": {
            "host": _OLLAMA_HOST,
            "port": _OLLAMA_PORT,
            "path": _CHAT_PATH,
            "retry_limit": _TRANSPORT_RETRY_LIMIT,
            "retry_scope": "transport/provider failure only; exact same payload",
            "semantic_output_retry": False,
            "max_response_bytes": _MAX_CHAT_RESPONSE_BYTES,
        },
        "capture": {
            "schema": _CAPTURE_SCHEMA,
            "responses_filename": _RESPONSES_FILENAME,
            "manifest_filename": _CAPTURE_MANIFEST_FILENAME,
            "failure_filename": _FAILURE_FILENAME,
            "preserve_raw_response_base64": True,
            "preserve_assistant_content_verbatim": True,
            "parse_structured_model_output": False,
            "score_during_capture": False,
            "resume_supported": False,
            "directory_mode": "0500 after close; 0700 while active",
            "file_mode": "0400 after close; 0600 while active",
        },
        "authorization": {
            "explicit_runtime_gate_required": True,
            "default_authorized": False,
        },
    }


def external_executor_semantics_sha256() -> str:
    """Return the content identity of the exact external executor semantics."""
    return hashlib.sha256(
        _canonical_json_bytes(external_executor_semantics())
    ).hexdigest()


def load_frozen_ablation_preregistration(
    prereg_root: Path,
) -> tuple[dict[str, object], tuple[dict[str, object], ...], dict[str, object]]:
    """Load and byte-verify the immutable operator-local preregistration inputs."""
    root = _validate_prereg_root(prereg_root)
    semantics_bytes = _read_exact_file(
        root / _EXECUTION_SEMANTICS_FILENAME,
        expected_sha256=ABLATION_EXECUTION_SEMANTICS_FILE_SHA256,
    )
    requests_bytes = _read_exact_file(
        root / _REQUESTS_FILENAME,
        expected_sha256=ABLATION_REQUEST_CATALOG_SHA256,
    )
    plan_bytes = _read_exact_file(
        root / _PLAN_FILENAME,
        expected_sha256=ABLATION_EXECUTION_PLAN_FILE_SHA256,
    )
    _read_exact_file(
        root / _RECEIPT_FILENAME,
        expected_sha256=ABLATION_RECEIPT_FILE_SHA256,
    )

    semantics = _json_object_from_bytes(
        semantics_bytes, field_name="execution semantics"
    )
    requests = _jsonl_objects_from_bytes(requests_bytes, field_name="request catalog")
    plan = _json_object_from_bytes(plan_bytes, field_name="execution plan")
    validate_ablation_execution_contract(semantics, requests, plan)
    return semantics, requests, plan


def validate_ablation_execution_contract(
    semantics: Mapping[str, object],
    requests: Sequence[Mapping[str, object]],
    plan: Mapping[str, object],
) -> None:
    """Validate the exact frozen 252-request / 756-attempt ablation contract."""
    _validate_execution_semantics(semantics)
    request_index = _validate_request_catalog(requests)
    _validate_execution_plan(plan, request_index=request_index)


def execute_frozen_ablation_capture(
    prereg_root: Path,
    destination: Path,
    *,
    scored_execution_authorized: bool = False,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    connection_factory: _ConnectionFactory | None = None,
) -> dict[str, object]:
    """Execute and privately capture the exact frozen 756-attempt ablation plan.

    The caller gate is deliberately false by default.  This function performs no
    scoring and does not parse the assistant content as structured benchmark output.
    """
    if type(scored_execution_authorized) is not bool or not scored_execution_authorized:
        raise ValueError("scored_execution_authorized must be explicitly True")
    timeout = _validate_timeout(timeout_seconds)
    semantics, requests, plan = load_frozen_ablation_preregistration(prereg_root)
    request_index = _validate_request_catalog(requests)
    attempts = _validated_attempts(plan, request_index=request_index)

    runtime_identity = fetch_ollama_runtime_identity(
        timeout_seconds=timeout,
        connection_factory=connection_factory,
    )
    root = _create_private_destination(destination)
    responses_path = root / _RESPONSES_FILENAME
    response_file = _open_exclusive_private_text(responses_path)
    completed = 0
    try:
        for attempt in attempts:
            coordinate = (
                cast(str, attempt["case_id"]),
                cast(str, attempt["mapping_id"]),
            )
            request_row = request_index[coordinate]
            capture = _execute_attempt(
                attempt,
                request_row=request_row,
                timeout_seconds=timeout,
                connection_factory=connection_factory,
            )
            response_file.write(_canonical_json_text(capture) + "\n")
            response_file.flush()
            os.fsync(response_file.fileno())
            completed += 1
    except BaseException as exc:
        response_file.close()
        _write_failure_record(
            root,
            completed_attempts=completed,
            failed_attempt=attempts[completed] if completed < len(attempts) else None,
            error=exc,
        )
        _finalize_private_tree(root)
        raise
    response_file.close()

    responses_sha256 = _sha256_file(responses_path)
    manifest: dict[str, object] = {
        "schema": _CAPTURE_MANIFEST_SCHEMA,
        "complete": True,
        "attempt_count": completed,
        "execution_semantics_sha256": ABLATION_EXECUTION_SEMANTICS_SHA256,
        "execution_plan_sha256": ABLATION_EXECUTION_PLAN_SHA256,
        "request_catalog_sha256": ABLATION_REQUEST_CATALOG_SHA256,
        "external_executor_semantics_sha256": external_executor_semantics_sha256(),
        "responses_sha256": responses_sha256,
        "runtime_identity": _json_copy(runtime_identity, field_name="runtime_identity"),
        "score_during_capture": False,
        "structured_model_output_parsed": False,
    }
    _write_private_json(root / _CAPTURE_MANIFEST_FILENAME, manifest)
    _write_sha256sums(
        root,
        filenames=(_RESPONSES_FILENAME, _CAPTURE_MANIFEST_FILENAME),
    )
    _finalize_private_tree(root)
    return manifest


def _validate_execution_semantics(value: Mapping[str, object]) -> None:
    normalized = _json_copy(value, field_name="execution semantics")
    if not isinstance(normalized, dict):
        raise ValueError("execution semantics must be a JSON object")
    if hashlib.sha256(_canonical_json_bytes(normalized)).hexdigest() != (
        ABLATION_EXECUTION_SEMANTICS_SHA256
    ):
        raise ValueError("execution semantics digest drift")
    expected_scalars: tuple[tuple[str, object], ...] = (
        ("schema", _EXECUTION_SEMANTICS_SCHEMA),
        ("status", "FROZEN_PRE_SCORE_ABLATION_EXECUTION_SEMANTICS"),
        ("ablation_attempt_count", _EXPECTED_ATTEMPT_COUNT),
        ("unique_ablation_request_count", _EXPECTED_REQUEST_COUNT),
        ("case_count", 36),
        ("mapping_count", 7),
        ("execution_order_seed", _EXECUTION_ORDER_SEED),
        ("execution_manifest_sha256", _EXECUTION_MANIFEST_SHA256),
        ("frozen_base_324_plan_file_sha256", _FROZEN_BASE_PLAN_FILE_SHA256),
        ("frozen_base_324_plan_semantic_sha256", _FROZEN_BASE_PLAN_SEMANTIC_SHA256),
        ("transport_retry_limit", _TRANSPORT_RETRY_LIMIT),
        ("fresh_request_per_attempt", True),
        ("reuse_base_outputs_for_unchanged_ablation_views", False),
        ("condition_blinding", True),
        ("mapping_identity_sent_to_reasoner", False),
        ("repeat_identity_sent_to_reasoner", False),
        ("hidden_gold_reasoner_access", False),
        ("score_during_capture", False),
        ("scored_execution_authorized", False),
        ("tools_enabled", False),
        ("web_enabled", False),
        ("full_vs_minimal_extra_ablation_execution", False),
    )
    for key, expected in expected_scalars:
        if normalized.get(key) != expected:
            raise ValueError(f"execution semantics field drift: {key}")
    if normalized.get("repeat_indices") != [1, 2, 3]:
        raise ValueError("execution semantics repeat_indices drift")
    if normalized.get("repeat_seeds") != {"1": 1729, "2": 3253, "3": 7919}:
        raise ValueError("execution semantics repeat_seeds drift")
    retry_semantics = normalized.get("retry_semantics")
    if retry_semantics != (
        "at most one retry of the exact same chat payload on transport/provider "
        "failure only"
    ):
        raise ValueError("execution semantics retry policy drift")


def _validate_request_catalog(
    values: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str], dict[str, object]]:
    if len(values) != _EXPECTED_REQUEST_COUNT:
        raise ValueError("request catalog must contain exactly 252 rows")
    indexed: dict[tuple[str, str], dict[str, object]] = {}
    for value in values:
        row = _exact_mapping(
            value, expected_keys=_REQUEST_KEYS, field_name="request row"
        )
        if row["schema"] != _REQUEST_SCHEMA:
            raise ValueError("unexpected ablation request schema")
        case_id = _case_id(row["case_id"])
        mapping_id = _mapping_id(row["mapping_id"])
        base_condition = _text(row["base_condition"], field_name="base_condition")
        if base_condition != _MAPPING_BASE_CONDITIONS[mapping_id]:
            raise ValueError("request base_condition does not match mapping")
        transformed_digest = _digest(
            row["transformed_bundle_sha256"], field_name="transformed_bundle_sha256"
        )
        semantic_digest = _digest(
            row["semantic_request_sha256"], field_name="semantic_request_sha256"
        )
        token_count = _positive_int(
            row["input_token_count"], field_name="input_token_count"
        )
        reasoner_request = _json_mapping_copy(
            row["reasoner_request"], field_name="reasoner_request"
        )
        actual_semantic_digest = hashlib.sha256(
            _canonical_json_bytes(reasoner_request)
        ).hexdigest()
        if actual_semantic_digest != semantic_digest:
            raise ValueError("reasoner_request semantic digest drift")
        key = (case_id, mapping_id)
        if key in indexed:
            raise ValueError("duplicate request coordinate")
        indexed[key] = {
            "schema": _REQUEST_SCHEMA,
            "case_id": case_id,
            "mapping_id": mapping_id,
            "base_condition": base_condition,
            "transformed_bundle_sha256": transformed_digest,
            "semantic_request_sha256": semantic_digest,
            "input_token_count": token_count,
            "reasoner_request": reasoner_request,
        }
    expected = {
        (case_id, mapping_id) for case_id in _CASE_IDS for mapping_id in _MAPPING_IDS
    }
    if set(indexed) != expected:
        raise ValueError("request catalog does not cover exact 36 x 7 coordinates")
    return indexed


def _validate_execution_plan(
    value: Mapping[str, object],
    *,
    request_index: Mapping[tuple[str, str], Mapping[str, object]],
) -> None:
    plan = _exact_mapping(value, expected_keys=_PLAN_KEYS, field_name="execution plan")
    if plan["schema"] != _EXECUTION_PLAN_SCHEMA:
        raise ValueError("unexpected ablation execution plan schema")
    if plan["execution_semantics_sha256"] != ABLATION_EXECUTION_SEMANTICS_SHA256:
        raise ValueError("execution plan semantics binding drift")
    if plan["request_catalog_sha256"] != ABLATION_REQUEST_CATALOG_SHA256:
        raise ValueError("execution plan request catalog binding drift")
    if plan["execution_manifest_sha256"] != _EXECUTION_MANIFEST_SHA256:
        raise ValueError("execution plan manifest binding drift")
    if plan["execution_order_seed"] != _EXECUTION_ORDER_SEED:
        raise ValueError("execution plan order seed drift")
    if plan["repeat_seeds"] != {"1": 1729, "2": 3253, "3": 7919}:
        raise ValueError("execution plan repeat_seeds drift")
    if plan["mapping_count"] != 7 or plan["case_count"] != 36:
        raise ValueError("execution plan case/mapping count drift")
    if plan["repeats"] != [1, 2, 3]:
        raise ValueError("execution plan repeats drift")
    if plan["transport_retry_limit"] != _TRANSPORT_RETRY_LIMIT:
        raise ValueError("execution plan retry limit drift")
    if plan["attempt_count"] != _EXPECTED_ATTEMPT_COUNT:
        raise ValueError("execution plan attempt count drift")
    if plan["execution_plan_sha256"] != ABLATION_EXECUTION_PLAN_SHA256:
        raise ValueError("recorded execution plan digest drift")
    plan_without_digest = {
        key: child for key, child in plan.items() if key != "execution_plan_sha256"
    }
    computed = hashlib.sha256(_canonical_json_bytes(plan_without_digest)).hexdigest()
    if computed != ABLATION_EXECUTION_PLAN_SHA256:
        raise ValueError("computed execution plan digest drift")
    _validated_attempts(plan, request_index=request_index)


def _validated_attempts(
    plan: Mapping[str, object],
    *,
    request_index: Mapping[tuple[str, str], Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    attempts_value = plan.get("attempts")
    if not isinstance(attempts_value, Sequence) or isinstance(
        attempts_value, (str, bytes)
    ):
        raise ValueError("execution plan attempts must be a sequence")
    raw_attempts = cast(Sequence[object], attempts_value)
    if len(raw_attempts) != _EXPECTED_ATTEMPT_COUNT:
        raise ValueError("execution plan must contain exactly 756 attempts")
    normalized: list[dict[str, object]] = []
    seen_attempt_ids: set[str] = set()
    seen_coordinates: set[tuple[str, str, int]] = set()
    for index, raw in enumerate(raw_attempts, start=1):
        attempt = _exact_mapping(raw, expected_keys=_ATTEMPT_KEYS, field_name="attempt")
        ordinal = _positive_int(attempt["ordinal"], field_name="ordinal")
        if ordinal != index:
            raise ValueError("attempt ordinal/order drift")
        attempt_id = _text(attempt["attempt_id"], field_name="attempt_id")
        if attempt_id in seen_attempt_ids:
            raise ValueError("duplicate attempt_id")
        seen_attempt_ids.add(attempt_id)
        case_id = _case_id(attempt["case_id"])
        mapping_id = _mapping_id(attempt["mapping_id"])
        base_condition = _text(attempt["base_condition"], field_name="base_condition")
        if base_condition != _MAPPING_BASE_CONDITIONS[mapping_id]:
            raise ValueError("attempt base_condition does not match mapping")
        repeat_index = _repeat_index(attempt["repeat_index"])
        seed = _positive_int(attempt["seed"], field_name="seed")
        if seed != _REPEAT_SEEDS[repeat_index] or seed != repeat_seed(repeat_index):
            raise ValueError("attempt seed drift")
        token_count = _positive_int(
            attempt["input_token_count"], field_name="input_token_count"
        )
        semantic_digest = _digest(
            attempt["semantic_request_sha256"], field_name="semantic_request_sha256"
        )
        payload_digest = _digest(
            attempt["chat_payload_sha256"], field_name="chat_payload_sha256"
        )
        request = request_index[(case_id, mapping_id)]
        if request["base_condition"] != base_condition:
            raise ValueError("attempt/request base_condition drift")
        if request["input_token_count"] != token_count:
            raise ValueError("attempt/request token count drift")
        if request["semantic_request_sha256"] != semantic_digest:
            raise ValueError("attempt/request semantic digest drift")
        payload = build_ollama_chat_payload(
            cast(Mapping[str, object], request["reasoner_request"]),
            repeat_index=repeat_index,
        )
        if ollama_payload_sha256(payload) != payload_digest:
            raise ValueError("attempt chat payload digest drift")
        coordinate = (case_id, mapping_id, repeat_index)
        if coordinate in seen_coordinates:
            raise ValueError("duplicate attempt coordinate")
        seen_coordinates.add(coordinate)
        normalized.append(
            {
                "ordinal": ordinal,
                "attempt_id": attempt_id,
                "case_id": case_id,
                "mapping_id": mapping_id,
                "base_condition": base_condition,
                "repeat_index": repeat_index,
                "seed": seed,
                "input_token_count": token_count,
                "semantic_request_sha256": semantic_digest,
                "chat_payload_sha256": payload_digest,
            }
        )
    expected_coordinates = {
        (case_id, mapping_id, repeat_index)
        for case_id in _CASE_IDS
        for mapping_id in _MAPPING_IDS
        for repeat_index in _REPEATS
    }
    if seen_coordinates != expected_coordinates:
        raise ValueError("execution plan does not cover exact 36 x 7 x 3 matrix")
    return tuple(normalized)


def _execute_attempt(
    attempt: Mapping[str, object],
    *,
    request_row: Mapping[str, object],
    timeout_seconds: float,
    connection_factory: _ConnectionFactory | None,
) -> dict[str, object]:
    repeat_index = cast(int, attempt["repeat_index"])
    reasoner_request = cast(Mapping[str, object], request_row["reasoner_request"])
    payload = build_ollama_chat_payload(reasoner_request, repeat_index=repeat_index)
    payload_digest = ollama_payload_sha256(payload)
    if payload_digest != attempt["chat_payload_sha256"]:
        raise ValueError("runtime chat payload digest drift")

    transport_attempts = 0
    while True:
        transport_attempts += 1
        try:
            body = _post_score_bearing_chat_once(
                payload,
                timeout_seconds=timeout_seconds,
                connection_factory=connection_factory,
            )
            envelope = _validate_chat_response_envelope(body)
            break
        except _TransportFailure:
            if transport_attempts > _TRANSPORT_RETRY_LIMIT:
                raise

    content = cast(str, envelope["assistant_content"])
    return {
        "schema": _CAPTURE_SCHEMA,
        "ordinal": attempt["ordinal"],
        "attempt_id": attempt["attempt_id"],
        "case_id": attempt["case_id"],
        "mapping_id": attempt["mapping_id"],
        "base_condition": attempt["base_condition"],
        "repeat_index": repeat_index,
        "seed": attempt["seed"],
        "input_token_count": attempt["input_token_count"],
        "semantic_request_sha256": attempt["semantic_request_sha256"],
        "chat_payload_sha256": payload_digest,
        "transport_attempts": transport_attempts,
        "response_body_sha256": hashlib.sha256(body).hexdigest(),
        "response_body_base64": base64.b64encode(body).decode("ascii"),
        "assistant_content": content,
        "assistant_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "done_reason": envelope["done_reason"],
    }


def _post_score_bearing_chat_once(
    payload: Mapping[str, object],
    *,
    timeout_seconds: float,
    connection_factory: _ConnectionFactory | None,
) -> bytes:
    timeout = _validate_timeout(timeout_seconds)
    factory = connection_factory or _open_http_connection
    connection = factory(_OLLAMA_HOST, _OLLAMA_PORT, timeout)
    body = _canonical_json_bytes(payload)
    try:
        try:
            connection.request(
                "POST",
                _CHAT_PATH,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response_body = response.read(_MAX_CHAT_RESPONSE_BYTES + 1)
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise _TransportFailure("local Ollama transport failure") from exc
        if response.status < 200 or response.status >= 300:
            raise _TransportFailure(
                f"local Ollama generation endpoint returned HTTP {response.status}"
            )
        if len(response_body) > _MAX_CHAT_RESPONSE_BYTES:
            raise _TransportFailure(
                "local Ollama generation response exceeded size bound"
            )
        return response_body
    finally:
        connection.close()


def _validate_chat_response_envelope(body: bytes) -> dict[str, object]:
    try:
        response = _json_object_from_bytes(body, field_name="chat response")
    except ValueError as exc:
        raise _TransportFailure("invalid Ollama chat response envelope") from exc
    model = response.get("model")
    if model != "sentinelx-gemma4-12b-qat:preflight":
        raise _TransportFailure("Ollama chat response model identity drift")
    if response.get("done") is not True:
        raise _TransportFailure("Ollama chat response is not complete")
    message = response.get("message")
    if not isinstance(message, Mapping):
        raise _TransportFailure("Ollama chat response message is missing")
    message_map = cast(Mapping[object, object], message)
    if message_map.get("role") != "assistant":
        raise _TransportFailure("Ollama chat response role drift")
    content = message_map.get("content")
    if not isinstance(content, str):
        raise _TransportFailure("Ollama chat response content is not text")
    tool_calls = message_map.get("tool_calls")
    if tool_calls not in (None, [], ()):
        raise _TransportFailure("Ollama chat response unexpectedly contains tool calls")
    done_reason = response.get("done_reason")
    if done_reason is not None and not isinstance(done_reason, str):
        raise _TransportFailure("Ollama chat response done_reason is invalid")
    return {
        "assistant_content": content,
        "done_reason": done_reason,
    }


def _validate_prereg_root(value: Path) -> Path:
    if not isinstance(value, Path):
        raise ValueError("prereg_root must be a pathlib.Path")
    root = value.expanduser()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("prereg_root must be an existing non-symlink directory")
    if root.stat().st_mode & 0o777 != 0o500:
        raise ValueError("prereg_root must retain frozen mode 0500")
    return root


def _read_exact_file(path: Path, *, expected_sha256: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"frozen prereg file missing or unsafe: {path.name}")
    if path.stat().st_mode & 0o777 != 0o400:
        raise ValueError(f"frozen prereg file mode drift: {path.name}")
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"frozen prereg file digest drift: {path.name}")
    return data


def _json_object_from_bytes(data: bytes, *, field_name: str) -> dict[str, object]:
    if not isinstance(data, bytes) or not data:
        raise ValueError(f"{field_name} must be non-empty bytes")
    try:
        decoded: object = json.loads(
            data.decode("utf-8"),
            parse_constant=lambda value: _raise_nonstandard_json(value),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} must be valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    raw = cast(dict[object, object], decoded)
    result: dict[str, object] = {}
    for key, child in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be text")
        result[key] = child
    return result


def _jsonl_objects_from_bytes(
    data: bytes, *, field_name: str
) -> tuple[dict[str, object], ...]:
    if not data:
        raise ValueError(f"{field_name} must be non-empty bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8") from exc
    rows: list[dict[str, object]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise ValueError(f"{field_name} contains blank line {lineno}")
        try:
            decoded: object = json.loads(
                line,
                parse_constant=lambda value: _raise_nonstandard_json(value),
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} line {lineno} is invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError(f"{field_name} line {lineno} must be a JSON object")
        raw = cast(dict[object, object], decoded)
        row: dict[str, object] = {}
        for key, child in raw.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} line {lineno} keys must be text")
            row[key] = child
        rows.append(row)
    return tuple(rows)


def _create_private_destination(destination: Path) -> Path:
    if not isinstance(destination, Path):
        raise ValueError("destination must be a pathlib.Path")
    root = destination.expanduser()
    if root.exists() or root.is_symlink():
        raise ValueError("capture destination must not already exist")
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    return root


def _open_exclusive_private_text(path: Path) -> TextIO:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", newline="\n")


def _write_private_json(path: Path, value: Mapping[str, object]) -> None:
    text = _canonical_json_text(value) + "\n"
    with _open_exclusive_private_text(path) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _write_failure_record(
    root: Path,
    *,
    completed_attempts: int,
    failed_attempt: Mapping[str, object] | None,
    error: BaseException,
) -> None:
    failure: dict[str, object] = {
        "schema": _FAILURE_SCHEMA,
        "complete": False,
        "completed_attempts": completed_attempts,
        "external_executor_semantics_sha256": external_executor_semantics_sha256(),
        "error_type": type(error).__name__,
        "error_message": str(error)[:1024],
        "failed_attempt": (
            None
            if failed_attempt is None
            else {
                key: failed_attempt[key]
                for key in (
                    "ordinal",
                    "attempt_id",
                    "case_id",
                    "mapping_id",
                    "repeat_index",
                    "chat_payload_sha256",
                )
            }
        ),
    }
    _write_private_json(root / _FAILURE_FILENAME, failure)
    filenames = [_RESPONSES_FILENAME, _FAILURE_FILENAME]
    _write_sha256sums(root, filenames=filenames)


def _write_sha256sums(root: Path, *, filenames: Sequence[str]) -> None:
    lines = [f"{_sha256_file(root / name)}  {name}" for name in filenames]
    path = root / _SHA256SUMS_FILENAME
    with _open_exclusive_private_text(path) as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _finalize_private_tree(root: Path) -> None:
    for child in root.iterdir():
        if child.is_file() and not child.is_symlink():
            child.chmod(0o400)
    root.chmod(0o500)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _open_http_connection(
    host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(host, port=port, timeout=timeout)


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout_seconds must be a finite positive number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0.0 or timeout > 300.0:
        raise ValueError("timeout_seconds must be finite and in (0, 300]")
    return timeout


def _exact_mapping(
    value: object, *, expected_keys: frozenset[str], field_name: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    raw = cast(Mapping[object, object], value)
    keys: set[str] = set()
    for key in raw:
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be text")
        keys.add(key)
    if keys != expected_keys:
        raise ValueError(
            f"{field_name} keys mismatch; missing={sorted(expected_keys - keys)!r} "
            f"extra={sorted(keys - expected_keys)!r}"
        )
    return cast(Mapping[str, object], value)


def _case_id(value: object) -> str:
    text = _text(value, field_name="case_id")
    if text not in _CASE_ID_SET:
        raise ValueError("case_id is not in frozen corpus-v1")
    return text


def _mapping_id(value: object) -> str:
    text = _text(value, field_name="mapping_id")
    if text not in _MAPPING_IDS:
        raise ValueError("unknown mandatory ablation mapping_id")
    return text


def _repeat_index(value: object) -> int:
    if type(value) is not int or value not in _REPEATS:
        raise ValueError("repeat_index must be exactly 1, 2, or 3")
    return value


def _positive_int(value: object, *, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{field_name} must be non-empty NUL-free text")
    return value


def _digest(value: object, *, field_name: str) -> str:
    text = _text(value, field_name=field_name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256 hex")
    return text


def _json_copy(value: object, *, field_name: str) -> object:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        result: object = json.loads(encoded)
        return result
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite JSON-compatible data") from exc


def _json_mapping_copy(value: object, *, field_name: str) -> dict[str, object]:
    copied = _json_copy(value, field_name=field_name)
    if not isinstance(copied, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    raw = cast(dict[object, object], copied)
    result: dict[str, object] = {}
    for key, child in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be text")
        result[key] = child
    return result


def _canonical_json_text(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be finite JSON-compatible data") from exc


def _canonical_json_bytes(value: object) -> bytes:
    return _canonical_json_text(value).encode("utf-8")


def _raise_nonstandard_json(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")
