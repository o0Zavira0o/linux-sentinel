"""Typed bounded journald evidence models for Sentinel-X."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Final, TypeAlias

from sentinel_x.systemd.models import validate_service_unit_name

JournalAtom: TypeAlias = str | bytes | None

_FIELD_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_BOOT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Fa-f]{32}$")
_MAX_CURSOR_CHARS: Final[int] = 8192
_MAX_DIAGNOSTIC_CHARS: Final[int] = 512


class SystemdJournalModelError(ValueError):
    """Base error for invalid journald evidence models."""


class SystemdJournalCursorError(SystemdJournalModelError):
    """Raised when an opaque journal cursor violates transport-safe bounds."""


class SystemdJournalFieldError(SystemdJournalModelError):
    """Raised when a journal field representation is structurally invalid."""


class JournalPriority(IntEnum):
    """Standard syslog priority levels stored by the journal."""

    EMERG = 0
    ALERT = 1
    CRIT = 2
    ERR = 3
    WARNING = 4
    NOTICE = 5
    INFO = 6
    DEBUG = 7


def validate_journal_cursor(value: str, *, field_name: str) -> str:
    """Validate one opaque cursor without interpreting its private syntax."""
    if not isinstance(value, str):
        raise SystemdJournalCursorError(f"{field_name} must be a string")
    if not value:
        raise SystemdJournalCursorError(f"{field_name} must be non-empty")
    if len(value) > _MAX_CURSOR_CHARS:
        raise SystemdJournalCursorError(f"{field_name} is too long")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise SystemdJournalCursorError(
            f"{field_name} must not contain NUL or newline characters"
        )
    return value


def _validate_nonnegative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemdJournalModelError(f"{field_name} must be a non-negative integer")


def _validate_boot_id(value: str) -> None:
    if not isinstance(value, str) or _BOOT_ID_PATTERN.fullmatch(value) is None:
        raise SystemdJournalModelError("boot_id must be a 32-character hexadecimal ID")


def _atom_to_json_value(value: JournalAtom) -> object:
    if isinstance(value, bytes):
        return list(value)
    return value


@dataclass(frozen=True, slots=True)
class JournalField:
    """One selected journal field preserving scalar, binary, or repeated values."""

    name: str
    values: tuple[JournalAtom, ...]

    def __post_init__(self) -> None:
        """Validate field-name and value representation invariants."""
        if (
            not isinstance(self.name, str)
            or _FIELD_NAME_PATTERN.fullmatch(self.name) is None
        ):
            raise SystemdJournalFieldError("name must be a valid journal field name")
        if not isinstance(self.values, tuple) or not self.values:
            raise SystemdJournalFieldError("values must be a non-empty tuple")
        for value in self.values:
            if value is None or isinstance(value, (str, bytes)):
                continue
            raise SystemdJournalFieldError(
                "journal field values must be strings, bytes, or None"
            )

    @property
    def is_multi_valued(self) -> bool:
        """Return whether the journal serialized duplicate values for this field."""
        return len(self.values) > 1

    @property
    def single_text(self) -> str | None:
        """Return the value only when the field has one textual value."""
        if len(self.values) != 1 or not isinstance(self.values[0], str):
            return None
        return self.values[0]

    @property
    def single_bytes(self) -> bytes | None:
        """Return the value only when the field has one binary value."""
        if len(self.values) != 1 or not isinstance(self.values[0], bytes):
            return None
        return self.values[0]

    @property
    def single_omitted(self) -> bool:
        """Return whether journalctl represented one oversized value as null."""
        return len(self.values) == 1 and self.values[0] is None

    def to_json_value(self) -> object:
        """Return the value shape used by journalctl JSON in JSON-friendly form."""
        if len(self.values) == 1:
            return _atom_to_json_value(self.values[0])
        return [_atom_to_json_value(value) for value in self.values]


@dataclass(frozen=True, slots=True)
class SystemdJournalEntry:
    """One typed journal entry with immutable selected evidence fields."""

    cursor: str
    realtime_timestamp_usec: int
    monotonic_timestamp_usec: int
    boot_id: str
    fields: tuple[JournalField, ...]

    def __post_init__(self) -> None:
        """Validate address metadata and deterministic field identity."""
        validate_journal_cursor(self.cursor, field_name="cursor")
        _validate_nonnegative_int(
            self.realtime_timestamp_usec,
            field_name="realtime_timestamp_usec",
        )
        _validate_nonnegative_int(
            self.monotonic_timestamp_usec,
            field_name="monotonic_timestamp_usec",
        )
        _validate_boot_id(self.boot_id)
        if not isinstance(self.fields, tuple):
            raise SystemdJournalModelError("fields must be a tuple")
        names = tuple(field.name for field in self.fields)
        if len(set(names)) != len(names):
            raise SystemdJournalModelError("fields must not contain duplicate names")

    def field(self, name: str) -> JournalField | None:
        """Return one selected field by exact journal field name."""
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def single_text(self, name: str) -> str | None:
        """Return one field only when it contains exactly one textual value."""
        field = self.field(name)
        if field is None:
            return None
        return field.single_text

    def single_nonnegative_int(self, name: str) -> int | None:
        """Return one decimal field as an integer when unambiguous and valid."""
        text = self.single_text(name)
        if text is None:
            return None
        try:
            value = int(text, 10)
        except ValueError:
            return None
        if value < 0:
            return None
        return value

    @property
    def message_text(self) -> str | None:
        """Return a textual MESSAGE when it is present and unambiguous."""
        return self.single_text("MESSAGE")

    @property
    def message_bytes(self) -> bytes | None:
        """Return a binary MESSAGE when journalctl encoded raw bytes."""
        field = self.field("MESSAGE")
        if field is None:
            return None
        return field.single_bytes

    @property
    def message_omitted(self) -> bool:
        """Return whether journalctl omitted an oversized MESSAGE as null."""
        field = self.field("MESSAGE")
        return field is not None and field.single_omitted

    @property
    def priority(self) -> JournalPriority | None:
        """Return a recognized syslog priority when the field is scalar."""
        value = self.single_nonnegative_int("PRIORITY")
        if value is None:
            return None
        try:
            return JournalPriority(value)
        except ValueError:
            return None

    @property
    def systemd_unit(self) -> str | None:
        """Return the originating systemd unit when directly attributed."""
        return self.single_text("_SYSTEMD_UNIT")

    @property
    def systemd_invocation_id(self) -> str | None:
        """Return the originating unit invocation ID when available."""
        return self.single_text("_SYSTEMD_INVOCATION_ID")

    @property
    def pid(self) -> int | None:
        """Return the originating process ID when unambiguous."""
        return self.single_nonnegative_int("_PID")

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly representation without losing field shape."""
        return {
            "cursor": self.cursor,
            "realtime_timestamp_usec": self.realtime_timestamp_usec,
            "monotonic_timestamp_usec": self.monotonic_timestamp_usec,
            "boot_id": self.boot_id,
            "fields": {field.name: field.to_json_value() for field in self.fields},
        }


@dataclass(frozen=True, slots=True)
class SystemdJournalBatch:
    """One bounded read result for a configured system service journal."""

    requested_unit: str
    after_cursor: str | None
    entry_limit: int
    entries: tuple[SystemdJournalEntry, ...]
    diagnostic: str | None
    captured_at: datetime

    def __post_init__(self) -> None:
        """Validate batch identity, bounds, and cursor uniqueness."""
        validate_service_unit_name(self.requested_unit, field_name="requested_unit")
        if self.after_cursor is not None:
            validate_journal_cursor(self.after_cursor, field_name="after_cursor")
        if (
            isinstance(self.entry_limit, bool)
            or not isinstance(self.entry_limit, int)
            or self.entry_limit <= 0
        ):
            raise SystemdJournalModelError("entry_limit must be a positive integer")
        if not isinstance(self.entries, tuple):
            raise SystemdJournalModelError("entries must be a tuple")
        if len(self.entries) > self.entry_limit:
            raise SystemdJournalModelError("entries must not exceed entry_limit")
        if self.diagnostic is not None:
            if not isinstance(self.diagnostic, str) or not self.diagnostic:
                raise SystemdJournalModelError(
                    "diagnostic must be a non-empty string or None"
                )
            if "\x00" in self.diagnostic:
                raise SystemdJournalModelError("diagnostic must not contain NUL bytes")
            if len(self.diagnostic) > _MAX_DIAGNOSTIC_CHARS:
                raise SystemdJournalModelError("diagnostic exceeds its bounded size")
        cursors = tuple(entry.cursor for entry in self.entries)
        if len(set(cursors)) != len(cursors):
            raise SystemdJournalModelError("entries must not contain duplicate cursors")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise SystemdJournalModelError("captured_at must be timezone-aware")

    @property
    def next_cursor(self) -> str | None:
        """Return the cursor to use for the next incremental read."""
        if self.entries:
            return self.entries[-1].cursor
        return self.after_cursor

    @property
    def entry_limit_reached(self) -> bool:
        """Return whether the current read filled the configured batch bound."""
        return len(self.entries) == self.entry_limit

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly batch representation."""
        return {
            "requested_unit": self.requested_unit,
            "after_cursor": self.after_cursor,
            "next_cursor": self.next_cursor,
            "entry_limit": self.entry_limit,
            "entry_limit_reached": self.entry_limit_reached,
            "entry_count": len(self.entries),
            "diagnostic": self.diagnostic,
            "captured_at": self.captured_at.isoformat(),
            "entries": [entry.to_dict() for entry in self.entries],
        }
