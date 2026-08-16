from __future__ import annotations

import unittest

import sentinel_x._phase5f as phase5f
from sentinel_x._phase5f import (
    CaseSource,
    run_b0_state_rule,
    run_b1_graph_time,
    run_b1s_current_synthesis,
)
from sentinel_x.dependency.synthesis import (
    PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
)


BOOT = "a" * 32
OTHER_BOOT = "b" * 32


class Phase5FDeterministicBaselineTests(unittest.TestCase):
    def _source(
        self,
        *items: dict[str, object],
        environment_boot: str = BOOT,
    ) -> CaseSource:
        return CaseSource(
            case_id="CASE-0200",
            task="Assess whether the observed evidence supports an effect.",
            environment={"boot_id": environment_boot},
            evidence_items=tuple(items),
        )

    def _item(
        self,
        ref: str,
        kind: str,
        payload: dict[str, object],
        *,
        raw: dict[str, object] | None = None,
        full: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "ref": ref,
            "kind": kind,
            "raw": dict(payload if raw is None else raw),
            "minimal": dict(payload),
            "full": dict(payload if full is None else full),
        }

    def _scope(
        self,
        *,
        ref: str = "REF-0001",
        boot_id: str = BOOT,
        source_unit: str | None = "source.service",
        target_unit: str = "dependent.service",
        window: int = 5_000_000,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "target_unit": target_unit,
            "boot_id": boot_id,
            "analysis_window_usec": window,
        }
        if source_unit is not None:
            payload["source_unit"] = source_unit
        return self._item(ref, "scope", payload)

    def _topology(
        self,
        *,
        ref: str = "REF-0002",
        source: str = "source.service",
        target: str = "dependent.service",
        boot_id: str = BOOT,
    ) -> dict[str, object]:
        return self._item(
            ref,
            "topology_relation",
            {
                "subject_unit": target,
                "object_unit": source,
                "relation": "requires",
                "boot_id": boot_id,
            },
        )

    def _timeline(
        self,
        ref: str,
        *,
        unit: str,
        state: str,
        when: int,
        boot_id: str = BOOT,
    ) -> dict[str, object]:
        return self._item(
            ref,
            "incident_timeline",
            {
                "unit": unit,
                "state": state,
                "transition_monotonic_usec": when,
                "boot_id": boot_id,
            },
        )

    def _observation(
        self,
        ref: str,
        *,
        unit: str = "dependent.service",
        status: str,
        boot_id: str = BOOT,
    ) -> dict[str, object]:
        return self._item(
            ref,
            "observation",
            {"unit": unit, "status": status, "boot_id": boot_id},
        )

    def _coverage(
        self,
        *,
        ref: str = "REF-0004",
        unit: str = "dependent.service",
        boot_id: str = BOOT,
        start: int = 1_000_000,
        end: int = 6_000_000,
        largest_gap: int = 100_000,
        max_gap: int = 250_000,
        healthy: int = 20,
        inactive: int = 0,
        failed: int = 0,
        unassessed: int = 0,
    ) -> dict[str, object]:
        return self._item(
            ref,
            "coverage",
            {
                "unit": unit,
                "boot_id": boot_id,
                "window_start_usec": start,
                "window_end_usec": end,
                "largest_gap_usec": largest_gap,
                "max_sample_gap_usec": max_gap,
                "healthy_count": healthy,
                "inactive_count": inactive,
                "failed_count": failed,
                "unassessed_count": unassessed,
            },
        )

    def _synthesis(
        self,
        *,
        affected: int = 0,
        forward: int = 0,
        counter: int = 0,
        negative: int = 0,
        insufficient: int = 0,
        schema_version: str = PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
    ) -> dict[str, object]:
        supportive = affected + forward
        conflict = supportive > 0 and (counter > 0 or negative > 0)
        if supportive == 0 and counter == 0 and negative == 0:
            interpretation = "insufficient_only" if insufficient > 0 else "no_evidence"
        elif conflict:
            interpretation = "conflicting_evidence"
        elif supportive > 0:
            interpretation = "supportive_without_counter_or_negative"
        elif counter > 0 and negative > 0:
            interpretation = "counter_and_bounded_negative_without_support"
        elif counter > 0:
            interpretation = "counterevidence_without_support_or_negative"
        else:
            interpretation = "bounded_negative_without_support_or_counter"
        payload = {
            "schema_version": schema_version,
            "affected_anomaly_observed_count": affected,
            "forward_temporal_consistency_count": forward,
            "directional_counterevidence_count": counter,
            "bounded_negative_observation_count": negative,
            "insufficient_evidence_count": insufficient,
            "supportive_signal_count": supportive,
            "conflicting_evidence_present": conflict,
            "interpretation": interpretation,
            "causal_claim": False,
            "propagation_claim_assigned": False,
        }
        return {"ref": "REF-0009", "kind": "synthesis", "full": payload}

    def test_b0_maps_observed_anomaly_to_effect(self) -> None:
        result = run_b0_state_rule(
            self._source(
                self._scope(),
                self._observation("REF-0002", status="inactive"),
            )
        )
        self.assertEqual(result["classification"], "EFFECT_OBSERVED")
        self.assertFalse(result["abstain"])
        self.assertEqual(result["evidence_refs"], ["REF-0001", "REF-0002"])

    def test_b0_naively_maps_all_healthy_to_negative_without_coverage(self) -> None:
        result = run_b0_state_rule(
            self._source(
                self._scope(),
                self._observation("REF-0002", status="healthy"),
            )
        )
        self.assertEqual(result["classification"], "BOUNDED_NEGATIVE")
        self.assertIn("b0_does_not_evaluate_sampling_coverage", result["unresolved"])

    def test_b0_unassessed_or_missing_state_is_insufficient(self) -> None:
        result = run_b0_state_rule(
            self._source(
                self._scope(),
                self._observation("REF-0002", status="unassessed"),
            )
        )
        self.assertEqual(result["classification"], "INSUFFICIENT")
        self.assertTrue(result["abstain"])

    def test_b0_missing_case_scope_is_insufficient(self) -> None:
        result = run_b0_state_rule(
            self._source(self._observation("REF-0002", status="failed"))
        )
        self.assertEqual(result["classification"], "INSUFFICIENT")
        self.assertIn("missing_case_scope", result["unresolved"])

    def test_b0_does_not_use_boot_provenance(self) -> None:
        result = run_b0_state_rule(
            self._source(
                self._scope(),
                self._observation(
                    "REF-0002",
                    status="failed",
                    boot_id=OTHER_BOOT,
                ),
            )
        )
        self.assertEqual(result["classification"], "EFFECT_OBSERVED")

    def test_b1_forward_topology_and_time_is_effect_observed(self) -> None:
        result = run_b1_graph_time(
            self._source(
                self._scope(),
                self._topology(),
                self._timeline(
                    "REF-0003",
                    unit="source.service",
                    state="inactive",
                    when=1_000_000,
                ),
                self._timeline(
                    "REF-0004",
                    unit="dependent.service",
                    state="inactive",
                    when=1_200_000,
                ),
            )
        )
        self.assertEqual(result["classification"], "EFFECT_OBSERVED")
        self.assertEqual(
            result["evidence_refs"],
            ["REF-0001", "REF-0002", "REF-0003", "REF-0004"],
        )

    def test_b1_reverse_sequence_is_counterevidence(self) -> None:
        result = run_b1_graph_time(
            self._source(
                self._scope(),
                self._topology(),
                self._timeline(
                    "REF-0003",
                    unit="dependent.service",
                    state="failed",
                    when=900_000,
                ),
                self._timeline(
                    "REF-0004",
                    unit="source.service",
                    state="failed",
                    when=1_000_000,
                ),
            )
        )
        self.assertEqual(result["classification"], "COUNTEREVIDENCE")

    def test_b1_simultaneous_or_outside_window_is_insufficient(self) -> None:
        for target_time in (1_000_000, 7_000_001):
            with self.subTest(target_time=target_time):
                result = run_b1_graph_time(
                    self._source(
                        self._scope(window=5_000_000),
                        self._topology(),
                        self._timeline(
                            "REF-0003",
                            unit="source.service",
                            state="inactive",
                            when=1_000_000,
                        ),
                        self._timeline(
                            "REF-0004",
                            unit="dependent.service",
                            state="failed",
                            when=target_time,
                        ),
                    )
                )
                self.assertEqual(result["classification"], "INSUFFICIENT")

    def test_b1_bounded_healthy_coverage_is_negative(self) -> None:
        result = run_b1_graph_time(
            self._source(
                self._scope(),
                self._topology(),
                self._timeline(
                    "REF-0003",
                    unit="source.service",
                    state="inactive",
                    when=1_000_000,
                ),
                self._coverage(),
            )
        )
        self.assertEqual(result["classification"], "BOUNDED_NEGATIVE")
        self.assertFalse(result["abstain"])

    def test_b1_sparse_or_unassessed_coverage_is_insufficient(self) -> None:
        for coverage in (
            self._coverage(largest_gap=500_000),
            self._coverage(unassessed=1),
        ):
            with self.subTest(coverage=coverage):
                result = run_b1_graph_time(
                    self._source(
                        self._scope(),
                        self._topology(),
                        self._timeline(
                            "REF-0003",
                            unit="source.service",
                            state="inactive",
                            when=1_000_000,
                        ),
                        coverage,
                    )
                )
                self.assertEqual(result["classification"], "INSUFFICIENT")

    def test_b1_wrong_boot_supportive_evidence_is_not_used(self) -> None:
        result = run_b1_graph_time(
            self._source(
                self._scope(),
                self._topology(boot_id=OTHER_BOOT),
                self._timeline(
                    "REF-0003",
                    unit="source.service",
                    state="inactive",
                    when=1_000_000,
                    boot_id=OTHER_BOOT,
                ),
                self._coverage(),
            )
        )
        self.assertEqual(result["classification"], "INSUFFICIENT")

    def test_b1_ignores_temporal_distractor_without_dependency_path(self) -> None:
        result = run_b1_graph_time(
            self._source(
                self._scope(source_unit=None),
                self._topology(source="source.service"),
                self._timeline(
                    "REF-0003",
                    unit="unrelated.service",
                    state="failed",
                    when=1_000_000,
                ),
                self._coverage(),
            )
        )
        self.assertEqual(result["classification"], "INSUFFICIENT")

    def test_b1_multiple_time_compatible_sources_is_ambiguous(self) -> None:
        result = run_b1_graph_time(
            self._source(
                self._scope(source_unit=None),
                self._topology(
                    ref="REF-0002",
                    source="source-a.service",
                ),
                self._topology(
                    ref="REF-0003",
                    source="source-b.service",
                ),
                self._timeline(
                    "REF-0004",
                    unit="source-a.service",
                    state="failed",
                    when=1_000_000,
                ),
                self._timeline(
                    "REF-0005",
                    unit="source-b.service",
                    state="failed",
                    when=1_050_000,
                ),
                self._timeline(
                    "REF-0006",
                    unit="dependent.service",
                    state="failed",
                    when=1_100_000,
                ),
            )
        )
        self.assertEqual(result["classification"], "AMBIGUOUS")
        self.assertTrue(result["abstain"])

    def test_b1_supports_multihop_requirement_reachability(self) -> None:
        result = run_b1_graph_time(
            self._source(
                self._scope(),
                self._topology(
                    ref="REF-0002",
                    source="source.service",
                    target="middle.service",
                ),
                self._topology(
                    ref="REF-0003",
                    source="middle.service",
                    target="dependent.service",
                ),
                self._timeline(
                    "REF-0004",
                    unit="source.service",
                    state="failed",
                    when=1_000_000,
                ),
                self._timeline(
                    "REF-0005",
                    unit="dependent.service",
                    state="failed",
                    when=1_200_000,
                ),
            )
        )
        self.assertEqual(result["classification"], "EFFECT_OBSERVED")
        self.assertEqual(
            result["evidence_refs"],
            ["REF-0001", "REF-0002", "REF-0003", "REF-0004", "REF-0005"],
        )

    def test_b1s_maps_observed_anomaly_negative_and_counter(self) -> None:
        expectations = (
            (self._synthesis(affected=1), "EFFECT_OBSERVED"),
            (self._synthesis(negative=1), "BOUNDED_NEGATIVE"),
            (self._synthesis(counter=1), "COUNTEREVIDENCE"),
        )
        for synthesis, expected in expectations:
            with self.subTest(expected=expected):
                result = run_b1s_current_synthesis(
                    self._source(self._scope(), synthesis)
                )
                self.assertEqual(result["classification"], expected)
                self.assertEqual(result["evidence_refs"], ["REF-0009"])

    def test_b1s_conflicting_synthesis_is_ambiguous(self) -> None:
        result = run_b1s_current_synthesis(
            self._source(self._scope(), self._synthesis(affected=1, negative=1))
        )
        self.assertEqual(result["classification"], "AMBIGUOUS")
        self.assertTrue(result["abstain"])

    def test_b1s_forward_temporal_consistency_alone_is_not_effect(self) -> None:
        result = run_b1s_current_synthesis(
            self._source(self._scope(), self._synthesis(forward=1))
        )
        self.assertEqual(result["classification"], "INSUFFICIENT")
        self.assertIn(
            "forward_temporal_consistency_without_observed_effect",
            result["unresolved"],
        )

    def test_b1s_rejects_schema_or_count_drift(self) -> None:
        bad_schema = self._synthesis(schema_version="wrong")
        with self.assertRaisesRegex(ValueError, "schema_version mismatch"):
            run_b1s_current_synthesis(self._source(self._scope(), bad_schema))

        bad_counts = self._synthesis(affected=1)
        full = bad_counts["full"]
        assert isinstance(full, dict)
        full["supportive_signal_count"] = 2
        with self.assertRaisesRegex(ValueError, "supportive_signal_count"):
            run_b1s_current_synthesis(self._source(self._scope(), bad_counts))

        bad_interpretation = self._synthesis(affected=1)
        full = bad_interpretation["full"]
        assert isinstance(full, dict)
        full["interpretation"] = "bounded_negative_without_support_or_counter"
        with self.assertRaisesRegex(ValueError, "interpretation"):
            run_b1s_current_synthesis(self._source(self._scope(), bad_interpretation))

    def test_baseline_result_uses_common_structured_output_without_condition_label(
        self,
    ) -> None:
        result = run_b0_state_rule(
            self._source(
                self._scope(),
                self._observation("REF-0002", status="healthy"),
            )
        )
        self.assertEqual(
            set(result),
            {"classification", "abstain", "claims", "unresolved", "evidence_refs"},
        )
        self.assertNotIn("baseline", result)
        self.assertNotIn("condition", result)

    def test_hidden_gold_remains_unimported_and_no_new_public_dependency_surface(
        self,
    ) -> None:
        self.assertNotIn("CaseGold", phase5f.__all__)
        self.assertEqual(
            phase5f.__all__,
            [
                "CaseSource",
                "project_case_evidence",
                "run_b0_state_rule",
                "run_b1_graph_time",
                "run_b1s_current_synthesis",
                "visible_evidence_refs",
            ],
        )


if __name__ == "__main__":
    unittest.main()
