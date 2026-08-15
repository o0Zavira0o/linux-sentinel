"""Tests for Phase 5A systemd dependency-evidence projection."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.dependency import (
    DependencyRelation,
    SystemdDependencyEvidenceContractError,
    project_systemd_dependency_evidence,
)
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

_NOW = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
_BOOT = "a" * 32


def _event(
    *,
    event_id: str = "service-event",
    kind: EventKind = EventKind.OBSERVATION,
    source: str = SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    overrides: dict[str, object] | None = None,
) -> SentinelEvent:
    attributes: dict[str, object] = {
        "observation_type": SYSTEMD_SERVICE_OBSERVATION_TYPE,
        "collector_name": "systemd.demo.0123456789ab",
        "requested_name": "demo.service",
        "canonical_name": "demo.service",
        "boot_id": _BOOT,
        "requires": ["dbus.socket", "sysinit.target"],
        "wants": ["network-online.target"],
        "after": ["dbus.socket", "network-online.target"],
        "before": ["shutdown.target"],
    }
    if overrides is not None:
        attributes.update(overrides)
    return SentinelEvent(
        kind=kind,
        source=source,
        message="systemd observation",
        event_id=event_id,
        occurred_at=_NOW,
        attributes=attributes,
    )


class SystemdDependencyEvidenceTests(unittest.TestCase):
    def test_projection_preserves_all_four_relation_types(self) -> None:
        evidence_set = project_systemd_dependency_evidence(_event())
        self.assertEqual(evidence_set.evidence_count, 6)
        self.assertEqual(evidence_set.requirement_count, 3)
        self.assertEqual(evidence_set.ordering_count, 3)
        self.assertEqual(
            tuple(item.relation for item in evidence_set.evidence),
            (
                DependencyRelation.REQUIRES,
                DependencyRelation.REQUIRES,
                DependencyRelation.WANTS,
                DependencyRelation.AFTER,
                DependencyRelation.AFTER,
                DependencyRelation.BEFORE,
            ),
        )

    def test_requirement_and_ordering_for_same_target_remain_separate(self) -> None:
        evidence_set = project_systemd_dependency_evidence(_event())
        dbus_edges = tuple(
            item
            for item in evidence_set.evidence
            if item.object.identity == "dbus.socket"
        )
        self.assertEqual(len(dbus_edges), 2)
        self.assertEqual(
            {item.relation for item in dbus_edges},
            {DependencyRelation.REQUIRES, DependencyRelation.AFTER},
        )

    def test_projection_preserves_manager_provenance_without_unit_file_claim(
        self,
    ) -> None:
        evidence_set = project_systemd_dependency_evidence(_event())
        payload = evidence_set.to_dict()
        self.assertIs(payload["causal_claims_assigned"], False)
        for item in payload["evidence"]:
            self.assertEqual(item["origin"], "systemd.manager.property")
            self.assertEqual(
                item["configuration_origin"],
                "manager_merged_unresolved",
            )
            self.assertIs(item["causal_claim"], False)

    def test_empty_dependency_properties_are_valid_explicit_evidence(self) -> None:
        evidence_set = project_systemd_dependency_evidence(
            _event(
                overrides={
                    "requires": [],
                    "wants": [],
                    "after": [],
                    "before": [],
                }
            )
        )
        self.assertEqual(evidence_set.evidence_count, 0)
        self.assertEqual(evidence_set.to_dict()["evidence"], [])

    def test_evidence_identity_is_stable_for_same_source_observation(self) -> None:
        first = project_systemd_dependency_evidence(_event())
        second = project_systemd_dependency_evidence(_event())
        self.assertEqual(
            tuple(item.evidence_id for item in first.evidence),
            tuple(item.evidence_id for item in second.evidence),
        )

    def test_new_source_event_gets_new_evidence_identities(self) -> None:
        first = project_systemd_dependency_evidence(_event(event_id="event-a"))
        second = project_systemd_dependency_evidence(_event(event_id="event-b"))
        self.assertNotEqual(
            tuple(item.evidence_id for item in first.evidence),
            tuple(item.evidence_id for item in second.evidence),
        )

    def test_wrong_event_kind_is_rejected(self) -> None:
        with self.assertRaises(SystemdDependencyEvidenceContractError):
            project_systemd_dependency_evidence(_event(kind=EventKind.ANOMALY))

    def test_wrong_event_source_is_rejected(self) -> None:
        with self.assertRaises(SystemdDependencyEvidenceContractError):
            project_systemd_dependency_evidence(_event(source="other.source"))

    def test_wrong_observation_type_is_rejected(self) -> None:
        with self.assertRaises(SystemdDependencyEvidenceContractError):
            project_systemd_dependency_evidence(
                _event(overrides={"observation_type": "other.type"})
            )

    def test_missing_dependency_property_is_rejected(self) -> None:
        event = _event()
        attributes = dict(event.attributes)
        del attributes["before"]
        malformed = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            attributes=attributes,
        )
        with self.assertRaisesRegex(
            SystemdDependencyEvidenceContractError,
            "missing before",
        ):
            project_systemd_dependency_evidence(malformed)

    def test_dependency_property_must_be_list(self) -> None:
        with self.assertRaisesRegex(
            SystemdDependencyEvidenceContractError,
            "requires must be a list",
        ):
            project_systemd_dependency_evidence(
                _event(overrides={"requires": ("dbus.socket",)})
            )

    def test_duplicate_dependency_entry_is_rejected_not_silently_deduplicated(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            SystemdDependencyEvidenceContractError,
            "contains duplicates",
        ):
            project_systemd_dependency_evidence(
                _event(overrides={"after": ["dbus.socket", "dbus.socket"]})
            )

    def test_invalid_boot_identity_is_rejected(self) -> None:
        with self.assertRaises(SystemdDependencyEvidenceContractError):
            project_systemd_dependency_evidence(
                _event(overrides={"boot_id": "invalid"})
            )

    def test_requested_alias_and_canonical_subject_are_both_preserved(self) -> None:
        evidence_set = project_systemd_dependency_evidence(
            _event(
                overrides={
                    "requested_name": "alias.service",
                    "canonical_name": "demo.service",
                }
            )
        )
        self.assertEqual(evidence_set.requested_unit, "alias.service")
        self.assertEqual(evidence_set.canonical_unit, "demo.service")
        self.assertTrue(
            all(
                item.subject.identity == "demo.service"
                for item in evidence_set.evidence
            )
        )


if __name__ == "__main__":
    unittest.main()
