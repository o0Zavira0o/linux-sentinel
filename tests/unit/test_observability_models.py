"""Unit tests for Sentinel-X observation models."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.observability.models import (
    CpuTimes,
    HostIdentity,
    HostSnapshot,
    LoadAverage,
)


def _valid_identity() -> HostIdentity:
    return HostIdentity(
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


def _valid_cpu_times() -> CpuTimes:
    return CpuTimes(
        user=100,
        nice=2,
        system=30,
        idle=400,
        iowait=5,
        irq=1,
        softirq=4,
        steal=0,
        guest=0,
        guest_nice=0,
    )


def _valid_load_average() -> LoadAverage:
    return LoadAverage(
        one_minute=0.25,
        five_minutes=0.50,
        fifteen_minutes=0.75,
        runnable_tasks=2,
        total_tasks=300,
        last_pid=12345,
    )


class ObservationModelTests(unittest.TestCase):
    """Tests for typed Phase 1 observation models."""

    def test_host_identity_normalizes_optional_text(self) -> None:
        identity = HostIdentity(
            hostname=" fedora-lab ",
            kernel_name=" Linux ",
            kernel_release=" 6.12.0 ",
            kernel_version=" #1 SMP ",
            machine=" x86_64 ",
            logical_cpu_count=8,
            os_id=" fedora ",
            os_version_id=" 42 ",
            os_pretty_name=" Fedora Linux 42 ",
        )

        self.assertEqual(identity.hostname, "fedora-lab")
        self.assertEqual(identity.os_id, "fedora")
        self.assertEqual(identity.os_version_id, "42")
        self.assertEqual(identity.os_pretty_name, "Fedora Linux 42")

    def test_cpu_times_reject_negative_counter(self) -> None:
        with self.assertRaises(ValueError):
            CpuTimes(
                user=-1,
                nice=0,
                system=0,
                idle=1,
            )

    def test_load_average_rejects_runnable_count_above_total(self) -> None:
        with self.assertRaises(ValueError):
            LoadAverage(
                one_minute=0.1,
                five_minutes=0.2,
                fifteen_minutes=0.3,
                runnable_tasks=5,
                total_tasks=4,
                last_pid=100,
            )

    def test_host_snapshot_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            HostSnapshot(
                captured_at=datetime.now(),
                identity=_valid_identity(),
                cpu_times=_valid_cpu_times(),
                load_average=_valid_load_average(),
            )

    def test_host_snapshot_serializes_structured_attributes(self) -> None:
        snapshot = HostSnapshot(
            captured_at=datetime.now(timezone.utc),
            identity=_valid_identity(),
            cpu_times=_valid_cpu_times(),
            load_average=_valid_load_average(),
        )

        attributes = snapshot.to_attributes()

        host = attributes["host"]
        cpu_times = attributes["cpu_times"]
        load_average = attributes["load_average"]

        self.assertIsInstance(host, dict)
        self.assertIsInstance(cpu_times, dict)
        self.assertIsInstance(load_average, dict)

        assert isinstance(host, dict)
        assert isinstance(cpu_times, dict)
        assert isinstance(load_average, dict)

        self.assertEqual(host["hostname"], "fedora-lab")
        self.assertEqual(cpu_times["user"], 100)
        self.assertEqual(load_average["runnable_tasks"], 2)


if __name__ == "__main__":
    unittest.main()
