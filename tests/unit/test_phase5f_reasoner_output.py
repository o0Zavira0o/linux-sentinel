from __future__ import annotations

import json
import unittest

from sentinel_x._phase5f.baselines import run_b0_state_rule
from sentinel_x._phase5f.reasoner_output import (
    parse_reasoner_output,
    validate_reasoner_output,
)
from sentinel_x._phase5f.visible import CaseSource


class Phase5FReasonerOutputTests(unittest.TestCase):
    def _valid_output(self) -> dict[str, object]:
        return {
            "classification": "EFFECT_OBSERVED",
            "abstain": False,
            "claims": [
                {
                    "claim_kind": "downstream_effect",
                    "causal_strength": "hypothesis",
                    "evidence_refs": ["REF-0002", "REF-0003"],
                    "text": (
                        "The evidence supports a bounded downstream-effect hypothesis."
                    ),
                }
            ],
            "unresolved": ["causal mechanism is not established"],
            "evidence_refs": ["REF-0001", "REF-0002", "REF-0003"],
        }

    def test_parse_accepts_exact_preregistered_shape(self) -> None:
        parsed = parse_reasoner_output(json.dumps(self._valid_output()))
        self.assertEqual(parsed, self._valid_output())
        self.assertEqual(
            set(parsed),
            {"classification", "abstain", "claims", "unresolved", "evidence_refs"},
        )

    def test_parse_rejects_markdown_fence_or_trailing_prose(self) -> None:
        payload = json.dumps(self._valid_output())
        for raw in (f"```json\n{payload}\n```", payload + "\nexplanation"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "bare JSON object"):
                    parse_reasoner_output(raw)

    def test_parse_rejects_duplicate_json_keys(self) -> None:
        raw = (
            '{"classification":"EFFECT_OBSERVED",'
            '"classification":"AMBIGUOUS",'
            '"abstain":false,"claims":[],"unresolved":[],"evidence_refs":[]}'
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            parse_reasoner_output(raw)

    def test_parse_rejects_nonstandard_json_constants(self) -> None:
        raw = (
            '{"classification":"EFFECT_OBSERVED","abstain":false,'
            '"claims":[],"unresolved":[NaN],"evidence_refs":[]}'
        )
        with self.assertRaisesRegex(ValueError, "non-standard JSON constant"):
            parse_reasoner_output(raw)

    def test_output_requires_exact_top_level_keys(self) -> None:
        output = self._valid_output()
        output["condition"] = "FULL"
        with self.assertRaisesRegex(ValueError, r"extra=\['condition'\]"):
            validate_reasoner_output(output)

        output = self._valid_output()
        del output["unresolved"]
        with self.assertRaisesRegex(ValueError, r"missing=\['unresolved'\]"):
            validate_reasoner_output(output)

    def test_classification_and_abstain_are_strictly_typed(self) -> None:
        for classification in (
            "EFFECT_OBSERVED",
            "BOUNDED_NEGATIVE",
            "COUNTEREVIDENCE",
            "INSUFFICIENT",
            "AMBIGUOUS",
        ):
            with self.subTest(classification=classification):
                output = self._valid_output()
                output["classification"] = classification
                self.assertEqual(
                    validate_reasoner_output(output)["classification"],
                    classification,
                )

        output = self._valid_output()
        output["classification"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "benchmark label"):
            validate_reasoner_output(output)

        output = self._valid_output()
        output["abstain"] = 1
        with self.assertRaisesRegex(ValueError, "abstain must be a boolean"):
            validate_reasoner_output(output)

    def test_abstention_is_not_derived_from_classification(self) -> None:
        effect_abstain = self._valid_output()
        effect_abstain["abstain"] = True
        self.assertTrue(validate_reasoner_output(effect_abstain)["abstain"])

        insufficient_nonabstain = self._valid_output()
        insufficient_nonabstain["classification"] = "INSUFFICIENT"
        insufficient_nonabstain["abstain"] = False
        self.assertFalse(validate_reasoner_output(insufficient_nonabstain)["abstain"])

    def test_claim_requires_exact_shape_and_supported_causal_strength_enum(
        self,
    ) -> None:
        for strength in ("none", "association", "hypothesis", "established_cause"):
            with self.subTest(strength=strength):
                output = self._valid_output()
                claims = output["claims"]
                assert isinstance(claims, list)
                claim = claims[0]
                assert isinstance(claim, dict)
                claim["causal_strength"] = strength
                validated = validate_reasoner_output(output)
                validated_claims = validated["claims"]
                assert isinstance(validated_claims, list)
                self.assertEqual(validated_claims[0]["causal_strength"], strength)

        output = self._valid_output()
        claims = output["claims"]
        assert isinstance(claims, list)
        claim = claims[0]
        assert isinstance(claim, dict)
        claim["rationale"] = "extra hidden reasoning field"
        with self.assertRaisesRegex(ValueError, r"claims\[0\] keys mismatch"):
            validate_reasoner_output(output)

        output = self._valid_output()
        claims = output["claims"]
        assert isinstance(claims, list)
        claim = claims[0]
        assert isinstance(claim, dict)
        claim["causal_strength"] = "certain"
        with self.assertRaisesRegex(ValueError, "causal_strength is invalid"):
            validate_reasoner_output(output)

    def test_claim_text_and_unresolved_items_must_be_nonempty_text(self) -> None:
        output = self._valid_output()
        claims = output["claims"]
        assert isinstance(claims, list)
        claim = claims[0]
        assert isinstance(claim, dict)
        claim["text"] = "   "
        with self.assertRaisesRegex(ValueError, r"claims\[0\]\.text"):
            validate_reasoner_output(output)

        output = self._valid_output()
        output["unresolved"] = [""]
        with self.assertRaisesRegex(ValueError, r"unresolved\[0\]"):
            validate_reasoner_output(output)

    def test_evidence_refs_require_opaque_ref_shape_and_no_duplicates(self) -> None:
        output = self._valid_output()
        output["evidence_refs"] = ["not-a-ref"]
        with self.assertRaisesRegex(ValueError, "REF-####"):
            validate_reasoner_output(output)

        output = self._valid_output()
        output["evidence_refs"] = ["REF-0001", "REF-0001"]
        with self.assertRaisesRegex(ValueError, "duplicate references"):
            validate_reasoner_output(output)

        output = self._valid_output()
        claims = output["claims"]
        assert isinstance(claims, list)
        claim = claims[0]
        assert isinstance(claim, dict)
        claim["evidence_refs"] = ["REF-0002", "REF-0002"]
        with self.assertRaisesRegex(ValueError, "duplicate references"):
            validate_reasoner_output(output)

    def test_claims_may_be_uncited_so_scoring_can_observe_unsupported_claims(
        self,
    ) -> None:
        output = self._valid_output()
        claims = output["claims"]
        assert isinstance(claims, list)
        claim = claims[0]
        assert isinstance(claim, dict)
        claim["evidence_refs"] = []
        validated = validate_reasoner_output(output)
        validated_claims = validated["claims"]
        assert isinstance(validated_claims, list)
        self.assertEqual(validated_claims[0]["evidence_refs"], [])

    def test_reference_membership_is_not_gold_or_visible_scope_validation(self) -> None:
        output = self._valid_output()
        output["evidence_refs"] = ["REF-9999"]
        claims = output["claims"]
        assert isinstance(claims, list)
        claim = claims[0]
        assert isinstance(claim, dict)
        claim["evidence_refs"] = ["REF-9998"]
        validated = validate_reasoner_output(output)
        self.assertEqual(validated["evidence_refs"], ["REF-9999"])

    def test_baseline_output_validates_through_common_boundary(self) -> None:
        source = CaseSource(
            case_id="CASE-0001",
            task="Assess the evidence.",
            environment={"boot_id": "a" * 32},
            evidence_items=(
                {
                    "ref": "REF-0001",
                    "kind": "scope",
                    "raw": {"target_unit": "dependent.service"},
                    "minimal": {
                        "target_unit": "dependent.service",
                        "boot_id": "a" * 32,
                        "analysis_window_usec": 5_000_000,
                    },
                    "full": {"target_unit": "dependent.service"},
                },
                {
                    "ref": "REF-0002",
                    "kind": "observation",
                    "raw": {"status": "healthy"},
                    "minimal": {
                        "unit": "dependent.service",
                        "status": "healthy",
                    },
                    "full": {"status": "healthy"},
                },
            ),
        )
        result = run_b0_state_rule(source)
        self.assertEqual(validate_reasoner_output(result), result)

    def test_validation_returns_fresh_plain_lists_and_claim_objects(self) -> None:
        output = self._valid_output()
        validated = validate_reasoner_output(output)
        self.assertIsNot(validated, output)
        self.assertIsNot(validated["claims"], output["claims"])
        claims = validated["claims"]
        source_claims = output["claims"]
        assert isinstance(claims, list)
        assert isinstance(source_claims, list)
        self.assertIsNot(claims[0], source_claims[0])
        self.assertIsNot(validated["unresolved"], output["unresolved"])
        self.assertIsNot(validated["evidence_refs"], output["evidence_refs"])


if __name__ == "__main__":
    unittest.main()
