"""Private Phase-5F.6 Ollama/Gemma local execution controls.

This module instantiates the frozen provider-neutral execution boundary for the
locally pinned Gemma 4 12B QAT model.  It can construct future score-bearing chat
payloads, but deliberately provides transport only for non-score-bearing runtime
identity, render-only, and tokenizer endpoints.

No function in this module sends a normal /api/chat generation request.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Final, Protocol, cast

from .execution import (
    FROZEN_CORPUS_ARCHIVE_SHA256,
    FROZEN_FULL_REFERENCE_SHA,
    FROZEN_PREREGISTRATION_CHECKPOINT_SHA,
    FROZEN_REASONER_OUTPUT_SHA256,
    FROZEN_SCORER_SHA256,
    OUTPUT_SCHEMA_SHA256,
    PROMPT_TEMPLATE_SHA256,
    TRANSPORT_RETRY_LIMIT,
    validate_execution_manifest,
)

_PROVIDER: Final[str] = "ollama-local"
_MODEL_IDENTIFIER: Final[str] = "sentinelx-gemma4-12b-qat:preflight"
_MODEL_VERSION: Final[str] = (
    "be1d79d105352d8cb0a25ee03f1f315935cc93fb4f0674422c2bb13be72fc025"
)
_OLLAMA_VERSION: Final[str] = "0.32.13"
_GGUF_SHA256: Final[str] = (
    "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b"
)
_MODEL_SIZE_BYTES: Final[int] = 6_975_879_517
_MODEL_ARCHITECTURE: Final[str] = "gemma4"
_MODEL_PARAMETER_SIZE: Final[str] = "11.9B"
_MODEL_QUANTIZATION: Final[str] = "Q4_0"
_MODEL_CONTEXT_CAPABILITY: Final[int] = 262_144

# Corrective pre-score capacity candidate.  The frozen 8192-context token map
# falsified the prior capacity assumption (56/108 requests over budget; maximum
# input 38,091 tokens).  A 49,152-context f16 live proof then passed at 38,087
# synthetic prompt tokens with exact tokenizer/inference count equality and zero
# swap.  This capacity remains unfrozen until the 108-request map is rebound to
# this corrected manifest and the generic no-truncation execution plan passes.
_CONTEXT_WINDOW_TOKENS: Final[int] = 49_152
_MAX_OUTPUT_TOKENS: Final[int] = 2_048
_TOKEN_SAFETY_MARGIN: Final[int] = 1_024

_REPEAT_SEEDS: Final[tuple[int, int, int]] = (1_729, 3_253, 7_919)
_TEMPERATURE: Final[float] = 1.0
_TOP_P: Final[float] = 0.95
_TOP_K: Final[int] = 64
_KEEP_ALIVE: Final[int] = 0

_OLLAMA_HOST: Final[str] = "127.0.0.1"
_OLLAMA_PORT: Final[int] = 11_434
_CHAT_PATH: Final[str] = "/api/chat"
_VERSION_PATH: Final[str] = "/api/version"
_TAGS_PATH: Final[str] = "/api/tags"
_SHOW_PATH: Final[str] = "/api/show"
_LLAMA_TOKENIZE_PATH: Final[str] = "/tokenize"

_TOKENIZER_IDENTIFIER: Final[str] = (
    "ollama-0.32.13:gemma4-renderer:"
    "gguf-sha256=93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b:"
    "llama-server-/tokenize"
)

_MAX_RESPONSE_BYTES: Final[int] = 8 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0

_REASONER_REQUEST_KEYS = frozenset(
    {
        "provider",
        "model_identifier",
        "model_version",
        "model_configuration",
        "max_output_tokens",
        "messages",
    }
)

_PROVIDER_CONFIGURATION: Final[dict[str, object]] = {
    "api": "ollama-native-chat",
    "chat_endpoint": _CHAT_PATH,
    "ollama_version": _OLLAMA_VERSION,
    "model_digest": _MODEL_VERSION,
    "gguf_sha256": _GGUF_SHA256,
    "model_size_bytes": _MODEL_SIZE_BYTES,
    "architecture": _MODEL_ARCHITECTURE,
    "parameter_size": _MODEL_PARAMETER_SIZE,
    "quantization": _MODEL_QUANTIZATION,
    "model_context_capability": _MODEL_CONTEXT_CAPABILITY,
    "renderer": "gemma4",
    "parser": "gemma4",
    "template": "{{ .Prompt }}",
    "stop": ["<turn|>"],
    "think": False,
    "stream": False,
    "truncate": False,
    "shift": False,
    "format": "omitted",
    "tools": "omitted",
    "web": "disabled",
    "keep_alive": _KEEP_ALIVE,
    "sampling": {
        "temperature": _TEMPERATURE,
        "top_p": _TOP_P,
        "top_k": _TOP_K,
    },
    "repeat_seeds": list(_REPEAT_SEEDS),
    "service": {
        "host": "127.0.0.1:11434",
        "no_cloud": True,
        "num_parallel": 1,
        "max_loaded_models": 1,
    },
    "render_only": {
        "endpoint": _CHAT_PATH,
        "debug_field": "_debug_render_only",
    },
    "token_count": {
        "renderer_source": "ollama-debug-render-only",
        "tokenizer_endpoint": _LLAMA_TOKENIZE_PATH,
        "add_special": False,
        "parse_special": True,
    },
}


class _HTTPResponse(Protocol):
    status: int

    def read(self, amt: int | None = None) -> bytes: ...


class _HTTPConnection(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None: ...

    def getresponse(self) -> _HTTPResponse: ...

    def close(self) -> None: ...


_ConnectionFactory = Callable[[str, int, float], _HTTPConnection]


def build_ollama_execution_manifest() -> dict[str, object]:
    """Return the concrete local pre-score execution-manifest candidate."""
    return validate_execution_manifest(
        {
            "schema_version": "sentinel-x.phase5f-execution-manifest.v1",
            "provider": _PROVIDER,
            "model_identifier": _MODEL_IDENTIFIER,
            "model_version": _MODEL_VERSION,
            "model_configuration": _json_mapping_copy(_PROVIDER_CONFIGURATION),
            "tokenizer_identifier": _TOKENIZER_IDENTIFIER,
            "context_window_tokens": _CONTEXT_WINDOW_TOKENS,
            "max_output_tokens": _MAX_OUTPUT_TOKENS,
            "token_safety_margin": _TOKEN_SAFETY_MARGIN,
            "transport_retry_limit": TRANSPORT_RETRY_LIMIT,
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
    )


def repeat_seed(repeat_index: int) -> int:
    """Return the preregistered local sampling seed for repeat 1/2/3."""
    if type(repeat_index) is not int or repeat_index not in (1, 2, 3):
        raise ValueError("repeat_index must be exactly 1, 2, or 3")
    return _REPEAT_SEEDS[repeat_index - 1]


def build_ollama_chat_payload(
    reasoner_request: Mapping[str, object],
    *,
    repeat_index: int,
) -> dict[str, object]:
    """Build one future score-bearing native Ollama chat payload.

    This function is pure.  This module intentionally has no transport that sends
    the returned payload to the normal generation path.
    """
    request = _validate_reasoner_request(reasoner_request)
    return {
        "model": _MODEL_IDENTIFIER,
        "messages": _messages_copy(request["messages"]),
        "stream": False,
        "think": False,
        "truncate": False,
        "shift": False,
        "keep_alive": _KEEP_ALIVE,
        "options": {
            "num_ctx": _CONTEXT_WINDOW_TOKENS,
            "num_predict": _MAX_OUTPUT_TOKENS,
            "seed": repeat_seed(repeat_index),
            "temperature": _TEMPERATURE,
            "top_p": _TOP_P,
            "top_k": _TOP_K,
        },
    }


def build_ollama_render_only_payload(
    reasoner_request: Mapping[str, object],
) -> dict[str, object]:
    """Build a non-score-bearing request that asks Ollama only to render chat."""
    request = _validate_reasoner_request(reasoner_request)
    return {
        "model": _MODEL_IDENTIFIER,
        "messages": _messages_copy(request["messages"]),
        "stream": False,
        "think": False,
        "truncate": False,
        "shift": False,
        "keep_alive": _KEEP_ALIVE,
        "_debug_render_only": True,
        "options": {
            "num_ctx": _CONTEXT_WINDOW_TOKENS,
            "temperature": _TEMPERATURE,
            "top_p": _TOP_P,
            "top_k": _TOP_K,
        },
    }


def build_llama_tokenize_payload(rendered_prompt: str) -> dict[str, object]:
    """Build the exact tokenizer-only payload for a rendered Ollama prompt."""
    prompt = _bounded_text(
        rendered_prompt,
        field_name="rendered_prompt",
        max_chars=8 * 1024 * 1024,
    )
    return {
        "content": prompt,
        "add_special": False,
        "parse_special": True,
        "with_pieces": False,
    }


def ollama_payload_sha256(payload: Mapping[str, object]) -> str:
    """Return canonical SHA-256 for one JSON-compatible local payload."""
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def parse_ollama_render_only_response(body: bytes) -> str:
    """Parse one bounded Ollama debug-render-only response."""
    mapping = _json_response_mapping(body, field_name="render-only response")
    debug = mapping.get("_debug_info")
    if not isinstance(debug, Mapping):
        raise ValueError("render-only response is missing _debug_info")
    debug_map = cast(Mapping[object, object], debug)
    rendered = debug_map.get("rendered_template")
    if not isinstance(rendered, str) or not rendered:
        raise ValueError("render-only response lacks rendered_template")
    message = mapping.get("message")
    if isinstance(message, Mapping):
        content = cast(Mapping[object, object], message).get("content")
        if content not in (None, ""):
            raise ValueError("render-only response unexpectedly contains model output")
    return rendered


def parse_llama_tokenize_response(body: bytes) -> int:
    """Parse one llama-server /tokenize response and return exact token count."""
    mapping = _json_response_mapping(body, field_name="tokenize response")
    tokens = mapping.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        raise ValueError("tokenize response must contain a non-empty tokens list")
    for token in cast(list[object], tokens):
        if type(token) is not int:
            raise ValueError("tokenize response tokens must be integers")
    return len(tokens)


def fetch_ollama_rendered_prompt(
    payload: Mapping[str, object],
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    connection_factory: _ConnectionFactory | None = None,
) -> str:
    """Call only Ollama's render-only chat path; the model must not be invoked."""
    normalized = _validate_render_only_payload(payload)
    body = _post_json(
        host=_OLLAMA_HOST,
        port=_OLLAMA_PORT,
        path=_CHAT_PATH,
        payload=normalized,
        timeout_seconds=timeout_seconds,
        connection_factory=connection_factory,
    )
    return parse_ollama_render_only_response(body)


def fetch_llama_token_count(
    payload: Mapping[str, object],
    *,
    tokenizer_port: int,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    connection_factory: _ConnectionFactory | None = None,
) -> int:
    """Call only a loopback llama-server /tokenize endpoint."""
    normalized = _validate_tokenize_payload(payload)
    if type(tokenizer_port) is not int or not 1024 <= tokenizer_port <= 65535:
        raise ValueError("tokenizer_port must be an unprivileged TCP port")
    body = _post_json(
        host=_OLLAMA_HOST,
        port=tokenizer_port,
        path=_LLAMA_TOKENIZE_PATH,
        payload=normalized,
        timeout_seconds=timeout_seconds,
        connection_factory=connection_factory,
    )
    return parse_llama_tokenize_response(body)


def fetch_ollama_runtime_identity(
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    connection_factory: _ConnectionFactory | None = None,
) -> dict[str, object]:
    """Read and validate local Ollama version/model identity without inference."""
    version_body = _request_json(
        host=_OLLAMA_HOST,
        port=_OLLAMA_PORT,
        method="GET",
        path=_VERSION_PATH,
        payload=None,
        timeout_seconds=timeout_seconds,
        connection_factory=connection_factory,
    )
    version = _json_response_mapping(version_body, field_name="version response")
    if version.get("version") != _OLLAMA_VERSION:
        raise ValueError("Ollama runtime version drift")

    tags_body = _request_json(
        host=_OLLAMA_HOST,
        port=_OLLAMA_PORT,
        method="GET",
        path=_TAGS_PATH,
        payload=None,
        timeout_seconds=timeout_seconds,
        connection_factory=connection_factory,
    )
    tags = _json_response_mapping(tags_body, field_name="tags response")
    models = tags.get("models")
    if not isinstance(models, list):
        raise ValueError("tags response models must be a list")

    selected: Mapping[object, object] | None = None
    for item in cast(list[object], models):
        if isinstance(item, Mapping):
            item_map = cast(Mapping[object, object], item)
            if item_map.get("name") == _MODEL_IDENTIFIER:
                if selected is not None:
                    raise ValueError("duplicate pinned Ollama model identity")
                selected = item_map
    if selected is None:
        raise ValueError("pinned Ollama model is not installed")
    if selected.get("digest") != _MODEL_VERSION:
        raise ValueError("pinned Ollama model digest drift")
    if selected.get("size") != _MODEL_SIZE_BYTES:
        raise ValueError("pinned Ollama model size drift")
    details = selected.get("details")
    if not isinstance(details, Mapping):
        raise ValueError("pinned Ollama model details missing")
    detail_map = cast(Mapping[object, object], details)
    if detail_map.get("parameter_size") != _MODEL_PARAMETER_SIZE:
        raise ValueError("pinned Ollama parameter size drift")
    if detail_map.get("quantization_level") != _MODEL_QUANTIZATION:
        raise ValueError("pinned Ollama quantization drift")

    show_body = _post_json(
        host=_OLLAMA_HOST,
        port=_OLLAMA_PORT,
        path=_SHOW_PATH,
        payload={"model": _MODEL_IDENTIFIER},
        timeout_seconds=timeout_seconds,
        connection_factory=connection_factory,
    )
    show = _json_response_mapping(show_body, field_name="show response")
    modelfile = show.get("modelfile")
    if not isinstance(modelfile, str):
        raise ValueError("show response modelfile missing")
    required_modelfile_fragments = (
        f"sha256-{_GGUF_SHA256}",
        "TEMPLATE {{ .Prompt }}",
        "RENDERER gemma4",
        "PARSER gemma4",
        "PARAMETER stop <turn|>",
    )
    for fragment in required_modelfile_fragments:
        if fragment not in modelfile:
            raise ValueError(f"pinned Ollama Modelfile drift: {fragment!r}")

    return {
        "ollama_version": _OLLAMA_VERSION,
        "model": _MODEL_IDENTIFIER,
        "model_digest": _MODEL_VERSION,
        "gguf_sha256": _GGUF_SHA256,
        "model_size_bytes": _MODEL_SIZE_BYTES,
        "architecture": _MODEL_ARCHITECTURE,
        "parameter_size": _MODEL_PARAMETER_SIZE,
        "quantization": _MODEL_QUANTIZATION,
    }


def _validate_reasoner_request(
    value: Mapping[str, object],
) -> dict[str, object]:
    request = _exact_mapping(
        value,
        expected_keys=_REASONER_REQUEST_KEYS,
        field_name="reasoner_request",
    )
    manifest = build_ollama_execution_manifest()
    configuration = _json_mapping_copy(
        request["model_configuration"],
        field_name="model_configuration",
    )
    if request["provider"] != _PROVIDER:
        raise ValueError("reasoner_request provider drift")
    if request["model_identifier"] != _MODEL_IDENTIFIER:
        raise ValueError("reasoner_request model_identifier drift")
    if request["model_version"] != _MODEL_VERSION:
        raise ValueError("reasoner_request model_version drift")
    if request["max_output_tokens"] != _MAX_OUTPUT_TOKENS:
        raise ValueError("reasoner_request max_output_tokens drift")
    if configuration != manifest["model_configuration"]:
        raise ValueError("reasoner_request model_configuration drift")
    return {
        "provider": _PROVIDER,
        "model_identifier": _MODEL_IDENTIFIER,
        "model_version": _MODEL_VERSION,
        "model_configuration": configuration,
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
        "messages": _messages_copy(request["messages"]),
    }


def _validate_render_only_payload(
    value: Mapping[str, object],
) -> dict[str, object]:
    expected = build_ollama_render_only_payload(
        {
            "provider": _PROVIDER,
            "model_identifier": _MODEL_IDENTIFIER,
            "model_version": _MODEL_VERSION,
            "model_configuration": _json_mapping_copy(_PROVIDER_CONFIGURATION),
            "max_output_tokens": _MAX_OUTPUT_TOKENS,
            "messages": _messages_copy(value.get("messages")),
        }
    )
    normalized = _json_mapping_copy(value)
    if normalized != expected:
        raise ValueError("render-only payload drift")
    return normalized


def _validate_tokenize_payload(
    value: Mapping[str, object],
) -> dict[str, object]:
    mapping = _exact_mapping(
        value,
        expected_keys=frozenset(
            {"content", "add_special", "parse_special", "with_pieces"}
        ),
        field_name="tokenize_payload",
    )
    content = _bounded_text(
        mapping["content"],
        field_name="content",
        max_chars=8 * 1024 * 1024,
    )
    if mapping["add_special"] is not False:
        raise ValueError("tokenize add_special drift")
    if mapping["parse_special"] is not True:
        raise ValueError("tokenize parse_special drift")
    if mapping["with_pieces"] is not False:
        raise ValueError("tokenize with_pieces drift")
    return {
        "content": content,
        "add_special": False,
        "parse_special": True,
        "with_pieces": False,
    }


def _post_json(
    *,
    host: str,
    port: int,
    path: str,
    payload: Mapping[str, object],
    timeout_seconds: float,
    connection_factory: _ConnectionFactory | None,
) -> bytes:
    return _request_json(
        host=host,
        port=port,
        method="POST",
        path=path,
        payload=payload,
        timeout_seconds=timeout_seconds,
        connection_factory=connection_factory,
    )


def _request_json(
    *,
    host: str,
    port: int,
    method: str,
    path: str,
    payload: Mapping[str, object] | None,
    timeout_seconds: float,
    connection_factory: _ConnectionFactory | None,
) -> bytes:
    if host != _OLLAMA_HOST:
        raise ValueError("local execution transport must remain loopback-only")
    timeout = _validate_timeout(timeout_seconds)
    factory = connection_factory or _open_http_connection
    connection = factory(host, port, timeout)
    body = None if payload is None else _canonical_json_bytes(payload)
    headers: Mapping[str, str] = (
        {} if payload is None else {"Content-Type": "application/json"}
    )
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read(_MAX_RESPONSE_BYTES + 1)
        if response.status < 200 or response.status >= 300:
            raise ValueError(
                f"local execution endpoint returned HTTP {response.status}"
            )
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise ValueError("local execution response exceeded size bound")
        return response_body
    finally:
        connection.close()


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout_seconds must be a finite positive number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0.0 or timeout > 300.0:
        raise ValueError("timeout_seconds must be finite and in (0, 300]")
    return timeout


def _open_http_connection(host: str, port: int, timeout: float) -> _HTTPConnection:
    return cast(
        _HTTPConnection,
        http.client.HTTPConnection(host, port=port, timeout=timeout),
    )


def _messages_copy(value: object) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("messages must be a sequence")
    raw = cast(Sequence[object], value)
    if len(raw) != 2:
        raise ValueError("messages must contain exactly system and user inputs")
    expected_roles = ("system", "user")
    result: list[dict[str, str]] = []
    for index, expected_role in enumerate(expected_roles):
        item = raw[index]
        if not isinstance(item, Mapping):
            raise ValueError("message must be a mapping")
        mapping = cast(Mapping[object, object], item)
        if set(mapping) != {"role", "content"}:
            raise ValueError("message keys must be exactly role/content")
        if mapping.get("role") != expected_role:
            raise ValueError("message role/order drift")
        content = _bounded_text(
            mapping.get("content"),
            field_name=f"messages[{index}].content",
            max_chars=4 * 1024 * 1024,
        )
        result.append({"role": expected_role, "content": content})
    return result


def _json_response_mapping(body: bytes, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(body, bytes) or not body or len(body) > _MAX_RESPONSE_BYTES:
        raise ValueError(f"{field_name} body size is invalid")
    try:
        decoded: object = json.loads(
            body.decode("utf-8"),
            parse_constant=lambda value: _raise_nonstandard_json(value),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} must be valid UTF-8 JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return cast(Mapping[str, object], decoded)


def _exact_mapping(
    value: object,
    *,
    expected_keys: frozenset[str],
    field_name: str,
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


def _bounded_text(value: object, *, field_name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty text")
    if len(value) > max_chars or "\x00" in value:
        raise ValueError(f"{field_name} is invalid or exceeds size bound")
    return value


def _json_copy(value: object) -> object:
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
        raise ValueError("value must be finite JSON-compatible data") from exc


def _json_mapping_copy(
    value: object,
    *,
    field_name: str = "value",
) -> dict[str, object]:
    copied = _json_copy(value)
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


def _raise_nonstandard_json(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")
