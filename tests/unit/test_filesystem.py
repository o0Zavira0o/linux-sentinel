"""Unit tests for Sentinel-X Linux filesystem observability."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_x.observability import (
    FilesystemKind,
    FilesystemMountParseError,
    FilesystemMountReadError,
    LinuxFilesystemReader,
)


def _mountinfo_text() -> str:
    return (
        "36 25 0:32 / / rw,relatime shared:1 - "
        "btrfs /dev/nvme0n1p3 rw,ssd\n"
        "37 36 0:35 / /proc rw,nosuid,nodev,noexec,relatime - "
        "proc proc rw\n"
        "38 36 0:5 / /run rw,nosuid,nodev - "
        "tmpfs tmpfs rw,size=4096k\n"
        "39 36 8:17 / /mnt/data\\040disk rw,relatime shared:2 - "
        "ext4 /dev/sdb1 rw\n"
        "40 36 0:50 / /mnt/remote rw,relatime - "
        "nfs4 server:/export rw\n"
        "41 36 0:51 / /net rw,relatime - "
        "autofs systemd-1 rw\n"
        "42 36 0:52 / /run/user/1000/doc rw,nosuid,nodev - "
        "fuse.portal portal rw\n"
    )


def _reader(
    root: Path,
    text: str | None = None,
) -> LinuxFilesystemReader:
    mountinfo_path = root / "mountinfo"

    if text is not None:
        mountinfo_path.write_text(
            text,
            encoding="utf-8",
        )

    return LinuxFilesystemReader(
        mountinfo_path=mountinfo_path,
    )


def _statvfs_result() -> os.statvfs_result:
    return os.statvfs_result(
        (
            4096,
            4096,
            1000,
            400,
            350,
            100,
            50,
            50,
            0,
            255,
        )
    )


class LinuxFilesystemReaderTests(unittest.TestCase):
    """Tests for mount discovery and raw statvfs collection."""

    def test_discover_mounts_parses_fields_and_decodes_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                _mountinfo_text(),
            )

            mounts = reader.discover_mounts()

        self.assertEqual(
            len(mounts),
            7,
        )

        root_mount = mounts[0]
        data_mount = mounts[3]

        self.assertEqual(
            root_mount.mount_id,
            36,
        )

        self.assertEqual(
            root_mount.parent_id,
            25,
        )

        self.assertEqual(
            root_mount.device_id,
            "0:32",
        )

        self.assertEqual(
            root_mount.optional_fields,
            ("shared:1",),
        )

        self.assertEqual(
            data_mount.mount_point,
            "/mnt/data disk",
        )

        self.assertEqual(
            data_mount.source,
            "/dev/sdb1",
        )

    def test_discover_mounts_classifies_mount_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                _mountinfo_text(),
            )

            mounts = reader.discover_mounts()

        kinds = {mount.mount_point: mount.kind for mount in mounts}

        self.assertIs(
            kinds["/"],
            FilesystemKind.LOCAL,
        )

        self.assertIs(
            kinds["/proc"],
            FilesystemKind.KERNEL_API,
        )

        self.assertIs(
            kinds["/run"],
            FilesystemKind.MEMORY,
        )

        self.assertIs(
            kinds["/mnt/remote"],
            FilesystemKind.REMOTE,
        )

        self.assertIs(
            kinds["/net"],
            FilesystemKind.AUTOMOUNT,
        )

        self.assertIs(
            kinds["/run/user/1000/doc"],
            FilesystemKind.USERSPACE,
        )

    def test_unknown_optional_mount_fields_are_preserved(self) -> None:
        text = _mountinfo_text().replace(
            "shared:1 - btrfs",
            "shared:1 future:99 - btrfs",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text,
            )

            mount = reader.discover_mounts()[0]

        self.assertEqual(
            mount.optional_fields,
            ("shared:1", "future:99"),
        )

    def test_missing_mountinfo_separator_is_rejected(self) -> None:
        text = "36 25 0:32 / / rw btrfs /dev/root rw\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text,
            )

            with self.assertRaises(FilesystemMountParseError):
                reader.discover_mounts()

    def test_invalid_device_identifier_is_rejected(self) -> None:
        text = _mountinfo_text().replace(
            "0:32",
            "invalid",
            1,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text,
            )

            with self.assertRaises(FilesystemMountParseError):
                reader.discover_mounts()

    def test_read_report_probes_only_safe_default_kinds(self) -> None:
        calls: list[str] = []

        def fake_statvfs(
            path: str,
        ) -> os.statvfs_result:
            calls.append(path)

            return _statvfs_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                _mountinfo_text(),
            )

            with patch(
                "sentinel_x.observability.filesystem.os.statvfs",
                side_effect=fake_statvfs,
            ):
                report = reader.read_report()

        self.assertEqual(
            calls,
            [
                "/",
                "/run",
                "/mnt/data disk",
            ],
        )

        self.assertEqual(
            report.discovered_count,
            7,
        )

        self.assertEqual(
            report.probed_count,
            3,
        )

        self.assertEqual(
            report.failed_count,
            0,
        )

        self.assertEqual(
            report.skipped_count,
            4,
        )

    def test_statvfs_values_are_preserved_and_bytes_derived(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                _mountinfo_text(),
            )

            with patch(
                "sentinel_x.observability.filesystem.os.statvfs",
                return_value=_statvfs_result(),
            ):
                report = reader.read_report()

        stats = report.filesystems[0]

        self.assertEqual(
            stats.fragment_size_bytes,
            4096,
        )

        self.assertEqual(
            stats.total_blocks,
            1000,
        )

        self.assertEqual(
            stats.free_blocks,
            400,
        )

        self.assertEqual(
            stats.available_blocks,
            350,
        )

        self.assertEqual(
            stats.total_bytes,
            4_096_000,
        )

        self.assertEqual(
            stats.free_bytes,
            1_638_400,
        )

        self.assertEqual(
            stats.available_bytes,
            1_433_600,
        )

        self.assertEqual(
            stats.total_inodes,
            100,
        )

        self.assertEqual(
            stats.free_inodes,
            50,
        )

        self.assertEqual(
            stats.available_inodes,
            50,
        )

    def test_statvfs_failure_is_isolated(self) -> None:
        def fake_statvfs(
            path: str,
        ) -> os.statvfs_result:
            if path == "/mnt/data disk":
                raise OSError("simulated statvfs failure")

            return _statvfs_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                _mountinfo_text(),
            )

            with patch(
                "sentinel_x.observability.filesystem.os.statvfs",
                side_effect=fake_statvfs,
            ):
                report = reader.read_report()

        self.assertEqual(
            report.probed_count,
            2,
        )

        self.assertEqual(
            report.failed_count,
            1,
        )

        self.assertEqual(
            report.failures[0].mount_point,
            "/mnt/data disk",
        )

        self.assertEqual(
            report.failures[0].error_type,
            "OSError",
        )

    def test_missing_mountinfo_is_reported_as_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(Path(tmpdir))

            with self.assertRaises(FilesystemMountReadError):
                reader.discover_mounts()


if __name__ == "__main__":
    unittest.main()
