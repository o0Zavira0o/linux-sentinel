"""Tests for Phase 5A typed dependency-evidence contracts."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.dependency import (
    DependencyConfigurationOrigin,
    DependencyEndpoint,
    DependencyEntityKind,
    DependencyEvidence,
    DependencyEvidenceOrigin,
    DependencyModelError,
    DependencyRelation,
    DependencySemanticClass,
    source_property_for_relation,
)

_NOW = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
_BOOT = "a" * 32


def _endpoint(identity: str = "demo.service") -> DependencyEndpoint:
    return DependencyEndpoint(
        kind=DependencyEntityKind.SYSTEMD_UNIT,
        identity=identity,
    )


def _evidence(
    *,
    relation: DependencyRelation = DependencyRelation.REQUIRES,
    source_property: str = "Requires",
    causal_claim: bool = False,
) -> DependencyEvidence:
    return DependencyEvidence(
        evidence_id="depev-" + "b" * 64,
        source_event_id="source-event",
        observed_at=_NOW,
        boot_id=_BOOT,
        subject=_endpoint(),
        relation=relation,
        object=_endpoint("network.target"),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=(DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED),
        source_property=source_property,
        causal_claim=causal_claim,
    )


class DependencyModelTests(unittest.TestCase):
    def test_relation_semantics_keep_requirement_and_ordering_distinct(self) -> None:
        self.assertIs(
            DependencyRelation.REQUIRES.semantic_class,
            DependencySemanticClass.STRONG_REQUIREMENT,
        )
        self.assertIs(
            DependencyRelation.WANTS.semantic_class,
            DependencySemanticClass.WEAK_REQUIREMENT,
        )
        self.assertIs(
            DependencyRelation.AFTER.semantic_class,
            DependencySemanticClass.ORDERING,
        )
        self.assertIs(
            DependencyRelation.BEFORE.semantic_class,
            DependencySemanticClass.ORDERING,
        )

    def test_source_property_mapping_is_exact(self) -> None:
        self.assertEqual(
            source_property_for_relation(DependencyRelation.REQUIRES),
            "Requires",
        )
        self.assertEqual(
            source_property_for_relation(DependencyRelation.BEFORE),
            "Before",
        )

    def test_endpoint_preserves_non_service_systemd_unit_identity(self) -> None:
        endpoint = _endpoint("network-online.target")
        self.assertEqual(endpoint.identity, "network-online.target")
        self.assertEqual(endpoint.to_dict()["kind"], "systemd.unit")

    def test_endpoint_rejects_surrounding_whitespace(self) -> None:
        with self.assertRaises(DependencyModelError):
            _endpoint(" network.target")

    def test_evidence_serialization_is_explicitly_noncausal(self) -> None:
        payload = _evidence().to_dict()
        self.assertEqual(payload["semantic_class"], "strong_requirement")
        self.assertEqual(payload["origin"], "systemd.manager.property")
        self.assertEqual(
            payload["configuration_origin"],
            "manager_merged_unresolved",
        )
        self.assertIs(payload["causal_claim"], False)
        self.assertNotIn("confidence", payload)
        self.assertNotIn("probability", payload)

    def test_phase5a_evidence_rejects_causal_claim(self) -> None:
        with self.assertRaisesRegex(DependencyModelError, "must not assert causality"):
            _evidence(causal_claim=True)

    def test_source_property_must_match_relation(self) -> None:
        with self.assertRaisesRegex(DependencyModelError, "source_property"):
            _evidence(
                relation=DependencyRelation.AFTER,
                source_property="Requires",
            )

    def test_evidence_id_is_strictly_typed(self) -> None:
        with self.assertRaises(DependencyModelError):
            replace(_evidence(), evidence_id="not-an-evidence-id")

    def test_boot_id_is_lowercase_hex(self) -> None:
        with self.assertRaises(DependencyModelError):
            replace(_evidence(), boot_id="Z" * 32)


if __name__ == "__main__":
    unittest.main()
