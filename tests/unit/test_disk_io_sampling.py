"""Unit tests for Sentinel-X sampled block-device I/O metrics."""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

from sentinel_x.observability import (
    BlockDeviceIdentity,
    BlockDeviceKind,
    DiskIoSampleStatus,
    DiskIoSamplingError,
    DiskStats,
    DiskStatsSnapshot,
    build_disk_io_observation,
)


_BASE_TIME = datetime(
    2026,
    1,
    1,
    tzinfo=timezone.utc,
)


def _identity(
    *,
    major: int = 259,
    minor: int = 0,
    name: str = "nvme0n1",
    kind: BlockDeviceKind = BlockDeviceKind.WHOLE_DISK,
    sysfs_path: str | None = "/sys/dev/block/259:0",
) -> BlockDeviceIdentity:
    return BlockDeviceIdentity(
        major=major,
        minor=minor,
        name=name,
        kind=kind,
        sysfs_path=sysfs_path,
    )


def _stats(
    *,
    identity: BlockDeviceIdentity | None = None,
    reads_completed: int = 100,
    reads_merged: int = 5,
    sectors_read: int = 2000,
    read_time_ms: int = 500,
    writes_completed: int = 80,
    writes_merged: int = 4,
    sectors_written: int = 1600,
    write_time_ms: int = 400,
    io_in_progress: int = 3,
    io_time_ms: int = 600,
    weighted_io_time_ms: int = 900,
    discards_completed: int | None = 10,
    discards_merged: int | None = 1,
    sectors_discarded: int | None = 100,
    discard_time_ms: int | None = 50,
    flushes_completed: int | None = 5,
    flush_time_ms: int | None = 20,
) -> DiskStats:
    return DiskStats(
        identity=identity or _identity(),
        reads_completed=reads_completed,
        reads_merged=reads_merged,
        sectors_read=sectors_read,
        read_time_ms=read_time_ms,
        writes_completed=writes_completed,
        writes_merged=writes_merged,
        sectors_written=sectors_written,
        write_time_ms=write_time_ms,
        io_in_progress=io_in_progress,
        io_time_ms=io_time_ms,
        weighted_io_time_ms=weighted_io_time_ms,
        discards_completed=discards_completed,
        discards_merged=discards_merged,
        sectors_discarded=sectors_discarded,
        discard_time_ms=discard_time_ms,
        flushes_completed=flushes_completed,
        flush_time_ms=flush_time_ms,
    )


def _active_end_stats(
    *,
    identity: BlockDeviceIdentity | None = None,
) -> DiskStats:
    return _stats(
        identity=identity,
        reads_completed=120,
        reads_merged=6,
        sectors_read=2400,
        read_time_ms=560,
        writes_completed=90,
        writes_merged=5,
        sectors_written=1900,
        write_time_ms=440,
        io_in_progress=1,
        io_time_ms=1500,
        weighted_io_time_ms=3900,
        discards_completed=14,
        discards_merged=2,
        sectors_discarded=180,
        discard_time_ms=90,
        flushes_completed=7,
        flush_time_ms=26,
    )


def _snapshot(
    devices: tuple[DiskStats, ...],
    *,
    offset_seconds: float,
) -> DiskStatsSnapshot:
    return DiskStatsSnapshot(
        captured_at=_BASE_TIME + timedelta(seconds=offset_seconds),
        devices=devices,
    )


class DiskIoSamplingTests(unittest.TestCase):
    """Tests for safe diskstats delta and rate calculation."""

    def test_calculates_sampled_disk_io_metrics(self) -> None:
        previous = _snapshot(
            (_stats(),),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (_active_end_stats(),),
            offset_seconds=2.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=2.0,
        )

        sample = observation.devices[0]
        metrics = sample.metrics

        self.assertIs(
            sample.status,
            DiskIoSampleStatus.SAMPLED,
        )
        self.assertIsNotNone(metrics)

        assert metrics is not None

        self.assertEqual(metrics.reads_completed_delta, 20)
        self.assertEqual(metrics.writes_completed_delta, 10)
        self.assertEqual(metrics.read_bytes_delta, 204_800)
        self.assertEqual(metrics.written_bytes_delta, 153_600)
        self.assertEqual(metrics.total_rw_operations_delta, 30)
        self.assertEqual(metrics.total_rw_bytes_delta, 358_400)

        self.assertAlmostEqual(metrics.read_iops, 10.0)
        self.assertAlmostEqual(metrics.write_iops, 5.0)
        self.assertAlmostEqual(metrics.total_rw_iops, 15.0)

        self.assertAlmostEqual(
            metrics.read_bytes_per_second,
            102_400.0,
        )
        self.assertAlmostEqual(
            metrics.write_bytes_per_second,
            76_800.0,
        )
        self.assertAlmostEqual(
            metrics.total_rw_bytes_per_second,
            179_200.0,
        )

        self.assertAlmostEqual(
            metrics.average_read_time_ms,
            3.0,
        )
        self.assertAlmostEqual(
            metrics.average_write_time_ms,
            4.0,
        )

        self.assertAlmostEqual(
            metrics.io_busy_percent_estimate,
            45.0,
        )
        self.assertAlmostEqual(
            metrics.weighted_queue_depth_estimate,
            1.5,
        )

        self.assertEqual(
            metrics.discards_completed_delta,
            4,
        )
        self.assertEqual(
            metrics.discarded_bytes_delta,
            40_960,
        )
        self.assertAlmostEqual(
            metrics.discard_iops,
            2.0,
        )
        self.assertAlmostEqual(
            metrics.discard_bytes_per_second,
            20_480.0,
        )
        self.assertAlmostEqual(
            metrics.average_discard_time_ms,
            10.0,
        )

        self.assertEqual(
            metrics.flushes_completed_delta,
            2,
        )
        self.assertAlmostEqual(
            metrics.flush_iops,
            1.0,
        )
        self.assertAlmostEqual(
            metrics.average_flush_time_ms,
            3.0,
        )

    def test_io_in_progress_decrease_is_not_counter_reset(self) -> None:
        previous = _snapshot(
            (
                _stats(
                    io_in_progress=8,
                ),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _stats(
                    io_in_progress=0,
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.devices[0]

        self.assertIs(
            sample.status,
            DiskIoSampleStatus.SAMPLED,
        )
        self.assertEqual(sample.regressed_fields, ())
        self.assertIsNotNone(sample.metrics)

    def test_cumulative_counter_regression_marks_device_reset(
        self,
    ) -> None:
        previous = _snapshot(
            (_stats(),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _stats(
                    reads_completed=99,
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.devices[0]

        self.assertIs(
            sample.status,
            DiskIoSampleStatus.COUNTER_RESET,
        )
        self.assertIsNone(sample.metrics)
        self.assertEqual(
            sample.regressed_fields,
            ("reads_completed",),
        )

    def test_optional_counter_regression_marks_device_reset(
        self,
    ) -> None:
        previous = _snapshot(
            (_stats(),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _stats(
                    flushes_completed=4,
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.devices[0]

        self.assertIs(
            sample.status,
            DiskIoSampleStatus.COUNTER_RESET,
        )
        self.assertIn(
            "flushes_completed",
            sample.regressed_fields,
        )
        self.assertIsNone(sample.metrics)

    def test_appeared_and_disappeared_devices_are_preserved(
        self,
    ) -> None:
        stable_identity = _identity()

        disappearing_identity = _identity(
            major=8,
            minor=0,
            name="sda",
            sysfs_path="/sys/dev/block/8:0",
        )

        appearing_identity = _identity(
            major=252,
            minor=0,
            name="dm-0",
            sysfs_path="/sys/dev/block/252:0",
        )

        previous = _snapshot(
            (
                _stats(identity=stable_identity),
                _stats(identity=disappearing_identity),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _stats(identity=stable_identity),
                _stats(identity=appearing_identity),
            ),
            offset_seconds=1.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        statuses = {
            sample.identity.device_id: sample.status for sample in observation.devices
        }

        self.assertIs(
            statuses["259:0"],
            DiskIoSampleStatus.SAMPLED,
        )
        self.assertIs(
            statuses["252:0"],
            DiskIoSampleStatus.APPEARED,
        )
        self.assertIs(
            statuses["8:0"],
            DiskIoSampleStatus.DISAPPEARED,
        )

        self.assertEqual(observation.sampled_count, 1)
        self.assertEqual(observation.appeared_count, 1)
        self.assertEqual(observation.disappeared_count, 1)

    def test_major_minor_reuse_with_new_name_marks_identity_change(
        self,
    ) -> None:
        previous_identity = _identity(
            name="nvme0n1",
        )
        current_identity = _identity(
            name="replacement0",
        )

        previous = _snapshot(
            (
                _stats(
                    identity=previous_identity,
                ),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _active_end_stats(
                    identity=current_identity,
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.devices[0]

        self.assertIs(
            sample.status,
            DiskIoSampleStatus.IDENTITY_CHANGED,
        )
        self.assertIsNone(sample.metrics)

    def test_unknown_to_resolved_sysfs_kind_remains_comparable(
        self,
    ) -> None:
        previous_identity = _identity(
            kind=BlockDeviceKind.UNKNOWN,
            sysfs_path=None,
        )

        current_identity = _identity(
            kind=BlockDeviceKind.WHOLE_DISK,
            sysfs_path="/sys/dev/block/259:0",
        )

        previous = _snapshot(
            (
                _stats(
                    identity=previous_identity,
                ),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _active_end_stats(
                    identity=current_identity,
                ),
            ),
            offset_seconds=2.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=2.0,
        )

        self.assertIs(
            observation.devices[0].status,
            DiskIoSampleStatus.SAMPLED,
        )

    def test_optional_layout_change_preserves_base_metrics(
        self,
    ) -> None:
        previous = _snapshot(
            (
                _stats(
                    discards_completed=None,
                    discards_merged=None,
                    sectors_discarded=None,
                    discard_time_ms=None,
                    flushes_completed=None,
                    flush_time_ms=None,
                ),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (_active_end_stats(),),
            offset_seconds=2.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=2.0,
        )

        sample = observation.devices[0]
        metrics = sample.metrics

        self.assertIs(
            sample.status,
            DiskIoSampleStatus.SAMPLED,
        )
        self.assertIsNotNone(metrics)

        assert metrics is not None

        self.assertAlmostEqual(metrics.read_iops, 10.0)
        self.assertIsNone(metrics.discards_completed_delta)
        self.assertIsNone(metrics.discard_iops)
        self.assertIsNone(metrics.flushes_completed_delta)
        self.assertIsNone(metrics.flush_iops)

    def test_zero_completed_operations_use_none_average_times(
        self,
    ) -> None:
        previous = _snapshot(
            (_stats(),),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (_stats(),),
            offset_seconds=1.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        metrics = observation.devices[0].metrics

        self.assertIsNotNone(metrics)

        assert metrics is not None

        self.assertEqual(metrics.total_rw_iops, 0.0)
        self.assertEqual(metrics.total_rw_bytes_per_second, 0.0)
        self.assertIsNone(metrics.average_read_time_ms)
        self.assertIsNone(metrics.average_write_time_ms)
        self.assertIsNone(metrics.average_discard_time_ms)
        self.assertIsNone(metrics.average_flush_time_ms)

    def test_invalid_sample_interval_is_rejected(self) -> None:
        previous = _snapshot(
            (_stats(),),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (_stats(),),
            offset_seconds=1.0,
        )

        invalid_values: tuple[object, ...] = (
            True,
            0,
            -1.0,
            math.inf,
            math.nan,
            "1",
        )

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(DiskIoSamplingError):
                    build_disk_io_observation(
                        previous,
                        current,
                        sample_interval_seconds=invalid_value,
                    )

    def test_observation_to_dict_preserves_summary_and_raw_endpoints(
        self,
    ) -> None:
        previous = _snapshot(
            (_stats(),),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (_active_end_stats(),),
            offset_seconds=2.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=2.0,
        )

        payload = observation.to_dict()

        self.assertEqual(
            payload["summary"],
            {
                "device_count": 1,
                "sampled_count": 1,
                "appeared_count": 0,
                "disappeared_count": 0,
                "counter_reset_count": 0,
                "identity_changed_count": 0,
            },
        )

        devices = payload["devices"]

        self.assertIsInstance(devices, list)

        assert isinstance(devices, list)

        self.assertEqual(
            devices[0]["status"],
            "sampled",
        )
        self.assertIsNotNone(
            devices[0]["start_stats"],
        )
        self.assertIsNotNone(
            devices[0]["end_stats"],
        )
        self.assertIsNotNone(
            devices[0]["metrics"],
        )

    def test_partition_metrics_are_not_filtered(self) -> None:
        partition_identity = _identity(
            major=259,
            minor=1,
            name="nvme0n1p1",
            kind=BlockDeviceKind.PARTITION,
            sysfs_path="/sys/dev/block/259:1",
        )

        previous = _snapshot(
            (
                _stats(
                    identity=partition_identity,
                ),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _active_end_stats(
                    identity=partition_identity,
                ),
            ),
            offset_seconds=2.0,
        )

        observation = build_disk_io_observation(
            previous,
            current,
            sample_interval_seconds=2.0,
        )

        sample = observation.devices[0]

        self.assertIs(
            sample.identity.kind,
            BlockDeviceKind.PARTITION,
        )
        self.assertIs(
            sample.status,
            DiskIoSampleStatus.SAMPLED,
        )
        self.assertIsNotNone(sample.metrics)


if __name__ == "__main__":
    unittest.main()
