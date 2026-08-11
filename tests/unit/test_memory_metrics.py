"""Unit tests for Sentinel-X memory utilization metrics."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.observability import (
    MemoryStats,
    MemoryUtilization,
    build_memory_observation,
    calculate_memory_utilization,
)


def _memory_stats(
    *,
    mem_total_kb: int = 16_384_000,
    mem_available_kb: int = 8_192_000,
    mem_free_kb: int = 2_048_000,
    swap_total_kb: int = 8_388_608,
    swap_free_kb: int = 7_340_032,
) -> MemoryStats:
    return MemoryStats(
        mem_total_kb=mem_total_kb,
        mem_available_kb=mem_available_kb,
        mem_free_kb=mem_free_kb,
        buffers_kb=256_000,
        cached_kb=4_096_000,
        swap_total_kb=swap_total_kb,
        swap_free_kb=swap_free_kb,
        sreclaimable_kb=64_000,
        shmem_kb=128_000,
        swap_cached_kb=12_000,
    )


class MemoryMetricTests(unittest.TestCase):
    """Tests for derived Linux memory and swap metrics."""

    def test_calculates_available_and_used_estimate_percentages(self) -> None:
        utilization = calculate_memory_utilization(_memory_stats())

        self.assertEqual(
            utilization.used_estimate_kb,
            8_192_000,
        )
        self.assertAlmostEqual(
            utilization.available_percent,
            50.0,
        )
        self.assertAlmostEqual(
            utilization.used_estimate_percent,
            50.0,
        )

    def test_used_estimate_is_based_on_memavailable(self) -> None:
        utilization = calculate_memory_utilization(
            _memory_stats(
                mem_total_kb=1_000,
                mem_available_kb=800,
                mem_free_kb=100,
                swap_total_kb=0,
                swap_free_kb=0,
            )
        )

        self.assertEqual(
            utilization.used_estimate_kb,
            200,
        )
        self.assertAlmostEqual(
            utilization.used_estimate_percent,
            20.0,
        )

    def test_calculates_swap_usage(self) -> None:
        utilization = calculate_memory_utilization(_memory_stats())

        self.assertTrue(utilization.swap_configured)
        self.assertEqual(
            utilization.swap_used_kb,
            1_048_576,
        )
        self.assertAlmostEqual(
            utilization.swap_used_percent,
            12.5,
        )

    def test_no_swap_configuration_uses_none_percentage(self) -> None:
        utilization = calculate_memory_utilization(
            _memory_stats(
                swap_total_kb=0,
                swap_free_kb=0,
            )
        )

        self.assertFalse(utilization.swap_configured)
        self.assertEqual(utilization.swap_used_kb, 0)
        self.assertIsNone(utilization.swap_used_percent)

    def test_memory_observation_preserves_timestamp_and_raw_stats(self) -> None:
        captured_at = datetime.now(timezone.utc)
        stats = _memory_stats()

        observation = build_memory_observation(
            stats,
            captured_at=captured_at,
        )

        self.assertEqual(observation.captured_at, captured_at)
        self.assertIs(observation.stats, stats)
        self.assertEqual(
            observation.utilization.used_estimate_kb,
            8_192_000,
        )

    def test_memory_utilization_rejects_inconsistent_percentages(self) -> None:
        with self.assertRaises(ValueError):
            MemoryUtilization(
                used_estimate_kb=500,
                available_percent=60.0,
                used_estimate_percent=50.0,
                swap_used_kb=0,
                swap_used_percent=None,
                swap_configured=False,
            )

    def test_configured_swap_requires_swap_percentage(self) -> None:
        with self.assertRaises(ValueError):
            MemoryUtilization(
                used_estimate_kb=500,
                available_percent=50.0,
                used_estimate_percent=50.0,
                swap_used_kb=10,
                swap_used_percent=None,
                swap_configured=True,
            )


if __name__ == "__main__":
    unittest.main()
