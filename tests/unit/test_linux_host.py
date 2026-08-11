"""Unit tests for Linux-native Sentinel-X host readers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_x.observability import LinuxHostReader
from sentinel_x.observability.linux_host import (
    LinuxObservationParseError,
    LinuxObservationReadError,
)


def _write_valid_sources(root: Path) -> tuple[Path, Path]:
    proc_root = root / "proc"
    proc_root.mkdir()

    (proc_root / "stat").write_text(
        (
            "cpu  100 2 30 400 5 1 4 0 0 0\n"
            "cpu0 50 1 15 200 2 0 2 0 0 0\n"
            "cpu1 50 1 15 200 3 1 2 0 0 0\n"
            "intr 123\n"
        ),
        encoding="utf-8",
    )

    (proc_root / "loadavg").write_text(
        "0.25 0.50 0.75 2/300 12345\n",
        encoding="utf-8",
    )

    os_release_path = root / "os-release"
    os_release_path.write_text(
        (
            'NAME="Fedora Linux"\n'
            'VERSION_ID="42"\n'
            "ID=fedora\n"
            'PRETTY_NAME="Fedora Linux 42 (Workstation Edition)"\n'
        ),
        encoding="utf-8",
    )

    return proc_root, os_release_path


class LinuxHostReaderTests(unittest.TestCase):
    """Tests for parsing Linux host observability sources."""

    def test_read_snapshot_parses_linux_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proc_root, os_release_path = _write_valid_sources(root)

            fake_uname = os.uname_result(
                (
                    "Linux",
                    "fedora-lab",
                    "6.12.0",
                    "#1 SMP PREEMPT_DYNAMIC",
                    "x86_64",
                )
            )

            with patch(
                "sentinel_x.observability.linux_host.os.uname",
                return_value=fake_uname,
            ):
                snapshot = LinuxHostReader(
                    proc_root=proc_root,
                    os_release_path=os_release_path,
                ).read_snapshot()

        self.assertEqual(snapshot.identity.hostname, "fedora-lab")
        self.assertEqual(snapshot.identity.logical_cpu_count, 2)
        self.assertEqual(snapshot.identity.os_id, "fedora")
        self.assertEqual(snapshot.cpu_times.user, 100)
        self.assertEqual(snapshot.cpu_times.idle, 400)
        self.assertEqual(snapshot.load_average.one_minute, 0.25)
        self.assertEqual(snapshot.load_average.runnable_tasks, 2)
        self.assertIsNotNone(snapshot.captured_at.tzinfo)

    def test_missing_proc_stat_is_reported_as_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proc_root = root / "proc"
            proc_root.mkdir()

            os_release_path = root / "os-release"
            os_release_path.write_text("ID=fedora\n", encoding="utf-8")

            reader = LinuxHostReader(
                proc_root=proc_root,
                os_release_path=os_release_path,
            )

            with self.assertRaises(LinuxObservationReadError):
                reader.read_snapshot()

    def test_proc_stat_requires_aggregate_cpu_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proc_root, os_release_path = _write_valid_sources(root)

            (proc_root / "stat").write_text(
                "cpu0 1 2 3 4\n",
                encoding="utf-8",
            )

            reader = LinuxHostReader(
                proc_root=proc_root,
                os_release_path=os_release_path,
            )

            with self.assertRaises(LinuxObservationParseError):
                reader.read_snapshot()

    def test_proc_stat_rejects_invalid_cpu_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proc_root, os_release_path = _write_valid_sources(root)

            (proc_root / "stat").write_text(
                "cpu  1 bad 3 4\ncpu0 1 2 3 4\n",
                encoding="utf-8",
            )

            reader = LinuxHostReader(
                proc_root=proc_root,
                os_release_path=os_release_path,
            )

            with self.assertRaises(LinuxObservationParseError):
                reader.read_snapshot()

    def test_loadavg_rejects_invalid_task_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proc_root, os_release_path = _write_valid_sources(root)

            (proc_root / "loadavg").write_text(
                "0.25 0.50 0.75 invalid 12345\n",
                encoding="utf-8",
            )

            reader = LinuxHostReader(
                proc_root=proc_root,
                os_release_path=os_release_path,
            )

            with self.assertRaises(LinuxObservationParseError):
                reader.read_snapshot()

    def test_os_release_rejects_unterminated_quote(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proc_root, os_release_path = _write_valid_sources(root)

            os_release_path.write_text(
                'PRETTY_NAME="Fedora Linux 42\n',
                encoding="utf-8",
            )

            reader = LinuxHostReader(
                proc_root=proc_root,
                os_release_path=os_release_path,
            )

            with self.assertRaises(LinuxObservationParseError):
                reader.read_snapshot()


if __name__ == "__main__":
    unittest.main()
