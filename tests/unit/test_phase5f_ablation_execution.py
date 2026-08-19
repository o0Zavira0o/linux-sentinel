from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from unittest.mock import patch

import sentinel_x._phase5f.ablation_execution as execution_module
from sentinel_x._phase5f.ablation_execution import (
    external_executor_semantics,
    external_executor_semantics_sha256,
    execute_frozen_ablation_capture,
    load_frozen_ablation_preregistration,
    validate_ablation_execution_contract,
)
from sentinel_x._phase5f.execution import build_reasoner_request
from sentinel_x._phase5f.ollama_execution import (
    build_ollama_chat_payload,
    build_ollama_execution_manifest,
    ollama_payload_sha256,
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
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
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
        return self.response

    def close(self) -> None:
        self.closed = True


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


class Phase5FAblationExecutionTests(unittest.TestCase):
    def _bundle(self, case_id: str, marker: int) -> dict[str, object]:
        return {
            "schema_version": "sentinel-x.phase5f-evidence-bundle.v1",
            "case_id": case_id,
            "task": "Classify the operational evidence conservatively.",
            "environment": {"boot_id": "a" * 32},
            "evidence": [
                {
                    "ref": "REF-0001",
                    "category": "service_state",
                    "payload": {"active_state": "failed", "marker": marker},
                }
            ],
        }

    def _synthetic_contract(
        self,
    ) -> tuple[
        dict[str, object],
        list[dict[str, object]],
        dict[str, object],
        str,
        str,
    ]:
        semantics: dict[str, object] = {
            "schema": "sentinel-x.phase5f6-ablation-score-execution-semantics.v1",
            "status": "FROZEN_PRE_SCORE_ABLATION_EXECUTION_SEMANTICS",
            "ablation_attempt_count": 756,
            "unique_ablation_request_count": 252,
            "case_count": 36,
            "mapping_count": 7,
            "execution_order_seed": (
                "sentinel-x.phase5f6-ablation-score-execution-order.v1"
            ),
            "execution_manifest_sha256": (
                "e0da42efc5e67fc3d86b434a99e3e46dda257ba1a8b049c2adbb408e79582ee6"
            ),
            "frozen_base_324_plan_file_sha256": (
                "61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80"
            ),
            "frozen_base_324_plan_semantic_sha256": (
                "0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542"
            ),
            "transport_retry_limit": 1,
            "fresh_request_per_attempt": True,
            "reuse_base_outputs_for_unchanged_ablation_views": False,
            "condition_blinding": True,
            "mapping_identity_sent_to_reasoner": False,
            "repeat_identity_sent_to_reasoner": False,
            "hidden_gold_reasoner_access": False,
            "score_during_capture": False,
            "scored_execution_authorized": False,
            "tools_enabled": False,
            "web_enabled": False,
            "full_vs_minimal_extra_ablation_execution": False,
            "repeat_indices": [1, 2, 3],
            "repeat_seeds": {"1": 1729, "2": 3253, "3": 7919},
            "retry_semantics": (
                "at most one retry of the exact same chat payload on "
                "transport/provider failure only"
            ),
        }
        semantics_sha = hashlib.sha256(_canonical_bytes(semantics)).hexdigest()

        mapping_conditions = {
            "minimal-minus-boot-provenance": "minimal",
            "minimal-minus-counterevidence": "minimal",
            "minimal-minus-coverage": "minimal",
            "minimal-minus-exact-timestamp-basis": "minimal",
            "minimal-minus-intervention-metadata": "minimal",
            "minimal-minus-topology": "minimal",
            "full-minus-current-derived-synthesis-interpretation": "full",
        }
        manifest = build_ollama_execution_manifest()
        requests: list[dict[str, object]] = []
        request_index: dict[tuple[str, str], dict[str, object]] = {}
        marker = 0
        for case_index in range(1, 37):
            case_id = f"CASE-{case_index:04d}"
            for mapping_id, condition in mapping_conditions.items():
                marker += 1
                reasoner_request = build_reasoner_request(
                    self._bundle(case_id, marker),
                    manifest,
                )
                semantic_sha = hashlib.sha256(
                    _canonical_bytes(reasoner_request)
                ).hexdigest()
                row = {
                    "schema": "sentinel-x.phase5f6-ablation-score-request.v1",
                    "case_id": case_id,
                    "mapping_id": mapping_id,
                    "base_condition": condition,
                    "transformed_bundle_sha256": hashlib.sha256(
                        f"bundle:{case_id}:{mapping_id}".encode()
                    ).hexdigest(),
                    "semantic_request_sha256": semantic_sha,
                    "input_token_count": 1000 + marker,
                    "reasoner_request": reasoner_request,
                }
                requests.append(row)
                request_index[(case_id, mapping_id)] = row

        attempts: list[dict[str, object]] = []
        ordinal = 0
        for case_index in range(1, 37):
            case_id = f"CASE-{case_index:04d}"
            for mapping_id, condition in mapping_conditions.items():
                row = request_index[(case_id, mapping_id)]
                for repeat_index, seed in ((1, 1729), (2, 3253), (3, 7919)):
                    ordinal += 1
                    payload = build_ollama_chat_payload(
                        cast(Mapping[str, object], row["reasoner_request"]),
                        repeat_index=repeat_index,
                    )
                    attempts.append(
                        {
                            "ordinal": ordinal,
                            "attempt_id": f"ABL-{ordinal:04d}",
                            "case_id": case_id,
                            "mapping_id": mapping_id,
                            "base_condition": condition,
                            "repeat_index": repeat_index,
                            "seed": seed,
                            "input_token_count": row["input_token_count"],
                            "semantic_request_sha256": row["semantic_request_sha256"],
                            "chat_payload_sha256": ollama_payload_sha256(payload),
                        }
                    )

        request_bytes = b"".join(_canonical_bytes(row) + b"\n" for row in requests)
        request_sha = hashlib.sha256(request_bytes).hexdigest()
        plan_without_sha: dict[str, object] = {
            "schema": "sentinel-x.phase5f6-ablation-score-execution-plan.v1",
            "execution_semantics_sha256": semantics_sha,
            "request_catalog_sha256": request_sha,
            "execution_manifest_sha256": (
                "e0da42efc5e67fc3d86b434a99e3e46dda257ba1a8b049c2adbb408e79582ee6"
            ),
            "execution_order_seed": (
                "sentinel-x.phase5f6-ablation-score-execution-order.v1"
            ),
            "repeat_seeds": {"1": 1729, "2": 3253, "3": 7919},
            "mapping_count": 7,
            "case_count": 36,
            "repeats": [1, 2, 3],
            "transport_retry_limit": 1,
            "attempt_count": 756,
            "attempts": attempts,
        }
        plan_sha = hashlib.sha256(_canonical_bytes(plan_without_sha)).hexdigest()
        plan = {**plan_without_sha, "execution_plan_sha256": plan_sha}
        return semantics, requests, plan, semantics_sha, request_sha

    def _contract_patch(self, semantics_sha: str, request_sha: str, plan_sha: str):
        return patch.multiple(
            execution_module,
            ABLATION_EXECUTION_SEMANTICS_SHA256=semantics_sha,
            ABLATION_REQUEST_CATALOG_SHA256=request_sha,
            ABLATION_EXECUTION_PLAN_SHA256=plan_sha,
        )

    def _freeze_synthetic_prereg(self, root: Path) -> None:
        for name in (
            "EXECUTION_SEMANTICS.json",
            "REQUESTS.jsonl",
            "PLAN.json",
            "RECEIPT.txt",
        ):
            (root / name).chmod(0o400)
        root.chmod(0o500)

    def _valid_chat_body(
        self,
        *,
        content: str = '{"classification":"INSUFFICIENT"}',
        done_reason: str = "stop",
    ) -> bytes:
        return json.dumps(
            {
                "model": "sentinelx-gemma4-12b-qat:preflight",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": done_reason,
            }
        ).encode()

    def test_executor_semantics_are_private_capture_only_and_unscored(self) -> None:
        semantics = external_executor_semantics()
        self.assertEqual(semantics["attempt_count"], 756)
        self.assertEqual(semantics["request_count"], 252)
        capture = cast(Mapping[str, object], semantics["capture"])
        self.assertFalse(capture["parse_structured_model_output"])
        self.assertFalse(capture["score_during_capture"])
        transport = cast(Mapping[str, object], semantics["transport"])
        self.assertEqual(transport["path"], "/api/chat")
        self.assertEqual(transport["retry_limit"], 1)
        self.assertFalse(transport["semantic_output_retry"])

    def test_executor_semantics_sha_is_deterministic(self) -> None:
        first = external_executor_semantics_sha256()
        second = external_executor_semantics_sha256()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_contract_accepts_exact_synthetic_36_by_7_by_3_matrix(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        with self._contract_patch(semantics_sha, request_sha, plan_sha):
            validate_ablation_execution_contract(semantics, requests, plan)

    def test_contract_rejects_semantics_drift(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        drifted = copy.deepcopy(semantics)
        drifted["score_during_capture"] = True
        with self._contract_patch(semantics_sha, request_sha, plan_sha):
            with self.assertRaises(ValueError):
                validate_ablation_execution_contract(drifted, requests, plan)

    def test_request_catalog_rejects_mapping_condition_drift(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        drifted = copy.deepcopy(requests)
        drifted[0]["base_condition"] = "full"
        with self._contract_patch(semantics_sha, request_sha, plan_sha):
            with self.assertRaises(ValueError):
                validate_ablation_execution_contract(semantics, drifted, plan)

    def test_request_catalog_rejects_reasoner_request_digest_drift(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        drifted = copy.deepcopy(requests)
        drifted[0]["semantic_request_sha256"] = "0" * 64
        with self._contract_patch(semantics_sha, request_sha, plan_sha):
            with self.assertRaises(ValueError):
                validate_ablation_execution_contract(semantics, drifted, plan)

    def test_plan_rejects_repeat_seed_drift(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        drifted = copy.deepcopy(plan)
        attempts = cast(list[dict[str, object]], drifted["attempts"])
        attempts[0]["seed"] = 999
        with self._contract_patch(semantics_sha, request_sha, plan_sha):
            with self.assertRaises(ValueError):
                validate_ablation_execution_contract(semantics, requests, drifted)

    def test_plan_rejects_chat_payload_digest_drift(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        drifted = copy.deepcopy(plan)
        attempts = cast(list[dict[str, object]], drifted["attempts"])
        attempts[0]["chat_payload_sha256"] = "0" * 64
        with self._contract_patch(semantics_sha, request_sha, plan_sha):
            with self.assertRaises(ValueError):
                validate_ablation_execution_contract(semantics, requests, drifted)

    def test_plan_rejects_order_or_ordinal_drift(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        drifted = copy.deepcopy(plan)
        attempts = cast(list[dict[str, object]], drifted["attempts"])
        attempts[0], attempts[1] = attempts[1], attempts[0]
        with self._contract_patch(semantics_sha, request_sha, plan_sha):
            with self.assertRaises(ValueError):
                validate_ablation_execution_contract(semantics, requests, drifted)

    def test_authorization_gate_blocks_before_prereg_or_provider_access(self) -> None:
        with patch.object(
            execution_module,
            "load_frozen_ablation_preregistration",
        ) as loader:
            with self.assertRaises(ValueError):
                execute_frozen_ablation_capture(
                    Path("/does/not/matter"),
                    Path("/does/not/matter/either"),
                )
        loader.assert_not_called()

    def test_single_attempt_posts_exact_loopback_chat_payload_and_captures_raw(
        self,
    ) -> None:
        semantics, requests, plan, _, _ = self._synthetic_contract()
        del semantics
        attempt = cast(list[dict[str, object]], plan["attempts"])[0]
        request = requests[0]
        body = self._valid_chat_body(content="not-valid-structured-json")
        connections: list[_FakeConnection] = []

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            self.assertEqual((host, port, timeout), ("127.0.0.1", 11434, 30.0))
            connection = _FakeConnection(_FakeResponse(status=200, body=body))
            connections.append(connection)
            return cast(http.client.HTTPConnection, connection)

        capture = execution_module._execute_attempt(
            attempt,
            request_row=request,
            timeout_seconds=30.0,
            connection_factory=factory,
        )
        self.assertEqual(capture["transport_attempts"], 1)
        self.assertEqual(capture["assistant_content"], "not-valid-structured-json")
        self.assertEqual(
            base64.b64decode(cast(str, capture["response_body_base64"])),
            body,
        )
        self.assertEqual(len(connections), 1)
        call = connections[0].calls[0]
        self.assertEqual(call[0:2], ("POST", "/api/chat"))
        self.assertEqual(call[3], {"Content-Type": "application/json"})
        sent = json.loads(cast(bytes, call[2]).decode())
        self.assertNotIn("mapping_id", sent)
        self.assertNotIn("repeat_index", sent)
        self.assertTrue(connections[0].closed)

    def test_transport_failure_retries_once_with_exact_same_payload(self) -> None:
        _, requests, plan, _, _ = self._synthetic_contract()
        attempt = cast(list[dict[str, object]], plan["attempts"])[0]
        responses = [
            _FakeResponse(status=500, body=b'{"error":"boom"}'),
            _FakeResponse(status=200, body=self._valid_chat_body()),
        ]
        connections: list[_FakeConnection] = []

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            del host, port, timeout
            connection = _FakeConnection(responses.pop(0))
            connections.append(connection)
            return cast(http.client.HTTPConnection, connection)

        capture = execution_module._execute_attempt(
            attempt,
            request_row=requests[0],
            timeout_seconds=30.0,
            connection_factory=factory,
        )
        self.assertEqual(capture["transport_attempts"], 2)
        self.assertEqual(len(connections), 2)
        self.assertEqual(connections[0].calls[0][2], connections[1].calls[0][2])

    def test_second_transport_failure_stops_without_third_request(self) -> None:
        _, requests, plan, _, _ = self._synthetic_contract()
        attempt = cast(list[dict[str, object]], plan["attempts"])[0]
        responses = [
            _FakeResponse(status=500, body=b"{}"),
            _FakeResponse(status=503, body=b"{}"),
        ]
        calls = 0

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            nonlocal calls
            del host, port, timeout
            calls += 1
            return cast(
                http.client.HTTPConnection,
                _FakeConnection(responses.pop(0)),
            )

        with self.assertRaises(execution_module._TransportFailure):
            execution_module._execute_attempt(
                attempt,
                request_row=requests[0],
                timeout_seconds=30.0,
                connection_factory=factory,
            )
        self.assertEqual(calls, 2)

    def test_invalid_provider_envelope_is_retryable(self) -> None:
        _, requests, plan, _, _ = self._synthetic_contract()
        attempt = cast(list[dict[str, object]], plan["attempts"])[0]
        responses = [
            _FakeResponse(status=200, body=b'{"done":true}'),
            _FakeResponse(status=200, body=self._valid_chat_body()),
        ]
        calls = 0

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            nonlocal calls
            del host, port, timeout
            calls += 1
            return cast(
                http.client.HTTPConnection,
                _FakeConnection(responses.pop(0)),
            )

        capture = execution_module._execute_attempt(
            attempt,
            request_row=requests[0],
            timeout_seconds=30.0,
            connection_factory=factory,
        )
        self.assertEqual(capture["transport_attempts"], 2)
        self.assertEqual(calls, 2)

    def test_invalid_structured_model_output_is_not_retried_or_repaired(self) -> None:
        _, requests, plan, _, _ = self._synthetic_contract()
        attempt = cast(list[dict[str, object]], plan["attempts"])[0]
        body = self._valid_chat_body(content="```json\nnot valid\n```")
        calls = 0

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            nonlocal calls
            del host, port, timeout
            calls += 1
            return cast(
                http.client.HTTPConnection,
                _FakeConnection(_FakeResponse(status=200, body=body)),
            )

        capture = execution_module._execute_attempt(
            attempt,
            request_row=requests[0],
            timeout_seconds=30.0,
            connection_factory=factory,
        )
        self.assertEqual(calls, 1)
        self.assertEqual(capture["assistant_content"], "```json\nnot valid\n```")

    def test_done_reason_length_is_captured_not_retried(self) -> None:
        _, requests, plan, _, _ = self._synthetic_contract()
        attempt = cast(list[dict[str, object]], plan["attempts"])[0]
        calls = 0

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            nonlocal calls
            del host, port, timeout
            calls += 1
            body = self._valid_chat_body(content="{", done_reason="length")
            return cast(
                http.client.HTTPConnection,
                _FakeConnection(_FakeResponse(status=200, body=body)),
            )

        capture = execution_module._execute_attempt(
            attempt,
            request_row=requests[0],
            timeout_seconds=30.0,
            connection_factory=factory,
        )
        self.assertEqual(calls, 1)
        self.assertEqual(capture["done_reason"], "length")
        self.assertEqual(capture["assistant_content"], "{")

    def test_unexpected_tool_calls_are_provider_envelope_failure(self) -> None:
        _, requests, plan, _, _ = self._synthetic_contract()
        attempt = cast(list[dict[str, object]], plan["attempts"])[0]
        invalid = json.dumps(
            {
                "model": "sentinelx-gemma4-12b-qat:preflight",
                "message": {
                    "role": "assistant",
                    "content": "{}",
                    "tool_calls": [{"function": {"name": "x"}}],
                },
                "done": True,
            }
        ).encode()
        responses = [
            _FakeResponse(status=200, body=invalid),
            _FakeResponse(status=200, body=self._valid_chat_body()),
        ]
        calls = 0

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            nonlocal calls
            del host, port, timeout
            calls += 1
            return cast(
                http.client.HTTPConnection,
                _FakeConnection(responses.pop(0)),
            )

        capture = execution_module._execute_attempt(
            attempt,
            request_row=requests[0],
            timeout_seconds=30.0,
            connection_factory=factory,
        )
        self.assertEqual(calls, 2)
        self.assertEqual(capture["transport_attempts"], 2)

    def test_module_does_not_import_scoring_gold_or_ablation_transform(self) -> None:
        source = Path(execution_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "from .scoring",
            "import scoring",
            "from .gold",
            "import gold",
            "from .ablation import",
            "score_attempt",
            "build_ablation_analysis_report",
        ):
            self.assertNotIn(forbidden, source)

    def test_executor_is_not_reexported_from_visible_phase5f_root(self) -> None:
        import sentinel_x._phase5f as phase5f

        for forbidden in (
            "execute_frozen_ablation_capture",
            "external_executor_semantics",
            "load_frozen_ablation_preregistration",
        ):
            self.assertFalse(hasattr(phase5f, forbidden))

    def test_load_preregistration_verifies_exact_file_bytes_and_contract(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        semantics_bytes = (
            json.dumps(semantics, indent=2, sort_keys=True).encode() + b"\n"
        )
        request_bytes = b"".join(_canonical_bytes(row) + b"\n" for row in requests)
        plan_bytes = json.dumps(plan, indent=2, sort_keys=True).encode() + b"\n"
        receipt_bytes = b"synthetic-receipt\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "prereg"
            root.mkdir()
            (root / "EXECUTION_SEMANTICS.json").write_bytes(semantics_bytes)
            (root / "REQUESTS.jsonl").write_bytes(request_bytes)
            (root / "PLAN.json").write_bytes(plan_bytes)
            (root / "RECEIPT.txt").write_bytes(receipt_bytes)
            self._freeze_synthetic_prereg(root)
            with patch.multiple(
                execution_module,
                ABLATION_EXECUTION_SEMANTICS_SHA256=semantics_sha,
                ABLATION_REQUEST_CATALOG_SHA256=request_sha,
                ABLATION_EXECUTION_PLAN_SHA256=plan_sha,
                ABLATION_EXECUTION_SEMANTICS_FILE_SHA256=hashlib.sha256(
                    semantics_bytes
                ).hexdigest(),
                ABLATION_EXECUTION_PLAN_FILE_SHA256=hashlib.sha256(
                    plan_bytes
                ).hexdigest(),
                ABLATION_RECEIPT_FILE_SHA256=hashlib.sha256(receipt_bytes).hexdigest(),
            ):
                loaded_semantics, loaded_requests, loaded_plan = (
                    load_frozen_ablation_preregistration(root)
                )
            self.assertEqual(loaded_semantics, semantics)
            self.assertEqual(len(loaded_requests), 252)
            self.assertEqual(loaded_plan, plan)

    def test_full_capture_writes_756_private_unscored_records_and_manifest(
        self,
    ) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        semantics_bytes = json.dumps(semantics, sort_keys=True).encode() + b"\n"
        request_bytes = b"".join(_canonical_bytes(row) + b"\n" for row in requests)
        plan_bytes = json.dumps(plan, sort_keys=True).encode() + b"\n"
        receipt_bytes = b"synthetic-receipt\n"
        response_body = self._valid_chat_body(content="not-structured-output")
        calls = 0

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            nonlocal calls
            self.assertEqual((host, port, timeout), ("127.0.0.1", 11434, 30.0))
            calls += 1
            return cast(
                http.client.HTTPConnection,
                _FakeConnection(_FakeResponse(status=200, body=response_body)),
            )

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            prereg = parent / "prereg"
            prereg.mkdir()
            (prereg / "EXECUTION_SEMANTICS.json").write_bytes(semantics_bytes)
            (prereg / "REQUESTS.jsonl").write_bytes(request_bytes)
            (prereg / "PLAN.json").write_bytes(plan_bytes)
            (prereg / "RECEIPT.txt").write_bytes(receipt_bytes)
            self._freeze_synthetic_prereg(prereg)
            destination = parent / "capture"
            with (
                patch.multiple(
                    execution_module,
                    ABLATION_EXECUTION_SEMANTICS_SHA256=semantics_sha,
                    ABLATION_REQUEST_CATALOG_SHA256=request_sha,
                    ABLATION_EXECUTION_PLAN_SHA256=plan_sha,
                    ABLATION_EXECUTION_SEMANTICS_FILE_SHA256=hashlib.sha256(
                        semantics_bytes
                    ).hexdigest(),
                    ABLATION_EXECUTION_PLAN_FILE_SHA256=hashlib.sha256(
                        plan_bytes
                    ).hexdigest(),
                    ABLATION_RECEIPT_FILE_SHA256=hashlib.sha256(
                        receipt_bytes
                    ).hexdigest(),
                ),
                patch.object(
                    execution_module,
                    "fetch_ollama_runtime_identity",
                    return_value={"ollama_version": "0.32.13", "model": "pinned"},
                ),
            ):
                manifest = execute_frozen_ablation_capture(
                    prereg,
                    destination,
                    scored_execution_authorized=True,
                    timeout_seconds=30.0,
                    connection_factory=factory,
                )
            self.assertEqual(calls, 756)
            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["attempt_count"], 756)
            self.assertFalse(manifest["score_during_capture"])
            self.assertFalse(manifest["structured_model_output_parsed"])
            rows = (destination / "RESPONSES.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 756)
            first = json.loads(rows[0])
            self.assertEqual(first["assistant_content"], "not-structured-output")
            self.assertEqual(first["transport_attempts"], 1)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o500)
            for name in ("RESPONSES.jsonl", "CAPTURE_MANIFEST.json", "SHA256SUMS"):
                self.assertEqual((destination / name).stat().st_mode & 0o777, 0o400)

    def test_capture_failure_preserves_partial_private_evidence_and_stops(self) -> None:
        (
            semantics,
            requests,
            plan,
            semantics_sha,
            request_sha,
        ) = self._synthetic_contract()
        plan_sha = cast(str, plan["execution_plan_sha256"])
        semantics_bytes = json.dumps(semantics, sort_keys=True).encode() + b"\n"
        request_bytes = b"".join(_canonical_bytes(row) + b"\n" for row in requests)
        plan_bytes = json.dumps(plan, sort_keys=True).encode() + b"\n"
        receipt_bytes = b"synthetic-receipt\n"
        success = self._valid_chat_body()
        provider_calls = 0

        def factory(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
            nonlocal provider_calls
            del host, port, timeout
            provider_calls += 1
            status = 200 if provider_calls == 1 else 500
            body = success if status == 200 else b"{}"
            return cast(
                http.client.HTTPConnection,
                _FakeConnection(_FakeResponse(status=status, body=body)),
            )

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            prereg = parent / "prereg"
            prereg.mkdir()
            (prereg / "EXECUTION_SEMANTICS.json").write_bytes(semantics_bytes)
            (prereg / "REQUESTS.jsonl").write_bytes(request_bytes)
            (prereg / "PLAN.json").write_bytes(plan_bytes)
            (prereg / "RECEIPT.txt").write_bytes(receipt_bytes)
            self._freeze_synthetic_prereg(prereg)
            destination = parent / "capture"
            with (
                patch.multiple(
                    execution_module,
                    ABLATION_EXECUTION_SEMANTICS_SHA256=semantics_sha,
                    ABLATION_REQUEST_CATALOG_SHA256=request_sha,
                    ABLATION_EXECUTION_PLAN_SHA256=plan_sha,
                    ABLATION_EXECUTION_SEMANTICS_FILE_SHA256=hashlib.sha256(
                        semantics_bytes
                    ).hexdigest(),
                    ABLATION_EXECUTION_PLAN_FILE_SHA256=hashlib.sha256(
                        plan_bytes
                    ).hexdigest(),
                    ABLATION_RECEIPT_FILE_SHA256=hashlib.sha256(
                        receipt_bytes
                    ).hexdigest(),
                ),
                patch.object(
                    execution_module,
                    "fetch_ollama_runtime_identity",
                    return_value={"ollama_version": "0.32.13"},
                ),
            ):
                with self.assertRaises(execution_module._TransportFailure):
                    execute_frozen_ablation_capture(
                        prereg,
                        destination,
                        scored_execution_authorized=True,
                        timeout_seconds=30.0,
                        connection_factory=factory,
                    )
            self.assertEqual(provider_calls, 3)
            rows = (destination / "RESPONSES.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 1)
            failure = json.loads((destination / "FAILURE.json").read_text())
            self.assertFalse(failure["complete"])
            self.assertEqual(failure["completed_attempts"], 1)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o500)


if __name__ == "__main__":
    unittest.main()
