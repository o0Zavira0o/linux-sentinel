"""Durable JSON Lines event recording for Sentinel-X."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Final, TextIO
from uuid import uuid4

from sentinel_x.core.events import SentinelEvent


RECORD_SCHEMA_VERSION: Final[int] = 1


class EventRecorderError(RuntimeError):
    """Base error raised by Sentinel-X event recording."""


class EventRecorderClosedError(EventRecorderError):
    """Raised when attempting to write to a closed recorder."""


class EventSerializationError(EventRecorderError):
    """Raised when an event cannot be serialized safely."""


class JsonlEventRecorder:
    """Thread-safe JSON Lines recorder for Sentinel-X events.

    Every recorder instance represents one Sentinel-X runtime
    execution and therefore owns a unique run_id and output file.
    """

    def __init__(
        self,
        *,
        directory: str | Path,
        instance_name: str,
        flush_on_write: bool = True,
    ) -> None:
        if not isinstance(instance_name, str):
            raise TypeError(
                "instance_name must be a string"
            )

        normalized_instance_name = (
            instance_name.strip()
        )

        if not normalized_instance_name:
            raise ValueError(
                "instance_name must not be empty"
            )

        if type(flush_on_write) is not bool:
            raise TypeError(
                "flush_on_write must be a boolean"
            )

        self._directory = (
            Path(directory).expanduser()
        )

        self._instance_name = (
            normalized_instance_name
        )

        self._flush_on_write = (
            flush_on_write
        )

        self._run_id = str(
            uuid4()
        )

        self._lock = RLock()

        self._closed = False

        self._records_written = 0

        self._last_error: str | None = None

        self._path = self._build_output_path()

        self._file = self._open_event_file()

    @property
    def path(self) -> Path:
        """Return the JSONL file owned by this recorder."""

        return self._path

    @property
    def run_id(self) -> str:
        """Return the unique identifier for this runtime session."""

        return self._run_id

    @property
    def records_written(self) -> int:
        """Return the number of successfully written records."""

        with self._lock:
            return self._records_written

    @property
    def closed(self) -> bool:
        """Return whether the recorder has been closed."""

        with self._lock:
            return self._closed

    @property
    def last_error(self) -> str | None:
        """Return the most recent recording error, if any."""

        with self._lock:
            return self._last_error

    def record(
        self,
        event: SentinelEvent,
    ) -> None:
        """Serialize and persist one SentinelEvent."""

        if not isinstance(
            event,
            SentinelEvent,
        ):
            raise TypeError(
                "record() requires a SentinelEvent"
            )

        with self._lock:
            if self._closed:
                raise EventRecorderClosedError(
                    "cannot record an event after "
                    "the recorder has been closed"
                )

            envelope = {
                "schema_version": (
                    RECORD_SCHEMA_VERSION
                ),
                "run_id": self._run_id,
                "instance_name": (
                    self._instance_name
                ),
                "recorded_at": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
                "event": event.to_dict(),
            }

            try:
                serialized = json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(
                        ",",
                        ":",
                    ),
                    default=_json_default,
                )

            except (
                TypeError,
                ValueError,
            ) as exc:
                self._last_error = (
                    f"{type(exc).__name__}: {exc}"
                )

                raise EventSerializationError(
                    "could not serialize Sentinel-X event "
                    f"{event.event_id}: {exc}"
                ) from exc

            line = serialized + "\n"

            try:
                written = self._file.write(
                    line
                )

                if written != len(line):
                    raise OSError(
                        "partial event-record write"
                    )

                if self._flush_on_write:
                    self._file.flush()

            except OSError as exc:
                self._last_error = (
                    f"{type(exc).__name__}: {exc}"
                )

                raise EventRecorderError(
                    "failed to write Sentinel-X event "
                    f"{event.event_id}: {exc}"
                ) from exc

            self._records_written += 1

    def close(self) -> None:
        """Flush, synchronize, and close the event file.

        Calling close more than once is safe.
        """

        with self._lock:
            if self._closed:
                return

            failure: EventRecorderError | None = None

            try:
                self._file.flush()

                os.fsync(
                    self._file.fileno()
                )

            except OSError as exc:
                self._last_error = (
                    f"{type(exc).__name__}: {exc}"
                )

                failure = EventRecorderError(
                    "failed to synchronize Sentinel-X "
                    f"event file {self._path}: {exc}"
                )

            try:
                self._file.close()

            except OSError as exc:
                self._last_error = (
                    f"{type(exc).__name__}: {exc}"
                )

                if failure is None:
                    failure = EventRecorderError(
                        "failed to close Sentinel-X "
                        f"event file {self._path}: {exc}"
                    )

            self._closed = True

            if failure is not None:
                raise failure

    def __call__(
        self,
        event: SentinelEvent,
    ) -> None:
        """Allow the recorder to be used directly as an EventBus handler."""

        self.record(
            event
        )

    def __enter__(
        self,
    ) -> JsonlEventRecorder:
        """Return this recorder for context-manager usage."""

        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        """Close the recorder when leaving a context manager."""

        self.close()

    def _build_output_path(
        self,
    ) -> Path:
        """Create the output directory and choose a unique file path."""

        try:
            self._directory.mkdir(
                mode=0o700,
                parents=True,
                exist_ok=True,
            )

        except OSError as exc:
            raise EventRecorderError(
                "could not create Sentinel-X event "
                f"directory {self._directory}: {exc}"
            ) from exc

        if not self._directory.is_dir():
            raise EventRecorderError(
                "Sentinel-X event storage path is "
                f"not a directory: {self._directory}"
            )

        timestamp = datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )

        filename = (
            f"{self._instance_name}-"
            f"{timestamp}-"
            f"pid{os.getpid()}-"
            f"{self._run_id}.jsonl"
        )

        return self._directory / filename

    def _open_event_file(
        self,
    ) -> TextIO:
        """Open the run file exclusively with private permissions."""

        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
        )

        try:
            file_descriptor = os.open(
                self._path,
                flags,
                0o600,
            )

        except OSError as exc:
            raise EventRecorderError(
                "could not create Sentinel-X "
                f"event file {self._path}: {exc}"
            ) from exc

        try:
            return os.fdopen(
                file_descriptor,
                mode="w",
                encoding="utf-8",
                newline="\n",
            )

        except Exception:
            os.close(
                file_descriptor
            )
            raise


def _json_default(
    value: Any,
) -> Any:
    """Serialize explicitly supported structured attribute types."""

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    if isinstance(
        value,
        Path,
    ):
        return str(value)

    if isinstance(
        value,
        Enum,
    ):
        return value.value

    raise TypeError(
        "unsupported event attribute type: "
        f"{type(value).__name__}"
    )
