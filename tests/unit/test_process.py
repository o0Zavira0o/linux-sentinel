"""Unit tests for Sentinel-X Linux process observability."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sentinel_x.observability.process import (
    LinuxProcessReader,
    ProcessDiscoveryError,
    ProcessProbeStage,
    ProcessSnapshot,
    _parse_process_stat,
)


_BOOT_ID = "12345678-1234-5678-1234-567812345678"


def _stat_line(
    *,
    pid: int = 100,
    comm: str = "worker",
    state: str = "S",
    ppid: int = 1,
    start_time_ticks: int = 12_345,
    user_time_ticks: int = 100,
    system_time_ticks: int = 50,
    virtual_memory_bytes: int = 1_048_576,
    resident_pages: int = 256,
) -> str:
    tail = (
        state,
        str(ppid),
        str(pid),
        str(pid),
        "0",
        "-1",
        "4194304",
        "10",
        "0",
        "2",
        "0",
        str(user_time_ticks),
        str(system_time_ticks),
        "0",
        "0",
        "20",
        "0",
        "2",
        "0",
        str(start_time_ticks),
        str(virtual_memory_bytes),
        str(resident_pages),
    )

    return f"{pid} ({comm}) {' '.join(tail)}\n"


def _status_text(
    *,
    pid: int = 100,
    ppid: int = 1,
    name: str = "worker",
    state: str = "S (sleeping)",
    include_memory: bool = True,
) -> str:
    lines = [
        f"Name:\t{name}",
        f"State:\t{state}",
        f"Tgid:\t{pid}",
        f"Pid:\t{pid}",
        f"PPid:\t{ppid}",
        "Uid:\t1000\t1001\t1002\t1003",
        "Gid:\t2000\t2001\t2002\t2003",
        f"NSpid:\t{pid}\t{pid}",
        "Kthread:\t0",
        "Threads:\t2",
    ]

    if include_memory:
        lines.extend(
            [
                "VmSize:\t1024 kB",
                "VmRSS:\t512 kB",
                "RssAnon:\t256 kB",
                "RssFile:\t192 kB",
                "RssShmem:\t64 kB",
                "VmSwap:\t32 kB",
            ]
        )

    lines.extend(
        [
            "voluntary_ctxt_switches:\t7",
            "nonvoluntary_ctxt_switches:\t3",
        ]
    )

    return "\n".join(lines) + "\n"


def _io_text() -> str:
    return (
        "rchar: 10000\n"
        "wchar: 20000\n"
        "syscr: 100\n"
        "syscw: 200\n"
        "read_bytes: 4096\n"
        "write_bytes: 8192\n"
        "cancelled_write_bytes: 1024\n"
    )


def _create_reader_root(root: Path, *, boot_id: str = _BOOT_ID) -> LinuxProcessReader:
    proc_root = root / "proc"
    boot_id_path = root / "boot_id"

    proc_root.mkdir(parents=True)
    boot_id_path.write_text(f"{boot_id}\n", encoding="utf-8")

    return LinuxProcessReader(
        proc_root=proc_root,
        boot_id_path=boot_id_path,
        clock_ticks_per_second=100,
        page_size_bytes=4096,
    )


def _write_process(
    root: Path,
    *,
    pid: int = 100,
    stat_text: str | None = None,
    status_text: str | None = None,
    io_text: str | None = None,
) -> Path:
    process_root = root / "proc" / str(pid)
    process_root.mkdir(parents=True)

    if stat_text is not None:
        (process_root / "stat").write_text(stat_text, encoding="utf-8")

    if status_text is not None:
        (process_root / "status").write_text(status_text, encoding="utf-8")

    if io_text is not None:
        (process_root / "io").write_text(io_text, encoding="utf-8")

    return process_root


class LinuxProcessReaderTests(unittest.TestCase):
    """Tests for identity-safe raw Linux process collection."""

    def test_collects_identity_status_and_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text=_status_text(),
                io_text=_io_text(),
            )

            snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.discovered_pid_count, 1)
        self.assertEqual(snapshot.process_count, 1)
        self.assertEqual(snapshot.dropped_process_count, 0)
        self.assertEqual(snapshot.partial_failure_count, 0)
        self.assertEqual(snapshot.clock_ticks_per_second, 100)
        self.assertEqual(snapshot.page_size_bytes, 4096)

        process = snapshot.processes[0]

        self.assertEqual(process.identity.pid, 100)
        self.assertEqual(process.identity.start_time_ticks, 12_345)
        self.assertEqual(process.identity.boot_id, _BOOT_ID)
        self.assertIn(":100:12345", process.identity.identity_key)
        self.assertTrue(process.status_available)
        self.assertTrue(process.io_available)

        assert process.status is not None
        assert process.io is not None

        self.assertEqual(process.status.effective_uid, 1001)
        self.assertEqual(process.status.vm_rss_kb, 512)
        self.assertEqual(process.status.namespace_pids, (100, 100))
        self.assertFalse(process.status.kernel_thread)
        self.assertEqual(process.io.read_bytes, 4096)
        self.assertEqual(process.io.cancelled_write_bytes, 1024)

    def test_stat_parser_handles_spaces_and_closing_parenthesis_in_comm(self) -> None:
        stat = _parse_process_stat(
            _stat_line(
                comm="worker ) demo",
            )
        )

        self.assertEqual(stat.comm, "worker ) demo")
        self.assertEqual(stat.pid, 100)
        self.assertEqual(stat.state, "S")

    def test_only_ascii_numeric_proc_entries_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)

            _write_process(
                root,
                pid=100,
                stat_text=_stat_line(),
                status_text=_status_text(),
                io_text=_io_text(),
            )

            for name in ("self", "thread-self", "net", "abc123", "١٢٣"):
                (root / "proc" / name).mkdir()

            snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.discovered_pid_count, 1)
        self.assertEqual(snapshot.process_count, 1)

    def test_missing_initial_stat_drops_process_without_aborting_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(root)

            snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.discovered_pid_count, 1)
        self.assertEqual(snapshot.process_count, 0)
        self.assertEqual(snapshot.dropped_process_count, 1)
        self.assertEqual(len(snapshot.dropped_failures), 1)
        self.assertIs(
            snapshot.dropped_failures[0].stage,
            ProcessProbeStage.STAT_INITIAL,
        )

    def test_stat_pid_mismatch_drops_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                pid=100,
                stat_text=_stat_line(pid=101),
            )

            snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.process_count, 0)
        self.assertEqual(snapshot.dropped_process_count, 1)
        self.assertEqual(
            snapshot.dropped_failures[0].error_type,
            "ProcessParseError",
        )

    def test_missing_status_is_partial_failure_and_record_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                io_text=_io_text(),
            )

            snapshot = reader.read_snapshot()

        process = snapshot.processes[0]

        self.assertFalse(process.status_available)
        self.assertTrue(process.io_available)
        self.assertEqual(snapshot.status_unavailable_count, 1)
        self.assertEqual(snapshot.partial_failure_count, 1)
        self.assertIs(process.partial_failures[0].stage, ProcessProbeStage.STATUS)

    def test_missing_io_is_partial_failure_and_record_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text=_status_text(),
            )

            snapshot = reader.read_snapshot()

        process = snapshot.processes[0]

        self.assertTrue(process.status_available)
        self.assertFalse(process.io_available)
        self.assertEqual(snapshot.io_unavailable_count, 1)
        self.assertEqual(snapshot.partial_failure_count, 1)
        self.assertIs(process.partial_failures[0].stage, ProcessProbeStage.IO)

    def test_malformed_status_isolated_as_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text="Name:\tworker\nPid:\t100\n",
                io_text=_io_text(),
            )

            snapshot = reader.read_snapshot()

        process = snapshot.processes[0]

        self.assertIsNone(process.status)
        self.assertIsNotNone(process.io)
        self.assertEqual(process.partial_failures[0].error_type, "ProcessParseError")

    def test_status_pid_mismatch_isolated_as_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text=_status_text(pid=101),
                io_text=_io_text(),
            )

            snapshot = reader.read_snapshot()

        process = snapshot.processes[0]

        self.assertIsNone(process.status)
        self.assertEqual(process.partial_failures[0].error_type, "ProcessParseError")

    def test_malformed_io_isolated_as_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text=_status_text(),
                io_text="rchar: 1\n",
            )

            snapshot = reader.read_snapshot()

        process = snapshot.processes[0]

        self.assertIsNotNone(process.status)
        self.assertIsNone(process.io)
        self.assertEqual(process.partial_failures[0].error_type, "ProcessParseError")

    def test_zombie_status_without_memory_fields_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(state="Z"),
                status_text=_status_text(
                    state="Z (zombie)",
                    include_memory=False,
                ),
                io_text=_io_text(),
            )

            snapshot = reader.read_snapshot()

        process = snapshot.processes[0]

        self.assertEqual(snapshot.zombie_count, 1)
        self.assertIsNotNone(process.status)

        assert process.status is not None

        self.assertIsNone(process.status.vm_size_kb)
        self.assertIsNone(process.status.vm_rss_kb)
        self.assertIsNone(process.status.vm_swap_kb)

    def test_pid_reuse_during_probe_drops_mixed_record(self) -> None:
        initial_stat = _parse_process_stat(
            _stat_line(
                start_time_ticks=12_345,
            )
        )
        replacement_stat = _parse_process_stat(
            _stat_line(
                start_time_ticks=99_999,
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text=_status_text(),
                io_text=_io_text(),
            )

            with patch(
                "sentinel_x.observability.process._read_process_stat",
                side_effect=(initial_stat, replacement_stat),
            ):
                snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.process_count, 0)
        self.assertEqual(snapshot.dropped_process_count, 1)
        self.assertIs(
            snapshot.dropped_failures[0].stage,
            ProcessProbeStage.IDENTITY_VERIFY,
        )
        self.assertEqual(
            snapshot.dropped_failures[0].error_type,
            "ProcessIdentityChanged",
        )

    def test_invalid_boot_id_is_fatal_for_snapshot_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root, boot_id="not-a-uuid")

            with self.assertRaises(ProcessDiscoveryError):
                reader.read_snapshot()

    def test_missing_proc_root_is_reported_as_discovery_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            boot_id_path = root / "boot_id"
            boot_id_path.write_text(f"{_BOOT_ID}\n", encoding="utf-8")

            reader = LinuxProcessReader(
                proc_root=root / "missing-proc",
                boot_id_path=boot_id_path,
                clock_ticks_per_second=100,
                page_size_bytes=4096,
            )

            with self.assertRaises(ProcessDiscoveryError):
                reader.read_snapshot()

    def test_snapshot_serialization_preserves_raw_evidence_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text=_status_text(),
                io_text=_io_text(),
            )

            payload = reader.read_snapshot().to_dict()

        self.assertEqual(
            payload["summary"],
            {
                "discovered_pid_count": 1,
                "process_count": 1,
                "dropped_process_count": 0,
                "partial_failure_count": 0,
                "status_unavailable_count": 0,
                "io_unavailable_count": 0,
                "zombie_count": 0,
            },
        )

        processes = payload["processes"]

        self.assertIsInstance(processes, list)

        assert isinstance(processes, list)

        self.assertEqual(processes[0]["identity"]["pid"], 100)
        self.assertEqual(processes[0]["stat"]["user_time_ticks"], 100)
        self.assertEqual(processes[0]["status"]["memory_kb"]["vm_rss"], 512)
        self.assertEqual(processes[0]["io"]["write_bytes"], 8192)

    def test_snapshot_rejects_duplicate_process_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _create_reader_root(root)
            _write_process(
                root,
                stat_text=_stat_line(),
                status_text=_status_text(),
                io_text=_io_text(),
            )

            snapshot = reader.read_snapshot()

        process = snapshot.processes[0]

        with self.assertRaises(ValueError):
            ProcessSnapshot(
                captured_at=datetime.now(timezone.utc),
                boot_id=snapshot.boot_id,
                clock_ticks_per_second=100,
                page_size_bytes=4096,
                discovered_pid_count=2,
                processes=(process, process),
                dropped_failures=(),
            )


if __name__ == "__main__":
    unittest.main()
