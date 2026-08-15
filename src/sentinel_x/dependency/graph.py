"""Versioned non-causal dependency evidence graph for Sentinel-X Phase 5C.

Phase 5C projects one bounded Phase 5B discovery report into a graph that
preserves the distinction between successfully observed units and units that
are only referenced by dependency evidence.  The graph carries two distinct
identities:

* ``topology_id`` fingerprints structural node/edge topology and is stable
  across evidence refreshes that preserve the same topology.
* ``graph_version_id`` fingerprints the concrete observation/evidence version,
  including coverage and explicit incompleteness.

Neither identity, nor any graph edge, asserts causality or root cause.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from sentinel_x.dependency.discovery import (
    SystemdDependencyDiscoveryFailure,
    SystemdDependencyDiscoveryReport,
    validate_systemd_unit_identity,
)
from sentinel_x.dependency.models import DependencyEvidence, DependencyRelation

DEPENDENCY_GRAPH_SCHEMA_VERSION: Final[str] = "sentinel-x.dependency-graph.v1"

_DEFAULT_MAX_GRAPH_NODES: Final[int] = 32_768
_DEFAULT_MAX_GRAPH_EDGES: Final[int] = 65_536
_MAX_GRAPH_NODES: Final[int] = 262_144
_MAX_GRAPH_EDGES: Final[int] = 524_288

_TOPOLOGY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"deptopo-[0-9a-f]{64}")
_GRAPH_VERSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"depgraphv-[0-9a-f]{64}"
)


class DependencyGraphError(RuntimeError):
    """Base error for Phase 5C graph contracts."""


class DependencyGraphCapacityError(DependencyGraphError):
    """Raised rather than silently dropping graph nodes or edges."""


class DependencyGraphNodeObservation(StrEnum):
    """Whether a graph node was directly observed in the discovery report."""

    OBSERVED = "observed"
    REFERENCED_ONLY = "referenced_only"


@dataclass(frozen=True, slots=True)
class DependencyGraphNode:
    """One systemd-unit node with explicit observation coverage."""

    identity: str
    observation: DependencyGraphNodeObservation
    depth: int | None = None
    names: tuple[str, ...] = ()
    load_state: str | None = None
    source_event_id: str | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_systemd_unit_identity(self.identity, field_name="identity")
        if not isinstance(self.observation, DependencyGraphNodeObservation):
            raise DependencyGraphError(
                "observation must be a DependencyGraphNodeObservation"
            )

        if self.observation is DependencyGraphNodeObservation.OBSERVED:
            if (
                isinstance(self.depth, bool)
                or not isinstance(self.depth, int)
                or self.depth < 0
            ):
                raise DependencyGraphError(
                    "observed nodes require a non-negative integer depth"
                )
            _validate_names(self.names, identity=self.identity)
            _validate_nonempty_text(self.load_state, field_name="load_state")
            _validate_nonempty_text(
                self.source_event_id,
                field_name="source_event_id",
            )
            _validate_aware_datetime(self.observed_at, field_name="observed_at")
            return

        if self.depth is not None:
            raise DependencyGraphError("referenced-only nodes must not invent depth")
        if self.names:
            raise DependencyGraphError("referenced-only nodes must not invent aliases")
        if self.load_state is not None:
            raise DependencyGraphError(
                "referenced-only nodes must not invent load_state"
            )
        if self.source_event_id is not None:
            raise DependencyGraphError(
                "referenced-only nodes must not invent source_event_id"
            )
        if self.observed_at is not None:
            raise DependencyGraphError(
                "referenced-only nodes must not invent observed_at"
            )

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly node without fabricated fields."""

        payload: dict[str, object] = {
            "identity": self.identity,
            "observation": self.observation.value,
        }
        if self.observation is DependencyGraphNodeObservation.OBSERVED:
            payload.update(
                {
                    "depth": self.depth,
                    "names": list(self.names),
                    "load_state": self.load_state,
                    "source_event_id": self.source_event_id,
                    "observed_at": self.observed_at.isoformat()
                    if self.observed_at is not None
                    else None,
                }
            )
        return payload


@dataclass(frozen=True, slots=True)
class DependencyGraphEdge:
    """One graph edge that preserves its original Phase 5A evidence."""

    evidence: DependencyEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, DependencyEvidence):
            raise DependencyGraphError("evidence must be a DependencyEvidence")
        if self.evidence.causal_claim:
            raise DependencyGraphError("dependency graph edges must remain non-causal")

    @property
    def subject_identity(self) -> str:
        return self.evidence.subject.identity

    @property
    def relation(self) -> DependencyRelation:
        return self.evidence.relation

    @property
    def object_identity(self) -> str:
        return self.evidence.object.identity

    @property
    def topology_key(self) -> tuple[str, str, str]:
        """Return the pure topology key, independent of evidence provenance."""

        return (
            self.subject_identity,
            self.relation.value,
            self.object_identity,
        )

    @property
    def structural_key(self) -> tuple[str, str, str, str, str, str]:
        """Return an evidence-semantic key without volatile observation identity."""

        return (
            self.subject_identity,
            self.relation.value,
            self.object_identity,
            self.evidence.origin.value,
            self.evidence.configuration_origin.value,
            self.evidence.source_property,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject_identity,
            "relation": self.relation.value,
            "object": self.object_identity,
            "evidence": self.evidence.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DependencyGraphSnapshot:
    """One immutable evidence graph version with explicit coverage metadata."""

    graph_version_id: str
    topology_id: str
    root_requested_unit: str
    root_canonical_unit: str
    boot_id: str
    max_depth: int
    max_units: int
    max_graph_nodes: int
    max_graph_edges: int
    nodes: tuple[DependencyGraphNode, ...]
    edges: tuple[DependencyGraphEdge, ...]
    failures: tuple[SystemdDependencyDiscoveryFailure, ...]
    truncated_by_depth: bool
    unexpanded_requirement_count: int

    def __post_init__(self) -> None:
        if _GRAPH_VERSION_ID_PATTERN.fullmatch(self.graph_version_id) is None:
            raise DependencyGraphError(
                "graph_version_id must be a depgraphv- prefixed SHA-256 identity"
            )
        if _TOPOLOGY_ID_PATTERN.fullmatch(self.topology_id) is None:
            raise DependencyGraphError(
                "topology_id must be a deptopo- prefixed SHA-256 identity"
            )
        validate_systemd_unit_identity(
            self.root_requested_unit,
            field_name="root_requested_unit",
        )
        validate_systemd_unit_identity(
            self.root_canonical_unit,
            field_name="root_canonical_unit",
        )
        _validate_boot_id(self.boot_id)
        _validate_nonnegative_int(self.max_depth, field_name="max_depth")
        _validate_positive_int(self.max_units, field_name="max_units")
        _validate_graph_bounds(self.max_graph_nodes, self.max_graph_edges)
        if not isinstance(self.nodes, tuple) or not self.nodes:
            raise DependencyGraphError("nodes must be a non-empty tuple")
        if not isinstance(self.edges, tuple):
            raise DependencyGraphError("edges must be a tuple")
        if not isinstance(self.failures, tuple):
            raise DependencyGraphError("failures must be a tuple")
        if type(self.truncated_by_depth) is not bool:
            raise DependencyGraphError("truncated_by_depth must be a boolean")
        _validate_nonnegative_int(
            self.unexpanded_requirement_count,
            field_name="unexpanded_requirement_count",
        )
        if self.truncated_by_depth != (self.unexpanded_requirement_count > 0):
            raise DependencyGraphError(
                "truncated_by_depth must match unexpanded_requirement_count"
            )
        if len(self.nodes) > self.max_graph_nodes:
            raise DependencyGraphError("nodes exceed max_graph_nodes")
        if len(self.edges) > self.max_graph_edges:
            raise DependencyGraphError("edges exceed max_graph_edges")

        for node in self.nodes:
            if not isinstance(node, DependencyGraphNode):
                raise DependencyGraphError(
                    "nodes must contain DependencyGraphNode values"
                )
        for edge in self.edges:
            if not isinstance(edge, DependencyGraphEdge):
                raise DependencyGraphError(
                    "edges must contain DependencyGraphEdge values"
                )
        for failure in self.failures:
            if not isinstance(failure, SystemdDependencyDiscoveryFailure):
                raise DependencyGraphError(
                    "failures must contain SystemdDependencyDiscoveryFailure values"
                )

        node_identities = tuple(node.identity for node in self.nodes)
        if node_identities != tuple(sorted(node_identities)):
            raise DependencyGraphError("nodes must be sorted by identity")
        if len(set(node_identities)) != len(node_identities):
            raise DependencyGraphError("nodes must have unique identities")
        if self.root_canonical_unit not in node_identities:
            raise DependencyGraphError("root_canonical_unit must be a graph node")

        observed_identities = {
            node.identity
            for node in self.nodes
            if node.observation is DependencyGraphNodeObservation.OBSERVED
        }
        if self.root_canonical_unit not in observed_identities:
            raise DependencyGraphError("root_canonical_unit must be observed")

        edge_keys = tuple(edge.structural_key for edge in self.edges)
        if edge_keys != tuple(sorted(edge_keys)):
            raise DependencyGraphError("edges must be sorted by structural key")
        topology_edge_keys = tuple(edge.topology_key for edge in self.edges)
        if len(set(topology_edge_keys)) != len(topology_edge_keys):
            raise DependencyGraphError("edges must have unique topology keys")

        evidence_ids: set[str] = set()
        all_node_identities = set(node_identities)
        for edge in self.edges:
            if edge.evidence.evidence_id in evidence_ids:
                raise DependencyGraphError("edges must have unique evidence identities")
            evidence_ids.add(edge.evidence.evidence_id)
            if edge.evidence.boot_id != self.boot_id:
                raise DependencyGraphError("edge boot_id must match graph boot_id")
            if edge.subject_identity not in observed_identities:
                raise DependencyGraphError("edge subjects must be observed nodes")
            if edge.object_identity not in all_node_identities:
                raise DependencyGraphError("edge objects must be graph nodes")

        expected_topology_id = _topology_id(
            root_canonical_unit=self.root_canonical_unit,
            nodes=self.nodes,
            edges=self.edges,
        )
        if self.topology_id != expected_topology_id:
            raise DependencyGraphError("topology_id does not match graph structure")
        expected_graph_version_id = _graph_version_id_from_parts(
            topology_id=self.topology_id,
            root_requested_unit=self.root_requested_unit,
            root_canonical_unit=self.root_canonical_unit,
            boot_id=self.boot_id,
            max_depth=self.max_depth,
            max_units=self.max_units,
            max_graph_nodes=self.max_graph_nodes,
            max_graph_edges=self.max_graph_edges,
            nodes=self.nodes,
            edges=self.edges,
            failures=self.failures,
            truncated_by_depth=self.truncated_by_depth,
            unexpanded_requirement_count=self.unexpanded_requirement_count,
        )
        if self.graph_version_id != expected_graph_version_id:
            raise DependencyGraphError(
                "graph_version_id does not match observation/evidence version"
            )

    @property
    def observed_node_count(self) -> int:
        return sum(
            node.observation is DependencyGraphNodeObservation.OBSERVED
            for node in self.nodes
        )

    @property
    def referenced_only_node_count(self) -> int:
        return len(self.nodes) - self.observed_node_count

    @property
    def complete_within_scope(self) -> bool:
        return not self.failures and not self.truncated_by_depth

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DEPENDENCY_GRAPH_SCHEMA_VERSION,
            "graph_version_id": self.graph_version_id,
            "topology_id": self.topology_id,
            "root_requested_unit": self.root_requested_unit,
            "root_canonical_unit": self.root_canonical_unit,
            "boot_id": self.boot_id,
            "max_depth": self.max_depth,
            "max_units": self.max_units,
            "max_graph_nodes": self.max_graph_nodes,
            "max_graph_edges": self.max_graph_edges,
            "observed_relations": [
                DependencyRelation.REQUIRES.value,
                DependencyRelation.WANTS.value,
                DependencyRelation.AFTER.value,
                DependencyRelation.BEFORE.value,
            ],
            "comprehensive_systemd_relation_scope": False,
            "causal_claims_assigned": False,
            "complete_within_scope": self.complete_within_scope,
            "truncated_by_depth": self.truncated_by_depth,
            "unexpanded_requirement_count": self.unexpanded_requirement_count,
            "node_count": len(self.nodes),
            "observed_node_count": self.observed_node_count,
            "referenced_only_node_count": self.referenced_only_node_count,
            "edge_count": len(self.edges),
            "failure_count": len(self.failures),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "failures": [failure.to_dict() for failure in self.failures],
        }


@dataclass(frozen=True, slots=True)
class DependencyGraphNodeObservationChange:
    """Coverage change for a node that remains structurally present."""

    identity: str
    previous: DependencyGraphNodeObservation
    current: DependencyGraphNodeObservation

    def __post_init__(self) -> None:
        validate_systemd_unit_identity(self.identity, field_name="identity")
        if not isinstance(self.previous, DependencyGraphNodeObservation):
            raise DependencyGraphError(
                "previous must be a DependencyGraphNodeObservation"
            )
        if not isinstance(self.current, DependencyGraphNodeObservation):
            raise DependencyGraphError(
                "current must be a DependencyGraphNodeObservation"
            )
        if self.previous is self.current:
            raise DependencyGraphError("observation change must change state")

    def to_dict(self) -> dict[str, str]:
        return {
            "identity": self.identity,
            "previous": self.previous.value,
            "current": self.current.value,
        }


@dataclass(frozen=True, slots=True)
class DependencyGraphDelta:
    """Structural and coverage delta between two graph versions."""

    previous_graph_version_id: str
    current_graph_version_id: str
    previous_topology_id: str
    current_topology_id: str
    previous_boot_id: str
    current_boot_id: str
    added_nodes: tuple[str, ...]
    removed_nodes: tuple[str, ...]
    added_edges: tuple[tuple[str, str, str], ...]
    removed_edges: tuple[tuple[str, str, str], ...]
    observation_changes: tuple[DependencyGraphNodeObservationChange, ...]
    completeness_changed: bool

    def __post_init__(self) -> None:
        if _GRAPH_VERSION_ID_PATTERN.fullmatch(self.previous_graph_version_id) is None:
            raise DependencyGraphError("previous_graph_version_id is invalid")
        if _GRAPH_VERSION_ID_PATTERN.fullmatch(self.current_graph_version_id) is None:
            raise DependencyGraphError("current_graph_version_id is invalid")
        if _TOPOLOGY_ID_PATTERN.fullmatch(self.previous_topology_id) is None:
            raise DependencyGraphError("previous_topology_id is invalid")
        if _TOPOLOGY_ID_PATTERN.fullmatch(self.current_topology_id) is None:
            raise DependencyGraphError("current_topology_id is invalid")
        _validate_boot_id(self.previous_boot_id)
        _validate_boot_id(self.current_boot_id)
        for field_name, values in (
            ("added_nodes", self.added_nodes),
            ("removed_nodes", self.removed_nodes),
        ):
            if not isinstance(values, tuple):
                raise DependencyGraphError(f"{field_name} must be a tuple")
            if values != tuple(sorted(values)) or len(set(values)) != len(values):
                raise DependencyGraphError(f"{field_name} must be sorted and unique")
            for value in values:
                validate_systemd_unit_identity(value, field_name=f"{field_name} entry")
        for field_name, edge_values in (
            ("added_edges", self.added_edges),
            ("removed_edges", self.removed_edges),
        ):
            if not isinstance(edge_values, tuple):
                raise DependencyGraphError(f"{field_name} must be a tuple")
            if edge_values != tuple(sorted(edge_values)) or len(
                set(edge_values)
            ) != len(edge_values):
                raise DependencyGraphError(f"{field_name} must be sorted and unique")
            for edge_value in edge_values:
                _validate_topology_edge_key(edge_value, field_name=field_name)
        if not isinstance(self.observation_changes, tuple):
            raise DependencyGraphError("observation_changes must be a tuple")
        for change in self.observation_changes:
            if not isinstance(change, DependencyGraphNodeObservationChange):
                raise DependencyGraphError(
                    "observation_changes must contain typed changes"
                )
        if type(self.completeness_changed) is not bool:
            raise DependencyGraphError("completeness_changed must be a boolean")

    @property
    def topology_changed(self) -> bool:
        return self.previous_topology_id != self.current_topology_id

    @property
    def boot_changed(self) -> bool:
        return self.previous_boot_id != self.current_boot_id

    @property
    def version_changed_without_topology_change(self) -> bool:
        return (
            self.previous_graph_version_id != self.current_graph_version_id
            and not self.topology_changed
        )

    @property
    def evidence_refreshed_without_topology_change(self) -> bool:
        return (
            self.version_changed_without_topology_change
            and not self.boot_changed
            and not self.observation_changes
            and not self.completeness_changed
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "previous_graph_version_id": self.previous_graph_version_id,
            "current_graph_version_id": self.current_graph_version_id,
            "previous_topology_id": self.previous_topology_id,
            "current_topology_id": self.current_topology_id,
            "previous_boot_id": self.previous_boot_id,
            "current_boot_id": self.current_boot_id,
            "boot_changed": self.boot_changed,
            "topology_changed": self.topology_changed,
            "version_changed_without_topology_change": (
                self.version_changed_without_topology_change
            ),
            "evidence_refreshed_without_topology_change": (
                self.evidence_refreshed_without_topology_change
            ),
            "added_nodes": list(self.added_nodes),
            "removed_nodes": list(self.removed_nodes),
            "added_edges": [list(key) for key in self.added_edges],
            "removed_edges": [list(key) for key in self.removed_edges],
            "observation_changes": [
                change.to_dict() for change in self.observation_changes
            ],
            "completeness_changed": self.completeness_changed,
            "causal_claims_assigned": False,
        }


def build_dependency_graph(
    report: SystemdDependencyDiscoveryReport,
    *,
    max_graph_nodes: int = _DEFAULT_MAX_GRAPH_NODES,
    max_graph_edges: int = _DEFAULT_MAX_GRAPH_EDGES,
) -> DependencyGraphSnapshot:
    """Build one deterministic bounded graph from Phase 5B discovery evidence."""

    if not isinstance(report, SystemdDependencyDiscoveryReport):
        raise DependencyGraphError("report must be a SystemdDependencyDiscoveryReport")
    _validate_graph_bounds(max_graph_nodes, max_graph_edges)

    observed_by_identity: dict[str, DependencyGraphNode] = {}
    edges: list[DependencyGraphEdge] = []
    referenced_identities: set[str] = set()
    topology_edge_keys: set[tuple[str, str, str]] = set()
    evidence_ids: set[str] = set()

    for unit in report.units:
        identity = unit.snapshot.canonical_name
        if identity in observed_by_identity:
            raise DependencyGraphError(
                "discovery report contains duplicate observed canonical identities"
            )
        observed_by_identity[identity] = DependencyGraphNode(
            identity=identity,
            observation=DependencyGraphNodeObservation.OBSERVED,
            depth=unit.depth,
            names=unit.snapshot.names,
            load_state=unit.snapshot.load_state,
            source_event_id=unit.source_event.event_id,
            observed_at=unit.snapshot.captured_at,
        )
        if len(observed_by_identity) > max_graph_nodes:
            raise DependencyGraphCapacityError(
                "observed graph nodes exceed max_graph_nodes; no node was evicted"
            )

        for evidence in unit.evidence:
            edge = DependencyGraphEdge(evidence=evidence)
            if edge.evidence.evidence_id in evidence_ids:
                raise DependencyGraphError(
                    "discovery report contains duplicate evidence identities"
                )
            if edge.topology_key in topology_edge_keys:
                raise DependencyGraphError(
                    "discovery report contains duplicate topology dependency edges"
                )
            evidence_ids.add(edge.evidence.evidence_id)
            topology_edge_keys.add(edge.topology_key)
            edges.append(edge)
            if len(edges) > max_graph_edges:
                raise DependencyGraphCapacityError(
                    "graph edges exceed max_graph_edges; no edge was evicted"
                )
            referenced_identities.add(edge.object_identity)

    all_identities = set(observed_by_identity)
    all_identities.update(referenced_identities)
    if len(all_identities) > max_graph_nodes:
        raise DependencyGraphCapacityError(
            "graph nodes exceed max_graph_nodes after referenced endpoints; "
            "no node was evicted"
        )

    nodes = list(observed_by_identity.values())
    for identity in sorted(referenced_identities - set(observed_by_identity)):
        nodes.append(
            DependencyGraphNode(
                identity=identity,
                observation=DependencyGraphNodeObservation.REFERENCED_ONLY,
            )
        )

    sorted_nodes = tuple(sorted(nodes, key=lambda node: node.identity))
    sorted_edges = tuple(sorted(edges, key=lambda edge: edge.structural_key))
    topology_id = _topology_id(
        root_canonical_unit=report.root_canonical_unit,
        nodes=sorted_nodes,
        edges=sorted_edges,
    )
    graph_version_id = _graph_version_id_from_parts(
        topology_id=topology_id,
        root_requested_unit=report.root_requested_unit,
        root_canonical_unit=report.root_canonical_unit,
        boot_id=report.boot_id,
        max_depth=report.max_depth,
        max_units=report.max_units,
        max_graph_nodes=max_graph_nodes,
        max_graph_edges=max_graph_edges,
        nodes=sorted_nodes,
        edges=sorted_edges,
        failures=report.failures,
        truncated_by_depth=report.truncated_by_depth,
        unexpanded_requirement_count=report.unexpanded_requirement_count,
    )
    return DependencyGraphSnapshot(
        graph_version_id=graph_version_id,
        topology_id=topology_id,
        root_requested_unit=report.root_requested_unit,
        root_canonical_unit=report.root_canonical_unit,
        boot_id=report.boot_id,
        max_depth=report.max_depth,
        max_units=report.max_units,
        max_graph_nodes=max_graph_nodes,
        max_graph_edges=max_graph_edges,
        nodes=sorted_nodes,
        edges=sorted_edges,
        failures=report.failures,
        truncated_by_depth=report.truncated_by_depth,
        unexpanded_requirement_count=report.unexpanded_requirement_count,
    )


def compare_dependency_graphs(
    previous: DependencyGraphSnapshot,
    current: DependencyGraphSnapshot,
) -> DependencyGraphDelta:
    """Compare graph versions without conflating evidence refresh with topology drift."""

    if not isinstance(previous, DependencyGraphSnapshot):
        raise DependencyGraphError("previous must be a DependencyGraphSnapshot")
    if not isinstance(current, DependencyGraphSnapshot):
        raise DependencyGraphError("current must be a DependencyGraphSnapshot")
    if previous.root_canonical_unit != current.root_canonical_unit:
        raise DependencyGraphError("graph comparison requires the same canonical root")

    previous_nodes = {node.identity: node for node in previous.nodes}
    current_nodes = {node.identity: node for node in current.nodes}
    previous_node_ids = set(previous_nodes)
    current_node_ids = set(current_nodes)
    shared_node_ids = previous_node_ids & current_node_ids

    observation_changes = tuple(
        DependencyGraphNodeObservationChange(
            identity=identity,
            previous=previous_nodes[identity].observation,
            current=current_nodes[identity].observation,
        )
        for identity in sorted(shared_node_ids)
        if previous_nodes[identity].observation
        is not current_nodes[identity].observation
    )

    previous_edge_keys = {edge.topology_key for edge in previous.edges}
    current_edge_keys = {edge.topology_key for edge in current.edges}

    return DependencyGraphDelta(
        previous_graph_version_id=previous.graph_version_id,
        current_graph_version_id=current.graph_version_id,
        previous_topology_id=previous.topology_id,
        current_topology_id=current.topology_id,
        previous_boot_id=previous.boot_id,
        current_boot_id=current.boot_id,
        added_nodes=tuple(sorted(current_node_ids - previous_node_ids)),
        removed_nodes=tuple(sorted(previous_node_ids - current_node_ids)),
        added_edges=tuple(sorted(current_edge_keys - previous_edge_keys)),
        removed_edges=tuple(sorted(previous_edge_keys - current_edge_keys)),
        observation_changes=observation_changes,
        completeness_changed=(
            previous.complete_within_scope != current.complete_within_scope
            or previous.truncated_by_depth != current.truncated_by_depth
            or previous.unexpanded_requirement_count
            != current.unexpanded_requirement_count
            or {_failure_scope_key(failure) for failure in previous.failures}
            != {_failure_scope_key(failure) for failure in current.failures}
        ),
    )


def _topology_id(
    *,
    root_canonical_unit: str,
    nodes: tuple[DependencyGraphNode, ...],
    edges: tuple[DependencyGraphEdge, ...],
) -> str:
    payload: dict[str, object] = {
        "schema_version": DEPENDENCY_GRAPH_SCHEMA_VERSION,
        "root_canonical_unit": root_canonical_unit,
        "nodes": [node.identity for node in nodes],
        "edges": [list(edge.topology_key) for edge in edges],
    }
    return "deptopo-" + _sha256_json(payload)


def _graph_version_id_from_parts(
    *,
    topology_id: str,
    root_requested_unit: str,
    root_canonical_unit: str,
    boot_id: str,
    max_depth: int,
    max_units: int,
    max_graph_nodes: int,
    max_graph_edges: int,
    nodes: tuple[DependencyGraphNode, ...],
    edges: tuple[DependencyGraphEdge, ...],
    failures: tuple[SystemdDependencyDiscoveryFailure, ...],
    truncated_by_depth: bool,
    unexpanded_requirement_count: int,
) -> str:
    payload = {
        "schema_version": DEPENDENCY_GRAPH_SCHEMA_VERSION,
        "topology_id": topology_id,
        "root_requested_unit": root_requested_unit,
        "root_canonical_unit": root_canonical_unit,
        "boot_id": boot_id,
        "max_depth": max_depth,
        "max_units": max_units,
        "max_graph_nodes": max_graph_nodes,
        "max_graph_edges": max_graph_edges,
        "nodes": [node.to_dict() for node in nodes],
        "edges": [edge.to_dict() for edge in edges],
        "failures": [failure.to_dict() for failure in failures],
        "truncated_by_depth": truncated_by_depth,
        "unexpanded_requirement_count": unexpanded_requirement_count,
    }
    return "depgraphv-" + _sha256_json(payload)


def _sha256_json(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failure_scope_key(
    failure: SystemdDependencyDiscoveryFailure,
) -> tuple[str, int, str]:
    return (failure.requested_unit, failure.depth, failure.error_type)


def _validate_topology_edge_key(value: object, *, field_name: str) -> None:
    if not isinstance(value, tuple) or len(value) != 3:
        raise DependencyGraphError(
            f"{field_name} entries must be three-part topology keys"
        )
    subject, relation, object_identity = value
    validate_systemd_unit_identity(subject, field_name=f"{field_name} subject")
    if not isinstance(relation, str) or relation not in {
        item.value for item in DependencyRelation
    }:
        raise DependencyGraphError(f"{field_name} relation is invalid")
    validate_systemd_unit_identity(
        object_identity,
        field_name=f"{field_name} object",
    )


def _validate_graph_bounds(max_graph_nodes: int, max_graph_edges: int) -> None:
    _validate_positive_int(max_graph_nodes, field_name="max_graph_nodes")
    _validate_positive_int(max_graph_edges, field_name="max_graph_edges")
    if max_graph_nodes > _MAX_GRAPH_NODES:
        raise DependencyGraphError(f"max_graph_nodes must be <= {_MAX_GRAPH_NODES}")
    if max_graph_edges > _MAX_GRAPH_EDGES:
        raise DependencyGraphError(f"max_graph_edges must be <= {_MAX_GRAPH_EDGES}")


def _validate_names(values: tuple[str, ...], *, identity: str) -> None:
    if not isinstance(values, tuple) or not values:
        raise DependencyGraphError("observed node names must be a non-empty tuple")
    if identity not in values:
        raise DependencyGraphError("observed node names must include identity")
    if len(set(values)) != len(values):
        raise DependencyGraphError("observed node names must be unique")
    for value in values:
        validate_systemd_unit_identity(value, field_name="names entry")


def _validate_nonempty_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DependencyGraphError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if "\x00" in value:
        raise DependencyGraphError(f"{field_name} must not contain NUL bytes")
    return value


def _validate_aware_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DependencyGraphError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DependencyGraphError(f"{field_name} must be timezone-aware")
    return value


def _validate_boot_id(value: str) -> None:
    if not isinstance(value, str) or len(value) != 32:
        raise DependencyGraphError("boot_id must be a 32-character identity")
    if not all(character in "0123456789abcdef" for character in value):
        raise DependencyGraphError("boot_id must contain lowercase hexadecimal text")


def _validate_nonnegative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DependencyGraphError(f"{field_name} must be a non-negative integer")


def _validate_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DependencyGraphError(f"{field_name} must be a positive integer")
