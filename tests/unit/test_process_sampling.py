"""Unit tests for Sentinel-X sampled process resource metrics."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sentinel_x.observability import (
    ProcessIdentity,
    ProcessIo,
    ProcessProbeFailure,
    ProcessProbeStage,
    ProcessRecord,
    ProcessSampleStatus,
    ProcessSamplingError,
    ProcessSnapshot,
    ProcessStat,
    ProcessStatus,
    build_process_observation,
)


_BOOT_ID = "11111111-2222-3333-4444-555555555555"
_OTHER_BOOT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _stat(
    *,
    pid: int = 100,
    comm: str = "worker",
    state: str = "S",
    ppid: int = 1,
    user_time_ticks: int = 100,
    system_time_ticks: int = 50,
    minor_faults: int = 10,
    major_faults: int = 2,
    start_time_ticks: int = 1000,
    virtual_memory_bytes: int = 1_000_000,
    resident_pages: int = 40,
    thread_count: int = 2,
) -> ProcessStat:
    return ProcessStat(
        pid=pid,
        comm=comm,
        state=state,
        ppid=ppid,
        process_group_id=pid,
        session_id=pid,
        flags=0,
        minor_faults=minor_faults,
        child_minor_faults=0,
        major_faults=major_faults,
        child_major_faults=0,
        user_time_ticks=user_time_ticks,
        system_time_ticks=system_time_ticks,
        child_user_time_ticks=0,
        child_system_time_ticks=0,
        priority=20,
        nice=0,
        thread_count=thread_count,
        start_time_ticks=start_time_ticks,
        virtual_memory_bytes=virtual_memory_bytes,
        resident_pages=resident_pages,
    )


def _status(
    *,
    pid: int = 100,
    name: str = "worker",
    state: str = "S",
    ppid: int = 1,
    effective_uid: int = 1000,
    effective_gid: int = 1000,
    vm_size_kb: int | None = 1000,
    vm_rss_kb: int | None = 200,
    rss_anon_kb: int | None = 100,
    rss_file_kb: int | None = 80,
    rss_shmem_kb: int | None = 20,
    vm_swap_kb: int | None = 0,
    voluntary_context_switches: int | None = 20,
    nonvoluntary_context_switches: int | None = 10,
) -> ProcessStatus:
    return ProcessStatus(
        name=name,
        state=state,
        tgid=pid,
        pid=pid,
        ppid=ppid,
        real_uid=1000,
        effective_uid=effective_uid,
        saved_uid=1000,
        filesystem_uid=1000,
        real_gid=1000,
        effective_gid=effective_gid,
        saved_gid=1000,
        filesystem_gid=1000,
        thread_count=2,
        namespace_pids=(pid,),
        kernel_thread=False,
        vm_size_kb=vm_size_kb,
        vm_rss_kb=vm_rss_kb,
        rss_anon_kb=rss_anon_kb,
        rss_file_kb=rss_file_kb,
        rss_shmem_kb=rss_shmem_kb,
        vm_swap_kb=vm_swap_kb,
        voluntary_context_switches=voluntary_context_switches,
        nonvoluntary_context_switches=nonvoluntary_context_switches,
    )


def _io(
    *,
    rchar: int = 1000,
    wchar: int = 2000,
    syscr: int = 10,
    syscw: int = 20,
    read_bytes: int = 4096,
    write_bytes: int = 8192,
    cancelled_write_bytes: int = 512,
) -> ProcessIo:
    return ProcessIo(
        rchar=rchar,
        wchar=wchar,
        syscr=syscr,
        syscw=syscw,
        read_bytes=read_bytes,
        write_bytes=write_bytes,
        cancelled_write_bytes=cancelled_write_bytes,
    )


def _record(
    *,
    pid: int = 100,
    comm: str = "worker",
    boot_id: str = _BOOT_ID,
    start_time_ticks: int = 1000,
    stat: ProcessStat | None = None,
    status: ProcessStatus | None = None,
    io: ProcessIo | None = None,
) -> ProcessRecord:
    process_stat = stat or _stat(
        pid=pid,
        comm=comm,
        start_time_ticks=start_time_ticks,
    )
    process_status = status if status is not None else _status(pid=pid, name=comm)
    process_io = io if io is not None else _io()

    return ProcessRecord(
        identity=ProcessIdentity(
            boot_id=boot_id,
            pid=pid,
            start_time_ticks=start_time_ticks,
            comm=comm,
        ),
        stat=process_stat,
        status=process_status,
        io=process_io,
        partial_failures=(),
    )


def _failure(pid: int) -> ProcessProbeFailure:
    return ProcessProbeFailure(
        pid=pid,
        stage=ProcessProbeStage.STAT_INITIAL,
        error_type="ProcessReadError",
        error_message="process disappeared during probe",
    )


def _snapshot(
    processes: tuple[ProcessRecord, ...],
    *,
    offset_seconds: float,
    boot_id: str = _BOOT_ID,
    clock_ticks_per_second: int = 100,
    page_size_bytes: int = 4096,
    dropped_failures: tuple[ProcessProbeFailure, ...] = (),
) -> ProcessSnapshot:
    return ProcessSnapshot(
        captured_at=_BASE_TIME + timedelta(seconds=offset_seconds),
        boot_id=boot_id,
        clock_ticks_per_second=clock_ticks_per_second,
        page_size_bytes=page_size_bytes,
        discovered_pid_count=len(processes) + len(dropped_failures),
        processes=processes,
        dropped_failures=dropped_failures,
    )


def _build(
    previous: ProcessSnapshot,
    current: ProcessSnapshot,
    *,
    interval: object = 2.0,
    logical_cpu_count: object = 4,
):
    return build_process_observation(
        previous,
        current,
        sample_interval_seconds=interval,
        logical_cpu_count=logical_cpu_count,
    )


class ProcessSamplingTests(unittest.TestCase):
    """Tests for process lifecycle, identity, and sampled resource metrics."""

    def test_calculates_cpu_fault_context_and_io_metrics(self) -> None:
        previous_record = _record()
        current_record = _record(
            stat=_stat(
                user_time_ticks=140,
                system_time_ticks=70,
                minor_faults=16,
                major_faults=4,
            ),
            status=_status(
                voluntary_context_switches=26,
                nonvoluntary_context_switches=14,
            ),
            io=_io(
                rchar=1200,
                wchar=2400,
                syscr=14,
                syscw=26,
                read_bytes=6144,
                write_bytes=12_288,
                cancelled_write_bytes=768,
            ),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]
        metrics = sample.metrics

        self.assertIs(sample.status, ProcessSampleStatus.SAMPLED)
        self.assertIsNotNone(metrics)

        assert metrics is not None
        assert metrics.context_switches is not None
        assert metrics.io is not None

        self.assertEqual(metrics.cpu.user_time_ticks_delta, 40)
        self.assertEqual(metrics.cpu.system_time_ticks_delta, 20)
        self.assertEqual(metrics.cpu.total_time_ticks_delta, 60)
        self.assertAlmostEqual(metrics.cpu.total_time_seconds, 0.6)
        self.assertAlmostEqual(metrics.cpu.single_core_equivalent_percent, 30.0)
        self.assertAlmostEqual(metrics.cpu.host_capacity_percent, 7.5)
        self.assertEqual(metrics.faults.minor_faults_delta, 6)
        self.assertEqual(metrics.faults.major_faults_delta, 2)
        self.assertAlmostEqual(metrics.faults.minor_faults_per_second, 3.0)
        self.assertEqual(metrics.context_switches.total_delta, 10)
        self.assertAlmostEqual(metrics.context_switches.total_per_second, 5.0)
        self.assertEqual(metrics.io.rchar_delta, 200)
        self.assertEqual(metrics.io.write_bytes_delta, 4096)
        self.assertAlmostEqual(metrics.io.rchar_per_second, 100.0)

    def test_single_core_equivalent_percent_is_not_clamped(self) -> None:
        previous_record = _record(
            stat=_stat(user_time_ticks=0, system_time_ticks=0),
        )
        current_record = _record(
            stat=_stat(user_time_ticks=250, system_time_ticks=50),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=1.0),
            interval=1.0,
            logical_cpu_count=4,
        )

        metrics = observation.samples[0].metrics

        assert metrics is not None

        self.assertAlmostEqual(metrics.cpu.single_core_equivalent_percent, 300.0)
        self.assertAlmostEqual(metrics.cpu.host_capacity_percent, 75.0)

    def test_started_and_exited_processes_are_preserved(self) -> None:
        stable_start = _record(pid=100)
        stable_end = _record(
            pid=100,
            stat=_stat(pid=100, user_time_ticks=110),
            status=_status(pid=100),
        )
        exited = _record(pid=200, comm="old")
        started = _record(pid=300, comm="new")

        observation = _build(
            _snapshot((stable_start, exited), offset_seconds=0.0),
            _snapshot((stable_end, started), offset_seconds=2.0),
        )

        statuses = {
            sample.identity.pid: sample.status for sample in observation.samples
        }

        self.assertIs(statuses[100], ProcessSampleStatus.SAMPLED)
        self.assertIs(statuses[200], ProcessSampleStatus.EXITED)
        self.assertIs(statuses[300], ProcessSampleStatus.STARTED)
        self.assertEqual(observation.started_count, 1)
        self.assertEqual(observation.exited_count, 1)

    def test_same_pid_with_new_start_time_marks_pid_reuse(self) -> None:
        previous_record = _record(pid=100, start_time_ticks=1000)
        current_record = _record(pid=100, start_time_ticks=9000, comm="replacement")

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]

        self.assertIs(sample.status, ProcessSampleStatus.PID_REUSED)
        self.assertIsNone(sample.metrics)
        self.assertEqual(observation.pid_reused_count, 1)

    def test_dropped_start_endpoint_marks_start_unverified(self) -> None:
        failure = _failure(100)
        current_record = _record(pid=100)

        observation = _build(
            _snapshot((), offset_seconds=0.0, dropped_failures=(failure,)),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]

        self.assertIs(sample.status, ProcessSampleStatus.START_UNVERIFIED)
        self.assertEqual(sample.missing_endpoint_failure, failure)
        self.assertEqual(observation.start_unverified_count, 1)

    def test_dropped_end_endpoint_marks_exit_unverified(self) -> None:
        failure = _failure(100)
        previous_record = _record(pid=100)

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((), offset_seconds=2.0, dropped_failures=(failure,)),
        )

        sample = observation.samples[0]

        self.assertIs(sample.status, ProcessSampleStatus.EXIT_UNVERIFIED)
        self.assertEqual(sample.missing_endpoint_failure, failure)
        self.assertEqual(observation.exit_unverified_count, 1)

    def test_cpu_counter_regression_marks_core_counter_reset(self) -> None:
        previous_record = _record()
        current_record = _record(
            stat=_stat(user_time_ticks=99),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]

        self.assertIs(sample.status, ProcessSampleStatus.COUNTER_RESET)
        self.assertEqual(sample.core_regressed_fields, ("user_time_ticks",))
        self.assertIsNone(sample.metrics)

    def test_fault_counter_regression_marks_core_counter_reset(self) -> None:
        previous_record = _record()
        current_record = _record(
            stat=_stat(minor_faults=9),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]

        self.assertIs(sample.status, ProcessSampleStatus.COUNTER_RESET)
        self.assertEqual(sample.core_regressed_fields, ("minor_faults",))

    def test_missing_io_preserves_core_metrics_without_io_metrics(self) -> None:
        previous_record = replace(_record(), io=None)
        current_record = _record(
            stat=_stat(user_time_ticks=110),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]
        metrics = sample.metrics

        self.assertIs(sample.status, ProcessSampleStatus.SAMPLED)
        self.assertIsNotNone(metrics)

        assert metrics is not None

        self.assertIsNone(metrics.io)
        self.assertEqual(sample.io_regressed_fields, ())
        self.assertEqual(observation.io_metrics_unavailable_count, 1)

    def test_io_regression_isolated_without_invalidating_cpu_metrics(self) -> None:
        previous_record = _record()
        current_record = _record(
            stat=_stat(user_time_ticks=110),
            io=_io(read_bytes=4095),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]
        metrics = sample.metrics

        self.assertIs(sample.status, ProcessSampleStatus.SAMPLED)
        self.assertIsNotNone(metrics)

        assert metrics is not None

        self.assertIsNone(metrics.io)
        self.assertEqual(sample.io_regressed_fields, ("read_bytes",))

    def test_context_switch_metrics_are_sampled_independently(self) -> None:
        previous_record = _record()
        current_record = _record(
            status=_status(
                voluntary_context_switches=30,
                nonvoluntary_context_switches=12,
            ),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        metrics = observation.samples[0].metrics

        assert metrics is not None
        assert metrics.context_switches is not None

        self.assertEqual(metrics.context_switches.voluntary_delta, 10)
        self.assertEqual(metrics.context_switches.nonvoluntary_delta, 2)
        self.assertAlmostEqual(metrics.context_switches.total_per_second, 6.0)

    def test_context_switch_regression_isolated_from_core_metrics(self) -> None:
        previous_record = _record()
        current_record = _record(
            stat=_stat(user_time_ticks=110),
            status=_status(voluntary_context_switches=19),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]
        metrics = sample.metrics

        assert metrics is not None

        self.assertIs(sample.status, ProcessSampleStatus.SAMPLED)
        self.assertIsNone(metrics.context_switches)
        self.assertEqual(
            sample.context_regressed_fields,
            ("voluntary_context_switches",),
        )

    def test_memory_metrics_use_current_stat_and_optional_status(self) -> None:
        previous_record = _record()
        current_record = _record(
            stat=_stat(
                virtual_memory_bytes=2_000_000,
                resident_pages=50,
                thread_count=3,
            ),
            status=_status(
                vm_size_kb=2000,
                vm_rss_kb=250,
                rss_anon_kb=120,
                rss_file_kb=100,
                rss_shmem_kb=30,
                vm_swap_kb=10,
            ),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        metrics = observation.samples[0].metrics

        assert metrics is not None

        self.assertEqual(metrics.memory.virtual_memory_bytes, 2_000_000)
        self.assertEqual(metrics.memory.resident_pages, 50)
        self.assertEqual(metrics.memory.resident_memory_bytes, 50 * 4096)
        self.assertEqual(metrics.memory.vm_rss_kb, 250)
        self.assertEqual(metrics.memory.vm_swap_kb, 10)
        self.assertEqual(metrics.memory.thread_count, 3)

    def test_metadata_changes_are_preserved_for_same_identity(self) -> None:
        previous_record = _record()
        current_record = _record(
            comm="renamed",
            stat=_stat(
                comm="renamed",
                state="R",
                ppid=2,
                user_time_ticks=110,
            ),
            status=_status(
                name="renamed",
                state="R",
                ppid=2,
                effective_uid=2000,
                effective_gid=3000,
            ),
        )

        observation = _build(
            _snapshot((previous_record,), offset_seconds=0.0),
            _snapshot((current_record,), offset_seconds=2.0),
        )

        sample = observation.samples[0]

        self.assertIs(sample.status, ProcessSampleStatus.SAMPLED)
        self.assertTrue(sample.comm_changed)
        self.assertTrue(sample.state_changed)
        self.assertTrue(sample.ppid_changed)
        self.assertTrue(sample.effective_uid_changed)
        self.assertTrue(sample.effective_gid_changed)
        self.assertEqual(observation.comm_changed_count, 1)

    def test_boot_id_mismatch_is_rejected(self) -> None:
        previous_record = _record()
        current_record = _record(boot_id=_OTHER_BOOT_ID)

        previous = _snapshot((previous_record,), offset_seconds=0.0)
        current = _snapshot(
            (current_record,),
            offset_seconds=2.0,
            boot_id=_OTHER_BOOT_ID,
        )

        with self.assertRaises(ProcessSamplingError):
            _build(previous, current)

    def test_snapshot_accounting_mismatch_is_rejected(self) -> None:
        previous_record = _record()
        current_record = _record()
        previous = _snapshot((previous_record,), offset_seconds=0.0)

        mismatched_snapshots = (
            _snapshot(
                (current_record,),
                offset_seconds=2.0,
                clock_ticks_per_second=250,
            ),
            _snapshot(
                (current_record,),
                offset_seconds=2.0,
                page_size_bytes=8192,
            ),
        )

        for current in mismatched_snapshots:
            with self.subTest(current=current):
                with self.assertRaises(ProcessSamplingError):
                    _build(previous, current)

    def test_invalid_interval_and_logical_cpu_count_are_rejected(self) -> None:
        previous = _snapshot((_record(),), offset_seconds=0.0)
        current = _snapshot((_record(),), offset_seconds=2.0)

        invalid_intervals: tuple[object, ...] = (
            True,
            0,
            -1.0,
            math.inf,
            math.nan,
            "1",
        )

        for interval in invalid_intervals:
            with self.subTest(interval=interval):
                with self.assertRaises(ProcessSamplingError):
                    _build(previous, current, interval=interval)

        invalid_cpu_counts: tuple[object, ...] = (True, 0, -1, 1.0, "4")

        for cpu_count in invalid_cpu_counts:
            with self.subTest(cpu_count=cpu_count):
                with self.assertRaises(ProcessSamplingError):
                    _build(previous, current, logical_cpu_count=cpu_count)

    def test_serialization_preserves_summary_raw_endpoints_and_uncertainty(
        self,
    ) -> None:
        failure = _failure(200)
        previous_stable = _record(pid=100)
        current_stable = _record(
            pid=100,
            stat=_stat(pid=100, user_time_ticks=110),
            status=_status(pid=100),
        )
        current_uncertain = _record(pid=200, comm="uncertain")

        observation = _build(
            _snapshot(
                (previous_stable,),
                offset_seconds=0.0,
                dropped_failures=(failure,),
            ),
            _snapshot(
                (current_stable, current_uncertain),
                offset_seconds=2.0,
            ),
        )

        payload = observation.to_dict()
        summary = payload["summary"]
        samples = payload["samples"]

        self.assertEqual(summary["sampled_count"], 1)
        self.assertEqual(summary["start_unverified_count"], 1)
        self.assertEqual(summary["start_dropped_process_count"], 1)
        self.assertIsInstance(samples, list)

        assert isinstance(samples, list)

        uncertain = next(
            sample for sample in samples if sample["identity"]["pid"] == 200
        )

        self.assertEqual(uncertain["status"], "start_unverified")
        self.assertIsNone(uncertain["start_record"])
        self.assertIsNotNone(uncertain["end_record"])
        self.assertIsNotNone(uncertain["missing_endpoint_failure"])


if __name__ == "__main__":
    unittest.main()
