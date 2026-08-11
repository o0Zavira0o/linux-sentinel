"""Linux filesystem discovery and raw capacity observability for Sentinel-X."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final


_MOUNTINFO_MAX_BYTES: Final[int] = 8_388_608

_KERNEL_API_FILESYSTEMS: Final[frozenset[str]] = frozenset(
    {
        "apparmorfs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "mqueue",
        "nfsd",
        "nsfs",
        "proc",
        "pstore",
        "rpc_pipefs",
        "securityfs",
        "selinuxfs",
        "smackfs",
        "sysfs",
        "tracefs",
    }
)

_MEMORY_FILESYSTEMS: Final[frozenset[str]] = frozenset(
    {
        "ramfs",
        "tmpfs",
    }
)

_REMOTE_FILESYSTEMS: Final[frozenset[str]] = frozenset(
    {
        "9p",
        "afs",
        "ceph",
        "cifs",
        "glusterfs",
        "nfs",
        "nfs4",
        "smb3",
    }
)

_REMOTE_PREFIXES: Final[tuple[str, ...]] = ("fuse.sshfs",)
_USERSPACE_PREFIXES: Final[tuple[str, ...]] = ("fuse.",)
_USERSPACE_FILESYSTEMS: Final[frozenset[str]] = frozenset({"fuseblk"})

_MOUNTINFO_ESCAPES: Final[tuple[tuple[str, str], ...]] = (
    ("\\040", " "),
    ("\\011", "\t"),
    ("\\012", "\n"),
    ("\\134", "\\"),
)


class FilesystemObservationError(RuntimeError):
    """Base error for filesystem observability failures."""


class FilesystemMountReadError(FilesystemObservationError):
    """Raised when mount metadata cannot be read safely."""


class FilesystemMountParseError(FilesystemObservationError):
    """Raised when mount metadata is malformed."""


class FilesystemKind(str, Enum):
    """Operational classification used by the default probe policy."""

    LOCAL = "local"
    MEMORY = "memory"
    REMOTE = "remote"
    USERSPACE = "userspace"
    KERNEL_API = "kernel_api"
    AUTOMOUNT = "automount"


@dataclass(frozen=True, slots=True)
class FilesystemMount:
    """One mount visible in the current process mount namespace."""

    mount_id: int
    parent_id: int
    device_major: int
    device_minor: int
    root: str
    mount_point: str
    mount_options: tuple[str, ...]
    optional_fields: tuple[str, ...]
    fs_type: str
    source: str
    super_options: tuple[str, ...]
    kind: FilesystemKind

    def __post_init__(self) -> None:
        """Validate essential mount metadata."""

        _require_positive_int("mount_id", self.mount_id)
        _require_positive_int("parent_id", self.parent_id)
        _require_nonnegative_int("device_major", self.device_major)
        _require_nonnegative_int("device_minor", self.device_minor)
        _require_absolute_path("root", self.root)
        _require_absolute_path("mount_point", self.mount_point)
        _require_text("fs_type", self.fs_type)
        _require_text("source", self.source)

        if not isinstance(self.kind, FilesystemKind):
            raise TypeError("kind must be a FilesystemKind")

    @property
    def device_id(self) -> str:
        """Return the Linux major:minor device identifier."""

        return f"{self.device_major}:{self.device_minor}"

    @property
    def read_only(self) -> bool:
        """Return whether the mount has the per-mount ro option."""

        return "ro" in self.mount_options

    @property
    def probe_by_default(self) -> bool:
        """Return whether the conservative policy probes this mount."""

        return self.kind in {
            FilesystemKind.LOCAL,
            FilesystemKind.MEMORY,
        }


@dataclass(frozen=True, slots=True)
class FilesystemStats:
    """Raw statvfs capacity and inode counters for one mount."""

    mount: FilesystemMount
    fragment_size_bytes: int
    total_blocks: int
    free_blocks: int
    available_blocks: int
    total_inodes: int
    free_inodes: int
    available_inodes: int
    name_max: int

    def __post_init__(self) -> None:
        """Validate raw statvfs counters."""

        if not isinstance(self.mount, FilesystemMount):
            raise TypeError("mount must be a FilesystemMount")

        _require_positive_int(
            "fragment_size_bytes",
            self.fragment_size_bytes,
        )

        counters = (
            ("total_blocks", self.total_blocks),
            ("free_blocks", self.free_blocks),
            ("available_blocks", self.available_blocks),
            ("total_inodes", self.total_inodes),
            ("free_inodes", self.free_inodes),
            ("available_inodes", self.available_inodes),
            ("name_max", self.name_max),
        )

        for name, value in counters:
            _require_nonnegative_int(name, value)

    @property
    def total_bytes(self) -> int:
        """Return filesystem capacity in bytes."""

        return self.total_blocks * self.fragment_size_bytes

    @property
    def free_bytes(self) -> int:
        """Return total free bytes."""

        return self.free_blocks * self.fragment_size_bytes

    @property
    def available_bytes(self) -> int:
        """Return bytes available to an unprivileged process."""

        return self.available_blocks * self.fragment_size_bytes


@dataclass(frozen=True, slots=True)
class FilesystemProbeFailure:
    """One isolated statvfs failure."""

    mount_id: int
    mount_point: str
    fs_type: str
    error_type: str
    error_message: str


@dataclass(frozen=True, slots=True)
class FilesystemReport:
    """Mount discovery plus safe default capacity probes."""

    mounts: tuple[FilesystemMount, ...]
    filesystems: tuple[FilesystemStats, ...]
    failures: tuple[FilesystemProbeFailure, ...]

    @property
    def discovered_count(self) -> int:
        """Return the number of discovered mounts."""

        return len(self.mounts)

    @property
    def probed_count(self) -> int:
        """Return the number of successful probes."""

        return len(self.filesystems)

    @property
    def failed_count(self) -> int:
        """Return the number of isolated probe failures."""

        return len(self.failures)

    @property
    def skipped_mounts(self) -> tuple[FilesystemMount, ...]:
        """Return mounts excluded by the conservative policy."""

        return tuple(mount for mount in self.mounts if not mount.probe_by_default)

    @property
    def skipped_count(self) -> int:
        """Return the number of intentionally skipped mounts."""

        return len(self.skipped_mounts)


class LinuxFilesystemReader:
    """Discover mounts and collect safe raw capacity evidence."""

    def __init__(
        self,
        *,
        mountinfo_path: str | Path = "/proc/self/mountinfo",
    ) -> None:
        self._mountinfo_path = Path(mountinfo_path)

    def discover_mounts(self) -> tuple[FilesystemMount, ...]:
        """Read mounts visible in the current process mount namespace."""

        return _parse_mountinfo(
            _read_bounded_text(
                self._mountinfo_path,
                max_bytes=_MOUNTINFO_MAX_BYTES,
            )
        )

    def read_report(self) -> FilesystemReport:
        """Probe default mounts while isolating per-mount failures."""

        mounts = self.discover_mounts()
        filesystems: list[FilesystemStats] = []
        failures: list[FilesystemProbeFailure] = []

        for mount in mounts:
            if not mount.probe_by_default:
                continue

            try:
                raw_stats = os.statvfs(mount.mount_point)
                filesystems.append(_build_filesystem_stats(mount, raw_stats))

            except (OSError, TypeError, ValueError) as exc:
                failures.append(
                    FilesystemProbeFailure(
                        mount_id=mount.mount_id,
                        mount_point=mount.mount_point,
                        fs_type=mount.fs_type,
                        error_type=type(exc).__name__,
                        error_message=str(exc).strip() or repr(exc),
                    )
                )

        return FilesystemReport(
            mounts=mounts,
            filesystems=tuple(filesystems),
            failures=tuple(failures),
        )


def _read_bounded_text(path: Path, *, max_bytes: int) -> str:
    """Read a small Linux metadata file with a strict size limit."""

    try:
        with path.open("rb") as file_handle:
            data = file_handle.read(max_bytes + 1)

    except (OSError, ValueError) as exc:
        raise FilesystemMountReadError(f"could not read {path}: {exc}") from exc

    if len(data) > max_bytes:
        raise FilesystemMountReadError(
            f"mount metadata exceeds {max_bytes} bytes: {path}"
        )

    try:
        return data.decode("utf-8")

    except UnicodeDecodeError as exc:
        raise FilesystemMountReadError(
            f"mount metadata is not valid UTF-8: {path}"
        ) from exc


def _parse_mountinfo(text: str) -> tuple[FilesystemMount, ...]:
    """Parse Linux /proc/self/mountinfo records."""

    mounts: list[FilesystemMount] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line:
            continue

        fields = raw_line.split()

        try:
            separator_index = fields.index("-")

        except ValueError as exc:
            raise FilesystemMountParseError(
                f"mountinfo line {line_number} is missing the '-' separator"
            ) from exc

        if separator_index < 6 or len(fields) < separator_index + 4:
            raise FilesystemMountParseError(
                f"mountinfo line {line_number} has an invalid field layout"
            )

        mount_id = _parse_positive_int(
            fields[0],
            field_name="mount ID",
            line_number=line_number,
        )

        parent_id = _parse_positive_int(
            fields[1],
            field_name="parent ID",
            line_number=line_number,
        )

        device_major, device_minor = _parse_device_id(
            fields[2],
            line_number=line_number,
        )

        root = _decode_mountinfo_field(fields[3])
        mount_point = _decode_mountinfo_field(fields[4])
        mount_options = _split_options(fields[5])
        optional_fields = tuple(fields[6:separator_index])

        fs_type = fields[separator_index + 1]
        source = _decode_mountinfo_field(fields[separator_index + 2])
        super_options = _split_options(fields[separator_index + 3])

        try:
            mounts.append(
                FilesystemMount(
                    mount_id=mount_id,
                    parent_id=parent_id,
                    device_major=device_major,
                    device_minor=device_minor,
                    root=root,
                    mount_point=mount_point,
                    mount_options=mount_options,
                    optional_fields=optional_fields,
                    fs_type=fs_type,
                    source=source,
                    super_options=super_options,
                    kind=_classify_filesystem(fs_type),
                )
            )

        except (TypeError, ValueError) as exc:
            raise FilesystemMountParseError(
                f"invalid mountinfo line {line_number}: {exc}"
            ) from exc

    if not mounts:
        raise FilesystemMountParseError("mountinfo does not contain any mount records")

    return tuple(mounts)


def _parse_device_id(
    raw_value: str,
    *,
    line_number: int,
) -> tuple[int, int]:
    """Parse the Linux major:minor mount device identifier."""

    components = raw_value.split(":", maxsplit=1)

    if len(components) != 2:
        raise FilesystemMountParseError(
            f"invalid device identifier on mountinfo line {line_number}: {raw_value!r}"
        )

    return (
        _parse_nonnegative_int(
            components[0],
            field_name="device major",
            line_number=line_number,
        ),
        _parse_nonnegative_int(
            components[1],
            field_name="device minor",
            line_number=line_number,
        ),
    )


def _parse_positive_int(
    raw_value: str,
    *,
    field_name: str,
    line_number: int,
) -> int:
    """Parse one strictly positive mountinfo integer."""

    value = _parse_nonnegative_int(
        raw_value,
        field_name=field_name,
        line_number=line_number,
    )

    if value == 0:
        raise FilesystemMountParseError(
            f"{field_name} must be positive on mountinfo line {line_number}"
        )

    return value


def _parse_nonnegative_int(
    raw_value: str,
    *,
    field_name: str,
    line_number: int,
) -> int:
    """Parse one non-negative mountinfo integer."""

    try:
        value = int(raw_value)

    except ValueError as exc:
        raise FilesystemMountParseError(
            f"invalid {field_name} on mountinfo line {line_number}: {raw_value!r}"
        ) from exc

    if value < 0:
        raise FilesystemMountParseError(
            f"negative {field_name} on mountinfo line {line_number}: {raw_value!r}"
        )

    return value


def _decode_mountinfo_field(value: str) -> str:
    """Decode whitespace and backslash escapes used by mountinfo."""

    decoded = value

    for encoded, replacement in _MOUNTINFO_ESCAPES:
        decoded = decoded.replace(encoded, replacement)

    return decoded


def _split_options(value: str) -> tuple[str, ...]:
    """Split a comma-delimited mount option field."""

    return tuple(option for option in value.split(",") if option)


def _classify_filesystem(fs_type: str) -> FilesystemKind:
    """Classify one filesystem type for the default probe policy."""

    if fs_type == "autofs":
        return FilesystemKind.AUTOMOUNT

    if fs_type in _KERNEL_API_FILESYSTEMS:
        return FilesystemKind.KERNEL_API

    if fs_type in _MEMORY_FILESYSTEMS:
        return FilesystemKind.MEMORY

    if fs_type in _REMOTE_FILESYSTEMS:
        return FilesystemKind.REMOTE

    if any(fs_type.startswith(prefix) for prefix in _REMOTE_PREFIXES):
        return FilesystemKind.REMOTE

    if fs_type in _USERSPACE_FILESYSTEMS:
        return FilesystemKind.USERSPACE

    if any(fs_type.startswith(prefix) for prefix in _USERSPACE_PREFIXES):
        return FilesystemKind.USERSPACE

    return FilesystemKind.LOCAL


def _build_filesystem_stats(
    mount: FilesystemMount,
    raw_stats: os.statvfs_result,
) -> FilesystemStats:
    """Convert statvfs output into typed raw filesystem evidence."""

    fragment_size = raw_stats.f_frsize or raw_stats.f_bsize

    return FilesystemStats(
        mount=mount,
        fragment_size_bytes=fragment_size,
        total_blocks=raw_stats.f_blocks,
        free_blocks=raw_stats.f_bfree,
        available_blocks=raw_stats.f_bavail,
        total_inodes=raw_stats.f_files,
        free_inodes=raw_stats.f_ffree,
        available_inodes=raw_stats.f_favail,
        name_max=raw_stats.f_namemax,
    )


def _require_text(name: str, value: str) -> None:
    """Require a non-empty string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_absolute_path(name: str, value: str) -> None:
    """Require a non-empty absolute Linux path."""

    _require_text(name, value)

    if not value.startswith("/"):
        raise ValueError(f"{name} must be an absolute path")


def _require_positive_int(name: str, value: int) -> None:
    """Require a strictly positive integer."""

    _require_nonnegative_int(name, value)

    if value == 0:
        raise ValueError(f"{name} must be greater than zero")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} must not be negative")
