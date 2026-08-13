"""Unit tests for bounded Sentinel-X process observation events."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sentinel_x.core import EventBus, EventKind
from sentinel_x.observability import (
    PROCESS_EVENT_MAX_SAMPLES,
    PROCESS_OBSERVATION_SOURCE,
    PROCESS_OBSERVATION_TYPE,
    ProcessIdentity,
    ProcessIo,
    ProcessRecord,
    ProcessSnapshot,
    ProcessStat,
    build_process_observation,
    process_observation_to_event,
)
from sentinel_x.storage import JsonlEventRecorder


_BOOT_ID = "11111111-2222-3333-4444-555555555555"
_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(
    pid: int,
    *,
    user_time_ticks: int,
    resident_pages: int = 1,
    read_bytes: int = 0,
    write_bytes: int = 0,
    start_time_ticks: int | None = None,
) -> ProcessRecord:
    comm = f"worker-{pid}"
    start_ticks = pid * 100 if start_time_ticks is None else start_time_ticks
    stat = ProcessStat(
        pid=pid,
        comm=comm,
        state="S",
        ppid=1,
        process_group_id=pid,
        session_id=pid,
        flags=0,
        minor_faults=10,
        child_minor_faults=0,
        major_faults=1,
        child_major_faults=0,
        user_time_ticks=user_time_ticks,
        system_time_ticks=0,
        child_user_time_ticks=0,
        child_system_time_ticks=0,
        priority=20,
        nice=0,
        thread_count=1,
        start_time_ticks=start_ticks,
        virtual_memory_bytes=1_000_000,
        resident_pages=resident_pages,
    )
    io = ProcessIo(
        rchar=read_bytes,
        wchar=write_bytes,
        syscr=0,
        syscw=0,
        read_bytes=read_bytes,
        write_bytes=write_bytes,
        cancelled_write_bytes=0,
    )

    return ProcessRecord(
        identity=ProcessIdentity(
            boot_id=_BOOT_ID,
            pid=pid,
            start_time_ticks=start_ticks,
            comm=comm,
        ),
        stat=stat,
        status=None,
        io=io,
        partial_failures=(),
    )


def _snapshot(
    processes: tuple[ProcessRecord, ...],
    *,
    offset_seconds: float,
) -> ProcessSnapshot:
    return ProcessSnapshot(
        captured_at=_BASE_TIME + timedelta(seconds=offset_seconds),
        boot_id=_BOOT_ID,
        clock_ticks_per_second=100,
        page_size_bytes=4096,
        discovered_pid_count=len(processes),
        processes=processes,
        dropped_failures=(),
    )


def _one_sample_observation():
    previous = _snapshot(
        (_record(100, user_time_ticks=0),),
        offset_seconds=0.0,
    )
    current = _snapshot(
        (
            _record(
                100,
                user_time_ticks=25,
                resident_pages=50,
                read_bytes=4096,
                write_bytes=8192,
            ),
        ),
        offset_seconds=1.0,
    )

    return build_process_observation(
        previous,
        current,
        sample_interval_seconds=1.0,
        logical_cpu_count=4,
    )


class ProcessObservationEventTests(unittest.TestCase):
    """Tests for bounded process event adaptation and persistence."""

    def test_process_observation_is_converted_to_bounded_typed_event(
        self,
    ) -> None:
        observation = _one_sample_observation()
        event = process_observation_to_event(observation)

        self.assertIs(event.kind, EventKind.OBSERVATION)
        self.assertEqual(event.source, PROCESS_OBSERVATION_SOURCE)
        self.assertEqual(event.occurred_at, observation.captured_at)
        self.assertEqual(
            event.attributes["observation_type"],
            PROCESS_OBSERVATION_TYPE,
        )
        self.assertEqual(event.attributes["summary"]["sampled_count"], 1)
        self.assertFalse(event.attributes["projection"]["truncated"])

        samples = event.attributes["samples"]

        self.assertIsInstance(samples, list)

        assert isinstance(samples, list)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["identity"]["pid"], 100)
        self.assertIn("top_cpu", samples[0]["selection_reasons"])
        self.assertIsNotNone(samples[0]["start_record"])
        self.assertIsNotNone(samples[0]["end_record"])

    def test_projection_caps_large_lifecycle_burst(self) -> None:
        previous = _snapshot((), offset_seconds=0.0)
        current = _snapshot(
            tuple(_record(pid, user_time_ticks=0) for pid in range(100, 160)),
            offset_seconds=1.0,
        )
        observation = build_process_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
            logical_cpu_count=4,
        )
        event = process_observation_to_event(observation)
        projection = event.attributes["projection"]
        samples = event.attributes["samples"]

        self.assertEqual(len(samples), PROCESS_EVENT_MAX_SAMPLES)
        self.assertEqual(
            projection["selected_sample_count"],
            PROCESS_EVENT_MAX_SAMPLES,
        )
        self.assertEqual(projection["omitted_sample_count"], 12)
        self.assertEqual(projection["exceptional_candidate_count"], 60)
        self.assertTrue(projection["truncated"])
        self.assertEqual(samples[0]["identity"]["pid"], 100)
        self.assertEqual(samples[-1]["identity"]["pid"], 147)
        self.assertEqual(
            samples[0]["selection_reasons"],
            ["status:started"],
        )

    def test_projection_preserves_distinct_resource_leaders(self) -> None:
        previous_records = tuple(
            _record(pid, user_time_ticks=0) for pid in range(100, 120)
        )
        current_records = []

        for pid in range(100, 120):
            current_records.append(
                _record(
                    pid,
                    user_time_ticks=1000 if pid == 100 else 1,
                    resident_pages=100_000 if pid == 119 else 1,
                    read_bytes=1_000_000 if pid == 118 else 0,
                )
            )

        observation = build_process_observation(
            _snapshot(previous_records, offset_seconds=0.0),
            _snapshot(tuple(current_records), offset_seconds=1.0),
            sample_interval_seconds=1.0,
            logical_cpu_count=4,
        )
        event = process_observation_to_event(observation)
        selected = {
            sample["identity"]["pid"]: sample for sample in event.attributes["samples"]
        }

        self.assertIn("top_cpu", selected[100]["selection_reasons"])
        self.assertIn("top_memory", selected[119]["selection_reasons"])
        self.assertNotIn("top_cpu", selected[119]["selection_reasons"])
        self.assertIn("top_io", selected[118]["selection_reasons"])
        self.assertNotIn("top_cpu", selected[118]["selection_reasons"])

    def test_process_observation_event_is_persisted_through_event_bus(
        self,
    ) -> None:
        event = process_observation_to_event(_one_sample_observation())

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
            PROCESS_OBSERVATION_TYPE,
        )
        self.assertEqual(attributes["summary"]["sampled_count"], 1)
        self.assertEqual(attributes["projection"]["selected_sample_count"], 1)
        self.assertEqual(len(attributes["samples"]), 1)
        self.assertEqual(attributes["samples"][0]["status"], "sampled")


if __name__ == "__main__":
    unittest.main()
