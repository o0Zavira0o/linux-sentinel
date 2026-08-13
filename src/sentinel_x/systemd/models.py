"""Typed read-only systemd service state models for Sentinel-X."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

_SERVICE_UNIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9_.:@-]+\.service$"
)


class SystemdModelError(ValueError):
    """Base error for invalid systemd state models."""


class SystemdUnitNameError(SystemdModelError):
    """Raised when a service unit name is outside Sentinel-X's safe subset."""


class SystemdActiveState(str, Enum):
    """Known high-level ACTIVE states currently documented by systemd."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    MAINTENANCE = "maintenance"
    RELOADING = "reloading"
    REFRESHING = "refreshing"


def validate_service_unit_name(value: str, *, field_name: str) -> str:
    """Validate one service name using a conservative systemd-safe subset."""
    if not isinstance(value, str):
        raise SystemdUnitNameError(f"{field_name} must be a string")

    normalized = value.strip()
    if normalized != value or not normalized:
        raise SystemdUnitNameError(
            f"{field_name} must be non-empty and contain no surrounding whitespace"
        )
    if len(normalized) > 255:
        raise SystemdUnitNameError(f"{field_name} is too long")
    if normalized.startswith("-"):
        raise SystemdUnitNameError(f"{field_name} must not start with a hyphen")
    if _SERVICE_UNIT_PATTERN.fullmatch(normalized) is None:
        raise SystemdUnitNameError(
            f"{field_name} must be a canonical-looking .service unit name"
        )
    return normalized


def _validate_nonnegative_optional_int(
    value: int | None,
    *,
    field_name: str,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemdModelError(f"{field_name} must be a non-negative integer or None")


def _validate_nonnegative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SystemdModelError(f"{field_name} must be a non-negative integer")


def _validate_optional_text(value: str | None, *, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise SystemdModelError(f"{field_name} must be a string or None")
    if "\x00" in value:
        raise SystemdModelError(f"{field_name} must not contain NUL bytes")


def _validate_text(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise SystemdModelError(f"{field_name} must be a non-empty string")
    if "\x00" in value:
        raise SystemdModelError(f"{field_name} must not contain NUL bytes")


def _validate_unit_names(values: tuple[str, ...], *, field_name: str) -> None:
    if not isinstance(values, tuple):
        raise SystemdModelError(f"{field_name} must be a tuple")
    if len(set(values)) != len(values):
        raise SystemdModelError(f"{field_name} must not contain duplicates")
    for value in values:
        validate_service_unit_name(value, field_name=field_name)


def _validate_dependency_names(values: tuple[str, ...], *, field_name: str) -> None:
    if not isinstance(values, tuple):
        raise SystemdModelError(f"{field_name} must be a tuple")
    if len(set(values)) != len(values):
        raise SystemdModelError(f"{field_name} must not contain duplicates")
    for value in values:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise SystemdModelError(
                f"{field_name} entries must be non-empty strings without NUL bytes"
            )


@dataclass(frozen=True, slots=True)
class SystemdServiceSnapshot:
    """One bounded read-only snapshot of a systemd service unit."""

    requested_name: str
    canonical_name: str
    names: tuple[str, ...]
    description: str
    load_state: str
    active_state: str
    sub_state: str
    unit_file_state: str | None
    service_type: str | None
    restart_policy: str | None
    result: str | None
    invocation_id: str | None
    control_group: str | None
    fragment_path: str | None
    source_path: str | None
    drop_in_paths: tuple[str, ...]
    requires: tuple[str, ...]
    wants: tuple[str, ...]
    after: tuple[str, ...]
    before: tuple[str, ...]
    can_start: bool
    can_stop: bool
    can_reload: bool
    main_pid: int | None
    exec_main_code: int | None
    exec_main_status: int | None
    restart_count: int
    state_change_monotonic_usec: int | None
    active_enter_monotonic_usec: int | None
    inactive_enter_monotonic_usec: int | None
    exec_main_start_monotonic_usec: int | None
    exec_main_exit_monotonic_usec: int | None
    captured_at: datetime

    def __post_init__(self) -> None:
        """Validate snapshot invariants while preserving forward-compatible states."""
        validate_service_unit_name(
            self.requested_name,
            field_name="requested_name",
        )
        validate_service_unit_name(
            self.canonical_name,
            field_name="canonical_name",
        )
        _validate_unit_names(self.names, field_name="names")
        if self.canonical_name not in self.names:
            raise SystemdModelError("names must include canonical_name")

        for text_field_name, text_value in (
            ("description", self.description),
            ("load_state", self.load_state),
            ("active_state", self.active_state),
            ("sub_state", self.sub_state),
        ):
            _validate_text(text_value, field_name=text_field_name)

        for optional_text_field_name, optional_text_value in (
            ("unit_file_state", self.unit_file_state),
            ("service_type", self.service_type),
            ("restart_policy", self.restart_policy),
            ("result", self.result),
            ("invocation_id", self.invocation_id),
            ("control_group", self.control_group),
            ("fragment_path", self.fragment_path),
            ("source_path", self.source_path),
        ):
            _validate_optional_text(
                optional_text_value,
                field_name=optional_text_field_name,
            )

        for list_field_name, list_values in (
            ("drop_in_paths", self.drop_in_paths),
            ("requires", self.requires),
            ("wants", self.wants),
            ("after", self.after),
            ("before", self.before),
        ):
            _validate_dependency_names(list_values, field_name=list_field_name)

        for boolean_field_name, boolean_value in (
            ("can_start", self.can_start),
            ("can_stop", self.can_stop),
            ("can_reload", self.can_reload),
        ):
            if type(boolean_value) is not bool:
                raise SystemdModelError(f"{boolean_field_name} must be a boolean")

        for integer_field_name, integer_value in (
            ("main_pid", self.main_pid),
            ("exec_main_code", self.exec_main_code),
            ("exec_main_status", self.exec_main_status),
            ("state_change_monotonic_usec", self.state_change_monotonic_usec),
            ("active_enter_monotonic_usec", self.active_enter_monotonic_usec),
            ("inactive_enter_monotonic_usec", self.inactive_enter_monotonic_usec),
            ("exec_main_start_monotonic_usec", self.exec_main_start_monotonic_usec),
            ("exec_main_exit_monotonic_usec", self.exec_main_exit_monotonic_usec),
        ):
            _validate_nonnegative_optional_int(
                integer_value,
                field_name=integer_field_name,
            )

        _validate_nonnegative_int(self.restart_count, field_name="restart_count")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise SystemdModelError("captured_at must be timezone-aware")

    @property
    def known_active_state(self) -> SystemdActiveState | None:
        """Return the documented high-level active state when recognized."""
        try:
            return SystemdActiveState(self.active_state)
        except ValueError:
            return None

    @property
    def is_active(self) -> bool:
        """Return whether systemd reports the service as active."""
        return self.active_state == SystemdActiveState.ACTIVE.value

    @property
    def is_failed(self) -> bool:
        """Return whether systemd reports the service as failed."""
        return self.active_state == SystemdActiveState.FAILED.value

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly service snapshot."""
        return {
            "requested_name": self.requested_name,
            "canonical_name": self.canonical_name,
            "names": list(self.names),
            "description": self.description,
            "load_state": self.load_state,
            "active_state": self.active_state,
            "known_active_state": (
                None
                if self.known_active_state is None
                else self.known_active_state.value
            ),
            "sub_state": self.sub_state,
            "unit_file_state": self.unit_file_state,
            "service_type": self.service_type,
            "restart_policy": self.restart_policy,
            "result": self.result,
            "invocation_id": self.invocation_id,
            "control_group": self.control_group,
            "fragment_path": self.fragment_path,
            "source_path": self.source_path,
            "drop_in_paths": list(self.drop_in_paths),
            "requires": list(self.requires),
            "wants": list(self.wants),
            "after": list(self.after),
            "before": list(self.before),
            "can_start": self.can_start,
            "can_stop": self.can_stop,
            "can_reload": self.can_reload,
            "main_pid": self.main_pid,
            "exec_main_code": self.exec_main_code,
            "exec_main_status": self.exec_main_status,
            "restart_count": self.restart_count,
            "state_change_monotonic_usec": self.state_change_monotonic_usec,
            "active_enter_monotonic_usec": self.active_enter_monotonic_usec,
            "inactive_enter_monotonic_usec": self.inactive_enter_monotonic_usec,
            "exec_main_start_monotonic_usec": self.exec_main_start_monotonic_usec,
            "exec_main_exit_monotonic_usec": self.exec_main_exit_monotonic_usec,
            "captured_at": self.captured_at.isoformat(),
        }
