"""Linux block-device I/O observability for Sentinel-X."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final


_DISKSTATS_MAX_BYTES: Final[int] = 4_194_304
DISK_SECTOR_BYTES: Final[int] = 512


class DiskIoObservationError(RuntimeError):
    """Base error for Linux block-device I/O observation failures."""


class DiskStatsReadError(DiskIoObservationError):
    """Raised when /proc/diskstats cannot be read safely."""


class DiskStatsParseError(DiskIoObservationError):
    """Raised when /proc/diskstats contains invalid data."""


class BlockDeviceKind(str, Enum):
    """Block-device topology classification resolved from sysfs."""

    WHOLE_DISK = "whole_disk"
    PARTITION = "partition"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BlockDeviceIdentity:
    """Stable identity fields for one block device within a snapshot."""

    major: int
    minor: int
    name: str
    kind: BlockDeviceKind
    sysfs_path: str | None

    def __post_init__(self) -> None:
        """Validate block-device identity fields."""

        _require_nonnegative_int("major", self.major)
        _require_nonnegative_int("minor", self.minor)
        _require_device_name(self.name)

        if not isinstance(self.kind, BlockDeviceKind):
            raise TypeError("kind must be a BlockDeviceKind")

        if self.sysfs_path is not None:
            _require_absolute_path("sysfs_path", self.sysfs_path)

    @property
    def device_id(self) -> str:
        """Return the Linux major:minor identifier."""

        return f"{self.major}:{self.minor}"

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly block-device identity."""

        return {
            "major": self.major,
            "minor": self.minor,
            "device_id": self.device_id,
            "name": self.name,
            "kind": self.kind.value,
            "sysfs_path": self.sysfs_path,
        }


@dataclass(frozen=True, slots=True)
class DiskStats:
    """Raw cumulative Linux block-layer I/O counters for one device."""

    identity: BlockDeviceIdentity
    reads_completed: int
    reads_merged: int
    sectors_read: int
    read_time_ms: int
    writes_completed: int
    writes_merged: int
    sectors_written: int
    write_time_ms: int
    io_in_progress: int
    io_time_ms: int
    weighted_io_time_ms: int
    discards_completed: int | None = None
    discards_merged: int | None = None
    sectors_discarded: int | None = None
    discard_time_ms: int | None = None
    flushes_completed: int | None = None
    flush_time_ms: int | None = None
    extra_fields: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        """Validate raw block-layer statistics."""

        if not isinstance(self.identity, BlockDeviceIdentity):
            raise TypeError("identity must be a BlockDeviceIdentity")

        base_counters = (
            ("reads_completed", self.reads_completed),
            ("reads_merged", self.reads_merged),
            ("sectors_read", self.sectors_read),
            ("read_time_ms", self.read_time_ms),
            ("writes_completed", self.writes_completed),
            ("writes_merged", self.writes_merged),
            ("sectors_written", self.sectors_written),
            ("write_time_ms", self.write_time_ms),
            ("io_in_progress", self.io_in_progress),
            ("io_time_ms", self.io_time_ms),
            ("weighted_io_time_ms", self.weighted_io_time_ms),
        )

        for name, value in base_counters:
            _require_nonnegative_int(name, value)

        discard_counters = (
            self.discards_completed,
            self.discards_merged,
            self.sectors_discarded,
            self.discard_time_ms,
        )

        if any(value is None for value in discard_counters):
            if not all(value is None for value in discard_counters):
                raise ValueError(
                    "discard counters must either all be present or all be None"
                )

        else:
            for discard_name, discard_value in (
                ("discards_completed", self.discards_completed),
                ("discards_merged", self.discards_merged),
                ("sectors_discarded", self.sectors_discarded),
                ("discard_time_ms", self.discard_time_ms),
            ):
                assert discard_value is not None
                _require_nonnegative_int(discard_name, discard_value)

        flush_counters = (
            self.flushes_completed,
            self.flush_time_ms,
        )

        if any(value is None for value in flush_counters):
            if not all(value is None for value in flush_counters):
                raise ValueError(
                    "flush counters must either both be present or both be None"
                )

        else:
            assert self.flushes_completed is not None
            assert self.flush_time_ms is not None

            _require_nonnegative_int(
                "flushes_completed",
                self.flushes_completed,
            )
            _require_nonnegative_int(
                "flush_time_ms",
                self.flush_time_ms,
            )

        for value in self.extra_fields:
            _require_nonnegative_int("extra diskstats field", value)

    @property
    def read_bytes(self) -> int:
        """Return cumulative bytes read using the kernel 512-byte sector unit."""

        return self.sectors_read * DISK_SECTOR_BYTES

    @property
    def written_bytes(self) -> int:
        """Return cumulative bytes written using the kernel sector unit."""

        return self.sectors_written * DISK_SECTOR_BYTES

    @property
    def discarded_bytes(self) -> int | None:
        """Return cumulative bytes discarded when discard counters exist."""

        if self.sectors_discarded is None:
            return None

        return self.sectors_discarded * DISK_SECTOR_BYTES

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly raw I/O evidence."""

        return {
            "identity": self.identity.to_dict(),
            "sector_unit_bytes": DISK_SECTOR_BYTES,
            "reads_completed": self.reads_completed,
            "reads_merged": self.reads_merged,
            "sectors_read": self.sectors_read,
            "read_bytes": self.read_bytes,
            "read_time_ms": self.read_time_ms,
            "writes_completed": self.writes_completed,
            "writes_merged": self.writes_merged,
            "sectors_written": self.sectors_written,
            "written_bytes": self.written_bytes,
            "write_time_ms": self.write_time_ms,
            "io_in_progress": self.io_in_progress,
            "io_time_ms": self.io_time_ms,
            "weighted_io_time_ms": self.weighted_io_time_ms,
            "discards_completed": self.discards_completed,
            "discards_merged": self.discards_merged,
            "sectors_discarded": self.sectors_discarded,
            "discarded_bytes": self.discarded_bytes,
            "discard_time_ms": self.discard_time_ms,
            "flushes_completed": self.flushes_completed,
            "flush_time_ms": self.flush_time_ms,
            "extra_fields": list(self.extra_fields),
        }


@dataclass(frozen=True, slots=True)
class DiskStatsSnapshot:
    """One timestamped /proc/diskstats snapshot."""

    captured_at: datetime
    devices: tuple[DiskStats, ...]

    def __post_init__(self) -> None:
        """Validate snapshot time and unique block-device identities."""

        _require_aware_datetime("captured_at", self.captured_at)

        if not self.devices:
            raise ValueError("devices must not be empty")

        device_ids: list[str] = []

        for device in self.devices:
            if not isinstance(device, DiskStats):
                raise TypeError("devices must contain DiskStats objects")

            device_ids.append(device.identity.device_id)

        if len(set(device_ids)) != len(device_ids):
            raise ValueError("diskstats snapshot contains duplicate device IDs")

    @property
    def whole_disk_count(self) -> int:
        """Return the number of sysfs-resolved whole block devices."""

        return sum(
            device.identity.kind is BlockDeviceKind.WHOLE_DISK
            for device in self.devices
        )

    @property
    def partition_count(self) -> int:
        """Return the number of sysfs-resolved partitions."""

        return sum(
            device.identity.kind is BlockDeviceKind.PARTITION for device in self.devices
        )

    @property
    def unknown_count(self) -> int:
        """Return the number of devices that could not be resolved in sysfs."""

        return sum(
            device.identity.kind is BlockDeviceKind.UNKNOWN for device in self.devices
        )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly snapshot evidence."""

        return {
            "captured_at": self.captured_at.isoformat(),
            "summary": {
                "device_count": len(self.devices),
                "whole_disk_count": self.whole_disk_count,
                "partition_count": self.partition_count,
                "unknown_count": self.unknown_count,
            },
            "devices": [device.to_dict() for device in self.devices],
        }


@dataclass(frozen=True, slots=True)
class _ParsedDiskStats:
    """Internal parsed diskstats record before sysfs identity enrichment."""

    major: int
    minor: int
    name: str
    values: tuple[int, ...]


class LinuxDiskStatsReader:
    """Read one raw Linux block-device I/O statistics snapshot."""

    def __init__(
        self,
        *,
        diskstats_path: str | Path = "/proc/diskstats",
        sys_dev_block_root: str | Path = "/sys/dev/block",
    ) -> None:
        self._diskstats_path = Path(diskstats_path)
        self._sys_dev_block_root = Path(sys_dev_block_root)

    def read_snapshot(self) -> DiskStatsSnapshot:
        """Read and enrich one /proc/diskstats snapshot."""

        parsed_records = _parse_diskstats(
            _read_bounded_text(
                self._diskstats_path,
                max_bytes=_DISKSTATS_MAX_BYTES,
            )
        )

        devices = tuple(
            _build_disk_stats(
                record,
                identity=_resolve_device_identity(
                    record,
                    sys_dev_block_root=self._sys_dev_block_root,
                ),
            )
            for record in parsed_records
        )

        try:
            return DiskStatsSnapshot(
                captured_at=datetime.now(timezone.utc),
                devices=devices,
            )

        except (TypeError, ValueError) as exc:
            raise DiskStatsParseError(f"invalid diskstats snapshot: {exc}") from exc


def _read_bounded_text(path: Path, *, max_bytes: int) -> str:
    """Read /proc/diskstats with a strict upper size bound."""

    try:
        with path.open("rb") as file_handle:
            data = file_handle.read(max_bytes + 1)

    except (OSError, ValueError) as exc:
        raise DiskStatsReadError(f"could not read {path}: {exc}") from exc

    if len(data) > max_bytes:
        raise DiskStatsReadError(f"diskstats exceeds {max_bytes} bytes: {path}")

    try:
        return data.decode("utf-8")

    except UnicodeDecodeError as exc:
        raise DiskStatsReadError(f"diskstats is not valid UTF-8: {path}") from exc


def _parse_diskstats(text: str) -> tuple[_ParsedDiskStats, ...]:
    """Parse /proc/diskstats while preserving forward-compatible fields."""

    records: list[_ParsedDiskStats] = []
    seen_device_ids: set[tuple[int, int]] = set()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        fields = raw_line.split()

        if not fields:
            continue

        if len(fields) < 14:
            raise DiskStatsParseError(
                f"diskstats line {line_number} has fewer than 14 fields"
            )

        major = _parse_nonnegative_int(
            fields[0],
            field_name="major number",
            line_number=line_number,
        )

        minor = _parse_nonnegative_int(
            fields[1],
            field_name="minor number",
            line_number=line_number,
        )

        name = fields[2]

        try:
            _require_device_name(name)

        except (TypeError, ValueError) as exc:
            raise DiskStatsParseError(
                f"invalid device name on diskstats line {line_number}: {exc}"
            ) from exc

        raw_values = fields[3:]
        value_count = len(raw_values)

        if 12 <= value_count <= 14 or value_count == 16:
            raise DiskStatsParseError(
                f"diskstats line {line_number} has an incomplete statistics extension"
            )

        values = tuple(
            _parse_nonnegative_int(
                raw_value,
                field_name="statistics counter",
                line_number=line_number,
            )
            for raw_value in raw_values
        )

        device_key = (major, minor)

        if device_key in seen_device_ids:
            raise DiskStatsParseError(
                f"duplicate block device {major}:{minor} "
                f"on diskstats line {line_number}"
            )

        seen_device_ids.add(device_key)

        records.append(
            _ParsedDiskStats(
                major=major,
                minor=minor,
                name=name,
                values=values,
            )
        )

    if not records:
        raise DiskStatsParseError("diskstats does not contain any block-device records")

    return tuple(records)


def _resolve_device_identity(
    record: _ParsedDiskStats,
    *,
    sys_dev_block_root: Path,
) -> BlockDeviceIdentity:
    """Resolve whole-disk versus partition identity through sysfs."""

    device_id = f"{record.major}:{record.minor}"
    sysfs_path = sys_dev_block_root / device_id

    try:
        exists = sysfs_path.exists()

    except OSError:
        exists = False

    if not exists:
        kind = BlockDeviceKind.UNKNOWN
        resolved_path: str | None = None

    else:
        try:
            is_partition = (sysfs_path / "partition").is_file()

        except OSError:
            is_partition = False

        kind = BlockDeviceKind.PARTITION if is_partition else BlockDeviceKind.WHOLE_DISK

        resolved_path = str(sysfs_path)

    return BlockDeviceIdentity(
        major=record.major,
        minor=record.minor,
        name=record.name,
        kind=kind,
        sysfs_path=resolved_path,
    )


def _build_disk_stats(
    record: _ParsedDiskStats,
    *,
    identity: BlockDeviceIdentity,
) -> DiskStats:
    """Convert a parsed diskstats record into the public typed model."""

    values = record.values

    discard_values: tuple[int, int, int, int] | None = None
    flush_values: tuple[int, int] | None = None
    extra_fields: tuple[int, ...] = ()

    if len(values) >= 15:
        discard_values = (
            values[11],
            values[12],
            values[13],
            values[14],
        )

    if len(values) >= 17:
        flush_values = (
            values[15],
            values[16],
        )

        extra_fields = values[17:]

    return DiskStats(
        identity=identity,
        reads_completed=values[0],
        reads_merged=values[1],
        sectors_read=values[2],
        read_time_ms=values[3],
        writes_completed=values[4],
        writes_merged=values[5],
        sectors_written=values[6],
        write_time_ms=values[7],
        io_in_progress=values[8],
        io_time_ms=values[9],
        weighted_io_time_ms=values[10],
        discards_completed=(None if discard_values is None else discard_values[0]),
        discards_merged=(None if discard_values is None else discard_values[1]),
        sectors_discarded=(None if discard_values is None else discard_values[2]),
        discard_time_ms=(None if discard_values is None else discard_values[3]),
        flushes_completed=(None if flush_values is None else flush_values[0]),
        flush_time_ms=(None if flush_values is None else flush_values[1]),
        extra_fields=extra_fields,
    )


def _parse_nonnegative_int(
    raw_value: str,
    *,
    field_name: str,
    line_number: int,
) -> int:
    """Parse one non-negative integer from /proc/diskstats."""

    try:
        value = int(raw_value)

    except ValueError as exc:
        raise DiskStatsParseError(
            f"invalid {field_name} on diskstats line {line_number}: {raw_value!r}"
        ) from exc

    if value < 0:
        raise DiskStatsParseError(
            f"negative {field_name} on diskstats line {line_number}: {raw_value!r}"
        )

    return value


def _require_device_name(value: str) -> None:
    """Require a non-empty whitespace-free block-device name."""

    if not isinstance(value, str):
        raise TypeError("device name must be a string")

    if not value:
        raise ValueError("device name must not be empty")

    if any(character.isspace() for character in value):
        raise ValueError("device name must not contain whitespace")


def _require_absolute_path(name: str, value: str) -> None:
    """Require a non-empty absolute path string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    if not value.startswith("/"):
        raise ValueError(f"{name} must be an absolute path")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _require_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware datetime."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
