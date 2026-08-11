"""Unit tests for sampled Sentinel-X CPU utilization."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.observability import (
    CpuSamplingError,
    CpuTimes,
    HostIdentity,
    HostSnapshot,
    LoadAverage,
    build_host_observation,
    calculate_cpu_utilization,
    validate_sample_interval,
)


def _previous_cpu_times() -> CpuTimes:
    return CpuTimes(
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
    )


def _current_cpu_times() -> CpuTimes:
    return CpuTimes(
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
    )


def _identity(
    *,
    hostname: str = "fedora-lab",
    logical_cpu_count: int = 8,
) -> HostIdentity:
    return HostIdentity(
        hostname=hostname,
        kernel_name="Linux",
        kernel_release="6.12.0",
        kernel_version="#1 SMP",
        machine="x86_64",
        logical_cpu_count=logical_cpu_count,
        os_id="fedora",
        os_version_id="42",
        os_pretty_name="Fedora Linux 42",
    )


def _load_average() -> LoadAverage:
    return LoadAverage(
        one_minute=0.25,
        five_minutes=0.50,
        fifteen_minutes=0.75,
        runnable_tasks=2,
        total_tasks=300,
        last_pid=12345,
    )


def _snapshot(
    cpu_times: CpuTimes,
    *,
    hostname: str = "fedora-lab",
    logical_cpu_count: int = 8,
) -> HostSnapshot:
    return HostSnapshot(
        captured_at=datetime.now(timezone.utc),
        identity=_identity(
            hostname=hostname,
            logical_cpu_count=logical_cpu_count,
        ),
        cpu_times=cpu_times,
        load_average=_load_average(),
    )


class CpuSamplingTests(unittest.TestCase):
    """Tests for two-point CPU counter sampling."""

    def test_calculates_cpu_percentages_without_guest_double_counting(
        self,
    ) -> None:
        utilization = calculate_cpu_utilization(
            _previous_cpu_times(),
            _current_cpu_times(),
        )

        self.assertEqual(utilization.total_ticks, 185)
        self.assertAlmostEqual(utilization.busy_percent, 62.16216216216216)
        self.assertAlmostEqual(utilization.user_percent, 24.324324324324323)
        self.assertAlmostEqual(utilization.guest_percent, 8.108108108108109)
        self.assertAlmostEqual(utilization.idle_percent, 32.432432432432435)
        self.assertFalse(utilization.iowait_regressed)
        self.assertFalse(utilization.guest_accounting_adjusted)

    def test_iowait_regression_is_clamped_and_reported(self) -> None:
        previous = CpuTimes(
            user=100,
            nice=0,
            system=20,
            idle=500,
            iowait=20,
        )

        current = CpuTimes(
            user=110,
            nice=0,
            system=25,
            idle=510,
            iowait=18,
        )

        utilization = calculate_cpu_utilization(
            previous,
            current,
        )

        self.assertTrue(utilization.iowait_regressed)
        self.assertEqual(utilization.iowait_percent, 0.0)
        self.assertEqual(utilization.total_ticks, 25)

    def test_regular_counter_regression_is_rejected(self) -> None:
        previous = _previous_cpu_times()

        current = CpuTimes(
            user=160,
            nice=20,
            system=39,
            idle=860,
            iowait=30,
            irq=7,
            softirq=18,
            steal=10,
            guest=35,
            guest_nice=4,
        )

        with self.assertRaises(CpuSamplingError):
            calculate_cpu_utilization(
                previous,
                current,
            )

    def test_zero_counter_delta_is_rejected(self) -> None:
        sample = _previous_cpu_times()

        with self.assertRaises(CpuSamplingError):
            calculate_cpu_utilization(
                sample,
                sample,
            )

    def test_guest_accounting_is_conservatively_adjusted(self) -> None:
        previous = CpuTimes(
            user=100,
            nice=0,
            system=20,
            idle=500,
            guest=20,
        )

        current = CpuTimes(
            user=105,
            nice=0,
            system=20,
            idle=505,
            guest=30,
        )

        utilization = calculate_cpu_utilization(
            previous,
            current,
        )

        self.assertTrue(utilization.guest_accounting_adjusted)
        self.assertEqual(utilization.user_percent, 0.0)
        self.assertEqual(utilization.guest_percent, 50.0)
        self.assertEqual(utilization.idle_percent, 50.0)

    def test_logical_cpu_count_change_is_rejected(self) -> None:
        previous = _snapshot(
            _previous_cpu_times(),
            logical_cpu_count=8,
        )

        current = _snapshot(
            _current_cpu_times(),
            logical_cpu_count=16,
        )

        with self.assertRaises(CpuSamplingError):
            build_host_observation(
                previous,
                current,
                sample_interval_seconds=1.0,
            )

    def test_hostname_change_is_rejected(self) -> None:
        previous = _snapshot(
            _previous_cpu_times(),
            hostname="host-a",
        )

        current = _snapshot(
            _current_cpu_times(),
            hostname="host-b",
        )

        with self.assertRaises(CpuSamplingError):
            build_host_observation(
                previous,
                current,
                sample_interval_seconds=1.0,
            )

    def test_sample_interval_validation(self) -> None:
        self.assertEqual(
            validate_sample_interval(1),
            1.0,
        )

        invalid_values = (
            True,
            0.0,
            0.09,
            61.0,
            float("nan"),
            float("inf"),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(CpuSamplingError):
                    validate_sample_interval(value)


if __name__ == "__main__":
    unittest.main()
