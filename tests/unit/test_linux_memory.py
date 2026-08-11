"""Unit tests for Sentinel-X Linux memory observability."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sentinel_x.observability import LinuxHostReader
from sentinel_x.observability.linux_host import LinuxObservationParseError


def _valid_meminfo() -> str:
    return (
        "MemTotal:       16384000 kB\n"
        "MemFree:         2048000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "Buffers:          256000 kB\n"
        "Cached:          4096000 kB\n"
        "SwapCached:        12000 kB\n"
        "Active:          5000000 kB\n"
        "Inactive:        3000000 kB\n"
        "Shmem:            128000 kB\n"
        "SReclaimable:      64000 kB\n"
        "SwapTotal:       8388608 kB\n"
        "SwapFree:        7340032 kB\n"
    )


def _reader_for_meminfo(root: Path, text: str) -> LinuxHostReader:
    proc_root = root / "proc"
    proc_root.mkdir()
    (proc_root / "meminfo").write_text(text, encoding="utf-8")

    return LinuxHostReader(proc_root=proc_root)


class LinuxMemoryReaderTests(unittest.TestCase):
    """Tests for parsing selected /proc/meminfo counters."""

    def test_read_memory_stats_parses_required_and_optional_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), _valid_meminfo())
            stats = reader.read_memory_stats()

        self.assertEqual(stats.mem_total_kb, 16384000)
        self.assertEqual(stats.mem_available_kb, 8192000)
        self.assertEqual(stats.cached_kb, 4096000)
        self.assertEqual(stats.swap_total_kb, 8388608)
        self.assertEqual(stats.sreclaimable_kb, 64000)
        self.assertEqual(stats.shmem_kb, 128000)
        self.assertEqual(stats.swap_cached_kb, 12000)
        self.assertEqual(stats.to_dict()["unit"], "kB")

    def test_zero_swap_configuration_is_valid(self) -> None:
        text = _valid_meminfo().replace(
            "SwapTotal:       8388608 kB\nSwapFree:        7340032 kB\n",
            "SwapTotal:             0 kB\nSwapFree:              0 kB\n",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), text)
            stats = reader.read_memory_stats()

        self.assertEqual(stats.swap_total_kb, 0)
        self.assertEqual(stats.swap_free_kb, 0)

    def test_missing_required_field_is_rejected(self) -> None:
        text = _valid_meminfo().replace("MemAvailable:    8192000 kB\n", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), text)

            with self.assertRaises(LinuxObservationParseError):
                reader.read_memory_stats()

    def test_wrong_unit_is_rejected(self) -> None:
        text = _valid_meminfo().replace(
            "MemTotal:       16384000 kB",
            "MemTotal:       16384000 MB",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), text)

            with self.assertRaises(LinuxObservationParseError):
                reader.read_memory_stats()

    def test_non_integer_value_is_rejected(self) -> None:
        text = _valid_meminfo().replace(
            "MemFree:         2048000 kB",
            "MemFree:             bad kB",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), text)

            with self.assertRaises(LinuxObservationParseError):
                reader.read_memory_stats()

    def test_negative_value_is_rejected(self) -> None:
        text = _valid_meminfo().replace(
            "Buffers:          256000 kB",
            "Buffers:              -1 kB",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), text)

            with self.assertRaises(LinuxObservationParseError):
                reader.read_memory_stats()

    def test_duplicate_selected_field_is_rejected(self) -> None:
        text = _valid_meminfo() + "MemTotal:       16384000 kB\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), text)

            with self.assertRaises(LinuxObservationParseError):
                reader.read_memory_stats()

    def test_inconsistent_memory_values_are_rejected(self) -> None:
        text = _valid_meminfo().replace(
            "MemAvailable:    8192000 kB",
            "MemAvailable:   20000000 kB",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader_for_meminfo(Path(tmpdir), text)

            with self.assertRaises(LinuxObservationParseError):
                reader.read_memory_stats()


if __name__ == "__main__":
    unittest.main()
