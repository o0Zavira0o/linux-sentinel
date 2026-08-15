"""Typed dependency-evidence contracts for Sentinel-X Phase 5A.

These models represent observed dependency declarations and ordering relations.
They deliberately do not assert causality, root cause, or probabilistic
confidence.  Later Phase 5 components may reason over this evidence, but must
preserve its provenance and semantics.
"""

from __future__ import annotations

import builtins
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

DEPENDENCY_EVIDENCE_SCHEMA_VERSION: Final[str] = "sentinel-x.dependency-evidence.v1"

_EVIDENCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"depev-[0-9a-f]{64}")
_MAX_IDENTITY_LENGTH: Final[int] = 4096
_MAX_TEXT_LENGTH: Final[int] = 512


class DependencyModelError(ValueError):
    """Raised when dependency evidence violates its typed contract."""


class DependencyEntityKind(StrEnum):
    """Entity kinds currently represented by dependency evidence."""

    SYSTEMD_UNIT = "systemd.unit"


class DependencyRelation(StrEnum):
    """Exact dependency relations currently preserved from systemd."""

    REQUIRES = "requires"
    WANTS = "wants"
    AFTER = "after"
    BEFORE = "before"

    @property
    def semantic_class(self) -> DependencySemanticClass:
        """Return the non-causal semantic class documented by systemd."""

        if self is DependencyRelation.REQUIRES:
            return DependencySemanticClass.STRONG_REQUIREMENT
        if self is DependencyRelation.WANTS:
            return DependencySemanticClass.WEAK_REQUIREMENT
        return DependencySemanticClass.ORDERING


class DependencySemanticClass(StrEnum):
    """Coarse semantics that avoid conflating ordering with requirement."""

    STRONG_REQUIREMENT = "strong_requirement"
    WEAK_REQUIREMENT = "weak_requirement"
    ORDERING = "ordering"


class DependencyEvidenceOrigin(StrEnum):
    """Observable origin from which an evidence edge was projected."""

    SYSTEMD_MANAGER_PROPERTY = "systemd.manager.property"


class DependencyConfigurationOrigin(StrEnum):
    """How precisely configuration provenance is known at Phase 5A."""

    MANAGER_MERGED_UNRESOLVED = "manager_merged_unresolved"


@dataclass(frozen=True, slots=True)
class DependencyEndpoint:
    """One endpoint in a typed dependency relation."""

    kind: DependencyEntityKind
    identity: str

    def __post_init__(self) -> None:
        """Validate endpoint identity without overconstraining systemd names."""

        if not isinstance(self.kind, DependencyEntityKind):
            raise DependencyModelError("kind must be a DependencyEntityKind")
        _validate_identity(self.identity, field_name="identity")

    def to_dict(self) -> dict[str, str]:
        """Return a stable serialization-friendly endpoint."""

        return {
            "kind": self.kind.value,
            "identity": self.identity,
        }


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    """One immutable, provenance-preserving dependency evidence edge.

    ``causal_claim`` is intentionally constrained to False.  A systemd
    dependency or ordering relation is evidence for later reasoning, not by
    itself proof that one unit caused another unit's state or failure.
    """

    evidence_id: str
    source_event_id: str
    observed_at: datetime
    boot_id: str
    subject: DependencyEndpoint
    relation: DependencyRelation
    object: DependencyEndpoint
    origin: DependencyEvidenceOrigin
    configuration_origin: DependencyConfigurationOrigin
    source_property: str
    causal_claim: bool = False

    def __post_init__(self) -> None:
        """Validate evidence identity, provenance, and non-causal contract."""

        if (
            not isinstance(self.evidence_id, str)
            or _EVIDENCE_ID_PATTERN.fullmatch(self.evidence_id) is None
        ):
            raise DependencyModelError(
                "evidence_id must be a depev- prefixed SHA-256 identity"
            )
        _validate_text(self.source_event_id, field_name="source_event_id")
        _validate_aware_datetime(self.observed_at, field_name="observed_at")
        _validate_boot_id(self.boot_id)
        if not isinstance(self.subject, DependencyEndpoint):
            raise DependencyModelError("subject must be a DependencyEndpoint")
        if not isinstance(self.relation, DependencyRelation):
            raise DependencyModelError("relation must be a DependencyRelation")
        if not isinstance(self.object, DependencyEndpoint):
            raise DependencyModelError("object must be a DependencyEndpoint")
        if not isinstance(self.origin, DependencyEvidenceOrigin):
            raise DependencyModelError("origin must be a DependencyEvidenceOrigin")
        if not isinstance(self.configuration_origin, DependencyConfigurationOrigin):
            raise DependencyModelError(
                "configuration_origin must be a DependencyConfigurationOrigin"
            )
        _validate_text(self.source_property, field_name="source_property")
        if type(self.causal_claim) is not bool:
            raise DependencyModelError("causal_claim must be a boolean")
        if self.causal_claim:
            raise DependencyModelError(
                "Phase 5A dependency evidence must not assert causality"
            )
        expected_property = _SOURCE_PROPERTY_BY_RELATION[self.relation]
        if self.source_property != expected_property:
            raise DependencyModelError(
                "source_property must match the exact dependency relation"
            )
        if self.subject.kind is not DependencyEntityKind.SYSTEMD_UNIT:
            raise DependencyModelError(
                "Phase 5A subject must be a systemd unit endpoint"
            )
        if self.object.kind is not DependencyEntityKind.SYSTEMD_UNIT:
            raise DependencyModelError(
                "Phase 5A object must be a systemd unit endpoint"
            )

    @property
    def semantic_class(self) -> DependencySemanticClass:
        """Return the documented non-causal semantic class of this relation."""

        return self.relation.semantic_class

    def to_dict(self) -> dict[str, builtins.object]:
        """Return a stable serialization-friendly evidence representation."""

        return {
            "schema_version": DEPENDENCY_EVIDENCE_SCHEMA_VERSION,
            "evidence_id": self.evidence_id,
            "source_event_id": self.source_event_id,
            "observed_at": self.observed_at.isoformat(),
            "boot_id": self.boot_id,
            "subject": self.subject.to_dict(),
            "relation": self.relation.value,
            "semantic_class": self.semantic_class.value,
            "object": self.object.to_dict(),
            "origin": self.origin.value,
            "configuration_origin": self.configuration_origin.value,
            "source_property": self.source_property,
            "causal_claim": self.causal_claim,
        }


_SOURCE_PROPERTY_BY_RELATION: Final[dict[DependencyRelation, str]] = {
    DependencyRelation.REQUIRES: "Requires",
    DependencyRelation.WANTS: "Wants",
    DependencyRelation.AFTER: "After",
    DependencyRelation.BEFORE: "Before",
}


def source_property_for_relation(relation: DependencyRelation) -> str:
    """Return the exact systemd manager property backing one relation."""

    if not isinstance(relation, DependencyRelation):
        raise DependencyModelError("relation must be a DependencyRelation")
    return _SOURCE_PROPERTY_BY_RELATION[relation]


def _validate_identity(value: str, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise DependencyModelError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise DependencyModelError(
            f"{field_name} must be non-empty and contain no surrounding whitespace"
        )
    if "\x00" in value:
        raise DependencyModelError(f"{field_name} must not contain NUL bytes")
    if len(value) > _MAX_IDENTITY_LENGTH:
        raise DependencyModelError(f"{field_name} is too long")


def _validate_text(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DependencyModelError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    if "\x00" in value:
        raise DependencyModelError(f"{field_name} must not contain NUL bytes")
    if len(value) > _MAX_TEXT_LENGTH:
        raise DependencyModelError(f"{field_name} is too long")


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise DependencyModelError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DependencyModelError(f"{field_name} must be timezone-aware")


def _validate_boot_id(value: str) -> None:
    if not isinstance(value, str) or len(value) != 32:
        raise DependencyModelError("boot_id must be a 32-character identity")
    if not all(character in "0123456789abcdef" for character in value):
        raise DependencyModelError("boot_id must contain lowercase hexadecimal text")
