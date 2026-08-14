"""Typed temporal-correlation models for systemd and journald evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SystemdCorrelationModelError(ValueError):
    """Raised when temporal-correlation data violates its typed contract."""


class SystemdCorrelationBasis(StrEnum):
    """Evidence basis used to correlate one journal entry to a service state."""

    EXACT_INVOCATION = "exact_invocation"
    UNIT_AND_TIME = "unit_and_time"


class SystemdTemporalRelation(StrEnum):
    """Position of a journal entry relative to the latest service transition."""

    BEFORE = "before"
    AT = "at"
    AFTER = "after"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SystemdCorrelationMatch:
    """One journal entry correlated to one systemd service observation."""

    service_event_id: str
    journal_event_id: str
    journal_entry_index: int
    requested_unit: str
    canonical_unit: str
    boot_id: str
    basis: SystemdCorrelationBasis
    temporal_relation: SystemdTemporalRelation
    monotonic_delta_usec: int | None
    service_invocation_id: str | None
    matched_journal_invocation_id: str | None
    journal_cursor_sha256: str
    journal_priority: int | None
    journal_message: str | None

    def __post_init__(self) -> None:
        """Validate bounded correlation-match invariants."""

        for field_name, value in (
            ("service_event_id", self.service_event_id),
            ("journal_event_id", self.journal_event_id),
            ("requested_unit", self.requested_unit),
            ("canonical_unit", self.canonical_unit),
            ("boot_id", self.boot_id),
            ("journal_cursor_sha256", self.journal_cursor_sha256),
        ):
            if not isinstance(value, str) or not value.strip():
                raise SystemdCorrelationModelError(
                    f"{field_name} must be a non-empty string"
                )
        if isinstance(self.journal_entry_index, bool) or not isinstance(
            self.journal_entry_index, int
        ):
            raise SystemdCorrelationModelError("journal_entry_index must be an integer")
        if self.journal_entry_index < 0:
            raise SystemdCorrelationModelError(
                "journal_entry_index must be non-negative"
            )
        if len(self.boot_id) != 32:
            raise SystemdCorrelationModelError("boot_id must be 32 characters")
        if len(self.journal_cursor_sha256) != 64:
            raise SystemdCorrelationModelError(
                "journal_cursor_sha256 must be a SHA-256 hexadecimal digest"
            )
        if self.monotonic_delta_usec is not None:
            if isinstance(self.monotonic_delta_usec, bool) or not isinstance(
                self.monotonic_delta_usec, int
            ):
                raise SystemdCorrelationModelError(
                    "monotonic_delta_usec must be an integer or None"
                )
        if self.journal_priority is not None:
            if isinstance(self.journal_priority, bool) or not isinstance(
                self.journal_priority, int
            ):
                raise SystemdCorrelationModelError(
                    "journal_priority must be an integer or None"
                )
            if not 0 <= self.journal_priority <= 7:
                raise SystemdCorrelationModelError(
                    "journal_priority must be between 0 and 7"
                )

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly match representation."""

        return {
            "service_event_id": self.service_event_id,
            "journal_event_id": self.journal_event_id,
            "journal_entry_index": self.journal_entry_index,
            "requested_unit": self.requested_unit,
            "canonical_unit": self.canonical_unit,
            "boot_id": self.boot_id,
            "basis": self.basis.value,
            "temporal_relation": self.temporal_relation.value,
            "monotonic_delta_usec": self.monotonic_delta_usec,
            "service_invocation_id": self.service_invocation_id,
            "matched_journal_invocation_id": self.matched_journal_invocation_id,
            "journal_cursor_sha256": self.journal_cursor_sha256,
            "journal_priority": self.journal_priority,
            "journal_message": self.journal_message,
        }


@dataclass(frozen=True, slots=True)
class SystemdTemporalCorrelationReport:
    """Deterministic correlation result for one service and one journal event."""

    service_event_id: str
    journal_event_id: str
    requested_unit: str
    canonical_unit: str
    boot_id: str
    evaluated_entry_count: int
    matches: tuple[SystemdCorrelationMatch, ...]

    def __post_init__(self) -> None:
        """Validate report-level invariants."""

        if isinstance(self.evaluated_entry_count, bool) or not isinstance(
            self.evaluated_entry_count, int
        ):
            raise SystemdCorrelationModelError(
                "evaluated_entry_count must be an integer"
            )
        if self.evaluated_entry_count < 0:
            raise SystemdCorrelationModelError(
                "evaluated_entry_count must be non-negative"
            )
        if len(self.matches) > self.evaluated_entry_count:
            raise SystemdCorrelationModelError(
                "match count cannot exceed evaluated entry count"
            )
        for match in self.matches:
            if not isinstance(match, SystemdCorrelationMatch):
                raise SystemdCorrelationModelError(
                    "matches must contain SystemdCorrelationMatch values"
                )
            if match.service_event_id != self.service_event_id:
                raise SystemdCorrelationModelError(
                    "match service_event_id must match report"
                )
            if match.journal_event_id != self.journal_event_id:
                raise SystemdCorrelationModelError(
                    "match journal_event_id must match report"
                )

    @property
    def match_count(self) -> int:
        """Return the total number of correlated journal entries."""

        return len(self.matches)

    @property
    def exact_invocation_count(self) -> int:
        """Return matches backed by an exact invocation identity."""

        return sum(
            match.basis is SystemdCorrelationBasis.EXACT_INVOCATION
            for match in self.matches
        )

    @property
    def unit_and_time_count(self) -> int:
        """Return matches backed by unit identity and bounded monotonic time."""

        return sum(
            match.basis is SystemdCorrelationBasis.UNIT_AND_TIME
            for match in self.matches
        )

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly report representation."""

        return {
            "service_event_id": self.service_event_id,
            "journal_event_id": self.journal_event_id,
            "requested_unit": self.requested_unit,
            "canonical_unit": self.canonical_unit,
            "boot_id": self.boot_id,
            "evaluated_entry_count": self.evaluated_entry_count,
            "match_count": self.match_count,
            "exact_invocation_count": self.exact_invocation_count,
            "unit_and_time_count": self.unit_and_time_count,
            "matches": [match.to_dict() for match in self.matches],
        }
