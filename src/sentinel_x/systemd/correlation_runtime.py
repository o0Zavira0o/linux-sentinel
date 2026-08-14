"""Bounded stateful fusion for systemd service and journald evidence."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Final

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.systemd.correlation import SystemdTemporalCorrelator
from sentinel_x.systemd.correlation_models import (
    SystemdCorrelationBasis,
    SystemdCorrelationMatch,
    SystemdTemporalCorrelationReport,
)
from sentinel_x.systemd.journal_observation import (
    SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
    SYSTEMD_JOURNAL_OBSERVATION_TYPE,
)
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

SYSTEMD_CORRELATION_OBSERVATION_SOURCE: Final[str] = "sentinel_x.systemd.correlation"
SYSTEMD_CORRELATION_OBSERVATION_TYPE: Final[str] = "linux.systemd.correlation"
_DEFAULT_RETAINED_EVENTS: Final[int] = 64
_MAX_RETAINED_EVENTS: Final[int] = 256
_DEFAULT_EMITTED_MATCH_KEYS: Final[int] = 1024
_MAX_EMITTED_MATCH_KEYS: Final[int] = 4096


@dataclass(frozen=True, slots=True)
class SystemdCorrelationTrackerSnapshot:
    """Read-only bounded state for temporal evidence fusion."""

    retained_service_events: int
    retained_journal_events: int
    emitted_match_keys: int
    service_events_ingested: int
    journal_events_ingested: int
    unrelated_events_ignored: int
    duplicate_input_events_ignored: int
    correlation_attempts: int
    zero_match_reports: int
    correlation_events_emitted: int
    correlation_upgrades_emitted: int
    matches_emitted: int
    duplicate_matches_suppressed: int

    def to_dict(self) -> dict[str, int]:
        """Return serialization-friendly tracker state."""

        return {
            "retained_service_events": self.retained_service_events,
            "retained_journal_events": self.retained_journal_events,
            "emitted_match_keys": self.emitted_match_keys,
            "service_events_ingested": self.service_events_ingested,
            "journal_events_ingested": self.journal_events_ingested,
            "unrelated_events_ignored": self.unrelated_events_ignored,
            "duplicate_input_events_ignored": self.duplicate_input_events_ignored,
            "correlation_attempts": self.correlation_attempts,
            "zero_match_reports": self.zero_match_reports,
            "correlation_events_emitted": self.correlation_events_emitted,
            "correlation_upgrades_emitted": self.correlation_upgrades_emitted,
            "matches_emitted": self.matches_emitted,
            "duplicate_matches_suppressed": self.duplicate_matches_suppressed,
        }


@dataclass(frozen=True, slots=True)
class _CandidateMatch:
    """One candidate attribution before deterministic arbitration."""

    service_event: SentinelEvent
    journal_event: SentinelEvent
    match: SystemdCorrelationMatch


@dataclass(frozen=True, slots=True)
class _EmissionRecord:
    """Best emitted attribution retained for one immutable journal entry."""

    quality: tuple[int, int]
    correlation_event_id: str


@dataclass(frozen=True, slots=True)
class _SelectedMatch:
    """Selected candidate and any weaker event it supersedes."""

    candidate: _CandidateMatch
    superseded_event_id: str | None


class SystemdCorrelationTracker:
    """Fuse service and journal events with bounded memory and deduplication.

    The tracker is deliberately independent from EventBus and Scheduler wiring.
    It can therefore be validated as a deterministic state machine before it is
    attached to the live runtime in the next integration subphase.
    """

    def __init__(
        self,
        *,
        correlator: SystemdTemporalCorrelator | None = None,
        max_retained_service_events: int = _DEFAULT_RETAINED_EVENTS,
        max_retained_journal_events: int = _DEFAULT_RETAINED_EVENTS,
        max_emitted_match_keys: int = _DEFAULT_EMITTED_MATCH_KEYS,
    ) -> None:
        if correlator is not None and not isinstance(
            correlator,
            SystemdTemporalCorrelator,
        ):
            raise TypeError("correlator must be a SystemdTemporalCorrelator or None")
        self._correlator = (
            SystemdTemporalCorrelator() if correlator is None else correlator
        )
        self._max_retained_service_events = _validate_bound(
            max_retained_service_events,
            field_name="max_retained_service_events",
            maximum=_MAX_RETAINED_EVENTS,
        )
        self._max_retained_journal_events = _validate_bound(
            max_retained_journal_events,
            field_name="max_retained_journal_events",
            maximum=_MAX_RETAINED_EVENTS,
        )
        self._max_emitted_match_keys = _validate_bound(
            max_emitted_match_keys,
            field_name="max_emitted_match_keys",
            maximum=_MAX_EMITTED_MATCH_KEYS,
        )
        self._service_events: deque[SentinelEvent] = deque()
        self._journal_events: deque[SentinelEvent] = deque()
        self._emitted_match_key_order: deque[tuple[str, str, str]] = deque()
        self._emitted_match_records: dict[
            tuple[str, str, str],
            _EmissionRecord,
        ] = {}
        self._lock = RLock()
        self._service_events_ingested = 0
        self._journal_events_ingested = 0
        self._unrelated_events_ignored = 0
        self._duplicate_input_events_ignored = 0
        self._correlation_attempts = 0
        self._zero_match_reports = 0
        self._correlation_events_emitted = 0
        self._correlation_upgrades_emitted = 0
        self._matches_emitted = 0
        self._duplicate_matches_suppressed = 0

    def ingest(self, event: SentinelEvent) -> tuple[SentinelEvent, ...]:
        """Ingest one event and return newly derived correlation observations."""

        if not isinstance(event, SentinelEvent):
            raise TypeError("event must be a SentinelEvent")

        with self._lock:
            if _is_service_event(event):
                return self._ingest_service(event)
            if _is_journal_event(event):
                return self._ingest_journal(event)
            self._unrelated_events_ignored += 1
            return ()

    def snapshot(self) -> SystemdCorrelationTrackerSnapshot:
        """Return immutable counters and retained-state sizes."""

        with self._lock:
            return SystemdCorrelationTrackerSnapshot(
                retained_service_events=len(self._service_events),
                retained_journal_events=len(self._journal_events),
                emitted_match_keys=len(self._emitted_match_records),
                service_events_ingested=self._service_events_ingested,
                journal_events_ingested=self._journal_events_ingested,
                unrelated_events_ignored=self._unrelated_events_ignored,
                duplicate_input_events_ignored=self._duplicate_input_events_ignored,
                correlation_attempts=self._correlation_attempts,
                zero_match_reports=self._zero_match_reports,
                correlation_events_emitted=self._correlation_events_emitted,
                correlation_upgrades_emitted=self._correlation_upgrades_emitted,
                matches_emitted=self._matches_emitted,
                duplicate_matches_suppressed=self._duplicate_matches_suppressed,
            )

    def _ingest_service(self, event: SentinelEvent) -> tuple[SentinelEvent, ...]:
        if _contains_event_id(self._service_events, event.event_id):
            self._duplicate_input_events_ignored += 1
            return ()
        self._service_events_ingested += 1
        _append_bounded(
            self._service_events,
            event,
            maximum=self._max_retained_service_events,
        )
        candidates = self._candidate_matches(
            service_events=(event,),
            journal_events=tuple(self._journal_events),
        )
        return self._emit_selected(candidates)

    def _ingest_journal(self, event: SentinelEvent) -> tuple[SentinelEvent, ...]:
        if _contains_event_id(self._journal_events, event.event_id):
            self._duplicate_input_events_ignored += 1
            return ()
        self._journal_events_ingested += 1
        _append_bounded(
            self._journal_events,
            event,
            maximum=self._max_retained_journal_events,
        )
        candidates = self._candidate_matches(
            service_events=tuple(self._service_events),
            journal_events=(event,),
        )
        return self._emit_selected(candidates)

    def _candidate_matches(
        self,
        *,
        service_events: tuple[SentinelEvent, ...],
        journal_events: tuple[SentinelEvent, ...],
    ) -> tuple[_CandidateMatch, ...]:
        candidates: list[_CandidateMatch] = []
        for service_event in service_events:
            for journal_event in journal_events:
                self._correlation_attempts += 1
                report = self._correlator.correlate(
                    service_event,
                    journal_event,
                )
                if report.match_count == 0:
                    self._zero_match_reports += 1
                    continue
                for match in report.matches:
                    candidates.append(
                        _CandidateMatch(
                            service_event=service_event,
                            journal_event=journal_event,
                            match=match,
                        )
                    )
        return tuple(candidates)

    def _emit_selected(
        self,
        candidates: tuple[_CandidateMatch, ...],
    ) -> tuple[SentinelEvent, ...]:
        selected = _select_best_candidates(candidates)
        grouped: dict[tuple[str, str], list[_SelectedMatch]] = {}

        for candidate in selected:
            entry_key = _journal_entry_key(candidate.match)
            quality = _match_quality(candidate.match)
            incumbent = self._emitted_match_records.get(entry_key)
            if incumbent is not None and quality >= incumbent.quality:
                self._duplicate_matches_suppressed += 1
                continue
            pair_key = (
                candidate.service_event.event_id,
                candidate.journal_event.event_id,
            )
            grouped.setdefault(pair_key, []).append(
                _SelectedMatch(
                    candidate=candidate,
                    superseded_event_id=(
                        None if incumbent is None else incumbent.correlation_event_id
                    ),
                )
            )

        derived_events: list[SentinelEvent] = []
        for selected_matches in grouped.values():
            first = selected_matches[0].candidate
            report = _filtered_report(
                matches=tuple(item.candidate.match for item in selected_matches),
                service_event=first.service_event,
                journal_event=first.journal_event,
            )
            superseded_ids = tuple(
                sorted(
                    {
                        item.superseded_event_id
                        for item in selected_matches
                        if item.superseded_event_id is not None
                    }
                )
            )
            derived = systemd_correlation_report_to_event(
                report,
                supersedes_event_ids=superseded_ids,
            )
            derived_events.append(derived)
            self._correlation_events_emitted += 1
            if superseded_ids:
                self._correlation_upgrades_emitted += 1
            self._matches_emitted += report.match_count
            for item in selected_matches:
                self._remember_emission(
                    _journal_entry_key(item.candidate.match),
                    _EmissionRecord(
                        quality=_match_quality(item.candidate.match),
                        correlation_event_id=derived.event_id,
                    ),
                )
        return tuple(derived_events)

    def _remember_emission(
        self,
        key: tuple[str, str, str],
        record: _EmissionRecord,
    ) -> None:
        if key not in self._emitted_match_records:
            if len(self._emitted_match_key_order) >= self._max_emitted_match_keys:
                oldest = self._emitted_match_key_order.popleft()
                del self._emitted_match_records[oldest]
            self._emitted_match_key_order.append(key)
        self._emitted_match_records[key] = record


def systemd_correlation_report_to_event(
    report: SystemdTemporalCorrelationReport,
    *,
    supersedes_event_ids: tuple[str, ...] = (),
) -> SentinelEvent:
    """Project one non-empty correlation report into a bounded typed event."""

    if not isinstance(report, SystemdTemporalCorrelationReport):
        raise TypeError("report must be a SystemdTemporalCorrelationReport")
    if report.match_count == 0:
        raise ValueError("empty correlation reports are not emitted")
    supersedes = _validate_supersedes_event_ids(supersedes_event_ids)
    strength = _evidence_strength(report)
    attributes = report.to_dict()
    attributes.update(
        {
            "observation_type": SYSTEMD_CORRELATION_OBSERVATION_TYPE,
            "evidence_strength": strength,
            "revision_kind": "upgrade" if supersedes else "initial",
            "supersedes_event_ids": list(supersedes),
        }
    )
    noun = "entry" if report.match_count == 1 else "entries"
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_CORRELATION_OBSERVATION_SOURCE,
        message=(
            f"Correlated {report.match_count} journal {noun} "
            f"to {report.canonical_unit}."
        ),
        severity=EventSeverity.INFO,
        attributes=attributes,
    )


def _is_service_event(event: SentinelEvent) -> bool:
    return (
        event.kind is EventKind.OBSERVATION
        and event.source == SYSTEMD_SERVICE_OBSERVATION_SOURCE
        and event.attributes.get("observation_type") == SYSTEMD_SERVICE_OBSERVATION_TYPE
    )


def _is_journal_event(event: SentinelEvent) -> bool:
    return (
        event.kind is EventKind.OBSERVATION
        and event.source == SYSTEMD_JOURNAL_OBSERVATION_SOURCE
        and event.attributes.get("observation_type") == SYSTEMD_JOURNAL_OBSERVATION_TYPE
    )


def _contains_event_id(events: deque[SentinelEvent], event_id: str) -> bool:
    return any(event.event_id == event_id for event in events)


def _append_bounded(
    events: deque[SentinelEvent],
    event: SentinelEvent,
    *,
    maximum: int,
) -> None:
    if len(events) >= maximum:
        events.popleft()
    events.append(event)


def _select_best_candidates(
    candidates: tuple[_CandidateMatch, ...],
) -> tuple[_CandidateMatch, ...]:
    best: dict[tuple[str, str, str], _CandidateMatch] = {}
    for candidate in candidates:
        entry_key = _journal_entry_key(candidate.match)
        incumbent = best.get(entry_key)
        if incumbent is None or _candidate_rank(candidate) < _candidate_rank(incumbent):
            best[entry_key] = candidate
    return tuple(sorted(best.values(), key=_candidate_output_key))


def _candidate_rank(candidate: _CandidateMatch) -> tuple[int, int, str]:
    quality = _match_quality(candidate.match)
    return quality[0], quality[1], candidate.service_event.event_id


def _match_quality(match: SystemdCorrelationMatch) -> tuple[int, int]:
    if match.basis is SystemdCorrelationBasis.EXACT_INVOCATION:
        return 0, 0
    delta = match.monotonic_delta_usec
    if delta is None:
        raise RuntimeError("unit-and-time correlation requires a monotonic delta")
    return 1, abs(delta)


def _candidate_output_key(candidate: _CandidateMatch) -> tuple[str, int, str]:
    match = candidate.match
    return (
        candidate.journal_event.event_id,
        match.journal_entry_index,
        candidate.service_event.event_id,
    )


def _journal_entry_key(match: SystemdCorrelationMatch) -> tuple[str, str, str]:
    return (
        match.boot_id,
        match.canonical_unit,
        match.journal_cursor_sha256,
    )


def _filtered_report(
    *,
    matches: tuple[SystemdCorrelationMatch, ...],
    service_event: SentinelEvent,
    journal_event: SentinelEvent,
) -> SystemdTemporalCorrelationReport:
    if not matches:
        raise RuntimeError("filtered correlation reports require at least one match")
    first = matches[0]
    evaluated = journal_event.attributes.get("entry_count")
    if isinstance(evaluated, bool) or not isinstance(evaluated, int) or evaluated < 0:
        raise RuntimeError("validated journal entry_count contract was lost")
    return SystemdTemporalCorrelationReport(
        service_event_id=service_event.event_id,
        journal_event_id=journal_event.event_id,
        requested_unit=first.requested_unit,
        canonical_unit=first.canonical_unit,
        boot_id=first.boot_id,
        evaluated_entry_count=evaluated,
        matches=matches,
    )


def _evidence_strength(report: SystemdTemporalCorrelationReport) -> str:
    if report.exact_invocation_count == report.match_count:
        return "strong"
    if report.unit_and_time_count == report.match_count:
        return "provisional"
    return "mixed"


def _validate_supersedes_event_ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError("supersedes_event_ids must be a tuple")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("supersedes_event_ids must contain non-empty strings")
        normalized.append(value.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError("supersedes_event_ids must not contain duplicates")
    return tuple(normalized)


def _validate_bound(value: object, *, field_name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return value
