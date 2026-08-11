"""Unit tests for Sentinel-X host observation events."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone

from sentinel_x.core import EventBus, EventKind
from sentinel_x.observability import (
    HOST_OBSERVATION_SOURCE,
    HOST_OBSERVATION_TYPE,
    CpuTimes,
    HostIdentity,
    HostSnapshot,
    LoadAverage,
    build_host_observation,
    host_observation_to_event,
)
from sentinel_x.storage import JsonlEventRecorder


def _observation():
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


class HostObservationEventTests(unittest.TestCase):
    """Tests for host-observation event adaptation and persistence."""

    def test_observation_is_converted_to_typed_event(self) -> None:
        observation = _observation()

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

    def test_observation_event_is_persisted_through_event_bus(self) -> None:
        observation = _observation()
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


if __name__ == "__main__":
    unittest.main()
