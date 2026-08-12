"""Linux filesystem discovery and observability for Sentinel-X."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime
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

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly mount metadata."""

        return {
            "mount_id": self.mount_id,
            "parent_id": self.parent_id,
            "device_id": self.device_id,
            "device_major": self.device_major,
            "device_minor": self.device_minor,
            "root": self.root,
            "mount_point": self.mount_point,
            "mount_options": list(self.mount_options),
            "optional_fields": list(self.optional_fields),
            "fs_type": self.fs_type,
            "source": self.source,
            "super_options": list(self.super_options),
            "kind": self.kind.value,
            "read_only": self.read_only,
            "probe_by_default": self.probe_by_default,
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

        if self.free_blocks > self.total_blocks:
            raise ValueError("free_blocks must not exceed total_blocks")

        if self.available_blocks > self.free_blocks:
            raise ValueError("available_blocks must not exceed free_blocks")

        if self.free_inodes > self.total_inodes:
            raise ValueError("free_inodes must not exceed total_inodes")

        if self.available_inodes > self.free_inodes:
            raise ValueError("available_inodes must not exceed free_inodes")

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

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly raw filesystem evidence."""

        return {
            "mount": self.mount.to_dict(),
            "fragment_size_bytes": self.fragment_size_bytes,
            "total_blocks": self.total_blocks,
            "free_blocks": self.free_blocks,
            "available_blocks": self.available_blocks,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "available_bytes": self.available_bytes,
            "total_inodes": self.total_inodes,
            "free_inodes": self.free_inodes,
            "available_inodes": self.available_inodes,
            "name_max": self.name_max,
        }


@dataclass(frozen=True, slots=True)
class FilesystemProbeFailure:
    """One isolated statvfs failure."""

    mount_id: int
    mount_point: str
    fs_type: str
    error_type: str
    error_message: str

    def __post_init__(self) -> None:
        """Validate failure metadata."""

        _require_positive_int("mount_id", self.mount_id)
        _require_absolute_path("mount_point", self.mount_point)
        _require_text("fs_type", self.fs_type)
        _require_text("error_type", self.error_type)
        _require_text("error_message", self.error_message)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly failure evidence."""

        return {
            "mount_id": self.mount_id,
            "mount_point": self.mount_point,
            "fs_type": self.fs_type,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


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


@dataclass(frozen=True, slots=True)
class FilesystemUtilization:
    """Derived space and inode utilization for one filesystem."""

    used_bytes: int
    restricted_free_bytes: int
    used_percent_of_total: float | None
    available_percent_of_total: float | None
    user_capacity_used_percent: float | None
    inode_used: int | None
    restricted_free_inodes: int | None
    inode_used_percent_of_total: float | None
    inode_available_percent_of_total: float | None
    inode_user_capacity_used_percent: float | None

    def __post_init__(self) -> None:
        """Validate derived filesystem utilization values."""

        _require_nonnegative_int("used_bytes", self.used_bytes)
        _require_nonnegative_int(
            "restricted_free_bytes",
            self.restricted_free_bytes,
        )

        space_percentages = (
            self.used_percent_of_total,
            self.available_percent_of_total,
            self.user_capacity_used_percent,
        )

        if any(value is None for value in space_percentages):
            if not all(value is None for value in space_percentages):
                raise ValueError(
                    "space percentages must either all be present or all be None"
                )

        else:
            for percentage_name, percentage_value in (
                ("used_percent_of_total", self.used_percent_of_total),
                (
                    "available_percent_of_total",
                    self.available_percent_of_total,
                ),
                (
                    "user_capacity_used_percent",
                    self.user_capacity_used_percent,
                ),
            ):
                assert percentage_value is not None
                _require_percentage(percentage_name, percentage_value)

            assert self.used_percent_of_total is not None
            assert self.available_percent_of_total is not None

            if (
                self.used_percent_of_total + self.available_percent_of_total
                > 100.0 + 1e-6
            ):
                raise ValueError(
                    "used and available percentages must not exceed 100 percent"
                )

        inode_values = (
            self.inode_used,
            self.restricted_free_inodes,
            self.inode_used_percent_of_total,
            self.inode_available_percent_of_total,
            self.inode_user_capacity_used_percent,
        )

        if any(value is None for value in inode_values):
            if not all(value is None for value in inode_values):
                raise ValueError(
                    "inode utilization fields must either all be present or all be None"
                )

        else:
            assert self.inode_used is not None
            assert self.restricted_free_inodes is not None
            assert self.inode_used_percent_of_total is not None
            assert self.inode_available_percent_of_total is not None
            assert self.inode_user_capacity_used_percent is not None

            _require_nonnegative_int("inode_used", self.inode_used)
            _require_nonnegative_int(
                "restricted_free_inodes",
                self.restricted_free_inodes,
            )
            _require_percentage(
                "inode_used_percent_of_total",
                self.inode_used_percent_of_total,
            )
            _require_percentage(
                "inode_available_percent_of_total",
                self.inode_available_percent_of_total,
            )
            _require_percentage(
                "inode_user_capacity_used_percent",
                self.inode_user_capacity_used_percent,
            )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly utilization metrics."""

        return {
            "used_bytes": self.used_bytes,
            "restricted_free_bytes": self.restricted_free_bytes,
            "used_percent_of_total": self.used_percent_of_total,
            "available_percent_of_total": self.available_percent_of_total,
            "user_capacity_used_percent": self.user_capacity_used_percent,
            "inode_used": self.inode_used,
            "restricted_free_inodes": self.restricted_free_inodes,
            "inode_used_percent_of_total": self.inode_used_percent_of_total,
            "inode_available_percent_of_total": (self.inode_available_percent_of_total),
            "inode_user_capacity_used_percent": (self.inode_user_capacity_used_percent),
        }


@dataclass(frozen=True, slots=True)
class FilesystemObservationEntry:
    """Raw and derived evidence for one successfully probed filesystem."""

    stats: FilesystemStats
    utilization: FilesystemUtilization

    def __post_init__(self) -> None:
        """Validate entry component types."""

        if not isinstance(self.stats, FilesystemStats):
            raise TypeError("stats must be FilesystemStats")

        if not isinstance(self.utilization, FilesystemUtilization):
            raise TypeError("utilization must be FilesystemUtilization")

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly filesystem evidence."""

        return {
            "raw_stats": self.stats.to_dict(),
            "utilization": self.utilization.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FilesystemObservation:
    """One timestamped filesystem observation across the mount namespace."""

    captured_at: datetime
    filesystems: tuple[FilesystemObservationEntry, ...]
    skipped_mounts: tuple[FilesystemMount, ...]
    failures: tuple[FilesystemProbeFailure, ...]

    def __post_init__(self) -> None:
        """Validate observation structure and mount uniqueness."""

        _require_aware_datetime("captured_at", self.captured_at)

        for entry in self.filesystems:
            if not isinstance(entry, FilesystemObservationEntry):
                raise TypeError(
                    "filesystems must contain FilesystemObservationEntry objects"
                )

        for mount in self.skipped_mounts:
            if not isinstance(mount, FilesystemMount):
                raise TypeError("skipped_mounts must contain FilesystemMount objects")

        for failure in self.failures:
            if not isinstance(failure, FilesystemProbeFailure):
                raise TypeError("failures must contain FilesystemProbeFailure objects")

        mount_ids = [
            *(entry.stats.mount.mount_id for entry in self.filesystems),
            *(mount.mount_id for mount in self.skipped_mounts),
            *(failure.mount_id for failure in self.failures),
        ]

        if len(set(mount_ids)) != len(mount_ids):
            raise ValueError("filesystem observation contains duplicate mount IDs")

    @property
    def discovered_count(self) -> int:
        """Return total represented mount count."""

        return self.probed_count + self.skipped_count + self.failed_count

    @property
    def probed_count(self) -> int:
        """Return successful filesystem probe count."""

        return len(self.filesystems)

    @property
    def skipped_count(self) -> int:
        """Return intentionally skipped mount count."""

        return len(self.skipped_mounts)

    @property
    def failed_count(self) -> int:
        """Return isolated filesystem probe failure count."""

        return len(self.failures)

    def to_attributes(self) -> dict[str, object]:
        """Return structured attributes for a SentinelEvent."""

        return {
            "captured_at": self.captured_at.isoformat(),
            "summary": {
                "discovered_count": self.discovered_count,
                "probed_count": self.probed_count,
                "skipped_count": self.skipped_count,
                "failed_count": self.failed_count,
            },
            "filesystems": [entry.to_dict() for entry in self.filesystems],
            "skipped_mounts": [mount.to_dict() for mount in self.skipped_mounts],
            "failures": [failure.to_dict() for failure in self.failures],
        }


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
                filesystems.append(
                    _build_filesystem_stats(
                        mount,
                        raw_stats,
                    )
                )

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


def calculate_filesystem_utilization(
    stats: FilesystemStats,
) -> FilesystemUtilization:
    """Derive capacity and inode utilization from raw statvfs evidence."""

    if not isinstance(stats, FilesystemStats):
        raise TypeError("calculate_filesystem_utilization() requires FilesystemStats")

    used_blocks = stats.total_blocks - stats.free_blocks
    restricted_free_blocks = stats.free_blocks - stats.available_blocks

    used_bytes = used_blocks * stats.fragment_size_bytes
    restricted_free_bytes = restricted_free_blocks * stats.fragment_size_bytes

    if stats.total_blocks == 0:
        used_percent_of_total: float | None = None
        available_percent_of_total: float | None = None
        user_capacity_used_percent: float | None = None

    else:
        used_percent_of_total = _percent(
            used_blocks,
            stats.total_blocks,
        )
        available_percent_of_total = _percent(
            stats.available_blocks,
            stats.total_blocks,
        )

        user_capacity_blocks = used_blocks + stats.available_blocks

        if user_capacity_blocks == 0:
            user_capacity_used_percent = 0.0

        else:
            user_capacity_used_percent = _percent(
                used_blocks,
                user_capacity_blocks,
            )

    if stats.total_inodes == 0:
        inode_used: int | None = None
        restricted_free_inodes: int | None = None
        inode_used_percent_of_total: float | None = None
        inode_available_percent_of_total: float | None = None
        inode_user_capacity_used_percent: float | None = None

    else:
        inode_used = stats.total_inodes - stats.free_inodes
        restricted_free_inodes = stats.free_inodes - stats.available_inodes

        inode_used_percent_of_total = _percent(
            inode_used,
            stats.total_inodes,
        )
        inode_available_percent_of_total = _percent(
            stats.available_inodes,
            stats.total_inodes,
        )

        inode_user_capacity = inode_used + stats.available_inodes

        if inode_user_capacity == 0:
            inode_user_capacity_used_percent = 0.0

        else:
            inode_user_capacity_used_percent = _percent(
                inode_used,
                inode_user_capacity,
            )

    return FilesystemUtilization(
        used_bytes=used_bytes,
        restricted_free_bytes=restricted_free_bytes,
        used_percent_of_total=used_percent_of_total,
        available_percent_of_total=available_percent_of_total,
        user_capacity_used_percent=user_capacity_used_percent,
        inode_used=inode_used,
        restricted_free_inodes=restricted_free_inodes,
        inode_used_percent_of_total=inode_used_percent_of_total,
        inode_available_percent_of_total=inode_available_percent_of_total,
        inode_user_capacity_used_percent=inode_user_capacity_used_percent,
    )


def build_filesystem_observation(
    report: FilesystemReport,
    *,
    captured_at: datetime,
) -> FilesystemObservation:
    """Build one filesystem observation from discovery and probe evidence."""

    if not isinstance(report, FilesystemReport):
        raise TypeError("build_filesystem_observation() requires FilesystemReport")

    entries = tuple(
        FilesystemObservationEntry(
            stats=stats,
            utilization=calculate_filesystem_utilization(stats),
        )
        for stats in report.filesystems
    )

    observation = FilesystemObservation(
        captured_at=captured_at,
        filesystems=entries,
        skipped_mounts=report.skipped_mounts,
        failures=report.failures,
    )

    if observation.discovered_count != report.discovered_count:
        raise FilesystemObservationError(
            "filesystem report accounting does not match discovered mount count"
        )

    return observation


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


def _percent(value: int, total: int) -> float:
    """Convert a non-negative quantity to a percentage."""

    return (float(value) / float(total)) * 100.0


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


def _require_percentage(name: str, value: float) -> None:
    """Require a finite percentage in the inclusive 0-100 range."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")

    if not 0.0 <= value <= 100.0:
        raise ValueError(f"{name} must be between 0 and 100")


def _require_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware datetime."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
