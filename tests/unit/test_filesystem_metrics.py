"""Unit tests for Sentinel-X filesystem utilization metrics."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.observability import (
    FilesystemKind,
    FilesystemMount,
    FilesystemObservationEntry,
    FilesystemProbeFailure,
    FilesystemReport,
    FilesystemStats,
    build_filesystem_observation,
    calculate_filesystem_utilization,
)


def _mount(
    *,
    mount_id: int = 36,
    mount_point: str = "/",
    kind: FilesystemKind = FilesystemKind.LOCAL,
    fs_type: str = "ext4",
) -> FilesystemMount:
    return FilesystemMount(
        mount_id=mount_id,
        parent_id=25,
        device_major=8,
        device_minor=1,
        root="/",
        mount_point=mount_point,
        mount_options=("rw", "relatime"),
        optional_fields=(),
        fs_type=fs_type,
        source="/dev/sda1",
        super_options=("rw",),
        kind=kind,
    )


def _stats(
    *,
    total_blocks: int = 1000,
    free_blocks: int = 400,
    available_blocks: int = 350,
    total_inodes: int = 100,
    free_inodes: int = 50,
    available_inodes: int = 40,
) -> FilesystemStats:
    return FilesystemStats(
        mount=_mount(),
        fragment_size_bytes=4096,
        total_blocks=total_blocks,
        free_blocks=free_blocks,
        available_blocks=available_blocks,
        total_inodes=total_inodes,
        free_inodes=free_inodes,
        available_inodes=available_inodes,
        name_max=255,
    )


class FilesystemMetricTests(unittest.TestCase):
    """Tests for derived filesystem capacity and inode metrics."""

    def test_space_metrics_preserve_available_and_restricted_free_space(
        self,
    ) -> None:
        utilization = calculate_filesystem_utilization(_stats())

        self.assertEqual(
            utilization.used_bytes,
            2_457_600,
        )

        self.assertEqual(
            utilization.restricted_free_bytes,
            204_800,
        )

        self.assertAlmostEqual(
            utilization.used_percent_of_total,
            60.0,
        )

        self.assertAlmostEqual(
            utilization.available_percent_of_total,
            35.0,
        )

        self.assertAlmostEqual(
            utilization.user_capacity_used_percent,
            63.1578947368421,
        )

    def test_inode_metrics_preserve_available_and_restricted_free_entries(
        self,
    ) -> None:
        utilization = calculate_filesystem_utilization(_stats())

        self.assertEqual(
            utilization.inode_used,
            50,
        )

        self.assertEqual(
            utilization.restricted_free_inodes,
            10,
        )

        self.assertAlmostEqual(
            utilization.inode_used_percent_of_total,
            50.0,
        )

        self.assertAlmostEqual(
            utilization.inode_available_percent_of_total,
            40.0,
        )

        self.assertAlmostEqual(
            utilization.inode_user_capacity_used_percent,
            55.55555555555556,
        )

    def test_zero_capacity_uses_none_percentages(self) -> None:
        utilization = calculate_filesystem_utilization(
            _stats(
                total_blocks=0,
                free_blocks=0,
                available_blocks=0,
            )
        )

        self.assertEqual(utilization.used_bytes, 0)
        self.assertEqual(utilization.restricted_free_bytes, 0)
        self.assertIsNone(utilization.used_percent_of_total)
        self.assertIsNone(utilization.available_percent_of_total)
        self.assertIsNone(utilization.user_capacity_used_percent)

    def test_zero_inode_accounting_uses_none_inode_metrics(self) -> None:
        utilization = calculate_filesystem_utilization(
            _stats(
                total_inodes=0,
                free_inodes=0,
                available_inodes=0,
            )
        )

        self.assertIsNone(utilization.inode_used)
        self.assertIsNone(utilization.restricted_free_inodes)
        self.assertIsNone(utilization.inode_used_percent_of_total)
        self.assertIsNone(utilization.inode_available_percent_of_total)
        self.assertIsNone(utilization.inode_user_capacity_used_percent)

    def test_free_blocks_above_total_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _stats(
                total_blocks=100,
                free_blocks=101,
                available_blocks=100,
            )

    def test_available_blocks_above_free_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _stats(
                total_blocks=100,
                free_blocks=80,
                available_blocks=81,
            )

    def test_invalid_inode_relationship_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _stats(
                total_inodes=100,
                free_inodes=50,
                available_inodes=51,
            )

    def test_build_observation_preserves_skips_failures_and_raw_evidence(
        self,
    ) -> None:
        successful_stats = _stats()

        skipped_mount = _mount(
            mount_id=37,
            mount_point="/proc",
            kind=FilesystemKind.KERNEL_API,
            fs_type="proc",
        )

        failure = FilesystemProbeFailure(
            mount_id=38,
            mount_point="/mnt/data",
            fs_type="ext4",
            error_type="OSError",
            error_message="simulated failure",
        )

        report = FilesystemReport(
            mounts=(
                successful_stats.mount,
                skipped_mount,
                _mount(
                    mount_id=38,
                    mount_point="/mnt/data",
                ),
            ),
            filesystems=(successful_stats,),
            failures=(failure,),
        )

        observation = build_filesystem_observation(
            report,
            captured_at=datetime.now(timezone.utc),
        )

        self.assertEqual(observation.discovered_count, 3)
        self.assertEqual(observation.probed_count, 1)
        self.assertEqual(observation.skipped_count, 1)
        self.assertEqual(observation.failed_count, 1)

        entry = observation.filesystems[0]

        self.assertIsInstance(
            entry,
            FilesystemObservationEntry,
        )

        attributes = observation.to_attributes()

        self.assertEqual(
            attributes["summary"],
            {
                "discovered_count": 3,
                "probed_count": 1,
                "skipped_count": 1,
                "failed_count": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
