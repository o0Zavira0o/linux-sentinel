"""Pure deterministic temporal correlation for systemd and journald evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.systemd.boot import SystemBootIdError, normalize_boot_id
from sentinel_x.systemd.correlation_models import (
    SystemdCorrelationBasis,
    SystemdCorrelationMatch,
    SystemdTemporalCorrelationReport,
    SystemdTemporalRelation,
)
from sentinel_x.systemd.journal_observation import (
    SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
    SYSTEMD_JOURNAL_OBSERVATION_TYPE,
)
from sentinel_x.systemd.models import validate_service_unit_name
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

_DEFAULT_MAX_STATE_DELTA_USEC: Final[int] = 30_000_000
_MAX_MAX_STATE_DELTA_USEC: Final[int] = 300_000_000
_INVOCATION_FIELDS: Final[tuple[str, ...]] = (
    "systemd_invocation_id",
    "invocation_id",
    "object_systemd_invocation_id",
)
_UNIT_FIELDS: Final[tuple[str, ...]] = (
    "systemd_unit",
    "unit",
    "object_systemd_unit",
    "coredump_unit",
)


class SystemdCorrelationError(RuntimeError):
    """Base error for typed temporal correlation."""


class SystemdCorrelationContractError(SystemdCorrelationError):
    """Raised when an input event violates the correlation evidence contract."""


@dataclass(frozen=True, slots=True)
class _ServiceEvidence:
    event_id: str
    requested_name: str
    canonical_name: str
    unit_names: frozenset[str]
    boot_id: str
    invocation_id: str | None
    state_change_monotonic_usec: int | None


@dataclass(frozen=True, slots=True)
class _JournalEvidence:
    event_id: str
    requested_unit: str
    boot_id: str
    entries: tuple[Mapping[str, object], ...]


class SystemdTemporalCorrelator:
    """Correlate one service observation with one bounded journal batch."""

    def __init__(
        self,
        *,
        max_state_delta_usec: int = _DEFAULT_MAX_STATE_DELTA_USEC,
    ) -> None:
        self._max_state_delta_usec = _validate_window(max_state_delta_usec)

    @property
    def max_state_delta_usec(self) -> int:
        """Return the bounded unit-and-time fallback window."""

        return self._max_state_delta_usec

    def correlate(
        self,
        service_event: SentinelEvent,
        journal_event: SentinelEvent,
    ) -> SystemdTemporalCorrelationReport:
        """Return deterministic journal matches for one service observation."""

        service = _service_evidence(service_event)
        journal = _journal_evidence(journal_event)
        if service.boot_id != journal.boot_id:
            return _report(service, journal, ())
        if journal.requested_unit not in service.unit_names:
            return _report(service, journal, ())

        matches: list[SystemdCorrelationMatch] = []
        for raw_entry in journal.entries:
            match = self._correlate_entry(service, journal, raw_entry)
            if match is not None:
                matches.append(match)
        return _report(service, journal, tuple(matches))

    def _correlate_entry(
        self,
        service: _ServiceEvidence,
        journal: _JournalEvidence,
        entry: Mapping[str, object],
    ) -> SystemdCorrelationMatch | None:
        entry_index = _required_nonnegative_int(entry, "index")
        entry_boot_id = _required_boot_id(entry, "boot_id")
        if entry_boot_id != journal.boot_id:
            raise SystemdCorrelationContractError(
                "journal entry boot_id must match its journal event"
            )
        entry_units = _entry_units(entry, journal.requested_unit)
        if service.unit_names.isdisjoint(entry_units):
            return None

        journal_invocations = _entry_invocations(entry)
        matched_invocation: str | None = None
        basis: SystemdCorrelationBasis
        service_invocation = service.invocation_id
        if service_invocation is not None and journal_invocations:
            if service_invocation not in journal_invocations:
                return None
            matched_invocation = service_invocation
            basis = SystemdCorrelationBasis.EXACT_INVOCATION
        else:
            transition = service.state_change_monotonic_usec
            if transition is None:
                return None
            monotonic = _required_nonnegative_int(
                entry,
                "monotonic_timestamp_usec",
            )
            if abs(monotonic - transition) > self._max_state_delta_usec:
                return None
            basis = SystemdCorrelationBasis.UNIT_AND_TIME

        monotonic = _required_nonnegative_int(
            entry,
            "monotonic_timestamp_usec",
        )
        delta = (
            None
            if service.state_change_monotonic_usec is None
            else monotonic - service.state_change_monotonic_usec
        )
        return SystemdCorrelationMatch(
            service_event_id=service.event_id,
            journal_event_id=journal.event_id,
            journal_entry_index=entry_index,
            requested_unit=service.requested_name,
            canonical_unit=service.canonical_name,
            boot_id=service.boot_id,
            basis=basis,
            temporal_relation=_relation(delta),
            monotonic_delta_usec=delta,
            service_invocation_id=service_invocation,
            matched_journal_invocation_id=matched_invocation,
            journal_cursor_sha256=_required_sha256(entry, "cursor_sha256"),
            journal_priority=_optional_priority(entry.get("priority")),
            journal_message=_journal_message(entry.get("message")),
        )


def _service_evidence(event: SentinelEvent) -> _ServiceEvidence:
    _validate_observation_event(
        event,
        expected_source=SYSTEMD_SERVICE_OBSERVATION_SOURCE,
        expected_type=SYSTEMD_SERVICE_OBSERVATION_TYPE,
        label="service",
    )
    attributes = event.attributes
    requested_name = _required_text(attributes, "requested_name")
    canonical_name = _required_text(attributes, "canonical_name")
    try:
        validate_service_unit_name(requested_name, field_name="requested_name")
        validate_service_unit_name(canonical_name, field_name="canonical_name")
    except ValueError as exc:
        raise SystemdCorrelationContractError(str(exc)) from exc
    names = attributes.get("names")
    if not isinstance(names, Sequence) or isinstance(names, (str, bytes)):
        raise SystemdCorrelationContractError("service names must be a sequence")
    normalized_names: set[str] = {requested_name, canonical_name}
    for value in names:
        if not isinstance(value, str):
            raise SystemdCorrelationContractError(
                "service names must contain only strings"
            )
        normalized_names.add(value)
    return _ServiceEvidence(
        event_id=event.event_id,
        requested_name=requested_name,
        canonical_name=canonical_name,
        unit_names=frozenset(normalized_names),
        boot_id=_required_boot_id(attributes, "boot_id"),
        invocation_id=_optional_identity(attributes.get("invocation_id")),
        state_change_monotonic_usec=_optional_nonnegative_int(
            attributes.get("state_change_monotonic_usec"),
            field_name="state_change_monotonic_usec",
        ),
    )


def _journal_evidence(event: SentinelEvent) -> _JournalEvidence:
    _validate_observation_event(
        event,
        expected_source=SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
        expected_type=SYSTEMD_JOURNAL_OBSERVATION_TYPE,
        label="journal",
    )
    attributes = event.attributes
    requested_unit = _required_text(attributes, "requested_unit")
    try:
        validate_service_unit_name(requested_unit, field_name="requested_unit")
    except ValueError as exc:
        raise SystemdCorrelationContractError(str(exc)) from exc
    raw_entries = attributes.get("entries")
    if not isinstance(raw_entries, Sequence) or isinstance(
        raw_entries,
        (str, bytes),
    ):
        raise SystemdCorrelationContractError("journal entries must be a sequence")
    entries: list[Mapping[str, object]] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise SystemdCorrelationContractError(
                "journal entries must contain mapping values"
            )
        entries.append(raw_entry)
    declared_count = _required_nonnegative_int(attributes, "entry_count")
    if declared_count != len(entries):
        raise SystemdCorrelationContractError(
            "journal entry_count must match projected entries"
        )
    boot_id = _required_boot_id(attributes, "boot_id")
    for entry in entries:
        if _required_boot_id(entry, "boot_id") != boot_id:
            raise SystemdCorrelationContractError(
                "journal entry boot_id must match its journal event"
            )
    return _JournalEvidence(
        event_id=event.event_id,
        requested_unit=requested_unit,
        boot_id=boot_id,
        entries=tuple(entries),
    )


def _validate_observation_event(
    event: SentinelEvent,
    *,
    expected_source: str,
    expected_type: str,
    label: str,
) -> None:
    if not isinstance(event, SentinelEvent):
        raise TypeError(f"{label}_event must be a SentinelEvent")
    if event.kind is not EventKind.OBSERVATION:
        raise SystemdCorrelationContractError(
            f"{label} event must have observation kind"
        )
    if event.source != expected_source:
        raise SystemdCorrelationContractError(
            f"{label} event source does not match the expected contract"
        )
    if event.attributes.get("observation_type") != expected_type:
        raise SystemdCorrelationContractError(
            f"{label} observation_type does not match the expected contract"
        )


def _entry_units(
    entry: Mapping[str, object],
    requested_unit: str,
) -> frozenset[str]:
    units: set[str] = {requested_unit}
    for field_name in _UNIT_FIELDS:
        value = entry.get(field_name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise SystemdCorrelationContractError(
                f"journal entry {field_name} must be a string or None"
            )
        if value:
            units.add(value)
    return frozenset(units)


def _entry_invocations(entry: Mapping[str, object]) -> frozenset[str]:
    values: set[str] = set()
    for field_name in _INVOCATION_FIELDS:
        value = entry.get(field_name)
        normalized = _optional_identity(value)
        if normalized is not None:
            values.add(normalized)
    return frozenset(values)


def _optional_identity(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SystemdCorrelationContractError(
            "invocation identity must be a string or None"
        )
    normalized = value.strip().lower()
    if not normalized:
        raise SystemdCorrelationContractError("invocation identity must not be empty")
    if len(normalized) > 128:
        raise SystemdCorrelationContractError(
            "invocation identity exceeds the bounded correlation limit"
        )
    return normalized


def _required_text(mapping: Mapping[str, object], field_name: str) -> str:
    value = mapping.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise SystemdCorrelationContractError(
            f"{field_name} must be a non-empty string"
        )
    return value.strip()


def _required_boot_id(mapping: Mapping[str, object], field_name: str) -> str:
    try:
        return normalize_boot_id(mapping.get(field_name), field_name=field_name)
    except SystemBootIdError as exc:
        raise SystemdCorrelationContractError(str(exc)) from exc


def _required_nonnegative_int(
    mapping: Mapping[str, object],
    field_name: str,
) -> int:
    value = mapping.get(field_name)
    result = _optional_nonnegative_int(value, field_name=field_name)
    if result is None:
        raise SystemdCorrelationContractError(
            f"{field_name} must be a non-negative integer"
        )
    return result


def _optional_nonnegative_int(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemdCorrelationContractError(
            f"{field_name} must be a non-negative integer or None"
        )
    return value


def _required_sha256(mapping: Mapping[str, object], field_name: str) -> str:
    value = mapping.get(field_name)
    if not isinstance(value, str):
        raise SystemdCorrelationContractError(f"{field_name} must be a string")
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise SystemdCorrelationContractError(
            f"{field_name} must be a SHA-256 hexadecimal digest"
        )
    return normalized


def _optional_priority(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 7:
        raise SystemdCorrelationContractError(
            "journal priority must be an integer between 0 and 7 or None"
        )
    return value


def _journal_message(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("kind") != "text":
        return None
    text = value.get("value")
    if not isinstance(text, str):
        raise SystemdCorrelationContractError(
            "text journal message must expose a string value"
        )
    return text


def _relation(delta: int | None) -> SystemdTemporalRelation:
    if delta is None:
        return SystemdTemporalRelation.UNKNOWN
    if delta < 0:
        return SystemdTemporalRelation.BEFORE
    if delta > 0:
        return SystemdTemporalRelation.AFTER
    return SystemdTemporalRelation.AT


def _validate_window(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_state_delta_usec must be an integer")
    if not 0 <= value <= _MAX_MAX_STATE_DELTA_USEC:
        raise ValueError(
            f"max_state_delta_usec must be between 0 and {_MAX_MAX_STATE_DELTA_USEC}"
        )
    return value


def _report(
    service: _ServiceEvidence,
    journal: _JournalEvidence,
    matches: tuple[SystemdCorrelationMatch, ...],
) -> SystemdTemporalCorrelationReport:
    return SystemdTemporalCorrelationReport(
        service_event_id=service.event_id,
        journal_event_id=journal.event_id,
        requested_unit=service.requested_name,
        canonical_unit=service.canonical_name,
        boot_id=service.boot_id,
        evaluated_entry_count=len(journal.entries),
        matches=matches,
    )
