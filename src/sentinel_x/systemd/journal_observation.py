"""Stateful cursor-aware journald observation collectors for Sentinel-X."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from threading import RLock
from typing import Final, Protocol

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.systemd.boot import (
    SystemBootIdError,
    SystemBootIdReader,
    normalize_boot_id,
    read_current_boot_id as _read_current_boot_id,
)
from sentinel_x.systemd.journal_checkpoint import (
    SystemdJournalCheckpoint,
    SystemdJournalCheckpointError,
    SystemdJournalCheckpointStore,
    build_journal_checkpoint,
)
from sentinel_x.systemd.journal_models import SystemdJournalBatch, SystemdJournalEntry
from sentinel_x.systemd.journal_reader import (
    JournalctlServiceReader,
    SystemdJournalCursorUnavailableError,
)
from sentinel_x.systemd.models import validate_service_unit_name

SYSTEMD_JOURNAL_OBSERVATION_SOURCE: Final[str] = "sentinel_x.systemd.journal"
SYSTEMD_JOURNAL_OBSERVATION_TYPE: Final[str] = "linux.systemd.journal"
SYSTEMD_JOURNAL_CONTINUITY_OBSERVATION_TYPE: Final[str] = (
    "linux.systemd.journal_continuity"
)
_DEFAULT_MAX_ENTRIES: Final[int] = 64
_MAX_MAX_ENTRIES: Final[int] = 64
_MAX_MESSAGE_CHARS: Final[int] = 1024
_MAX_TEXT_FIELD_CHARS: Final[int] = 512
_COLLECTOR_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)


class SystemdJournalCollectorError(RuntimeError):
    """Base error for stateful journald observation collection."""


class SystemdJournalCollectorContractError(SystemdJournalCollectorError):
    """Raised when a trusted journal dependency violates its contract."""


class SystemdJournalCollectorStateError(SystemdJournalCollectorError):
    """Raised when publication acknowledgment state is inconsistent."""


def read_current_boot_id() -> str:
    """Read current boot identity while preserving journal collector errors."""

    try:
        return _read_current_boot_id()
    except SystemBootIdError as exc:
        raise SystemdJournalCollectorError(str(exc)) from exc


class SystemdJournalBatchReader(Protocol):
    """Structural dependency for bounded service-journal reads."""

    def read_service(
        self,
        unit_name: str,
        *,
        after_cursor: str | None = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> SystemdJournalBatch:
        """Return one bounded current-boot journal batch."""

        ...


@dataclass(frozen=True, slots=True)
class SystemdJournalCollectorSnapshot:
    """Read-only state for one cursor-aware journal collector."""

    collector_name: str
    unit_name: str
    committed_boot_id: str | None
    committed_cursor: str | None
    pending_publication: bool
    checkpoint_enabled: bool
    checkpoint_restored: bool
    invocation_count: int
    reader_call_count: int
    empty_poll_count: int
    emission_count: int
    retry_emission_count: int
    publication_commit_count: int
    publication_rollback_count: int
    boot_reset_count: int
    read_failure_count: int
    checkpoint_load_count: int
    checkpoint_save_count: int
    checkpoint_failure_count: int
    continuity_event_count: int
    cursor_recovery_count: int

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly collector state."""

        return {
            "collector_name": self.collector_name,
            "unit_name": self.unit_name,
            "committed_boot_id": self.committed_boot_id,
            "committed_cursor": self.committed_cursor,
            "pending_publication": self.pending_publication,
            "checkpoint_enabled": self.checkpoint_enabled,
            "checkpoint_restored": self.checkpoint_restored,
            "invocation_count": self.invocation_count,
            "reader_call_count": self.reader_call_count,
            "empty_poll_count": self.empty_poll_count,
            "emission_count": self.emission_count,
            "retry_emission_count": self.retry_emission_count,
            "publication_commit_count": self.publication_commit_count,
            "publication_rollback_count": self.publication_rollback_count,
            "boot_reset_count": self.boot_reset_count,
            "read_failure_count": self.read_failure_count,
            "checkpoint_load_count": self.checkpoint_load_count,
            "checkpoint_save_count": self.checkpoint_save_count,
            "checkpoint_failure_count": self.checkpoint_failure_count,
            "continuity_event_count": self.continuity_event_count,
            "cursor_recovery_count": self.cursor_recovery_count,
        }


@dataclass(frozen=True, slots=True)
class _PendingPublication:
    token: int
    event: SentinelEvent
    boot_id: str
    cursor: str | None
    boot_reset: bool


class SystemdJournalEmission:
    """Runtime emission with publication acknowledgment callbacks."""

    __slots__ = ("_collector", "_event", "_token")

    def __init__(
        self,
        collector: StatefulSystemdJournalCollector,
        event: SentinelEvent | None,
        token: int | None,
    ) -> None:
        if event is not None and not isinstance(event, SentinelEvent):
            raise TypeError("event must be a SentinelEvent or None")
        if event is None:
            if token is not None:
                raise SystemdJournalCollectorStateError(
                    "eventless journal emissions must not carry a publication token"
                )
        elif isinstance(token, bool) or not isinstance(token, int) or token <= 0:
            raise SystemdJournalCollectorStateError(
                "journal event emissions require a positive publication token"
            )
        self._collector = collector
        self._event = event
        self._token = token

    @property
    def collector_name(self) -> str:
        """Return the scheduler identity that produced this emission."""

        return self._collector.name

    @property
    def event(self) -> SentinelEvent | None:
        """Return the journal event, or None when no evidence was available."""

        return self._event

    def commit_publication(self) -> None:
        """Commit the candidate cursor after successful EventBus publication."""

        if self._token is None:
            raise SystemdJournalCollectorStateError(
                "an eventless journal emission has no publication to commit"
            )
        self._collector._commit_publication(self._token)

    def rollback_publication(self) -> None:
        """Keep the candidate event pending for an at-least-once retry."""

        if self._token is None:
            raise SystemdJournalCollectorStateError(
                "an eventless journal emission has no publication to roll back"
            )
        self._collector._rollback_publication(self._token)

    def to_dict(self) -> dict[str, object]:
        """Return bounded metadata without duplicating journal attributes."""

        return {
            "collector_name": self.collector_name,
            "event_id": None if self._event is None else self._event.event_id,
            "publication_pending": self._token is not None,
        }


class StatefulSystemdJournalCollector:
    """Collect journal evidence with acknowledged and durable cursor progress."""

    def __init__(
        self,
        collector_name: str,
        unit_name: str,
        *,
        reader: SystemdJournalBatchReader | None = None,
        boot_id_reader: SystemBootIdReader = read_current_boot_id,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        checkpoint_store: SystemdJournalCheckpointStore | None = None,
    ) -> None:
        self._collector_name = _validate_collector_name(collector_name)
        self._unit_name = validate_service_unit_name(unit_name, field_name="unit_name")
        self._reader: SystemdJournalBatchReader = (
            JournalctlServiceReader() if reader is None else reader
        )
        if not callable(boot_id_reader):
            raise TypeError("boot_id_reader must be callable")
        self._boot_id_reader = boot_id_reader
        self._max_entries = _validate_max_entries(max_entries)
        if checkpoint_store is not None:
            if not callable(getattr(checkpoint_store, "load", None)):
                raise TypeError("checkpoint_store.load must be callable")
            if not callable(getattr(checkpoint_store, "save", None)):
                raise TypeError("checkpoint_store.save must be callable")
        self._checkpoint_store = checkpoint_store
        self._lock = RLock()
        self._committed_boot_id: str | None = None
        self._committed_cursor: str | None = None
        self._checkpoint_restored = False
        self._pending: _PendingPublication | None = None
        self._next_token = 1
        self._invocation_count = 0
        self._reader_call_count = 0
        self._empty_poll_count = 0
        self._emission_count = 0
        self._retry_emission_count = 0
        self._publication_commit_count = 0
        self._publication_rollback_count = 0
        self._boot_reset_count = 0
        self._read_failure_count = 0
        self._checkpoint_load_count = 0
        self._checkpoint_save_count = 0
        self._checkpoint_failure_count = 0
        self._continuity_event_count = 0
        self._cursor_recovery_count = 0
        self._restore_checkpoint()

    @property
    def name(self) -> str:
        """Return the scheduler identity for this journal collector."""

        return self._collector_name

    @property
    def unit_name(self) -> str:
        """Return the systemd service whose journal is queried."""

        return self._unit_name

    def collect(self) -> SystemdJournalEmission:
        """Read, recover, or retry one bounded journal evidence emission."""

        with self._lock:
            self._invocation_count += 1
            if self._pending is not None:
                self._retry_emission_count += 1
                return SystemdJournalEmission(
                    self,
                    self._pending.event,
                    self._pending.token,
                )

            current_boot_id = _normalize_boot_id(self._boot_id_reader())
            if (
                self._committed_boot_id is not None
                and self._committed_boot_id != current_boot_id
            ):
                return self._create_continuity_emission(
                    reason="boot_changed",
                    current_boot_id=current_boot_id,
                    candidate_cursor=None,
                    boot_reset=True,
                )

            after_cursor = self._committed_cursor
            try:
                batch = self._reader.read_service(
                    self._unit_name,
                    after_cursor=after_cursor,
                    max_entries=self._max_entries,
                )
            except SystemdJournalCursorUnavailableError:
                self._read_failure_count += 1
                if after_cursor is None:
                    raise
                return self._create_continuity_emission(
                    reason="cursor_unavailable",
                    current_boot_id=current_boot_id,
                    candidate_cursor=None,
                    boot_reset=False,
                )
            except Exception:
                self._read_failure_count += 1
                raise
            self._reader_call_count += 1
            self._validate_batch(
                batch,
                current_boot_id=current_boot_id,
                expected_after_cursor=after_cursor,
            )

            candidate_cursor = batch.next_cursor
            if not batch.entries and batch.diagnostic is None:
                self._empty_poll_count += 1
                self._persist_checkpoint(current_boot_id, candidate_cursor)
                self._committed_boot_id = current_boot_id
                self._committed_cursor = candidate_cursor
                return SystemdJournalEmission(self, None, None)

            event = systemd_journal_batch_to_event(
                batch,
                collector_name=self._collector_name,
                current_boot_id=current_boot_id,
            )
            return self._set_pending(
                event=event,
                boot_id=current_boot_id,
                cursor=candidate_cursor,
                boot_reset=False,
            )

    def snapshot(self) -> SystemdJournalCollectorSnapshot:
        """Return a thread-safe state snapshot."""

        with self._lock:
            return SystemdJournalCollectorSnapshot(
                collector_name=self._collector_name,
                unit_name=self._unit_name,
                committed_boot_id=self._committed_boot_id,
                committed_cursor=self._committed_cursor,
                pending_publication=self._pending is not None,
                checkpoint_enabled=self._checkpoint_store is not None,
                checkpoint_restored=self._checkpoint_restored,
                invocation_count=self._invocation_count,
                reader_call_count=self._reader_call_count,
                empty_poll_count=self._empty_poll_count,
                emission_count=self._emission_count,
                retry_emission_count=self._retry_emission_count,
                publication_commit_count=self._publication_commit_count,
                publication_rollback_count=self._publication_rollback_count,
                boot_reset_count=self._boot_reset_count,
                read_failure_count=self._read_failure_count,
                checkpoint_load_count=self._checkpoint_load_count,
                checkpoint_save_count=self._checkpoint_save_count,
                checkpoint_failure_count=self._checkpoint_failure_count,
                continuity_event_count=self._continuity_event_count,
                cursor_recovery_count=self._cursor_recovery_count,
            )

    def _restore_checkpoint(self) -> None:
        store = self._checkpoint_store
        if store is None:
            return
        try:
            checkpoint: object = store.load(self._collector_name, self._unit_name)
        except SystemdJournalCheckpointError as exc:
            self._checkpoint_failure_count += 1
            raise SystemdJournalCollectorError(
                f"failed to load journald cursor checkpoint: {exc}"
            ) from exc
        except Exception as exc:
            self._checkpoint_failure_count += 1
            raise SystemdJournalCollectorContractError(
                "journal checkpoint store raised an unexpected load error"
            ) from exc
        if checkpoint is None:
            return
        if not isinstance(checkpoint, SystemdJournalCheckpoint):
            self._checkpoint_failure_count += 1
            raise SystemdJournalCollectorContractError(
                "journal checkpoint store must return a typed checkpoint or None"
            )
        self._committed_boot_id = checkpoint.boot_id
        self._committed_cursor = checkpoint.cursor
        self._checkpoint_restored = True
        self._checkpoint_load_count += 1

    def _persist_checkpoint(self, boot_id: str, cursor: str | None) -> None:
        store = self._checkpoint_store
        if store is None:
            return
        checkpoint = build_journal_checkpoint(
            collector_name=self._collector_name,
            unit_name=self._unit_name,
            boot_id=boot_id,
            cursor=cursor,
        )
        try:
            store.save(checkpoint)
        except SystemdJournalCheckpointError as exc:
            self._checkpoint_failure_count += 1
            raise SystemdJournalCollectorError(
                f"failed to persist journald cursor checkpoint: {exc}"
            ) from exc
        except Exception as exc:
            self._checkpoint_failure_count += 1
            raise SystemdJournalCollectorContractError(
                "journal checkpoint store raised an unexpected save error"
            ) from exc
        self._checkpoint_save_count += 1

    def _create_continuity_emission(
        self,
        *,
        reason: str,
        current_boot_id: str,
        candidate_cursor: str | None,
        boot_reset: bool,
    ) -> SystemdJournalEmission:
        previous_cursor_sha256 = (
            None
            if self._committed_cursor is None
            else hashlib.sha256(self._committed_cursor.encode("utf-8")).hexdigest()
        )
        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source=SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
            message=(
                "journald continuity recovery required for "
                f"{self._unit_name}: {reason}."
            ),
            severity=EventSeverity.WARNING,
            attributes={
                "observation_type": SYSTEMD_JOURNAL_CONTINUITY_OBSERVATION_TYPE,
                "collector_name": self._collector_name,
                "requested_unit": self._unit_name,
                "reason": reason,
                "previous_boot_id": self._committed_boot_id,
                "current_boot_id": current_boot_id,
                "previous_cursor_sha256": previous_cursor_sha256,
                "recovery_action": "reset_to_current_boot_tail_after_ack",
                "continuity_lost": reason == "cursor_unavailable",
            },
        )
        self._continuity_event_count += 1
        if reason == "cursor_unavailable":
            self._cursor_recovery_count += 1
        return self._set_pending(
            event=event,
            boot_id=current_boot_id,
            cursor=candidate_cursor,
            boot_reset=boot_reset,
        )

    def _set_pending(
        self,
        *,
        event: SentinelEvent,
        boot_id: str,
        cursor: str | None,
        boot_reset: bool,
    ) -> SystemdJournalEmission:
        token = self._next_token
        self._next_token += 1
        self._pending = _PendingPublication(
            token=token,
            event=event,
            boot_id=boot_id,
            cursor=cursor,
            boot_reset=boot_reset,
        )
        self._emission_count += 1
        return SystemdJournalEmission(self, event, token)

    def _validate_batch(
        self,
        batch: object,
        *,
        current_boot_id: str,
        expected_after_cursor: str | None,
    ) -> None:
        if not isinstance(batch, SystemdJournalBatch):
            raise SystemdJournalCollectorContractError(
                "journal reader must return a SystemdJournalBatch"
            )
        if batch.requested_unit != self._unit_name:
            raise SystemdJournalCollectorContractError(
                "journal reader returned a batch for the wrong service unit"
            )
        if batch.after_cursor != expected_after_cursor:
            raise SystemdJournalCollectorContractError(
                "journal reader returned a batch with the wrong starting cursor"
            )
        if batch.entry_limit != self._max_entries:
            raise SystemdJournalCollectorContractError(
                "journal reader returned a batch with the wrong entry limit"
            )

        previous_monotonic_usec: int | None = None
        for entry in batch.entries:
            if entry.boot_id.lower() != current_boot_id:
                raise SystemdJournalCollectorContractError(
                    "journal batch contains evidence from a different boot"
                )
            if (
                expected_after_cursor is not None
                and entry.cursor == expected_after_cursor
            ):
                raise SystemdJournalCollectorContractError(
                    "incremental journal batch repeated its starting cursor"
                )
            if (
                previous_monotonic_usec is not None
                and entry.monotonic_timestamp_usec < previous_monotonic_usec
            ):
                raise SystemdJournalCollectorContractError(
                    "journal batch monotonic timestamps must not regress"
                )
            previous_monotonic_usec = entry.monotonic_timestamp_usec

    def _commit_publication(self, token: int) -> None:
        with self._lock:
            pending = self._require_pending_token(token)
            self._persist_checkpoint(pending.boot_id, pending.cursor)
            self._committed_boot_id = pending.boot_id
            self._committed_cursor = pending.cursor
            if pending.boot_reset:
                self._boot_reset_count += 1
            self._pending = None
            self._publication_commit_count += 1

    def _rollback_publication(self, token: int) -> None:
        with self._lock:
            self._require_pending_token(token)
            self._publication_rollback_count += 1

    def _require_pending_token(self, token: int) -> _PendingPublication:
        pending = self._pending
        if pending is None or pending.token != token:
            raise SystemdJournalCollectorStateError(
                "journal publication acknowledgment does not match pending state"
            )
        return pending


def systemd_journal_batch_to_event(
    batch: SystemdJournalBatch,
    *,
    collector_name: str,
    current_boot_id: str,
) -> SentinelEvent:
    """Project one bounded journal batch into a compact SentinelEvent."""

    if not isinstance(batch, SystemdJournalBatch):
        raise TypeError("batch must be a SystemdJournalBatch")
    normalized_collector_name = _validate_collector_name(collector_name)
    normalized_boot_id = _normalize_boot_id(current_boot_id)
    for entry in batch.entries:
        if entry.boot_id.lower() != normalized_boot_id:
            raise SystemdJournalCollectorContractError(
                "journal event projection cannot mix boot identities"
            )

    attributes: dict[str, object] = {
        "observation_type": SYSTEMD_JOURNAL_OBSERVATION_TYPE,
        "collector_name": normalized_collector_name,
        "requested_unit": batch.requested_unit,
        "boot_id": normalized_boot_id,
        "after_cursor": batch.after_cursor,
        "next_cursor": batch.next_cursor,
        "entry_count": len(batch.entries),
        "entry_limit": batch.entry_limit,
        "entry_limit_reached": batch.entry_limit_reached,
        "diagnostic": batch.diagnostic,
        "entries": [
            _project_entry(entry, index=index)
            for index, entry in enumerate(batch.entries)
        ],
    }
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
        message=f"journald evidence collected for {batch.requested_unit}.",
        attributes=attributes,
        occurred_at=batch.captured_at,
    )


def _project_entry(entry: SystemdJournalEntry, *, index: int) -> dict[str, object]:
    message: dict[str, object]
    message_text = entry.message_text
    message_bytes = entry.message_bytes
    if message_text is not None:
        original = message_text
        message = {
            "kind": "text",
            "value": _bounded_text(original, _MAX_MESSAGE_CHARS),
            "original_length": len(original),
            "truncated": len(original) > _MAX_MESSAGE_CHARS,
        }
    elif message_bytes is not None:
        raw = message_bytes
        message = {
            "kind": "binary",
            "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    elif entry.message_omitted:
        message = {"kind": "omitted"}
    else:
        message = {"kind": "absent_or_ambiguous"}

    return {
        "index": index,
        "cursor_sha256": hashlib.sha256(entry.cursor.encode("utf-8")).hexdigest(),
        "realtime_timestamp_usec": entry.realtime_timestamp_usec,
        "monotonic_timestamp_usec": entry.monotonic_timestamp_usec,
        "boot_id": entry.boot_id.lower(),
        "priority": None if entry.priority is None else int(entry.priority),
        "message": message,
        "message_id": _bounded_field(entry, "MESSAGE_ID"),
        "systemd_unit": _bounded_field(entry, "_SYSTEMD_UNIT"),
        "systemd_invocation_id": _bounded_field(entry, "_SYSTEMD_INVOCATION_ID"),
        "invocation_id": _bounded_field(entry, "INVOCATION_ID"),
        "unit": _bounded_field(entry, "UNIT"),
        "object_systemd_unit": _bounded_field(entry, "OBJECT_SYSTEMD_UNIT"),
        "object_systemd_invocation_id": _bounded_field(
            entry,
            "OBJECT_SYSTEMD_INVOCATION_ID",
        ),
        "coredump_unit": _bounded_field(entry, "COREDUMP_UNIT"),
        "pid": entry.pid,
        "uid": entry.single_nonnegative_int("_UID"),
        "gid": entry.single_nonnegative_int("_GID"),
        "comm": _bounded_field(entry, "_COMM"),
        "exe": _bounded_field(entry, "_EXE"),
        "transport": _bounded_field(entry, "_TRANSPORT"),
        "syslog_identifier": _bounded_field(entry, "SYSLOG_IDENTIFIER"),
        "code_file": _bounded_field(entry, "CODE_FILE"),
        "code_line": entry.single_nonnegative_int("CODE_LINE"),
        "code_func": _bounded_field(entry, "CODE_FUNC"),
        "errno": entry.single_nonnegative_int("ERRNO"),
    }


def _bounded_field(entry: SystemdJournalEntry, field_name: str) -> str | None:
    value = entry.single_text(field_name)
    if value is None:
        return None
    return _bounded_text(value, _MAX_TEXT_FIELD_CHARS)


def _bounded_text(value: str, maximum_chars: int) -> str:
    if len(value) <= maximum_chars:
        return value
    return value[: maximum_chars - 3] + "..."


def _validate_collector_name(value: object) -> str:
    if not isinstance(value, str):
        raise SystemdJournalCollectorError("collector_name must be a string")
    normalized = value.strip()
    if _COLLECTOR_NAME_PATTERN.fullmatch(normalized) is None:
        raise SystemdJournalCollectorError(
            "collector_name must be 1-64 scheduler-safe characters"
        )
    return normalized


def _validate_max_entries(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_entries must be an integer")
    if not 1 <= value <= _MAX_MAX_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {_MAX_MAX_ENTRIES}")
    return value


def _normalize_boot_id(value: object) -> str:
    try:
        return normalize_boot_id(value, field_name="boot ID")
    except SystemBootIdError as exc:
        raise SystemdJournalCollectorContractError(str(exc)) from exc
