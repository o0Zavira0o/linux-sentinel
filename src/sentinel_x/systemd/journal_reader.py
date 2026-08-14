"""Bounded read-only journald evidence access through journalctl JSON output."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol

from sentinel_x.systemd.journal_models import (
    JournalAtom,
    JournalField,
    SystemdJournalBatch,
    SystemdJournalEntry,
    SystemdJournalModelError,
    validate_journal_cursor,
)
from sentinel_x.systemd.models import validate_service_unit_name

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 3.0
_MIN_TIMEOUT_SECONDS: Final[float] = 0.1
_MAX_TIMEOUT_SECONDS: Final[float] = 30.0
_DEFAULT_MAX_ENTRIES: Final[int] = 64
_MIN_MAX_ENTRIES: Final[int] = 1
_MAX_MAX_ENTRIES: Final[int] = 64
_MAX_STDOUT_BYTES: Final[int] = 8 * 1024 * 1024
_MAX_STDERR_BYTES: Final[int] = 65_536
_MAX_ERROR_MESSAGE_CHARS: Final[int] = 512

_JOURNAL_OUTPUT_FIELDS: Final[tuple[str, ...]] = (
    "MESSAGE",
    "PRIORITY",
    "MESSAGE_ID",
    "SYSLOG_IDENTIFIER",
    "SYSLOG_FACILITY",
    "_TRANSPORT",
    "_SYSTEMD_UNIT",
    "_SYSTEMD_INVOCATION_ID",
    "INVOCATION_ID",
    "UNIT",
    "OBJECT_SYSTEMD_UNIT",
    "OBJECT_SYSTEMD_INVOCATION_ID",
    "COREDUMP_UNIT",
    "_PID",
    "_UID",
    "_GID",
    "_COMM",
    "_EXE",
    "_HOSTNAME",
    "_MACHINE_ID",
    "_SOURCE_REALTIME_TIMESTAMP",
    "CODE_FILE",
    "CODE_LINE",
    "CODE_FUNC",
    "ERRNO",
)


class SystemdJournalReadError(RuntimeError):
    """Base error for bounded read-only journald access."""


class SystemdJournalExecutableNotFoundError(SystemdJournalReadError):
    """Raised when journalctl cannot be resolved."""


class SystemdJournalCommandError(SystemdJournalReadError):
    """Raised when journalctl fails to execute or returns non-zero."""


class SystemdJournalCommandTimeoutError(SystemdJournalCommandError):
    """Raised when a journal read exceeds its bounded timeout."""


class SystemdJournalCursorUnavailableError(SystemdJournalCommandError):
    """Raised when journalctl cannot resume from a previously valid cursor."""


class SystemdJournalProtocolError(SystemdJournalReadError):
    """Raised when journalctl JSON violates the expected bounded protocol."""


@dataclass(frozen=True, slots=True)
class JournalctlCommandResult:
    """Raw bounded command result returned by a journalctl runner."""

    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        """Validate primitive command-result types."""
        if isinstance(self.returncode, bool) or not isinstance(self.returncode, int):
            raise TypeError("returncode must be an integer")
        if not isinstance(self.stdout, bytes):
            raise TypeError("stdout must be bytes")
        if not isinstance(self.stderr, bytes):
            raise TypeError("stderr must be bytes")


class JournalctlCommandRunner(Protocol):
    """Callable contract for one explicit journalctl argv execution."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> JournalctlCommandResult:
        """Execute one bounded journalctl command without a shell."""

        ...


class JournalClock(Protocol):
    """Typed callable contract for journal batch capture timestamps."""

    def __call__(self) -> datetime:
        """Return a timezone-aware timestamp."""

        ...


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _command_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C",
            "SYSTEMD_COLORS": "0",
            "SYSTEMD_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    return environment


def _default_runner(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> JournalctlCommandResult:
    try:
        completed = subprocess.run(
            tuple(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=timeout_seconds,
            env=_command_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemdJournalCommandTimeoutError(
            f"journalctl read exceeded {timeout_seconds:.3f} seconds"
        ) from exc
    except OSError as exc:
        raise SystemdJournalCommandError(f"journalctl execution failed: {exc}") from exc
    return JournalctlCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _bounded_text(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_ERROR_MESSAGE_CHARS:
        return normalized
    return normalized[: _MAX_ERROR_MESSAGE_CHARS - 3] + "..."


def _decode_capture(value: bytes, *, stream_name: str, byte_limit: int) -> str:
    if len(value) > byte_limit:
        raise SystemdJournalProtocolError(
            f"journalctl {stream_name} exceeded {byte_limit} bytes"
        )
    if b"\x00" in value:
        raise SystemdJournalProtocolError(
            f"journalctl {stream_name} contains a NUL byte"
        )
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SystemdJournalProtocolError(
            f"journalctl {stream_name} is not valid UTF-8"
        ) from exc


def _json_object(line: str, *, line_number: int) -> dict[str, object]:
    try:
        parsed: object = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SystemdJournalProtocolError(
            f"journalctl JSON line {line_number} is malformed"
        ) from exc
    if not isinstance(parsed, dict):
        raise SystemdJournalProtocolError(
            f"journalctl JSON line {line_number} must be an object"
        )
    normalized: dict[str, object] = {}
    for raw_key, raw_value in parsed.items():
        if not isinstance(raw_key, str):
            raise SystemdJournalProtocolError(
                f"journalctl JSON line {line_number} has a non-string key"
            )
        normalized[raw_key] = raw_value
    return normalized


def _required_scalar_text(
    record: Mapping[str, object],
    field_name: str,
    *,
    line_number: int,
) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value:
        raise SystemdJournalProtocolError(
            f"journalctl JSON line {line_number} field {field_name} "
            "must be a non-empty string"
        )
    return value


def _required_nonnegative_int(
    record: Mapping[str, object],
    field_name: str,
    *,
    line_number: int,
) -> int:
    text = _required_scalar_text(
        record,
        field_name,
        line_number=line_number,
    )
    try:
        value = int(text, 10)
    except ValueError as exc:
        raise SystemdJournalProtocolError(
            f"journalctl JSON line {line_number} field {field_name} "
            "must be a decimal integer"
        ) from exc
    if value < 0:
        raise SystemdJournalProtocolError(
            f"journalctl JSON line {line_number} field {field_name} "
            "must not be negative"
        )
    return value


def _byte_array(value: list[object], *, field_name: str) -> bytes:
    byte_values: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255:
            raise SystemdJournalProtocolError(
                f"journalctl field {field_name} has an invalid binary byte array"
            )
        byte_values.append(item)
    return bytes(byte_values)


def _atom(value: object, *, field_name: str) -> JournalAtom:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list):
        return _byte_array(value, field_name=field_name)
    raise SystemdJournalProtocolError(
        f"journalctl field {field_name} has an unsupported JSON value"
    )


def _field_values(value: object, *, field_name: str) -> tuple[JournalAtom, ...]:
    if not isinstance(value, list):
        return (_atom(value, field_name=field_name),)
    if not value:
        return (b"",)
    if all(
        not isinstance(item, bool) and isinstance(item, int) and 0 <= item <= 255
        for item in value
    ):
        return (_byte_array(value, field_name=field_name),)
    values = tuple(_atom(item, field_name=field_name) for item in value)
    if not values:
        raise SystemdJournalProtocolError(
            f"journalctl field {field_name} has no values"
        )
    return values


def _selected_fields(record: Mapping[str, object]) -> tuple[JournalField, ...]:
    fields: list[JournalField] = []
    for field_name in _JOURNAL_OUTPUT_FIELDS:
        if field_name not in record:
            continue
        try:
            fields.append(
                JournalField(
                    name=field_name,
                    values=_field_values(
                        record[field_name],
                        field_name=field_name,
                    ),
                )
            )
        except SystemdJournalModelError as exc:
            raise SystemdJournalProtocolError(
                f"journalctl field {field_name} is invalid: {exc}"
            ) from exc
    return tuple(fields)


def _entry_from_record(
    record: Mapping[str, object],
    *,
    line_number: int,
) -> SystemdJournalEntry:
    cursor = _required_scalar_text(
        record,
        "__CURSOR",
        line_number=line_number,
    )
    try:
        validate_journal_cursor(cursor, field_name="__CURSOR")
    except SystemdJournalModelError as exc:
        raise SystemdJournalProtocolError(
            f"journalctl JSON line {line_number} has an invalid cursor: {exc}"
        ) from exc
    realtime_timestamp_usec = _required_nonnegative_int(
        record,
        "__REALTIME_TIMESTAMP",
        line_number=line_number,
    )
    monotonic_timestamp_usec = _required_nonnegative_int(
        record,
        "__MONOTONIC_TIMESTAMP",
        line_number=line_number,
    )
    boot_id = _required_scalar_text(
        record,
        "_BOOT_ID",
        line_number=line_number,
    )
    try:
        return SystemdJournalEntry(
            cursor=cursor,
            realtime_timestamp_usec=realtime_timestamp_usec,
            monotonic_timestamp_usec=monotonic_timestamp_usec,
            boot_id=boot_id,
            fields=_selected_fields(record),
        )
    except SystemdJournalModelError as exc:
        raise SystemdJournalProtocolError(
            f"journalctl JSON line {line_number} violates entry invariants: {exc}"
        ) from exc


def _parse_entries(stdout: str, *, entry_limit: int) -> tuple[SystemdJournalEntry, ...]:
    entries: list[SystemdJournalEntry] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line:
            continue
        record = _json_object(line, line_number=line_number)
        entry = _entry_from_record(record, line_number=line_number)
        entries.append(entry)
        if len(entries) > entry_limit:
            raise SystemdJournalProtocolError(
                "journalctl returned more entries than the requested bound"
            )
    return tuple(entries)


def _validate_timeout_seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_seconds must be a real number")
    normalized = float(value)
    if not _MIN_TIMEOUT_SECONDS <= normalized <= _MAX_TIMEOUT_SECONDS:
        raise ValueError(
            "timeout_seconds must be between "
            f"{_MIN_TIMEOUT_SECONDS} and {_MAX_TIMEOUT_SECONDS} seconds"
        )
    return normalized


def _validate_max_entries(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_entries must be an integer")
    if not _MIN_MAX_ENTRIES <= value <= _MAX_MAX_ENTRIES:
        raise ValueError(
            f"max_entries must be between {_MIN_MAX_ENTRIES} and {_MAX_MAX_ENTRIES}"
        )
    return value


class JournalctlServiceReader:
    """Read bounded current-boot journal evidence for one system service."""

    def __init__(
        self,
        *,
        journalctl_path: str | Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        runner: JournalctlCommandRunner | None = None,
        clock: JournalClock | None = None,
    ) -> None:
        self._timeout_seconds = _validate_timeout_seconds(timeout_seconds)
        if journalctl_path is None:
            resolved = shutil.which("journalctl")
            if resolved is None:
                raise SystemdJournalExecutableNotFoundError(
                    "journalctl executable was not found in PATH"
                )
            normalized_path = resolved
        else:
            normalized_path = os.fspath(journalctl_path)
            if not normalized_path or "\x00" in normalized_path:
                raise ValueError("journalctl_path must be non-empty and contain no NUL")
        self._journalctl_path = normalized_path
        self._runner = _default_runner if runner is None else runner
        self._clock: JournalClock = _utc_now if clock is None else clock

    @property
    def journalctl_path(self) -> str:
        """Return the resolved executable used for read-only journal queries."""
        return self._journalctl_path

    @property
    def timeout_seconds(self) -> float:
        """Return the bounded command timeout."""
        return self._timeout_seconds

    def read_service(
        self,
        unit_name: str,
        *,
        after_cursor: str | None = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> SystemdJournalBatch:
        """Read one bounded current-boot batch, optionally after an opaque cursor."""
        requested_unit = validate_service_unit_name(
            unit_name,
            field_name="unit_name",
        )
        entry_limit = _validate_max_entries(max_entries)
        if after_cursor is not None:
            try:
                validate_journal_cursor(after_cursor, field_name="after_cursor")
            except SystemdJournalModelError as exc:
                raise SystemdJournalProtocolError(str(exc)) from exc

        line_argument = (
            f"+{entry_limit}" if after_cursor is not None else str(entry_limit)
        )
        argv: list[str] = [
            self._journalctl_path,
            "--system",
            "--no-pager",
            "--output=json",
            "--output-fields=" + ",".join(_JOURNAL_OUTPUT_FIELDS),
            "--boot=0",
            "--unit=" + requested_unit,
            "--lines=" + line_argument,
        ]
        if after_cursor is not None:
            argv.append("--after-cursor=" + after_cursor)

        try:
            result = self._runner(
                tuple(argv),
                timeout_seconds=self._timeout_seconds,
            )
        except SystemdJournalCommandError:
            raise
        except OSError as exc:
            raise SystemdJournalCommandError(
                f"journalctl execution failed: {exc}"
            ) from exc

        stdout = _decode_capture(
            result.stdout,
            stream_name="stdout",
            byte_limit=_MAX_STDOUT_BYTES,
        )
        stderr = _decode_capture(
            result.stderr,
            stream_name="stderr",
            byte_limit=_MAX_STDERR_BYTES,
        )
        if result.returncode != 0:
            detail = _bounded_text(stderr or stdout or "no diagnostic output")
            message = (
                f"journalctl read failed for {requested_unit} "
                f"with exit status {result.returncode}: {detail}"
            )
            if after_cursor is not None and stderr.lstrip().startswith(
                "Failed to seek to cursor:"
            ):
                raise SystemdJournalCursorUnavailableError(message)
            raise SystemdJournalCommandError(message)

        entries = _parse_entries(stdout, entry_limit=entry_limit)
        diagnostic = _bounded_text(stderr) if stderr.strip() else None
        captured_at = self._clock()
        if not isinstance(captured_at, datetime):
            raise SystemdJournalProtocolError("clock must return a datetime")
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise SystemdJournalProtocolError(
                "clock must return a timezone-aware datetime"
            )
        try:
            return SystemdJournalBatch(
                requested_unit=requested_unit,
                after_cursor=after_cursor,
                entry_limit=entry_limit,
                entries=entries,
                diagnostic=diagnostic,
                captured_at=captured_at,
            )
        except SystemdJournalModelError as exc:
            raise SystemdJournalProtocolError(
                f"journal batch violates model invariants: {exc}"
            ) from exc
