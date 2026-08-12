"""Unit tests for Sentinel-X Linux block-device I/O observability."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sentinel_x.observability import (
    DISK_SECTOR_BYTES,
    BlockDeviceKind,
    DiskStatsParseError,
    DiskStatsReadError,
    LinuxDiskStatsReader,
)


def _diskstats_line(
    *,
    major: int = 259,
    minor: int = 0,
    name: str = "nvme0n1",
    values: tuple[object, ...],
) -> str:
    counters = " ".join(str(value) for value in values)

    return f"{major} {minor} {name} {counters}\n"


def _base_values() -> tuple[int, ...]:
    return (
        100,
        5,
        2000,
        50,
        80,
        4,
        1600,
        40,
        0,
        70,
        90,
    )


def _current_values() -> tuple[int, ...]:
    return (
        *_base_values(),
        3,
        1,
        20,
        5,
        7,
        8,
    )


def _reader(
    root: Path,
    *,
    text: str | None,
) -> LinuxDiskStatsReader:
    proc_root = root / "proc"
    sys_dev_block_root = root / "sys" / "dev" / "block"

    proc_root.mkdir(parents=True)
    sys_dev_block_root.mkdir(parents=True)

    diskstats_path = proc_root / "diskstats"

    if text is not None:
        diskstats_path.write_text(
            text,
            encoding="utf-8",
        )

    return LinuxDiskStatsReader(
        diskstats_path=diskstats_path,
        sys_dev_block_root=sys_dev_block_root,
    )


class LinuxDiskStatsReaderTests(unittest.TestCase):
    """Tests for raw /proc/diskstats parsing and sysfs enrichment."""

    def test_current_layout_parses_and_classifies_devices(self) -> None:
        text = _diskstats_line(
            major=259,
            minor=0,
            name="nvme0n1",
            values=_current_values(),
        ) + _diskstats_line(
            major=259,
            minor=1,
            name="nvme0n1p1",
            values=_current_values(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _reader(
                root,
                text=text,
            )

            sys_root = root / "sys" / "dev" / "block"

            (sys_root / "259:0").mkdir()
            (sys_root / "259:1").mkdir()

            (sys_root / "259:1" / "partition").write_text(
                "1\n",
                encoding="utf-8",
            )

            snapshot = reader.read_snapshot()

        self.assertEqual(len(snapshot.devices), 2)
        self.assertEqual(snapshot.whole_disk_count, 1)
        self.assertEqual(snapshot.partition_count, 1)
        self.assertEqual(snapshot.unknown_count, 0)

        self.assertIs(
            snapshot.devices[0].identity.kind,
            BlockDeviceKind.WHOLE_DISK,
        )

        self.assertIs(
            snapshot.devices[1].identity.kind,
            BlockDeviceKind.PARTITION,
        )

        self.assertEqual(
            snapshot.devices[0].identity.device_id,
            "259:0",
        )

        self.assertEqual(
            snapshot.devices[0].flushes_completed,
            7,
        )

        self.assertEqual(
            snapshot.devices[0].flush_time_ms,
            8,
        )

    def test_base_eleven_counter_layout_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_diskstats_line(
                    values=_base_values(),
                ),
            )

            stats = reader.read_snapshot().devices[0]

        self.assertIsNone(stats.discards_completed)
        self.assertIsNone(stats.sectors_discarded)
        self.assertIsNone(stats.flushes_completed)
        self.assertEqual(stats.extra_fields, ())

    def test_discard_layout_without_flush_fields_is_supported(self) -> None:
        values = (
            *_base_values(),
            3,
            1,
            20,
            5,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_diskstats_line(
                    values=values,
                ),
            )

            stats = reader.read_snapshot().devices[0]

        self.assertEqual(stats.discards_completed, 3)
        self.assertEqual(stats.sectors_discarded, 20)
        self.assertEqual(stats.discard_time_ms, 5)
        self.assertIsNone(stats.flushes_completed)
        self.assertIsNone(stats.flush_time_ms)

    def test_future_trailing_counters_are_preserved(self) -> None:
        values = (
            *_current_values(),
            123,
            456,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_diskstats_line(
                    values=values,
                ),
            )

            stats = reader.read_snapshot().devices[0]

        self.assertEqual(
            stats.extra_fields,
            (123, 456),
        )

    def test_incomplete_known_extension_is_rejected(self) -> None:
        values = (
            *_base_values(),
            3,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_diskstats_line(
                    values=values,
                ),
            )

            with self.assertRaises(DiskStatsParseError):
                reader.read_snapshot()

    def test_invalid_counter_is_rejected(self) -> None:
        for invalid_value in ("bad", -1):
            with self.subTest(invalid_value=invalid_value):
                values = list(_base_values())
                values[2] = invalid_value

                with tempfile.TemporaryDirectory() as tmpdir:
                    reader = _reader(
                        Path(tmpdir),
                        text=_diskstats_line(
                            values=tuple(values),
                        ),
                    )

                    with self.assertRaises(DiskStatsParseError):
                        reader.read_snapshot()

    def test_duplicate_major_minor_identity_is_rejected(self) -> None:
        line = _diskstats_line(
            values=_base_values(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=line + line,
            )

            with self.assertRaises(DiskStatsParseError):
                reader.read_snapshot()

    def test_missing_diskstats_source_is_reported_as_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=None,
            )

            with self.assertRaises(DiskStatsReadError):
                reader.read_snapshot()

    def test_missing_sysfs_entry_uses_unknown_device_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_diskstats_line(
                    values=_base_values(),
                ),
            )

            stats = reader.read_snapshot().devices[0]

        self.assertIs(
            stats.identity.kind,
            BlockDeviceKind.UNKNOWN,
        )

        self.assertIsNone(stats.identity.sysfs_path)

    def test_sector_counters_use_standard_512_byte_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_diskstats_line(
                    values=_current_values(),
                ),
            )

            stats = reader.read_snapshot().devices[0]

        self.assertEqual(DISK_SECTOR_BYTES, 512)
        self.assertEqual(stats.read_bytes, 2000 * 512)
        self.assertEqual(stats.written_bytes, 1600 * 512)
        self.assertEqual(stats.discarded_bytes, 20 * 512)


if __name__ == "__main__":
    unittest.main()
