"""Read-only systemd service inspection through a constrained systemctl adapter."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol

from sentinel_x.systemd.models import (
    SystemdModelError,
    SystemdServiceSnapshot,
    validate_service_unit_name,
)

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 3.0
_MIN_TIMEOUT_SECONDS: Final[float] = 0.1
_MAX_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_CAPTURE_BYTES: Final[int] = 65_536
_MAX_ERROR_MESSAGE_CHARS: Final[int] = 512

_SYSTEMCTL_PROPERTIES: Final[tuple[str, ...]] = (
    "Id",
    "Names",
    "Description",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "FragmentPath",
    "SourcePath",
    "DropInPaths",
    "Requires",
    "Wants",
    "After",
    "Before",
    "CanStart",
    "CanStop",
    "CanReload",
    "ControlGroup",
    "InvocationID",
    "StateChangeTimestampMonotonic",
    "ActiveEnterTimestampMonotonic",
    "InactiveEnterTimestampMonotonic",
    "Type",
    "Restart",
    "MainPID",
    "ExecMainCode",
    "ExecMainStatus",
    "Result",
    "NRestarts",
    "ExecMainStartTimestampMonotonic",
    "ExecMainExitTimestampMonotonic",
)
_REQUIRED_PROPERTIES: Final[frozenset[str]] = frozenset(
    {
        "Id",
        "Description",
        "LoadState",
        "ActiveState",
        "SubState",
    }
)


class SystemdReadError(RuntimeError):
    """Base error for read-only systemd service inspection."""


class SystemdExecutableNotFoundError(SystemdReadError):
    """Raised when the systemctl executable cannot be resolved."""


class SystemdCommandError(SystemdReadError):
    """Raised when systemctl returns a non-zero status or cannot execute."""


class SystemdCommandTimeoutError(SystemdCommandError):
    """Raised when a systemctl read exceeds its bounded timeout."""


class SystemdProtocolError(SystemdReadError):
    """Raised when systemctl output violates the expected property protocol."""


@dataclass(frozen=True, slots=True)
class SystemctlCommandResult:
    """Bounded raw result returned by a systemctl command runner."""

    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        """Validate command-result primitives."""
        if isinstance(self.returncode, bool) or not isinstance(self.returncode, int):
            raise TypeError("returncode must be an integer")
        if not isinstance(self.stdout, bytes):
            raise TypeError("stdout must be bytes")
        if not isinstance(self.stderr, bytes):
            raise TypeError("stderr must be bytes")


class SystemctlCommandRunner(Protocol):
    """Callable contract used to execute one explicit systemctl argv."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> SystemctlCommandResult:
        """Execute one bounded command without a shell."""

        ...


class Clock(Protocol):
    """Typed callable contract for UTC timestamp providers."""

    def __call__(self) -> datetime:
        """Return a timezone-aware timestamp."""

        ...


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _bounded_text(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_ERROR_MESSAGE_CHARS:
        return normalized
    return normalized[: _MAX_ERROR_MESSAGE_CHARS - 3] + "..."


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
) -> SystemctlCommandResult:
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
        raise SystemdCommandTimeoutError(
            f"systemctl read exceeded {timeout_seconds:.3f} seconds"
        ) from exc
    except OSError as exc:
        raise SystemdCommandError(f"systemctl execution failed: {exc}") from exc

    return SystemctlCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _decode_capture(value: bytes, *, stream_name: str) -> str:
    if len(value) > _MAX_CAPTURE_BYTES:
        raise SystemdProtocolError(
            f"systemctl {stream_name} exceeded {_MAX_CAPTURE_BYTES} bytes"
        )
    if b"\x00" in value:
        raise SystemdProtocolError(f"systemctl {stream_name} contains a NUL byte")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SystemdProtocolError(
            f"systemctl {stream_name} is not valid UTF-8"
        ) from exc


def _parse_properties(stdout: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line:
            continue
        if "=" not in line:
            raise SystemdProtocolError(
                f"systemctl output line {line_number} is not a property assignment"
            )
        key, value = line.split("=", 1)
        if not key:
            raise SystemdProtocolError(
                f"systemctl output line {line_number} has an empty property name"
            )
        if key in properties:
            raise SystemdProtocolError(f"duplicate systemctl property: {key}")
        properties[key] = value

    missing = sorted(_REQUIRED_PROPERTIES - properties.keys())
    if missing:
        raise SystemdProtocolError(
            "systemctl output is missing required properties: " + ", ".join(missing)
        )
    return properties


def _optional_text(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _parse_words(value: str | None) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    words = tuple(value.split())
    if len(set(words)) != len(words):
        raise SystemdProtocolError("systemctl list property contains duplicate entries")
    return words


def _parse_bool(value: str | None, *, property_name: str) -> bool:
    if value == "yes":
        return True
    if value == "no":
        return False
    raise SystemdProtocolError(
        f"systemctl property {property_name} must be 'yes' or 'no'"
    )


def _parse_nonnegative_int(
    value: str | None,
    *,
    property_name: str,
    empty_default: int | None = None,
) -> int | None:
    if value is None or value == "":
        return empty_default
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise SystemdProtocolError(
            f"systemctl property {property_name} must be an integer"
        ) from exc
    if parsed < 0:
        raise SystemdProtocolError(
            f"systemctl property {property_name} must not be negative"
        )
    return parsed


def _zero_as_none(value: int | None) -> int | None:
    if value == 0:
        return None
    return value


def _parse_names(properties: Mapping[str, str], canonical_name: str) -> tuple[str, ...]:
    names = _parse_words(properties.get("Names"))
    if not names:
        return (canonical_name,)
    if canonical_name in names:
        return names
    return (canonical_name, *names)


class SystemctlServiceReader:
    """Inspect system services through read-only, explicit systemctl show calls."""

    def __init__(
        self,
        *,
        systemctl_path: str | Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        runner: SystemctlCommandRunner | None = None,
        clock: Clock | None = None,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds,
            (int, float),
        ):
            raise TypeError("timeout_seconds must be a real number")
        normalized_timeout = float(timeout_seconds)
        if not _MIN_TIMEOUT_SECONDS <= normalized_timeout <= _MAX_TIMEOUT_SECONDS:
            raise ValueError(
                "timeout_seconds must be between "
                f"{_MIN_TIMEOUT_SECONDS} and {_MAX_TIMEOUT_SECONDS} seconds"
            )

        if systemctl_path is None:
            resolved = shutil.which("systemctl")
            if resolved is None:
                raise SystemdExecutableNotFoundError(
                    "systemctl executable was not found in PATH"
                )
            normalized_path = resolved
        else:
            normalized_path = os.fspath(systemctl_path)
            if not normalized_path or "\x00" in normalized_path:
                raise ValueError("systemctl_path must be non-empty and contain no NUL")

        self._systemctl_path = normalized_path
        self._timeout_seconds = normalized_timeout
        self._runner = _default_runner if runner is None else runner
        self._clock: Clock = _utc_now if clock is None else clock

    @property
    def systemctl_path(self) -> str:
        """Return the resolved executable path used for read-only inspection."""
        return self._systemctl_path

    @property
    def timeout_seconds(self) -> float:
        """Return the bounded command timeout."""
        return self._timeout_seconds

    def read_service(self, unit_name: str) -> SystemdServiceSnapshot:
        """Read one system service without performing any lifecycle operation."""
        requested_name = validate_service_unit_name(
            unit_name,
            field_name="unit_name",
        )
        argv = (
            self._systemctl_path,
            "--system",
            "--no-pager",
            "--no-ask-password",
            "show",
            "--all",
            "--property=" + ",".join(_SYSTEMCTL_PROPERTIES),
            requested_name,
        )

        try:
            result = self._runner(
                argv,
                timeout_seconds=self._timeout_seconds,
            )
        except SystemdCommandError:
            raise
        except OSError as exc:
            raise SystemdCommandError(f"systemctl execution failed: {exc}") from exc

        stdout = _decode_capture(result.stdout, stream_name="stdout")
        stderr = _decode_capture(result.stderr, stream_name="stderr")
        if result.returncode != 0:
            detail = _bounded_text(stderr or stdout or "no diagnostic output")
            raise SystemdCommandError(
                f"systemctl show failed for {requested_name} "
                f"with exit status {result.returncode}: {detail}"
            )

        properties = _parse_properties(stdout)
        canonical_name = validate_service_unit_name(
            properties["Id"],
            field_name="Id",
        )
        captured_at = self._clock()
        if not isinstance(captured_at, datetime):
            raise SystemdProtocolError("clock must return a datetime")
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise SystemdProtocolError("clock must return a timezone-aware datetime")

        main_pid = _zero_as_none(
            _parse_nonnegative_int(
                properties.get("MainPID"),
                property_name="MainPID",
            )
        )
        exec_main_code = _zero_as_none(
            _parse_nonnegative_int(
                properties.get("ExecMainCode"),
                property_name="ExecMainCode",
            )
        )
        exec_main_status = _parse_nonnegative_int(
            properties.get("ExecMainStatus"),
            property_name="ExecMainStatus",
        )
        if exec_main_code is None:
            exec_main_status = None

        restart_count = _parse_nonnegative_int(
            properties.get("NRestarts"),
            property_name="NRestarts",
            empty_default=0,
        )
        assert restart_count is not None

        try:
            return SystemdServiceSnapshot(
                requested_name=requested_name,
                canonical_name=canonical_name,
                names=_parse_names(properties, canonical_name),
                description=properties["Description"] or canonical_name,
                load_state=properties["LoadState"],
                active_state=properties["ActiveState"],
                sub_state=properties["SubState"],
                unit_file_state=_optional_text(properties.get("UnitFileState")),
                service_type=_optional_text(properties.get("Type")),
                restart_policy=_optional_text(properties.get("Restart")),
                result=_optional_text(properties.get("Result")),
                invocation_id=_optional_text(properties.get("InvocationID")),
                control_group=_optional_text(properties.get("ControlGroup")),
                fragment_path=_optional_text(properties.get("FragmentPath")),
                source_path=_optional_text(properties.get("SourcePath")),
                drop_in_paths=_parse_words(properties.get("DropInPaths")),
                requires=_parse_words(properties.get("Requires")),
                wants=_parse_words(properties.get("Wants")),
                after=_parse_words(properties.get("After")),
                before=_parse_words(properties.get("Before")),
                can_start=_parse_bool(
                    properties.get("CanStart"),
                    property_name="CanStart",
                ),
                can_stop=_parse_bool(
                    properties.get("CanStop"),
                    property_name="CanStop",
                ),
                can_reload=_parse_bool(
                    properties.get("CanReload"),
                    property_name="CanReload",
                ),
                main_pid=main_pid,
                exec_main_code=exec_main_code,
                exec_main_status=exec_main_status,
                restart_count=restart_count,
                state_change_monotonic_usec=_zero_as_none(
                    _parse_nonnegative_int(
                        properties.get("StateChangeTimestampMonotonic"),
                        property_name="StateChangeTimestampMonotonic",
                    )
                ),
                active_enter_monotonic_usec=_zero_as_none(
                    _parse_nonnegative_int(
                        properties.get("ActiveEnterTimestampMonotonic"),
                        property_name="ActiveEnterTimestampMonotonic",
                    )
                ),
                inactive_enter_monotonic_usec=_zero_as_none(
                    _parse_nonnegative_int(
                        properties.get("InactiveEnterTimestampMonotonic"),
                        property_name="InactiveEnterTimestampMonotonic",
                    )
                ),
                exec_main_start_monotonic_usec=_zero_as_none(
                    _parse_nonnegative_int(
                        properties.get("ExecMainStartTimestampMonotonic"),
                        property_name="ExecMainStartTimestampMonotonic",
                    )
                ),
                exec_main_exit_monotonic_usec=_zero_as_none(
                    _parse_nonnegative_int(
                        properties.get("ExecMainExitTimestampMonotonic"),
                        property_name="ExecMainExitTimestampMonotonic",
                    )
                ),
                captured_at=captured_at,
            )
        except SystemdModelError as exc:
            raise SystemdProtocolError(str(exc)) from exc
