from __future__ import annotations

import copy
import inspect
import unittest

import sentinel_x._phase5f.execution as execution_module
from sentinel_x._phase5f.execution import (
    FROZEN_CORPUS_ARCHIVE_SHA256,
    FROZEN_FULL_REFERENCE_SHA,
    FROZEN_REASONER_OUTPUT_SHA256,
    FROZEN_PREREGISTRATION_CHECKPOINT_SHA,
    FROZEN_SCORER_SHA256,
    OUTPUT_SCHEMA_SHA256,
    PROMPT_TEMPLATE_SHA256,
    TRANSPORT_RETRY_LIMIT,
    build_execution_plan,
    build_reasoner_request,
    execution_manifest_sha256,
    validate_execution_manifest,
)


class Phase5FExecutionPreflightTests(unittest.TestCase):
    def _manifest(self) -> dict[str, object]:
        return {
            "schema_version": "sentinel-x.phase5f-execution-manifest.v1",
            "provider": "provider-under-test",
            "model_identifier": "model-under-test",
            "model_version": "version-2026-08-18",
            "model_configuration": {
                "temperature": 0.0,
                "top_p": 1.0,
                "response_format": "json",
            },
            "tokenizer_identifier": "tokenizer-under-test-v1",
            "context_window_tokens": 100_000,
            "max_output_tokens": 2_000,
            "token_safety_margin": 1_000,
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

    def _views(self, condition: str) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for index in range(1, 37):
            case_id = f"CASE-{index:04d}"
            result.append(
                {
                    "schema_version": "sentinel-x.phase5f-evidence-bundle.v1",
                    "case_id": case_id,
                    "task": "Assess the operational evidence conservatively.",
                    "environment": {
                        "platform": "linux",
                        "fixture": "sentinel-x-lab",
                    },
                    "evidence": [
                        {
                            "ref": "REF-0001",
                            "kind": "observation",
                            "payload": {
                                "view_payload": {
                                    "raw": "A",
                                    "minimal": "B",
                                    "full": "C",
                                }[condition],
                                "case": case_id,
                            },
                        }
                    ],
                }
            )
        return result

    def _token_counts(self, count: int = 5_000) -> dict[str, int]:
        return {
            f"CASE-{index:04d}:{condition}": count
            for index in range(1, 37)
            for condition in ("raw", "minimal", "full")
        }

    def _plan(self) -> dict[str, object]:
        return build_execution_plan(
            self._views("raw"),
            self._views("minimal"),
            self._views("full"),
            manifest=self._manifest(),
            input_token_counts=self._token_counts(),
        )

    def test_manifest_accepts_exact_pre_score_execution_controls(self) -> None:
        manifest = validate_execution_manifest(self._manifest())
        self.assertEqual(manifest["transport_retry_limit"], 1)
        self.assertTrue(manifest["fresh_context"])
        self.assertFalse(manifest["tools_enabled"])
        self.assertFalse(manifest["web_enabled"])
        self.assertEqual(manifest["prompt_template_sha256"], PROMPT_TEMPLATE_SHA256)
        self.assertEqual(manifest["output_schema_sha256"], OUTPUT_SCHEMA_SHA256)

    def test_manifest_rejects_frozen_boundary_identity_drift(self) -> None:
        fields = (
            "prompt_template_sha256",
            "output_schema_sha256",
            "frozen_reasoner_output_sha256",
            "frozen_scorer_sha256",
            "frozen_corpus_archive_sha256",
            "frozen_full_reference_sha",
            "frozen_preregistration_checkpoint_sha",
        )
        for field in fields:
            with self.subTest(field=field):
                manifest = self._manifest()
                manifest[field] = "0" * 64
                with self.assertRaisesRegex(ValueError, field):
                    validate_execution_manifest(manifest)

    def test_manifest_rejects_retry_or_isolation_policy_drift(self) -> None:
        for field, value in (
            ("transport_retry_limit", 0),
            ("transport_retry_limit", 2),
            ("fresh_context", False),
            ("tools_enabled", True),
            ("web_enabled", True),
        ):
            with self.subTest(field=field, value=value):
                manifest = self._manifest()
                manifest[field] = value
                with self.assertRaises(ValueError):
                    validate_execution_manifest(manifest)

    def test_manifest_requires_explicit_model_configuration_and_capacity(self) -> None:
        manifest = self._manifest()
        manifest["model_configuration"] = {}
        with self.assertRaisesRegex(ValueError, "model_configuration"):
            validate_execution_manifest(manifest)

        manifest = self._manifest()
        manifest["context_window_tokens"] = 2_500
        with self.assertRaisesRegex(ValueError, "output budget"):
            validate_execution_manifest(manifest)

    def test_manifest_digest_is_canonical_and_caller_mutation_safe(self) -> None:
        first = self._manifest()
        second = copy.deepcopy(first)
        config = second["model_configuration"]
        assert isinstance(config, dict)
        second["model_configuration"] = dict(reversed(list(config.items())))
        self.assertEqual(
            execution_manifest_sha256(first), execution_manifest_sha256(second)
        )

        validated = validate_execution_manifest(first)
        original_config = validated["model_configuration"]
        assert isinstance(original_config, dict)
        source_config = first["model_configuration"]
        assert isinstance(source_config, dict)
        source_config["temperature"] = 0.9
        self.assertEqual(original_config["temperature"], 0.0)

    def test_prompt_and_output_schema_digests_are_stable(self) -> None:
        self.assertEqual(
            PROMPT_TEMPLATE_SHA256,
            "53924b77bb4b922fdebb484fa1022d56b782a902cb967034b1e0a4d8d5e2ab48",
        )
        self.assertEqual(
            OUTPUT_SCHEMA_SHA256,
            "1874fe57f0efa0f03c65e9c0bfe2f022f824390d97e9a9a9e17bfdd463f4e788",
        )

    def test_reasoner_request_contains_only_stateless_semantic_input(self) -> None:
        request = build_reasoner_request(self._views("minimal")[0], self._manifest())
        self.assertEqual(
            set(request),
            {
                "provider",
                "model_identifier",
                "model_version",
                "model_configuration",
                "max_output_tokens",
                "messages",
            },
        )
        self.assertNotIn("condition", request)
        self.assertNotIn("repeat_index", request)
        messages = request["messages"]
        assert isinstance(messages, list)
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("CASE-0001", messages[1]["content"])
        self.assertIn("EFFECT_OBSERVED", messages[1]["content"])
        self.assertNotIn("chain-of-thought", messages[0]["content"].lower())

    def test_reasoner_request_rejects_hidden_gold_leakage(self) -> None:
        bundle = self._views("raw")[0]
        environment = bundle["environment"]
        assert isinstance(environment, dict)
        environment["gold_classification"] = "EFFECT_OBSERVED"
        with self.assertRaisesRegex(ValueError, "hidden-gold"):
            build_reasoner_request(bundle, self._manifest())

    def test_execution_plan_requires_exact_324_matrix(self) -> None:
        plan = self._plan()
        self.assertEqual(plan["base_request_count"], 108)
        self.assertEqual(plan["attempt_count"], 324)
        self.assertEqual(plan["case_count"], 36)
        self.assertEqual(plan["conditions"], ["raw", "minimal", "full"])
        self.assertEqual(plan["repeats"], [1, 2, 3])
        attempts = plan["attempts"]
        assert isinstance(attempts, list)
        coordinates = {
            (row["case_id"], row["condition"], row["repeat_index"]) for row in attempts
        }
        self.assertEqual(len(coordinates), 324)

    def test_execution_plan_rejects_missing_duplicate_or_wrong_case_views(self) -> None:
        raw = self._views("raw")
        with self.assertRaisesRegex(ValueError, "exactly 36"):
            build_execution_plan(
                raw[:-1],
                self._views("minimal"),
                self._views("full"),
                manifest=self._manifest(),
                input_token_counts=self._token_counts(),
            )

        duplicate = self._views("raw")
        duplicate[-1] = copy.deepcopy(duplicate[0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_execution_plan(
                duplicate,
                self._views("minimal"),
                self._views("full"),
                manifest=self._manifest(),
                input_token_counts=self._token_counts(),
            )

    def test_execution_plan_rejects_outer_lineage_drift_between_conditions(
        self,
    ) -> None:
        minimal = self._views("minimal")
        minimal[0]["task"] = "Different task wording."
        with self.assertRaisesRegex(ValueError, "outer lineage drift"):
            build_execution_plan(
                self._views("raw"),
                minimal,
                self._views("full"),
                manifest=self._manifest(),
                input_token_counts=self._token_counts(),
            )

    def test_execution_plan_requires_exact_external_token_count_map(self) -> None:
        counts = self._token_counts()
        del counts["CASE-0001:raw"]
        with self.assertRaisesRegex(ValueError, "exactly 108"):
            build_execution_plan(
                self._views("raw"),
                self._views("minimal"),
                self._views("full"),
                manifest=self._manifest(),
                input_token_counts=counts,
            )

    def test_execution_plan_rejects_silent_truncation_risk(self) -> None:
        manifest = self._manifest()
        manifest["context_window_tokens"] = 10_000
        manifest["max_output_tokens"] = 2_000
        manifest["token_safety_margin"] = 1_000
        counts = self._token_counts()
        counts["CASE-0036:full"] = 7_001
        with self.assertRaisesRegex(ValueError, "silent truncation risk"):
            build_execution_plan(
                self._views("raw"),
                self._views("minimal"),
                self._views("full"),
                manifest=manifest,
                input_token_counts=counts,
            )

    def test_repeats_reuse_exact_semantic_request_without_exposing_repeat(self) -> None:
        attempts = self._plan()["attempts"]
        assert isinstance(attempts, list)
        selected = [
            row
            for row in attempts
            if row["case_id"] == "CASE-0001" and row["condition"] == "minimal"
        ]
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            {row["semantic_request_sha256"] for row in selected},
            {selected[0]["semantic_request_sha256"]},
        )
        self.assertEqual(
            {str(row["reasoner_request"]) for row in selected},
            {str(selected[0]["reasoner_request"])},
        )
        request = selected[0]["reasoner_request"]
        assert isinstance(request, dict)
        self.assertNotIn("repeat_index", request)
        self.assertNotIn("condition", request)

    def test_execution_order_and_plan_digest_are_deterministic(self) -> None:
        first = self._plan()
        raw = list(reversed(self._views("raw")))
        minimal = list(reversed(self._views("minimal")))
        full = list(reversed(self._views("full")))
        second = build_execution_plan(
            raw,
            minimal,
            full,
            manifest=self._manifest(),
            input_token_counts=dict(reversed(list(self._token_counts().items()))),
        )
        self.assertEqual(
            first["execution_plan_sha256"], second["execution_plan_sha256"]
        )
        self.assertEqual(first["attempts"], second["attempts"])

    def test_execution_boundary_has_no_gold_scoring_or_provider_runtime_import(
        self,
    ) -> None:
        source = inspect.getsource(execution_module)
        self.assertNotIn("from .gold", source)
        self.assertNotIn("from .scoring", source)
        self.assertNotIn("CaseGold", source)
        self.assertNotIn("openai", source.lower())
        self.assertNotIn("anthropic", source.lower())
        self.assertNotIn("import requests", source.lower())
        self.assertNotIn("from requests", source.lower())


if __name__ == "__main__":
    unittest.main()
