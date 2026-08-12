"""Unit tests for Sentinel-X host observation events."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone

from sentinel_x.core import EventBus, EventKind
from sentinel_x.observability import (
    FILESYSTEM_OBSERVATION_SOURCE,
    FILESYSTEM_OBSERVATION_TYPE,
    HOST_OBSERVATION_SOURCE,
    HOST_OBSERVATION_TYPE,
    MEMORY_OBSERVATION_SOURCE,
    MEMORY_OBSERVATION_TYPE,
    CpuTimes,
    FilesystemKind,
    FilesystemMount,
    FilesystemReport,
    FilesystemStats,
    HostIdentity,
    HostSnapshot,
    LoadAverage,
    MemoryStats,
    build_filesystem_observation,
    build_host_observation,
    build_memory_observation,
    filesystem_observation_to_event,
    host_observation_to_event,
    memory_observation_to_event,
)
from sentinel_x.storage import JsonlEventRecorder


def _host_observation():
    identity = HostIdentity(
        hostname="fedora-lab",
        kernel_name="Linux",
        kernel_release="6.12.0",
        kernel_version="#1 SMP",
        machine="x86_64",
        logical_cpu_count=8,
        os_id="fedora",
        os_version_id="42",
        os_pretty_name="Fedora Linux 42",
    )

    load_average = LoadAverage(
        one_minute=0.25,
        five_minutes=0.50,
        fifteen_minutes=0.75,
        runnable_tasks=2,
        total_tasks=300,
        last_pid=12345,
    )

    previous = HostSnapshot(
        captured_at=datetime.now(timezone.utc),
        identity=identity,
        cpu_times=CpuTimes(
            user=100,
            nice=10,
            system=40,
            idle=800,
            iowait=20,
            irq=5,
            softirq=10,
            steal=5,
            guest=20,
            guest_nice=2,
        ),
        load_average=load_average,
    )

    current = HostSnapshot(
        captured_at=datetime.now(timezone.utc),
        identity=identity,
        cpu_times=CpuTimes(
            user=160,
            nice=20,
            system=70,
            idle=860,
            iowait=30,
            irq=7,
            softirq=18,
            steal=10,
            guest=35,
            guest_nice=4,
        ),
        load_average=load_average,
    )

    return build_host_observation(
        previous,
        current,
        sample_interval_seconds=1.0,
    )


def _memory_observation():
    stats = MemoryStats(
        mem_total_kb=16_384_000,
        mem_available_kb=8_192_000,
        mem_free_kb=2_048_000,
        buffers_kb=256_000,
        cached_kb=4_096_000,
        swap_total_kb=8_388_608,
        swap_free_kb=7_340_032,
        sreclaimable_kb=64_000,
        shmem_kb=128_000,
        swap_cached_kb=12_000,
    )

    return build_memory_observation(
        stats,
        captured_at=datetime.now(timezone.utc),
    )


def _filesystem_observation():
    mount = FilesystemMount(
        mount_id=36,
        parent_id=25,
        device_major=8,
        device_minor=1,
        root="/",
        mount_point="/",
        mount_options=("rw", "relatime"),
        optional_fields=(),
        fs_type="ext4",
        source="/dev/sda1",
        super_options=("rw",),
        kind=FilesystemKind.LOCAL,
    )

    stats = FilesystemStats(
        mount=mount,
        fragment_size_bytes=4096,
        total_blocks=1000,
        free_blocks=400,
        available_blocks=350,
        total_inodes=100,
        free_inodes=50,
        available_inodes=40,
        name_max=255,
    )

    report = FilesystemReport(
        mounts=(mount,),
        filesystems=(stats,),
        failures=(),
    )

    return build_filesystem_observation(
        report,
        captured_at=datetime.now(timezone.utc),
    )


class HostObservationEventTests(unittest.TestCase):
    """Tests for observation event adaptation and persistence."""

    def test_host_observation_is_converted_to_typed_event(self) -> None:
        observation = _host_observation()

        event = host_observation_to_event(observation)

        self.assertIs(event.kind, EventKind.OBSERVATION)
        self.assertEqual(event.source, HOST_OBSERVATION_SOURCE)
        self.assertEqual(event.occurred_at, observation.captured_at)
        self.assertEqual(
            event.attributes["observation_type"],
            HOST_OBSERVATION_TYPE,
        )

        utilization = event.attributes["cpu_utilization"]

        self.assertIsInstance(utilization, dict)

        assert isinstance(utilization, dict)

        self.assertAlmostEqual(
            utilization["busy_percent"],
            62.16216216216216,
        )

    def test_host_observation_event_is_persisted_through_event_bus(self) -> None:
        observation = _host_observation()
        event = host_observation_to_event(observation)

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            bus = EventBus()
            bus.subscribe(recorder)

            report = bus.publish(event)

            event_path = recorder.path
            recorder.close()

            lines = event_path.read_text(encoding="utf-8").splitlines()

        self.assertTrue(report.succeeded)
        self.assertEqual(report.delivered, 1)
        self.assertEqual(len(lines), 1)

        payload = json.loads(lines[0])

        self.assertEqual(
            payload["event"]["kind"],
            "observation",
        )

        self.assertEqual(
            payload["event"]["attributes"]["observation_type"],
            HOST_OBSERVATION_TYPE,
        )

        self.assertIn(
            "cpu_utilization",
            payload["event"]["attributes"],
        )

    def test_memory_observation_is_converted_to_typed_event(self) -> None:
        observation = _memory_observation()

        event = memory_observation_to_event(observation)

        self.assertIs(event.kind, EventKind.OBSERVATION)
        self.assertEqual(event.source, MEMORY_OBSERVATION_SOURCE)
        self.assertEqual(event.occurred_at, observation.captured_at)
        self.assertEqual(
            event.attributes["observation_type"],
            MEMORY_OBSERVATION_TYPE,
        )

        utilization = event.attributes["memory_utilization"]

        self.assertIsInstance(utilization, dict)

        assert isinstance(utilization, dict)

        self.assertAlmostEqual(
            utilization["used_estimate_percent"],
            50.0,
        )

    def test_memory_observation_event_is_persisted_through_event_bus(self) -> None:
        observation = _memory_observation()
        event = memory_observation_to_event(observation)

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            bus = EventBus()
            bus.subscribe(recorder)

            report = bus.publish(event)

            event_path = recorder.path
            recorder.close()

            lines = event_path.read_text(encoding="utf-8").splitlines()

        self.assertTrue(report.succeeded)
        self.assertEqual(report.delivered, 1)
        self.assertEqual(len(lines), 1)

        payload = json.loads(lines[0])
        attributes = payload["event"]["attributes"]

        self.assertEqual(
            attributes["observation_type"],
            MEMORY_OBSERVATION_TYPE,
        )

        self.assertEqual(
            attributes["memory_stats"]["unit"],
            "kB",
        )

        self.assertEqual(
            attributes["memory_utilization"]["used_estimate_kb"],
            8_192_000,
        )

    def test_filesystem_observation_is_converted_to_typed_event(self) -> None:
        observation = _filesystem_observation()

        event = filesystem_observation_to_event(observation)

        self.assertIs(event.kind, EventKind.OBSERVATION)
        self.assertEqual(event.source, FILESYSTEM_OBSERVATION_SOURCE)
        self.assertEqual(event.occurred_at, observation.captured_at)
        self.assertEqual(
            event.attributes["observation_type"],
            FILESYSTEM_OBSERVATION_TYPE,
        )

        summary = event.attributes["summary"]

        self.assertIsInstance(summary, dict)

        assert isinstance(summary, dict)

        self.assertEqual(
            summary["probed_count"],
            1,
        )

    def test_filesystem_observation_event_is_persisted_through_event_bus(
        self,
    ) -> None:
        observation = _filesystem_observation()
        event = filesystem_observation_to_event(observation)

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            bus = EventBus()
            bus.subscribe(recorder)

            report = bus.publish(event)

            event_path = recorder.path
            recorder.close()

            lines = event_path.read_text(encoding="utf-8").splitlines()

        self.assertTrue(report.succeeded)
        self.assertEqual(report.delivered, 1)
        self.assertEqual(len(lines), 1)

        payload = json.loads(lines[0])
        attributes = payload["event"]["attributes"]

        self.assertEqual(
            attributes["observation_type"],
            FILESYSTEM_OBSERVATION_TYPE,
        )

        filesystems = attributes["filesystems"]

        self.assertIsInstance(filesystems, list)

        assert isinstance(filesystems, list)

        self.assertEqual(len(filesystems), 1)

        utilization = filesystems[0]["utilization"]

        self.assertAlmostEqual(
            utilization["used_percent_of_total"],
            60.0,
        )

        self.assertAlmostEqual(
            utilization["user_capacity_used_percent"],
            63.1578947368421,
        )


if __name__ == "__main__":
    unittest.main()
