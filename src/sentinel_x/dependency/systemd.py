"""Projection of systemd observations into Phase 5A dependency evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.dependency.models import (
    DEPENDENCY_EVIDENCE_SCHEMA_VERSION,
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
from sentinel_x.systemd.boot import SystemBootIdError, normalize_boot_id
from sentinel_x.systemd.models import SystemdUnitNameError, validate_service_unit_name
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

_EVIDENCE_ID_DOMAIN: Final[bytes] = b"sentinel-x.systemd-dependency-evidence.v1\x00"
_RELATION_FIELDS: Final[tuple[tuple[DependencyRelation, str], ...]] = (
    (DependencyRelation.REQUIRES, "requires"),
    (DependencyRelation.WANTS, "wants"),
    (DependencyRelation.AFTER, "after"),
    (DependencyRelation.BEFORE, "before"),
)
_MAX_DEPENDENCY_ENTRIES: Final[int] = 4096


class SystemdDependencyEvidenceError(RuntimeError):
    """Base error for systemd dependency-evidence projection."""


class SystemdDependencyEvidenceContractError(SystemdDependencyEvidenceError):
    """Raised when a service observation violates the Phase 5A contract."""


@dataclass(frozen=True, slots=True)
class SystemdDependencyEvidenceSet:
    """One bounded evidence set projected from one service observation."""

    source_event_id: str
    requested_unit: str
    canonical_unit: str
    boot_id: str
    observed_at: datetime
    evidence: tuple[DependencyEvidence, ...]

    def __post_init__(self) -> None:
        """Validate set-level identity, provenance, and boundedness."""

        _validate_nonempty_text(self.source_event_id, field_name="source_event_id")
        _validate_service_name(
            self.requested_unit,
            field_name="requested_unit",
        )
        canonical_unit = _validate_service_name(
            self.canonical_unit,
            field_name="canonical_unit",
        )
        boot_id = _normalize_boot_id(self.boot_id)
        _validate_observed_at(self.observed_at)
        if not isinstance(self.evidence, tuple):
            raise SystemdDependencyEvidenceContractError("evidence must be a tuple")
        if len(self.evidence) > _MAX_DEPENDENCY_ENTRIES:
            raise SystemdDependencyEvidenceContractError(
                "dependency evidence exceeds the bounded entry limit"
            )
        evidence_ids: set[str] = set()
        expected_subject = DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=canonical_unit,
        )
        for item in self.evidence:
            if not isinstance(item, DependencyEvidence):
                raise SystemdDependencyEvidenceContractError(
                    "evidence must contain DependencyEvidence values"
                )
            if item.evidence_id in evidence_ids:
                raise SystemdDependencyEvidenceContractError(
                    "evidence must not contain duplicate evidence identities"
                )
            evidence_ids.add(item.evidence_id)
            if item.source_event_id != self.source_event_id:
                raise SystemdDependencyEvidenceContractError(
                    "evidence source_event_id must match the evidence set"
                )
            if item.observed_at != self.observed_at:
                raise SystemdDependencyEvidenceContractError(
                    "evidence observed_at must match the evidence set"
                )
            if item.boot_id != boot_id:
                raise SystemdDependencyEvidenceContractError(
                    "evidence boot_id must match the evidence set"
                )
            if item.subject != expected_subject:
                raise SystemdDependencyEvidenceContractError(
                    "evidence subject must match canonical_unit"
                )

    @property
    def evidence_count(self) -> int:
        """Return the total number of projected evidence edges."""

        return len(self.evidence)

    @property
    def requirement_count(self) -> int:
        """Return strong and weak requirement evidence count."""

        return sum(
            item.semantic_class
            in {
                DependencySemanticClass.STRONG_REQUIREMENT,
                DependencySemanticClass.WEAK_REQUIREMENT,
            }
            for item in self.evidence
        )

    @property
    def ordering_count(self) -> int:
        """Return ordering-only evidence count."""

        return sum(
            item.semantic_class is DependencySemanticClass.ORDERING
            for item in self.evidence
        )

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly evidence set."""

        return {
            "schema_version": DEPENDENCY_EVIDENCE_SCHEMA_VERSION,
            "source_event_id": self.source_event_id,
            "requested_unit": self.requested_unit,
            "canonical_unit": self.canonical_unit,
            "boot_id": self.boot_id,
            "observed_at": self.observed_at.isoformat(),
            "evidence_count": self.evidence_count,
            "requirement_count": self.requirement_count,
            "ordering_count": self.ordering_count,
            "causal_claims_assigned": False,
            "evidence": [item.to_dict() for item in self.evidence],
        }


def project_systemd_dependency_evidence(
    event: SentinelEvent,
) -> SystemdDependencyEvidenceSet:
    """Project exact systemd dependency properties without causal inference."""

    if not isinstance(event, SentinelEvent):
        raise SystemdDependencyEvidenceContractError("event must be a SentinelEvent")
    if event.kind is not EventKind.OBSERVATION:
        raise SystemdDependencyEvidenceContractError(
            "dependency projection accepts observation events only"
        )
    if event.source != SYSTEMD_SERVICE_OBSERVATION_SOURCE:
        raise SystemdDependencyEvidenceContractError(
            "dependency projection received an unsupported event source"
        )

    attributes = event.attributes
    observation_type = _required_text(attributes, "observation_type")
    if observation_type != SYSTEMD_SERVICE_OBSERVATION_TYPE:
        raise SystemdDependencyEvidenceContractError(
            "dependency projection received an unsupported observation type"
        )

    requested_unit = _validate_service_name(
        _required_text(attributes, "requested_name"),
        field_name="requested_name",
    )
    canonical_unit = _validate_service_name(
        _required_text(attributes, "canonical_name"),
        field_name="canonical_name",
    )
    boot_id = _normalize_boot_id(_required_text(attributes, "boot_id"))
    _required_text(attributes, "collector_name")

    try:
        subject = DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=canonical_unit,
        )
        projected: list[DependencyEvidence] = []
        total_entries = 0

        for relation, field_name in _RELATION_FIELDS:
            targets = _required_dependency_list(attributes, field_name)
            total_entries += len(targets)
            if total_entries > _MAX_DEPENDENCY_ENTRIES:
                raise SystemdDependencyEvidenceContractError(
                    "systemd observation dependency entries exceed the bounded limit"
                )
            source_property = source_property_for_relation(relation)
            for target in targets:
                object_endpoint = DependencyEndpoint(
                    kind=DependencyEntityKind.SYSTEMD_UNIT,
                    identity=target,
                )
                projected.append(
                    DependencyEvidence(
                        evidence_id=_evidence_id(
                            source_event_id=event.event_id,
                            canonical_unit=canonical_unit,
                            relation=relation,
                            target_unit=target,
                        ),
                        source_event_id=event.event_id,
                        observed_at=event.occurred_at,
                        boot_id=boot_id,
                        subject=subject,
                        relation=relation,
                        object=object_endpoint,
                        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
                        configuration_origin=(
                            DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED
                        ),
                        source_property=source_property,
                        causal_claim=False,
                    )
                )

        return SystemdDependencyEvidenceSet(
            source_event_id=event.event_id,
            requested_unit=requested_unit,
            canonical_unit=canonical_unit,
            boot_id=boot_id,
            observed_at=event.occurred_at,
            evidence=tuple(projected),
        )
    except DependencyModelError as exc:
        raise SystemdDependencyEvidenceContractError(str(exc)) from exc


def _evidence_id(
    *,
    source_event_id: str,
    canonical_unit: str,
    relation: DependencyRelation,
    target_unit: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(_EVIDENCE_ID_DOMAIN)
    for value in (
        source_event_id,
        canonical_unit,
        relation.value,
        target_unit,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\x00")
    return f"depev-{digest.hexdigest()}"


def _required_text(attributes: Mapping[str, object], field_name: str) -> str:
    if field_name not in attributes:
        raise SystemdDependencyEvidenceContractError(
            f"systemd observation is missing {field_name}"
        )
    value = attributes[field_name]
    return _validate_nonempty_text(value, field_name=field_name)


def _validate_nonempty_text(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise SystemdDependencyEvidenceContractError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    return value


def _required_dependency_list(
    attributes: Mapping[str, object],
    field_name: str,
) -> tuple[str, ...]:
    if field_name not in attributes:
        raise SystemdDependencyEvidenceContractError(
            f"systemd observation is missing {field_name}"
        )
    value = attributes[field_name]
    if not isinstance(value, list):
        raise SystemdDependencyEvidenceContractError(
            f"systemd observation field {field_name} must be a list"
        )
    if len(value) > _MAX_DEPENDENCY_ENTRIES:
        raise SystemdDependencyEvidenceContractError(
            f"systemd observation field {field_name} exceeds the bounded limit"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        normalized_item = _validate_nonempty_text(
            item,
            field_name=f"{field_name}[{index}]",
        )
        if normalized_item in seen:
            raise SystemdDependencyEvidenceContractError(
                f"systemd observation field {field_name} contains duplicates"
            )
        seen.add(normalized_item)
        normalized.append(normalized_item)
    return tuple(normalized)


def _validate_service_name(value: str, *, field_name: str) -> str:
    try:
        return validate_service_unit_name(value, field_name=field_name)
    except SystemdUnitNameError as exc:
        raise SystemdDependencyEvidenceContractError(str(exc)) from exc


def _normalize_boot_id(value: str) -> str:
    try:
        return normalize_boot_id(value, field_name="boot_id")
    except SystemBootIdError as exc:
        raise SystemdDependencyEvidenceContractError(str(exc)) from exc


def _validate_observed_at(value: datetime) -> None:
    if not isinstance(value, datetime):
        raise SystemdDependencyEvidenceContractError("observed_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise SystemdDependencyEvidenceContractError(
            "observed_at must be timezone-aware"
        )
