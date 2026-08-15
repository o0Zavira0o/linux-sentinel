from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.dependency.discovery import (
    SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
    DiscoveredSystemdUnit,
    SystemdDependencyDiscoveryFailure,
    SystemdDependencyDiscoveryReport,
    SystemdDependencyUnitSnapshot,
)
from sentinel_x.dependency.graph import (
    DependencyGraphCapacityError,
    DependencyGraphError,
    DependencyGraphNode,
    DependencyGraphNodeObservation,
    build_dependency_graph,
    compare_dependency_graphs,
)
from sentinel_x.dependency.models import (
    DependencyConfigurationOrigin,
    DependencyEndpoint,
    DependencyEntityKind,
    DependencyEvidence,
    DependencyEvidenceOrigin,
    DependencyRelation,
)

_BOOT = "a" * 32
_TIME = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)


def _event(event_id: str, unit: str, *, observed_at: datetime = _TIME) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message=f"dependency observation for {unit}",
        severity=EventSeverity.INFO,
        attributes={"observation_type": "linux.systemd.unit.dependency"},
        event_id=event_id,
        occurred_at=observed_at,
    )


def _evidence(
    *,
    evidence_id_hex: str,
    event_id: str,
    subject: str,
    relation: DependencyRelation,
    object_unit: str,
    observed_at: datetime = _TIME,
    boot_id: str = _BOOT,
) -> DependencyEvidence:
    property_name = {
        DependencyRelation.REQUIRES: "Requires",
        DependencyRelation.WANTS: "Wants",
        DependencyRelation.AFTER: "After",
        DependencyRelation.BEFORE: "Before",
    }[relation]
    return DependencyEvidence(
        evidence_id="depev-" + evidence_id_hex * 64,
        source_event_id=event_id,
        observed_at=observed_at,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=subject,
        ),
        relation=relation,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=object_unit,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=(DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED),
        source_property=property_name,
    )


def _unit(
    *,
    canonical: str,
    depth: int,
    event_id: str,
    evidence: tuple[DependencyEvidence, ...] = (),
    observed_at: datetime = _TIME,
) -> DiscoveredSystemdUnit:
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name=canonical,
        canonical_name=canonical,
        names=(canonical,),
        load_state="loaded",
        requires=tuple(
            item.object.identity
            for item in evidence
            if item.relation is DependencyRelation.REQUIRES
        ),
        wants=tuple(
            item.object.identity
            for item in evidence
            if item.relation is DependencyRelation.WANTS
        ),
        after=tuple(
            item.object.identity
            for item in evidence
            if item.relation is DependencyRelation.AFTER
        ),
        before=tuple(
            item.object.identity
            for item in evidence
            if item.relation is DependencyRelation.BEFORE
        ),
        captured_at=observed_at,
    )
    return DiscoveredSystemdUnit(
        depth=depth,
        snapshot=snapshot,
        source_event=_event(event_id, canonical, observed_at=observed_at),
        evidence=evidence,
    )


def _report(
    *,
    units: tuple[DiscoveredSystemdUnit, ...],
    failures: tuple[SystemdDependencyDiscoveryFailure, ...] = (),
    truncated_by_depth: bool = False,
    unexpanded_requirement_count: int = 0,
    max_depth: int = 2,
    boot_id: str = _BOOT,
) -> SystemdDependencyDiscoveryReport:
    return SystemdDependencyDiscoveryReport(
        root_requested_unit=units[0].snapshot.requested_name,
        root_canonical_unit=units[0].snapshot.canonical_name,
        boot_id=boot_id,
        max_depth=max_depth,
        max_units=64,
        units=units,
        failures=failures,
        truncated_by_depth=truncated_by_depth,
        unexpanded_requirement_count=unexpanded_requirement_count,
    )


class DependencyGraphTests(unittest.TestCase):
    def test_build_distinguishes_observed_and_referenced_only_nodes(self) -> None:
        root_event = "evt-root"
        edge = _evidence(
            evidence_id_hex="1",
            event_id=root_event,
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="child.service",
        )
        graph = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id=root_event,
                        evidence=(edge,),
                    ),
                )
            )
        )

        states = {node.identity: node.observation for node in graph.nodes}
        self.assertEqual(
            states["root.service"], DependencyGraphNodeObservation.OBSERVED
        )
        self.assertEqual(
            states["child.service"],
            DependencyGraphNodeObservation.REFERENCED_ONLY,
        )
        referenced = next(
            node for node in graph.nodes if node.identity == "child.service"
        )
        self.assertIsNone(referenced.depth)
        self.assertIsNone(referenced.load_state)
        self.assertIsNone(referenced.source_event_id)
        self.assertIsNone(referenced.observed_at)
        self.assertEqual(referenced.names, ())

    def test_observed_target_upgrades_referenced_endpoint_without_duplicate_node(
        self,
    ) -> None:
        root_event = "evt-root"
        child_event = "evt-child"
        edge = _evidence(
            evidence_id_hex="2",
            event_id=root_event,
            subject="root.service",
            relation=DependencyRelation.WANTS,
            object_unit="child.service",
        )
        graph = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id=root_event,
                        evidence=(edge,),
                    ),
                    _unit(canonical="child.service", depth=1, event_id=child_event),
                )
            )
        )

        child_nodes = [node for node in graph.nodes if node.identity == "child.service"]
        self.assertEqual(len(child_nodes), 1)
        self.assertEqual(
            child_nodes[0].observation,
            DependencyGraphNodeObservation.OBSERVED,
        )
        self.assertEqual(child_nodes[0].depth, 1)

    def test_graph_preserves_all_relation_semantics_and_noncausal_evidence(
        self,
    ) -> None:
        event_id = "evt-root"
        evidence = tuple(
            _evidence(
                evidence_id_hex=str(index),
                event_id=event_id,
                subject="root.service",
                relation=relation,
                object_unit=f"target-{index}.target",
            )
            for index, relation in enumerate(
                (
                    DependencyRelation.REQUIRES,
                    DependencyRelation.WANTS,
                    DependencyRelation.AFTER,
                    DependencyRelation.BEFORE,
                ),
                start=1,
            )
        )
        graph = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id=event_id,
                        evidence=evidence,
                    ),
                )
            )
        )

        self.assertEqual(
            {edge.relation for edge in graph.edges},
            {
                DependencyRelation.REQUIRES,
                DependencyRelation.WANTS,
                DependencyRelation.AFTER,
                DependencyRelation.BEFORE,
            },
        )
        self.assertTrue(
            all(edge.evidence.causal_claim is False for edge in graph.edges)
        )
        payload = graph.to_dict()
        self.assertIs(payload["causal_claims_assigned"], False)
        self.assertIs(payload["comprehensive_systemd_relation_scope"], False)

    def test_graph_nodes_and_edges_are_canonically_sorted(self) -> None:
        event_id = "evt-root"
        evidence = (
            _evidence(
                evidence_id_hex="a",
                event_id=event_id,
                subject="z.service",
                relation=DependencyRelation.WANTS,
                object_unit="b.service",
            ),
            _evidence(
                evidence_id_hex="b",
                event_id=event_id,
                subject="z.service",
                relation=DependencyRelation.REQUIRES,
                object_unit="a.service",
            ),
        )
        graph = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="z.service",
                        depth=0,
                        event_id=event_id,
                        evidence=evidence,
                    ),
                )
            )
        )

        self.assertEqual(
            tuple(node.identity for node in graph.nodes),
            ("a.service", "b.service", "z.service"),
        )
        self.assertEqual(
            tuple(edge.structural_key for edge in graph.edges),
            tuple(sorted(edge.structural_key for edge in graph.edges)),
        )

    def test_topology_id_is_stable_across_evidence_refresh(self) -> None:
        first_edge = _evidence(
            evidence_id_hex="1",
            event_id="evt-one",
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="child.service",
            observed_at=_TIME,
        )
        second_time = datetime(2026, 8, 15, 10, 5, tzinfo=timezone.utc)
        second_edge = _evidence(
            evidence_id_hex="2",
            event_id="evt-two",
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="child.service",
            observed_at=second_time,
        )
        first = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id="evt-one",
                        evidence=(first_edge,),
                        observed_at=_TIME,
                    ),
                )
            )
        )
        second = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id="evt-two",
                        evidence=(second_edge,),
                        observed_at=second_time,
                    ),
                )
            )
        )

        self.assertEqual(first.topology_id, second.topology_id)
        self.assertNotEqual(first.graph_version_id, second.graph_version_id)
        delta = compare_dependency_graphs(first, second)
        self.assertFalse(delta.topology_changed)
        self.assertFalse(delta.boot_changed)
        self.assertTrue(delta.version_changed_without_topology_change)
        self.assertTrue(delta.evidence_refreshed_without_topology_change)
        self.assertEqual(delta.added_nodes, ())
        self.assertEqual(delta.removed_nodes, ())
        self.assertEqual(delta.added_edges, ())
        self.assertEqual(delta.removed_edges, ())

    def test_topology_id_changes_for_structural_edge_change(self) -> None:
        first_edge = _evidence(
            evidence_id_hex="1",
            event_id="evt-one",
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="child.service",
        )
        second_edge = _evidence(
            evidence_id_hex="2",
            event_id="evt-two",
            subject="root.service",
            relation=DependencyRelation.WANTS,
            object_unit="child.service",
        )
        first = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id="evt-one",
                        evidence=(first_edge,),
                    ),
                )
            )
        )
        second = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id="evt-two",
                        evidence=(second_edge,),
                    ),
                )
            )
        )

        delta = compare_dependency_graphs(first, second)
        self.assertTrue(delta.topology_changed)
        self.assertEqual(len(delta.added_edges), 1)
        self.assertEqual(len(delta.removed_edges), 1)

    def test_observation_coverage_change_does_not_fake_node_add_remove(self) -> None:
        root_event = "evt-root"
        edge = _evidence(
            evidence_id_hex="1",
            event_id=root_event,
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="child.service",
        )
        first = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id=root_event,
                        evidence=(edge,),
                    ),
                )
            )
        )
        second = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id=root_event,
                        evidence=(edge,),
                    ),
                    _unit(canonical="child.service", depth=1, event_id="evt-child"),
                )
            )
        )

        delta = compare_dependency_graphs(first, second)
        self.assertFalse(delta.topology_changed)
        self.assertEqual(delta.added_nodes, ())
        self.assertEqual(delta.removed_nodes, ())
        self.assertEqual(len(delta.observation_changes), 1)
        self.assertFalse(delta.evidence_refreshed_without_topology_change)
        self.assertEqual(delta.observation_changes[0].identity, "child.service")
        self.assertEqual(
            delta.observation_changes[0].previous,
            DependencyGraphNodeObservation.REFERENCED_ONLY,
        )
        self.assertEqual(
            delta.observation_changes[0].current,
            DependencyGraphNodeObservation.OBSERVED,
        )

    def test_completeness_change_is_reported_separately_from_topology(self) -> None:
        unit = _unit(canonical="root.service", depth=0, event_id="evt-root")
        complete = build_dependency_graph(_report(units=(unit,)))
        partial = build_dependency_graph(
            _report(
                units=(unit,),
                truncated_by_depth=True,
                unexpanded_requirement_count=1,
                max_depth=0,
            )
        )

        delta = compare_dependency_graphs(complete, partial)
        self.assertFalse(delta.topology_changed)
        self.assertTrue(delta.completeness_changed)
        self.assertFalse(delta.evidence_refreshed_without_topology_change)
        self.assertFalse(partial.complete_within_scope)

    def test_failure_metadata_is_preserved_without_fabricating_failed_node(
        self,
    ) -> None:
        unit = _unit(canonical="root.service", depth=0, event_id="evt-root")
        failure = SystemdDependencyDiscoveryFailure(
            requested_unit="missing.service",
            depth=1,
            error_type="SystemdDependencyCommandError",
            message="bounded read failed",
        )
        graph = build_dependency_graph(_report(units=(unit,), failures=(failure,)))

        self.assertEqual(graph.failures, (failure,))
        self.assertNotIn("missing.service", {node.identity for node in graph.nodes})
        self.assertFalse(graph.complete_within_scope)

    def test_graph_capacity_fails_without_silent_edge_eviction(self) -> None:
        event_id = "evt-root"
        evidence = (
            _evidence(
                evidence_id_hex="1",
                event_id=event_id,
                subject="root.service",
                relation=DependencyRelation.REQUIRES,
                object_unit="one.service",
            ),
            _evidence(
                evidence_id_hex="2",
                event_id=event_id,
                subject="root.service",
                relation=DependencyRelation.WANTS,
                object_unit="two.service",
            ),
        )
        report = _report(
            units=(
                _unit(
                    canonical="root.service",
                    depth=0,
                    event_id=event_id,
                    evidence=evidence,
                ),
            )
        )

        with self.assertRaises(DependencyGraphCapacityError):
            build_dependency_graph(report, max_graph_edges=1)

    def test_graph_capacity_fails_without_silent_referenced_node_eviction(self) -> None:
        event_id = "evt-root"
        evidence = (
            _evidence(
                evidence_id_hex="1",
                event_id=event_id,
                subject="root.service",
                relation=DependencyRelation.REQUIRES,
                object_unit="one.service",
            ),
            _evidence(
                evidence_id_hex="2",
                event_id=event_id,
                subject="root.service",
                relation=DependencyRelation.WANTS,
                object_unit="two.service",
            ),
        )
        report = _report(
            units=(
                _unit(
                    canonical="root.service",
                    depth=0,
                    event_id=event_id,
                    evidence=evidence,
                ),
            )
        )

        with self.assertRaises(DependencyGraphCapacityError):
            build_dependency_graph(report, max_graph_nodes=2)

    def test_referenced_only_node_rejects_invented_observation_fields(self) -> None:
        with self.assertRaises(DependencyGraphError):
            DependencyGraphNode(
                identity="child.service",
                observation=DependencyGraphNodeObservation.REFERENCED_ONLY,
                load_state="unknown",
            )

    def test_compare_rejects_different_roots(self) -> None:
        first = build_dependency_graph(
            _report(
                units=(_unit(canonical="one.service", depth=0, event_id="evt-one"),)
            )
        )
        second = build_dependency_graph(
            _report(
                units=(_unit(canonical="two.service", depth=0, event_id="evt-two"),)
            )
        )

        with self.assertRaises(DependencyGraphError):
            compare_dependency_graphs(first, second)

    def test_graph_identity_validation_rejects_digest_drift(self) -> None:
        graph = build_dependency_graph(
            _report(
                units=(_unit(canonical="root.service", depth=0, event_id="evt-root"),)
            )
        )

        with self.assertRaises(DependencyGraphError):
            replace(graph, topology_id="deptopo-" + "0" * 64)
        with self.assertRaises(DependencyGraphError):
            replace(graph, graph_version_id="depgraphv-" + "0" * 64)

    def test_serialization_reports_observation_coverage_counts(self) -> None:
        event_id = "evt-root"
        edge = _evidence(
            evidence_id_hex="1",
            event_id=event_id,
            subject="root.service",
            relation=DependencyRelation.AFTER,
            object_unit="remote.target",
        )
        graph = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id=event_id,
                        evidence=(edge,),
                    ),
                )
            )
        )
        payload = graph.to_dict()

        self.assertEqual(payload["node_count"], 2)
        self.assertEqual(payload["observed_node_count"], 1)
        self.assertEqual(payload["referenced_only_node_count"], 1)
        self.assertEqual(payload["edge_count"], 1)
        self.assertIs(payload["causal_claims_assigned"], False)

    def test_referenced_failed_unit_remains_referenced_only_with_failure_evidence(
        self,
    ) -> None:
        event_id = "evt-root"
        edge = _evidence(
            evidence_id_hex="1",
            event_id=event_id,
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="missing.service",
        )
        failure = SystemdDependencyDiscoveryFailure(
            requested_unit="missing.service",
            depth=1,
            error_type="SystemdDependencyCommandError",
            message="bounded read failed",
        )
        graph = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id=event_id,
                        evidence=(edge,),
                    ),
                ),
                failures=(failure,),
            )
        )

        missing = next(
            node for node in graph.nodes if node.identity == "missing.service"
        )
        self.assertEqual(
            missing.observation,
            DependencyGraphNodeObservation.REFERENCED_ONLY,
        )
        self.assertEqual(graph.failures, (failure,))

    def test_topology_id_is_stable_across_boots_for_same_structure(self) -> None:
        second_boot = "b" * 32
        first_edge = _evidence(
            evidence_id_hex="1",
            event_id="evt-one",
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="child.service",
            boot_id=_BOOT,
        )
        second_edge = _evidence(
            evidence_id_hex="2",
            event_id="evt-two",
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="child.service",
            boot_id=second_boot,
        )
        first = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id="evt-one",
                        evidence=(first_edge,),
                    ),
                ),
                boot_id=_BOOT,
            )
        )
        second_unit = _unit(
            canonical="root.service",
            depth=0,
            event_id="evt-two",
            evidence=(second_edge,),
        )
        second = build_dependency_graph(
            _report(units=(second_unit,), boot_id=second_boot)
        )

        self.assertEqual(first.topology_id, second.topology_id)
        self.assertNotEqual(first.graph_version_id, second.graph_version_id)
        delta = compare_dependency_graphs(first, second)
        self.assertFalse(delta.topology_changed)
        self.assertTrue(delta.boot_changed)
        self.assertTrue(delta.version_changed_without_topology_change)
        self.assertFalse(delta.evidence_refreshed_without_topology_change)

    def test_graph_version_id_is_deterministic_for_same_report(self) -> None:
        report = _report(
            units=(_unit(canonical="root.service", depth=0, event_id="evt-root"),)
        )

        first = build_dependency_graph(report)
        second = build_dependency_graph(report)

        self.assertEqual(first.topology_id, second.topology_id)
        self.assertEqual(first.graph_version_id, second.graph_version_id)

    def test_referenced_only_serialization_omits_unobserved_state_fields(self) -> None:
        node = DependencyGraphNode(
            identity="child.service",
            observation=DependencyGraphNodeObservation.REFERENCED_ONLY,
        )

        self.assertEqual(
            node.to_dict(),
            {
                "identity": "child.service",
                "observation": "referenced_only",
            },
        )

    def test_graph_serialization_contains_no_probability_or_confidence_claim(
        self,
    ) -> None:
        graph = build_dependency_graph(
            _report(
                units=(_unit(canonical="root.service", depth=0, event_id="evt-root"),)
            )
        )
        serialized = repr(graph.to_dict()).lower()

        self.assertNotIn("probability", serialized)
        self.assertNotIn("confidence", serialized)
        self.assertNotIn("root_cause", serialized)

    def test_graph_bounds_reject_boolean_zero_and_excessive_values(self) -> None:
        report = _report(
            units=(_unit(canonical="root.service", depth=0, event_id="evt-root"),)
        )

        for kwargs in (
            {"max_graph_nodes": True},
            {"max_graph_edges": False},
            {"max_graph_nodes": 0},
            {"max_graph_edges": 0},
            {"max_graph_nodes": 262_145},
            {"max_graph_edges": 524_289},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DependencyGraphError):
                    build_dependency_graph(report, **kwargs)

    def test_builder_rejects_untyped_report(self) -> None:
        with self.assertRaises(DependencyGraphError):
            build_dependency_graph(object())  # type: ignore[arg-type]

    def test_completeness_only_change_creates_new_version_not_new_topology(
        self,
    ) -> None:
        unit = _unit(canonical="root.service", depth=0, event_id="evt-root")
        complete = build_dependency_graph(_report(units=(unit,)))
        partial = build_dependency_graph(
            _report(
                units=(unit,),
                truncated_by_depth=True,
                unexpanded_requirement_count=2,
                max_depth=0,
            )
        )

        self.assertEqual(complete.topology_id, partial.topology_id)
        self.assertNotEqual(complete.graph_version_id, partial.graph_version_id)

    def test_added_observed_orphan_node_changes_topology(self) -> None:
        first = build_dependency_graph(
            _report(
                units=(_unit(canonical="root.service", depth=0, event_id="evt-root"),)
            )
        )
        second = build_dependency_graph(
            _report(
                units=(
                    _unit(canonical="root.service", depth=0, event_id="evt-root"),
                    _unit(canonical="extra.service", depth=1, event_id="evt-extra"),
                )
            )
        )

        delta = compare_dependency_graphs(first, second)
        self.assertTrue(delta.topology_changed)
        self.assertEqual(delta.added_nodes, ("extra.service",))

    def test_observed_node_requires_real_observation_fields(self) -> None:
        with self.assertRaises(DependencyGraphError):
            DependencyGraphNode(
                identity="root.service",
                observation=DependencyGraphNodeObservation.OBSERVED,
            )

    def test_failure_message_refresh_does_not_fake_completeness_change(self) -> None:
        unit = _unit(canonical="root.service", depth=0, event_id="evt-root")
        first_failure = SystemdDependencyDiscoveryFailure(
            requested_unit="missing.service",
            depth=1,
            error_type="SystemdDependencyCommandError",
            message="first bounded stderr",
        )
        second_failure = SystemdDependencyDiscoveryFailure(
            requested_unit="missing.service",
            depth=1,
            error_type="SystemdDependencyCommandError",
            message="second bounded stderr",
        )
        first = build_dependency_graph(
            _report(units=(unit,), failures=(first_failure,))
        )
        second = build_dependency_graph(
            _report(units=(unit,), failures=(second_failure,))
        )

        delta = compare_dependency_graphs(first, second)
        self.assertFalse(delta.topology_changed)
        self.assertFalse(delta.completeness_changed)
        self.assertTrue(delta.version_changed_without_topology_change)
        self.assertTrue(delta.evidence_refreshed_without_topology_change)

    def test_delta_edges_use_pure_topology_keys(self) -> None:
        first_edge = _evidence(
            evidence_id_hex="1",
            event_id="evt-one",
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="one.service",
        )
        second_edge = _evidence(
            evidence_id_hex="2",
            event_id="evt-two",
            subject="root.service",
            relation=DependencyRelation.REQUIRES,
            object_unit="two.service",
        )
        first = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id="evt-one",
                        evidence=(first_edge,),
                    ),
                )
            )
        )
        second = build_dependency_graph(
            _report(
                units=(
                    _unit(
                        canonical="root.service",
                        depth=0,
                        event_id="evt-two",
                        evidence=(second_edge,),
                    ),
                )
            )
        )

        delta = compare_dependency_graphs(first, second)
        self.assertEqual(
            delta.removed_edges,
            (("root.service", "requires", "one.service"),),
        )
        self.assertEqual(
            delta.added_edges,
            (("root.service", "requires", "two.service"),),
        )


if __name__ == "__main__":
    unittest.main()
