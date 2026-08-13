"""Atomic restart-safe journald cursor checkpoints for Sentinel-X."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol

from sentinel_x.systemd.journal_models import (
    SystemdJournalModelError,
    validate_journal_cursor,
)
from sentinel_x.systemd.models import SystemdUnitNameError, validate_service_unit_name

SYSTEMD_JOURNAL_CHECKPOINT_SCHEMA_VERSION: Final[int] = 1
_MAX_CHECKPOINT_BYTES: Final[int] = 32_768
_COLLECTOR_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)
_INSTANCE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)
_BOOT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{32}$")
_CHECKSUM_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


class SystemdJournalCheckpointError(RuntimeError):
    """Base error for durable journald checkpoint operations."""


class SystemdJournalCheckpointValidationError(SystemdJournalCheckpointError):
    """Raised when checkpoint content violates the typed schema."""


class SystemdJournalCheckpointStorageError(SystemdJournalCheckpointError):
    """Raised when a checkpoint cannot be safely loaded or persisted."""


@dataclass(frozen=True, slots=True)
class SystemdJournalCheckpoint:
    """One durable cursor position for one configured journal collector."""

    collector_name: str
    unit_name: str
    boot_id: str
    cursor: str | None
    committed_at: datetime
    schema_version: int = SYSTEMD_JOURNAL_CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Normalize and validate all checkpoint identity and state fields."""

        collector_name = _validate_collector_name(self.collector_name)
        try:
            unit_name = validate_service_unit_name(
                self.unit_name,
                field_name="unit_name",
            )
        except SystemdUnitNameError as exc:
            raise SystemdJournalCheckpointValidationError(str(exc)) from exc
        boot_id = _normalize_boot_id(self.boot_id)
        cursor = self.cursor
        if cursor is not None:
            try:
                cursor = validate_journal_cursor(cursor, field_name="cursor")
            except SystemdJournalModelError as exc:
                raise SystemdJournalCheckpointValidationError(str(exc)) from exc
        if not isinstance(self.committed_at, datetime):
            raise SystemdJournalCheckpointValidationError(
                "committed_at must be a datetime"
            )
        if self.committed_at.tzinfo is None or self.committed_at.utcoffset() is None:
            raise SystemdJournalCheckpointValidationError(
                "committed_at must be timezone-aware"
            )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != SYSTEMD_JOURNAL_CHECKPOINT_SCHEMA_VERSION
        ):
            raise SystemdJournalCheckpointValidationError(
                "unsupported journald checkpoint schema version"
            )

        object.__setattr__(self, "collector_name", collector_name)
        object.__setattr__(self, "unit_name", unit_name)
        object.__setattr__(self, "boot_id", boot_id)
        object.__setattr__(self, "cursor", cursor)

    def to_dict(self) -> dict[str, object]:
        """Return a serialization-friendly checkpoint representation."""

        return {
            "schema_version": self.schema_version,
            "collector_name": self.collector_name,
            "unit_name": self.unit_name,
            "boot_id": self.boot_id,
            "cursor": self.cursor,
            "committed_at": self.committed_at.isoformat(),
        }


class SystemdJournalCheckpointStore(Protocol):
    """Structural persistence contract consumed by journal collectors."""

    def load(
        self,
        collector_name: str,
        unit_name: str,
    ) -> SystemdJournalCheckpoint | None:
        """Load one checkpoint or return None when none exists."""

        ...

    def save(self, checkpoint: SystemdJournalCheckpoint) -> None:
        """Atomically persist one committed checkpoint."""

        ...


class AtomicSystemdJournalCheckpointStore:
    """Private atomic JSON checkpoint storage scoped to one agent instance."""

    def __init__(
        self,
        *,
        directory: str | Path,
        instance_name: str,
    ) -> None:
        if not isinstance(instance_name, str):
            raise TypeError("instance_name must be a string")
        normalized_instance = instance_name.strip()
        if _INSTANCE_NAME_PATTERN.fullmatch(normalized_instance) is None:
            raise ValueError("instance_name must be 1-64 scheduler-safe characters")
        self._root = Path(directory).expanduser()
        self._instance_name = normalized_instance
        self._instance_directory = self._root / normalized_instance

    @property
    def directory(self) -> Path:
        """Return the instance-scoped checkpoint directory."""

        return self._instance_directory

    def path_for(self, collector_name: str) -> Path:
        """Return the deterministic file path for one collector checkpoint."""

        normalized = _validate_collector_name(collector_name)
        return self._instance_directory / f"{normalized}.json"

    def load(
        self,
        collector_name: str,
        unit_name: str,
    ) -> SystemdJournalCheckpoint | None:
        """Load and integrity-check one checkpoint without following symlinks."""

        normalized_collector = _validate_collector_name(collector_name)
        try:
            normalized_unit = validate_service_unit_name(
                unit_name,
                field_name="unit_name",
            )
        except SystemdUnitNameError as exc:
            raise SystemdJournalCheckpointValidationError(str(exc)) from exc
        if not self._validate_existing_private_directory():
            return None
        path = self.path_for(normalized_collector)
        if path.is_symlink():
            raise SystemdJournalCheckpointStorageError(
                f"journal checkpoint path must not be a symlink: {path}"
            )
        if not path.exists():
            return None

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SystemdJournalCheckpointStorageError(
                f"failed to open journal checkpoint {path}: {exc}"
            ) from exc

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise SystemdJournalCheckpointStorageError(
                    f"journal checkpoint is not a regular file: {path}"
                )
            if metadata.st_uid != os.geteuid():
                raise SystemdJournalCheckpointStorageError(
                    f"journal checkpoint is not owned by the current user: {path}"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise SystemdJournalCheckpointStorageError(
                    f"journal checkpoint permissions are not private: {path}"
                )
            if metadata.st_size > _MAX_CHECKPOINT_BYTES:
                raise SystemdJournalCheckpointStorageError(
                    f"journal checkpoint exceeds {_MAX_CHECKPOINT_BYTES} bytes"
                )
            raw = _read_bounded(descriptor, _MAX_CHECKPOINT_BYTES)
        finally:
            os.close(descriptor)

        checkpoint = _decode_checkpoint(raw)
        if checkpoint.collector_name != normalized_collector:
            raise SystemdJournalCheckpointValidationError(
                "journal checkpoint collector identity does not match its path"
            )
        if checkpoint.unit_name != normalized_unit:
            raise SystemdJournalCheckpointValidationError(
                "journal checkpoint unit identity does not match configuration"
            )
        return checkpoint

    def save(self, checkpoint: SystemdJournalCheckpoint) -> None:
        """Persist one checkpoint with fsync and same-directory atomic replace."""

        if not isinstance(checkpoint, SystemdJournalCheckpoint):
            raise TypeError("checkpoint must be a SystemdJournalCheckpoint")
        self._ensure_private_directory()
        final_path = self.path_for(checkpoint.collector_name)
        payload = _encode_checkpoint(checkpoint)
        temporary_path = self._temporary_path(final_path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary_path, final_path)
            os.chmod(final_path, 0o600)
            self._sync_directory()
        except OSError as exc:
            raise SystemdJournalCheckpointStorageError(
                f"failed to persist journal checkpoint {final_path}: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _validate_existing_private_directory(self) -> bool:
        if self._root.is_symlink():
            raise SystemdJournalCheckpointStorageError(
                f"checkpoint root must not be a symlink: {self._root}"
            )
        directory = self._instance_directory
        if directory.is_symlink():
            raise SystemdJournalCheckpointStorageError(
                f"journald checkpoint directory must not be a symlink: {directory}"
            )
        if not directory.exists():
            return False
        try:
            metadata = directory.stat()
        except OSError as exc:
            raise SystemdJournalCheckpointStorageError(
                f"failed to inspect journald checkpoint directory {directory}: {exc}"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise SystemdJournalCheckpointStorageError(
                f"journald checkpoint path is not a directory: {directory}"
            )
        if metadata.st_uid != os.geteuid():
            raise SystemdJournalCheckpointStorageError(
                f"journald checkpoint directory is not owned by the current user: "
                f"{directory}"
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise SystemdJournalCheckpointStorageError(
                f"journald checkpoint directory permissions are not private: "
                f"{directory}"
            )
        return True

    def _ensure_private_directory(self) -> None:
        if self._root.exists() and self._root.is_symlink():
            raise SystemdJournalCheckpointStorageError(
                f"checkpoint root must not be a symlink: {self._root}"
            )
        try:
            self._instance_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._instance_directory, 0o700)
        except OSError as exc:
            raise SystemdJournalCheckpointStorageError(
                "failed to create private journald checkpoint directory "
                f"{self._instance_directory}: {exc}"
            ) from exc
        if not self._instance_directory.is_dir():
            raise SystemdJournalCheckpointStorageError(
                "journald checkpoint path is not a directory: "
                f"{self._instance_directory}"
            )
        if self._instance_directory.is_symlink():
            raise SystemdJournalCheckpointStorageError(
                "journald checkpoint directory must not be a symlink: "
                f"{self._instance_directory}"
            )

    def _temporary_path(self, final_path: Path) -> Path:
        digest = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
        return final_path.with_name(f".{final_path.name}.{os.getpid()}.{digest}.tmp")

    def _sync_directory(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self._instance_directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def build_journal_checkpoint(
    *,
    collector_name: str,
    unit_name: str,
    boot_id: str,
    cursor: str | None,
) -> SystemdJournalCheckpoint:
    """Build one checkpoint using the current UTC commit timestamp."""

    return SystemdJournalCheckpoint(
        collector_name=collector_name,
        unit_name=unit_name,
        boot_id=boot_id,
        cursor=cursor,
        committed_at=datetime.now(timezone.utc),
    )


def _encode_checkpoint(checkpoint: SystemdJournalCheckpoint) -> bytes:
    body = checkpoint.to_dict()
    checksum = _payload_checksum(body)
    envelope = dict(body)
    envelope["payload_sha256"] = checksum
    serialized = json.dumps(
        envelope,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (serialized + "\n").encode("utf-8")


def _decode_checkpoint(raw: bytes) -> SystemdJournalCheckpoint:
    if not raw:
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint file must not be empty"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint must be valid UTF-8"
        ) from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint must contain valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint JSON root must be an object"
        )
    allowed = {
        "schema_version",
        "collector_name",
        "unit_name",
        "boot_id",
        "cursor",
        "committed_at",
        "payload_sha256",
    }
    unknown = set(value) - allowed
    missing = allowed - set(value)
    if unknown or missing:
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint keys do not match schema"
        )
    checksum = value["payload_sha256"]
    if not isinstance(checksum, str) or _CHECKSUM_PATTERN.fullmatch(checksum) is None:
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint checksum is invalid"
        )
    body = {key: value[key] for key in allowed if key != "payload_sha256"}
    if not hmac.compare_digest(_payload_checksum(body), checksum):
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint checksum does not match payload"
        )
    schema_version = _require_integer(body, "schema_version")
    collector_name = _require_string(body, "collector_name")
    unit_name = _require_string(body, "unit_name")
    boot_id = _require_string(body, "boot_id")
    cursor = _require_optional_string(body, "cursor")
    committed_at_value = _require_string(body, "committed_at")
    try:
        committed_at = datetime.fromisoformat(committed_at_value)
    except ValueError as exc:
        raise SystemdJournalCheckpointValidationError(
            "journal checkpoint committed_at is invalid"
        ) from exc
    return SystemdJournalCheckpoint(
        schema_version=schema_version,
        collector_name=collector_name,
        unit_name=unit_name,
        boot_id=boot_id,
        cursor=cursor,
        committed_at=committed_at,
    )


def _require_string(body: dict[str, object], key: str) -> str:
    value = body[key]
    if not isinstance(value, str):
        raise SystemdJournalCheckpointValidationError(
            f"journal checkpoint {key} must be a string"
        )
    return value


def _require_optional_string(body: dict[str, object], key: str) -> str | None:
    value = body[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise SystemdJournalCheckpointValidationError(
            f"journal checkpoint {key} must be a string or null"
        )
    return value


def _require_integer(body: dict[str, object], key: str) -> int:
    value = body[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SystemdJournalCheckpointValidationError(
            f"journal checkpoint {key} must be an integer"
        )
    return value


def _payload_checksum(body: dict[str, object]) -> str:
    serialized = json.dumps(
        body,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _read_bounded(descriptor: int, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum_bytes + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(4096, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > maximum_bytes:
        raise SystemdJournalCheckpointStorageError(
            f"journal checkpoint exceeds {maximum_bytes} bytes"
        )
    return raw


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("partial journal checkpoint write")
        offset += written


def _validate_collector_name(value: object) -> str:
    if not isinstance(value, str):
        raise SystemdJournalCheckpointValidationError("collector_name must be a string")
    normalized = value.strip()
    if _COLLECTOR_NAME_PATTERN.fullmatch(normalized) is None:
        raise SystemdJournalCheckpointValidationError(
            "collector_name must be 1-64 scheduler-safe characters"
        )
    return normalized


def _normalize_boot_id(value: object) -> str:
    if not isinstance(value, str):
        raise SystemdJournalCheckpointValidationError("boot_id must be a string")
    normalized = value.strip().replace("-", "").lower()
    if _BOOT_ID_PATTERN.fullmatch(normalized) is None:
        raise SystemdJournalCheckpointValidationError(
            "boot_id must contain exactly 32 hexadecimal characters"
        )
    return normalized
