"""Hardened systemd fixture artifacts for the Sentinel-X fault laboratory.

Phase 3B defines and renders a dedicated system-scope service fixture that is
observable by the frozen Phase 2 systemd/journald pipeline.  This module does
not install, start, stop, or otherwise mutate systemd state.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from sentinel_x.systemd.models import validate_service_unit_name

SYSTEMD_LAB_UNIT_PREFIX: Final[str] = "sentinel-x-lab-"
SYSTEMD_LAB_RUNTIME_ROOT: Final[Path] = Path("/run/systemd/system")
_FORBIDDEN_SYSTEMD_TREES: Final[tuple[Path, ...]] = (
    Path("/run/systemd/system"),
    Path("/etc/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/lib/systemd/system"),
)

_FIXTURE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
_MAX_RUNTIME_SECONDS: Final[int] = 300
_DEFAULT_RUNTIME_SECONDS: Final[int] = 180
_UNIT_FILE_MODE: Final[int] = 0o600


class FaultLabFixtureError(ValueError):
    """Raised when a fault-lab fixture artifact violates its contract."""


def validate_lab_fixture_id(value: str) -> str:
    """Validate and return one conservative fixture identifier."""

    if not isinstance(value, str):
        raise FaultLabFixtureError("fixture_id must be a string")
    if _FIXTURE_ID_PATTERN.fullmatch(value) is None:
        raise FaultLabFixtureError("fixture_id must match [a-z][a-z0-9-]{2,31}")
    return value


def validate_lab_fixture_unit_name(value: str) -> str:
    """Validate that a service name belongs to the Sentinel-X lab namespace."""

    try:
        normalized = validate_service_unit_name(value, field_name="unit_name")
    except ValueError as exc:
        raise FaultLabFixtureError(str(exc)) from exc

    if not normalized.startswith(SYSTEMD_LAB_UNIT_PREFIX):
        raise FaultLabFixtureError(
            f"unit_name must start with {SYSTEMD_LAB_UNIT_PREFIX!r}"
        )
    if not normalized.endswith(".service"):
        raise FaultLabFixtureError("unit_name must end with '.service'")

    fixture_id = normalized[len(SYSTEMD_LAB_UNIT_PREFIX) : -len(".service")]
    validate_lab_fixture_id(fixture_id)
    return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_runtime_seconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FaultLabFixtureError("runtime_max_seconds must be an integer")
    if not 1 <= value <= _MAX_RUNTIME_SECONDS:
        raise FaultLabFixtureError(
            f"runtime_max_seconds must be between 1 and {_MAX_RUNTIME_SECONDS}"
        )
    return value


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise FaultLabFixtureError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FaultLabFixtureError(f"{field_name} must be timezone-aware")


def _render_unit_text(*, fixture_id: str, runtime_max_seconds: int) -> str:
    description = f"Sentinel-X isolated fault laboratory fixture {fixture_id}"
    ready_marker = f"SENTINEL_X_LAB_FIXTURE_READY fixture_id={fixture_id}"
    stopped_marker = f"SENTINEL_X_LAB_FIXTURE_STOPPED fixture_id={fixture_id}"

    return "\n".join(
        (
            "[Unit]",
            f"Description={description}",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStartPre=/usr/bin/echo {ready_marker}",
            "ExecStart=/usr/bin/sleep infinity",
            f"ExecStopPost=/usr/bin/echo {stopped_marker}",
            "Restart=no",
            "KillMode=control-group",
            "TimeoutStopSec=5s",
            f"RuntimeMaxSec={runtime_max_seconds}s",
            "DynamicUser=yes",
            "NoNewPrivileges=yes",
            "PrivateTmp=yes",
            "PrivateDevices=yes",
            "PrivateNetwork=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "ProtectKernelTunables=yes",
            "ProtectKernelModules=yes",
            "ProtectControlGroups=yes",
            "RestrictSUIDSGID=yes",
            "RestrictNamespaces=yes",
            "LockPersonality=yes",
            "MemoryDenyWriteExecute=yes",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "UMask=0077",
            "StandardOutput=journal",
            "StandardError=journal",
            "",
        )
    )


@dataclass(frozen=True, slots=True)
class SystemdLabFixtureSpec:
    """Immutable specification for one dedicated systemd lab fixture."""

    fixture_id: str
    runtime_max_seconds: int = _DEFAULT_RUNTIME_SECONDS

    def __post_init__(self) -> None:
        validate_lab_fixture_id(self.fixture_id)
        _validate_runtime_seconds(self.runtime_max_seconds)

    @property
    def unit_name(self) -> str:
        """Return the fixed-namespace service name for this fixture."""

        unit_name = f"{SYSTEMD_LAB_UNIT_PREFIX}{self.fixture_id}.service"
        return validate_lab_fixture_unit_name(unit_name)

    @property
    def runtime_install_path(self) -> Path:
        """Return the volatile systemd unit-file location used by the lab."""

        return SYSTEMD_LAB_RUNTIME_ROOT / self.unit_name

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly fixture specification."""

        return {
            "fixture_id": self.fixture_id,
            "unit_name": self.unit_name,
            "runtime_max_seconds": self.runtime_max_seconds,
            "runtime_install_path": os.fspath(self.runtime_install_path),
        }


@dataclass(frozen=True, slots=True)
class SystemdLabFixtureArtifact:
    """Immutable rendered unit artifact with integrity metadata."""

    spec: SystemdLabFixtureSpec
    unit_text: str
    sha256: str
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.spec, SystemdLabFixtureSpec):
            raise FaultLabFixtureError("spec must be a SystemdLabFixtureSpec")
        if not isinstance(self.unit_text, str) or not self.unit_text:
            raise FaultLabFixtureError("unit_text must be a non-empty string")
        if "\x00" in self.unit_text:
            raise FaultLabFixtureError("unit_text must not contain NUL bytes")
        canonical_text = _render_unit_text(
            fixture_id=self.spec.fixture_id,
            runtime_max_seconds=self.spec.runtime_max_seconds,
        )
        if self.unit_text != canonical_text:
            raise FaultLabFixtureError(
                "unit_text is not the canonical fixture rendering"
            )
        expected = hashlib.sha256(self.unit_text.encode("utf-8")).hexdigest()
        if self.sha256 != expected:
            raise FaultLabFixtureError("sha256 does not match unit_text")
        _validate_aware_datetime(self.created_at, field_name="created_at")

    @property
    def unit_name(self) -> str:
        """Return the rendered fixture unit name."""

        return self.spec.unit_name

    @property
    def runtime_install_path(self) -> Path:
        """Return the volatile systemd destination for operator installation."""

        return self.spec.runtime_install_path

    def write_private_copy(self, directory: str | Path) -> Path:
        """Write one exclusive 0600 staging copy outside the systemd runtime tree."""

        directory_path = Path(directory)
        try:
            resolved_directory = directory_path.resolve(strict=True)
        except OSError as exc:
            raise FaultLabFixtureError(
                f"staging directory cannot be resolved: {exc}"
            ) from exc
        if not resolved_directory.is_dir():
            raise FaultLabFixtureError("staging directory must be a directory")
        for systemd_tree in _FORBIDDEN_SYSTEMD_TREES:
            try:
                resolved_systemd_tree = systemd_tree.resolve(strict=True)
            except OSError:
                resolved_systemd_tree = systemd_tree
            if resolved_directory == resolved_systemd_tree or (
                resolved_directory.is_relative_to(resolved_systemd_tree)
            ):
                raise FaultLabFixtureError(
                    "write_private_copy must not write inside a systemd unit tree"
                )

        destination = resolved_directory / self.unit_name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(destination, flags, _UNIT_FILE_MODE)
        except OSError as exc:
            raise FaultLabFixtureError(
                f"cannot create private fixture artifact: {exc}"
            ) from exc

        try:
            payload = self.unit_text.encode("utf-8")
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise FaultLabFixtureError(
                f"cannot write private fixture artifact: {exc}"
            ) from exc

        return destination

    def to_dict(self) -> dict[str, object]:
        """Return stable artifact metadata without duplicating the full unit text."""

        return {
            "spec": self.spec.to_dict(),
            "unit_name": self.unit_name,
            "runtime_install_path": os.fspath(self.runtime_install_path),
            "sha256": self.sha256,
            "created_at": self.created_at.isoformat(),
            "unit_text_bytes": len(self.unit_text.encode("utf-8")),
        }


def build_systemd_lab_fixture(
    spec: SystemdLabFixtureSpec,
) -> SystemdLabFixtureArtifact:
    """Render one deterministic hardened fixture artifact without executing it."""

    if not isinstance(spec, SystemdLabFixtureSpec):
        raise FaultLabFixtureError("spec must be a SystemdLabFixtureSpec")
    unit_text = _render_unit_text(
        fixture_id=spec.fixture_id,
        runtime_max_seconds=spec.runtime_max_seconds,
    )
    digest = hashlib.sha256(unit_text.encode("utf-8")).hexdigest()
    return SystemdLabFixtureArtifact(
        spec=spec,
        unit_text=unit_text,
        sha256=digest,
    )
