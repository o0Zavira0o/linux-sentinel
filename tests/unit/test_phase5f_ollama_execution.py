from __future__ import annotations

import copy
import inspect
import json
import unittest
from collections.abc import Mapping
from typing import cast

import sentinel_x._phase5f.ollama_execution as ollama_execution_module
from sentinel_x._phase5f.execution import (
    build_reasoner_request,
    execution_manifest_sha256,
)
from sentinel_x._phase5f.ollama_execution import (
    build_llama_tokenize_payload,
    build_ollama_chat_payload,
    build_ollama_execution_manifest,
    build_ollama_render_only_payload,
    fetch_llama_token_count,
    fetch_ollama_rendered_prompt,
    fetch_ollama_runtime_identity,
    ollama_payload_sha256,
    parse_llama_tokenize_response,
    parse_ollama_render_only_response,
    repeat_seed,
)


class _FakeResponse:
    def __init__(self, *, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            return self._body
        return self._body[:amt]


class _FakeConnection:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, bytes | None, Mapping[str, str] | None]] = []
        self.closed = False

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.calls.append((method, url, body, headers))

    def getresponse(self) -> _FakeResponse:
        if not self.responses:
            raise AssertionError("unexpected extra response request")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class Phase5FOllamaExecutionTests(unittest.TestCase):
    def _bundle(self) -> dict[str, object]:
        return {
            "schema_version": "sentinel-x.phase5f-evidence-bundle.v1",
            "case_id": "CASE-0001",
            "task": "Classify the operational evidence conservatively.",
            "environment": {"boot_id": "a" * 32},
            "evidence": [
                {
                    "ref": "REF-0001",
                    "category": "service_state",
                    "payload": {"active_state": "failed"},
                }
            ],
        }

    def _manifest(self) -> dict[str, object]:
        return build_ollama_execution_manifest()

    def _request(self) -> dict[str, object]:
        return build_reasoner_request(self._bundle(), self._manifest())

    def test_manifest_pins_local_runtime_model_and_weight_identity(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest["provider"], "ollama-local")
        self.assertEqual(
            manifest["model_identifier"],
            "sentinelx-gemma4-12b-qat:preflight",
        )
        self.assertEqual(
            manifest["model_version"],
            "be1d79d105352d8cb0a25ee03f1f315935cc93fb4f0674422c2bb13be72fc025",
        )
        self.assertEqual(manifest["context_window_tokens"], 49152)
        self.assertEqual(manifest["max_output_tokens"], 2048)
        self.assertEqual(manifest["token_safety_margin"], 1024)

    def test_manifest_pins_no_cloud_tools_web_thinking_or_truncation(self) -> None:
        configuration = cast(
            Mapping[str, object],
            self._manifest()["model_configuration"],
        )
        self.assertFalse(self._manifest()["tools_enabled"])
        self.assertFalse(self._manifest()["web_enabled"])
        self.assertTrue(self._manifest()["fresh_context"])
        self.assertFalse(configuration["think"])
        self.assertFalse(configuration["stream"])
        self.assertFalse(configuration["truncate"])
        self.assertFalse(configuration["shift"])
        self.assertEqual(configuration["format"], "omitted")
        self.assertEqual(configuration["tools"], "omitted")
        service = cast(Mapping[str, object], configuration["service"])
        self.assertEqual(service["host"], "127.0.0.1:11434")
        self.assertTrue(service["no_cloud"])
        self.assertEqual(service["num_parallel"], 1)
        self.assertEqual(service["max_loaded_models"], 1)

    def test_manifest_pins_model_renderer_parser_and_sampler(self) -> None:
        configuration = cast(
            Mapping[str, object],
            self._manifest()["model_configuration"],
        )
        self.assertEqual(configuration["renderer"], "gemma4")
        self.assertEqual(configuration["parser"], "gemma4")
        self.assertEqual(configuration["template"], "{{ .Prompt }}")
        self.assertEqual(configuration["stop"], ["<turn|>"])
        self.assertEqual(
            configuration["gguf_sha256"],
            "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b",
        )
        sampling = cast(Mapping[str, object], configuration["sampling"])
        self.assertEqual(
            sampling,
            {"temperature": 1.0, "top_p": 0.95, "top_k": 64},
        )

    def test_manifest_digest_is_deterministic_and_mutation_safe(self) -> None:
        first = self._manifest()
        second = self._manifest()
        self.assertEqual(
            execution_manifest_sha256(first),
            execution_manifest_sha256(second),
        )
        configuration = cast(dict[str, object], first["model_configuration"])
        configuration["think"] = True
        second_configuration = cast(
            Mapping[str, object],
            second["model_configuration"],
        )
        self.assertFalse(second_configuration["think"])

    def test_repeat_seeds_are_exact_distinct_and_bounded(self) -> None:
        seeds = [repeat_seed(index) for index in (1, 2, 3)]
        self.assertEqual(seeds, [1729, 3253, 7919])
        self.assertEqual(len(set(seeds)), 3)
        for bad in (0, 4, True, 1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    repeat_seed(cast(int, bad))

    def test_chat_payload_is_exact_stateless_no_tool_shape(self) -> None:
        payload = build_ollama_chat_payload(self._request(), repeat_index=2)
        self.assertEqual(
            set(payload),
            {
                "model",
                "messages",
                "stream",
                "think",
                "truncate",
                "shift",
                "keep_alive",
                "options",
            },
        )
        self.assertEqual(
            payload["model"],
            "sentinelx-gemma4-12b-qat:preflight",
        )
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertFalse(payload["truncate"])
        self.assertFalse(payload["shift"])
        self.assertEqual(payload["keep_alive"], 0)
        options = cast(Mapping[str, object], payload["options"])
        self.assertEqual(options["num_ctx"], 49152)
        self.assertEqual(options["num_predict"], 2048)
        self.assertEqual(options["seed"], 3253)
        self.assertEqual(options["temperature"], 1.0)
        self.assertEqual(options["top_p"], 0.95)
        self.assertEqual(options["top_k"], 64)

    def test_chat_payload_contains_no_condition_repeat_gold_or_schema_repair(
        self,
    ) -> None:
        serialized = json.dumps(
            build_ollama_chat_payload(self._request(), repeat_index=1),
            sort_keys=True,
        )
        for forbidden in (
            '"condition"',
            '"repeat_index"',
            '"gold_label"',
            '"expected_outcome"',
            '"tools"',
            '"format"',
            '"web_search"',
            '"conversation"',
            '"previous_response_id"',
            '"response_format"',
            '"json_schema"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_chat_payload_rejects_reasoner_identity_or_config_drift(self) -> None:
        for field, value in (
            ("provider", "other"),
            ("model_identifier", "other"),
            ("model_version", "other"),
            ("max_output_tokens", 1),
        ):
            request = copy.deepcopy(self._request())
            request[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    build_ollama_chat_payload(request, repeat_index=1)
        request = copy.deepcopy(self._request())
        configuration = cast(dict[str, object], request["model_configuration"])
        configuration["think"] = True
        with self.assertRaises(ValueError):
            build_ollama_chat_payload(request, repeat_index=1)

    def test_render_only_payload_is_non_score_bearing_derivation(self) -> None:
        payload = build_ollama_render_only_payload(self._request())
        self.assertTrue(payload["_debug_render_only"])
        self.assertFalse(payload["think"])
        self.assertFalse(payload["truncate"])
        self.assertFalse(payload["shift"])
        self.assertNotIn("num_predict", cast(Mapping[str, object], payload["options"]))
        self.assertNotIn("seed", cast(Mapping[str, object], payload["options"]))

    def test_render_response_accepts_template_and_rejects_model_output(self) -> None:
        valid = {
            "message": {"role": "assistant", "content": ""},
            "_debug_info": {"rendered_template": "<bos>rendered"},
        }
        self.assertEqual(
            parse_ollama_render_only_response(json.dumps(valid).encode()),
            "<bos>rendered",
        )
        invalid = copy.deepcopy(valid)
        cast(dict[str, object], invalid["message"])["content"] = "generated"
        with self.assertRaises(ValueError):
            parse_ollama_render_only_response(json.dumps(invalid).encode())

    def test_tokenize_payload_pins_special_token_semantics(self) -> None:
        payload = build_llama_tokenize_payload("<bos>rendered")
        self.assertEqual(
            payload,
            {
                "content": "<bos>rendered",
                "add_special": False,
                "parse_special": True,
                "with_pieces": False,
            },
        )

    def test_tokenize_response_counts_integer_tokens_only(self) -> None:
        self.assertEqual(
            parse_llama_tokenize_response(b'{"tokens":[2,10,11,12]}'),
            4,
        )
        for body in (
            b'{"tokens":[]}',
            b'{"tokens":[2,true]}',
            b'{"tokens":["2"]}',
            b'{"pieces":[]}',
        ):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    parse_llama_tokenize_response(body)

    def test_render_fetch_posts_only_debug_render_request_to_loopback(self) -> None:
        response = _FakeResponse(
            status=200,
            body=json.dumps(
                {
                    "message": {"role": "assistant", "content": ""},
                    "_debug_info": {"rendered_template": "<bos>rendered"},
                }
            ).encode(),
        )
        connection = _FakeConnection([response])
        seen: list[tuple[str, int, float]] = []

        def factory(host: str, port: int, timeout: float) -> _FakeConnection:
            seen.append((host, port, timeout))
            return connection

        prompt = fetch_ollama_rendered_prompt(
            build_ollama_render_only_payload(self._request()),
            connection_factory=factory,
        )
        self.assertEqual(prompt, "<bos>rendered")
        self.assertEqual(seen, [("127.0.0.1", 11434, 30.0)])
        self.assertEqual(connection.calls[0][0:2], ("POST", "/api/chat"))
        sent = json.loads(cast(bytes, connection.calls[0][2]).decode())
        self.assertTrue(sent["_debug_render_only"])
        self.assertTrue(connection.closed)

    def test_token_fetch_posts_only_to_loopback_llama_tokenize(self) -> None:
        connection = _FakeConnection(
            [_FakeResponse(status=200, body=b'{"tokens":[2,5,9]}')]
        )
        seen: list[tuple[str, int, float]] = []

        def factory(host: str, port: int, timeout: float) -> _FakeConnection:
            seen.append((host, port, timeout))
            return connection

        count = fetch_llama_token_count(
            build_llama_tokenize_payload("<bos>rendered"),
            tokenizer_port=18080,
            connection_factory=factory,
        )
        self.assertEqual(count, 3)
        self.assertEqual(seen, [("127.0.0.1", 18080, 30.0)])
        self.assertEqual(connection.calls[0][0:2], ("POST", "/tokenize"))
        self.assertTrue(connection.closed)

    def test_runtime_identity_reads_version_tags_show_without_inference(self) -> None:
        version = b'{"version":"0.32.13"}'
        tags = json.dumps(
            {
                "models": [
                    {
                        "name": "sentinelx-gemma4-12b-qat:preflight",
                        "digest": (
                            "be1d79d105352d8cb0a25ee03f1f3159"
                            "35cc93fb4f0674422c2bb13be72fc025"
                        ),
                        "size": 6975879517,
                        "details": {
                            "parameter_size": "11.9B",
                            "quantization_level": "Q4_0",
                        },
                    }
                ]
            }
        ).encode()
        show = json.dumps(
            {
                "modelfile": (
                    "FROM /models/sha256-"
                    "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b\n"
                    "TEMPLATE {{ .Prompt }}\n"
                    "RENDERER gemma4\n"
                    "PARSER gemma4\n"
                    "PARAMETER stop <turn|>\n"
                )
            }
        ).encode()
        connection = _FakeConnection(
            [
                _FakeResponse(status=200, body=version),
                _FakeResponse(status=200, body=tags),
                _FakeResponse(status=200, body=show),
            ]
        )

        def factory(host: str, port: int, timeout: float) -> _FakeConnection:
            return connection

        identity = fetch_ollama_runtime_identity(connection_factory=factory)
        self.assertEqual(identity["ollama_version"], "0.32.13")
        self.assertEqual(
            identity["gguf_sha256"],
            "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b",
        )
        self.assertEqual(
            [(call[0], call[1]) for call in connection.calls],
            [
                ("GET", "/api/version"),
                ("GET", "/api/tags"),
                ("POST", "/api/show"),
            ],
        )
        self.assertEqual(
            connection.calls[0][3],
            {},
        )
        self.assertEqual(
            connection.calls[1][3],
            {},
        )
        self.assertEqual(
            connection.calls[2][3],
            {"Content-Type": "application/json"},
        )

    def test_transport_failure_surfaces_without_automatic_retry(self) -> None:
        connection = _FakeConnection(
            [_FakeResponse(status=500, body=b'{"error":"boom"}')]
        )
        calls = 0

        def factory(host: str, port: int, timeout: float) -> _FakeConnection:
            nonlocal calls
            calls += 1
            return connection

        with self.assertRaisesRegex(ValueError, "HTTP 500"):
            fetch_ollama_rendered_prompt(
                build_ollama_render_only_payload(self._request()),
                connection_factory=factory,
            )
        self.assertEqual(calls, 1)
        self.assertEqual(len(connection.calls), 1)

    def test_payload_digest_is_canonical_and_input_sensitive(self) -> None:
        first = build_ollama_render_only_payload(self._request())
        second = copy.deepcopy(first)
        self.assertEqual(
            ollama_payload_sha256(first),
            ollama_payload_sha256(second),
        )
        messages = cast(list[dict[str, str]], second["messages"])
        messages[1]["content"] += " changed"
        self.assertNotEqual(
            ollama_payload_sha256(first),
            ollama_payload_sha256(second),
        )

    def test_module_has_no_scoring_gold_sdk_or_generation_transport(self) -> None:
        source = inspect.getsource(ollama_execution_module)
        self.assertNotIn("._phase5f.scoring", source)
        self.assertNotIn("._phase5f.gold", source)
        self.assertNotIn("import ollama", source)
        self.assertNotIn("from ollama", source)
        self.assertNotIn("parse_reasoner_output", source)
        self.assertNotIn(
            "path=_CHAT_PATH,\n        payload=build_ollama_chat_payload",
            source,
        )


if __name__ == "__main__":
    unittest.main()
