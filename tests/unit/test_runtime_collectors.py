from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.observability.models import (
    CpuTimes,
    HostIdentity,
    HostSnapshot,
    LoadAverage,
    MemoryStats,
)
from sentinel_x.observability.runtime_collectors import (
    BuiltinHostCollectors,
    ObservationEmission,
    ObservationEmissionStatus,
    PointInTimeEventCollector,
    RuntimeCollectorClockError,
    RuntimeCollectorContractError,
    SuccessiveSnapshotEventCollector,
    build_builtin_host_collectors,
)


class _Clock:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


class _SequenceReader:
    def __init__(self, *values: object) -> None:
        self._values = iter(values)

    def read(self) -> object:
        value = next(self._values)
        if isinstance(value, Exception):
            raise value
        return value


class _HostReader:
    def __init__(
        self,
        snapshots: tuple[HostSnapshot, ...] = (),
        memory_stats: MemoryStats | None = None,
    ) -> None:
        self._snapshots = iter(snapshots)
        self._memory_stats = memory_stats

    def read_snapshot(self) -> HostSnapshot:
        return next(self._snapshots)

    def read_memory_stats(self) -> MemoryStats:
        if self._memory_stats is None:
            raise RuntimeError("memory stats unavailable")
        return self._memory_stats


class _UnusedReader:
    def read_snapshot(self) -> object:
        raise RuntimeError("unused snapshot reader")

    def read_report(self) -> object:
        raise RuntimeError("unused filesystem reader")


def _event(message: str = "ok") -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="tests.runtime_collectors",
        message=message,
        attributes={"observation_type": "test.runtime"},
    )


def _host_snapshot(
    *,
    second: int,
    user: int,
    system: int,
    idle: int,
) -> HostSnapshot:
    captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second)
    return HostSnapshot(
        captured_at=captured_at,
        identity=HostIdentity(
            hostname="fedora",
            kernel_name="Linux",
            kernel_release="6.0",
            kernel_version="#1",
            machine="x86_64",
            logical_cpu_count=4,
        ),
        cpu_times=CpuTimes(
            user=user,
            nice=0,
            system=system,
            idle=idle,
        ),
        load_average=LoadAverage(
            one_minute=0.1,
            five_minutes=0.2,
            fifteen_minutes=0.3,
            runnable_tasks=1,
            total_tasks=10,
            last_pid=100,
        ),
    )


class RuntimeCollectorTests(unittest.TestCase):
    def test_sampled_collector_first_capture_is_warmup(self) -> None:
        reader = _SequenceReader("first")

        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            reader.read,
            lambda _previous, _current, _interval: _event(),
            clock_ns=_Clock(1_000),
        )

        result = collector.collect()

        self.assertEqual(result.status, ObservationEmissionStatus.WARMING_UP)
        self.assertIsNone(result.event)
        self.assertTrue(collector.snapshot().baseline_ready)
        self.assertEqual(collector.snapshot().warmup_count, 1)

    def test_sampled_collector_second_capture_emits_with_real_interval(self) -> None:
        reader = _SequenceReader("first", "second")
        seen: list[tuple[object, object, float]] = []

        def build(previous: object, current: object, interval: float) -> SentinelEvent:
            seen.append((previous, current, interval))
            return _event()

        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            reader.read,
            build,
            clock_ns=_Clock(1_000_000_000, 2_250_000_000),
        )

        collector.collect()
        result = collector.collect()

        self.assertEqual(result.status, ObservationEmissionStatus.EMITTED)
        self.assertAlmostEqual(result.sample_interval_seconds or 0.0, 1.25)
        self.assertEqual(seen, [("first", "second", 1.25)])
        self.assertEqual(collector.snapshot().emission_count, 1)

    def test_sampled_collector_does_not_sleep_between_captures(self) -> None:
        calls: list[str] = []

        def read() -> str:
            calls.append("read")
            return str(len(calls))

        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            read,
            lambda _previous, _current, _interval: _event(),
            clock_ns=_Clock(10, 20),
        )

        collector.collect()
        collector.collect()

        self.assertEqual(calls, ["read", "read"])

    def test_reader_failure_preserves_existing_baseline(self) -> None:
        reader = _SequenceReader("first", RuntimeError("read failed"), "third")
        seen: list[tuple[object, object]] = []

        def build(previous: object, current: object, _interval: float) -> SentinelEvent:
            seen.append((previous, current))
            return _event()

        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            reader.read,
            build,
            clock_ns=_Clock(100, 300),
        )

        collector.collect()
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            collector.collect()
        collector.collect()

        self.assertEqual(seen, [("first", "third")])
        self.assertEqual(collector.snapshot().failure_count, 1)

    def test_builder_failure_replaces_baseline_for_recovery(self) -> None:
        reader = _SequenceReader("first", "second", "third")
        seen: list[tuple[object, object]] = []

        def build(previous: object, current: object, _interval: float) -> SentinelEvent:
            seen.append((previous, current))
            if current == "second":
                raise RuntimeError("comparison failed")
            return _event()

        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            reader.read,
            build,
            clock_ns=_Clock(100, 200, 300),
        )

        collector.collect()
        with self.assertRaisesRegex(RuntimeError, "comparison failed"):
            collector.collect()
        collector.collect()

        self.assertEqual(seen, [("first", "second"), ("second", "third")])
        self.assertEqual(collector.snapshot().failure_count, 1)

    def test_monotonic_regression_replaces_baseline_for_recovery(self) -> None:
        reader = _SequenceReader("first", "second", "third")
        seen: list[tuple[object, object]] = []

        def build(previous: object, current: object, _interval: float) -> SentinelEvent:
            seen.append((previous, current))
            return _event()

        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            reader.read,
            build,
            clock_ns=_Clock(100, 90, 110),
        )

        collector.collect()
        with self.assertRaises(RuntimeCollectorClockError):
            collector.collect()
        collector.collect()

        self.assertEqual(seen, [("second", "third")])
        self.assertEqual(collector.snapshot().failure_count, 1)

    def test_invalid_clock_value_is_rejected(self) -> None:
        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            lambda: "snapshot",
            lambda _previous, _current, _interval: _event(),
            clock_ns=lambda: -1,
        )

        with self.assertRaises(RuntimeCollectorClockError):
            collector.collect()

        self.assertFalse(collector.snapshot().baseline_ready)
        self.assertEqual(collector.snapshot().failure_count, 1)

    def test_invalid_event_builder_result_is_rejected(self) -> None:
        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            _SequenceReader("a", "b").read,
            (lambda _previous, _current, _interval: "not an event"),  # type: ignore[arg-type,return-value]
            clock_ns=_Clock(100, 200),
        )

        collector.collect()
        with self.assertRaises(RuntimeCollectorContractError):
            collector.collect()

    def test_reset_baseline_requires_fresh_warmup(self) -> None:
        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            _SequenceReader("a", "b").read,
            lambda _previous, _current, _interval: _event(),
            clock_ns=_Clock(100, 200),
        )

        collector.collect()
        self.assertTrue(collector.reset_baseline())
        self.assertFalse(collector.snapshot().baseline_ready)

        result = collector.collect()

        self.assertEqual(result.status, ObservationEmissionStatus.WARMING_UP)
        self.assertEqual(collector.snapshot().warmup_count, 2)
        self.assertEqual(collector.snapshot().baseline_reset_count, 1)

    def test_reset_without_baseline_is_idempotent(self) -> None:
        collector = SuccessiveSnapshotEventCollector(
            "cpu",
            lambda: "snapshot",
            lambda _previous, _current, _interval: _event(),
        )

        self.assertFalse(collector.reset_baseline())
        self.assertEqual(collector.snapshot().baseline_reset_count, 0)

    def test_point_in_time_collector_emits_on_first_invocation(self) -> None:
        collector = PointInTimeEventCollector("memory", lambda: _event("memory"))

        result = collector.collect()

        self.assertEqual(result.status, ObservationEmissionStatus.EMITTED)
        self.assertIsNotNone(result.event)
        self.assertIsNone(result.sample_interval_seconds)
        self.assertFalse(collector.snapshot().requires_baseline)
        self.assertEqual(collector.snapshot().emission_count, 1)

    def test_point_in_time_failure_is_counted_and_propagated(self) -> None:
        def fail() -> SentinelEvent:
            raise RuntimeError("point failure")

        collector = PointInTimeEventCollector("memory", fail)

        with self.assertRaisesRegex(RuntimeError, "point failure"):
            collector.collect()

        snapshot = collector.snapshot()
        self.assertEqual(snapshot.invocation_count, 1)
        self.assertEqual(snapshot.emission_count, 0)
        self.assertEqual(snapshot.failure_count, 1)

    def test_point_in_time_invalid_event_is_rejected(self) -> None:
        def producer() -> SentinelEvent:
            return "bad"  # type: ignore[return-value]

        collector = PointInTimeEventCollector("memory", producer)

        with self.assertRaises(RuntimeCollectorContractError):
            collector.collect()

    def test_emission_serialization_does_not_duplicate_event_attributes(self) -> None:
        result = ObservationEmission(
            collector_name="memory",
            status=ObservationEmissionStatus.EMITTED,
            event=_event(),
        )

        payload = result.to_dict()

        self.assertEqual(payload["status"], "emitted")
        self.assertNotIn("attributes", payload)
        self.assertNotIn("event", payload)

    def test_builtin_collector_set_requires_exact_names(self) -> None:
        collector = PointInTimeEventCollector("wrong", _event)

        with self.assertRaises(RuntimeCollectorContractError):
            BuiltinHostCollectors(
                cpu_load=collector,
                memory=PointInTimeEventCollector("memory", _event),
                filesystem=PointInTimeEventCollector("filesystem", _event),
                disk_io=PointInTimeEventCollector("disk_io", _event),
                network=PointInTimeEventCollector("network", _event),
                process=PointInTimeEventCollector("process", _event),
            )

    def test_builtin_collector_set_rejects_reused_objects(self) -> None:
        shared = PointInTimeEventCollector("memory", _event)

        with self.assertRaises(RuntimeCollectorContractError):
            BuiltinHostCollectors(
                cpu_load=PointInTimeEventCollector("cpu_load", _event),
                memory=shared,
                filesystem=PointInTimeEventCollector("filesystem", _event),
                disk_io=PointInTimeEventCollector("disk_io", _event),
                network=PointInTimeEventCollector("network", _event),
                process=shared,
            )

    def test_builtin_factory_exposes_exact_registry_handler_names(self) -> None:
        unused = _UnusedReader()
        host = _HostReader()
        collectors = build_builtin_host_collectors(
            cpu_host_reader=host,  # type: ignore[arg-type]
            memory_host_reader=host,  # type: ignore[arg-type]
            filesystem_reader=unused,  # type: ignore[arg-type]
            disk_io_reader=unused,  # type: ignore[arg-type]
            network_reader=unused,  # type: ignore[arg-type]
            process_reader=unused,  # type: ignore[arg-type]
        )

        self.assertEqual(
            tuple(collectors.handlers()),
            (
                "cpu_load",
                "memory",
                "filesystem",
                "disk_io",
                "network",
                "process",
            ),
        )

    def test_builtin_cpu_collector_uses_successive_host_snapshots(self) -> None:
        host = _HostReader(
            snapshots=(
                _host_snapshot(second=0, user=100, system=50, idle=850),
                _host_snapshot(second=1, user=110, system=55, idle=935),
            )
        )
        unused = _UnusedReader()
        collectors = build_builtin_host_collectors(
            cpu_host_reader=host,  # type: ignore[arg-type]
            memory_host_reader=_HostReader(),  # type: ignore[arg-type]
            filesystem_reader=unused,  # type: ignore[arg-type]
            disk_io_reader=unused,  # type: ignore[arg-type]
            network_reader=unused,  # type: ignore[arg-type]
            process_reader=unused,  # type: ignore[arg-type]
            clock_ns=_Clock(1_000_000_000, 2_000_000_000),
        )

        first = collectors.cpu_load.collect()
        second = collectors.cpu_load.collect()

        self.assertEqual(first.status, ObservationEmissionStatus.WARMING_UP)
        self.assertEqual(second.status, ObservationEmissionStatus.EMITTED)
        assert second.event is not None
        self.assertEqual(
            second.event.attributes["observation_type"],
            "linux.host.cpu_load",
        )
        self.assertEqual(second.sample_interval_seconds, 1.0)

    def test_builtin_memory_collector_emits_without_warmup(self) -> None:
        memory_stats = MemoryStats(
            mem_total_kb=1_000,
            mem_available_kb=600,
            mem_free_kb=400,
            buffers_kb=50,
            cached_kb=100,
            swap_total_kb=200,
            swap_free_kb=150,
        )
        memory_reader = _HostReader(memory_stats=memory_stats)
        unused = _UnusedReader()
        collectors = build_builtin_host_collectors(
            cpu_host_reader=_HostReader(),  # type: ignore[arg-type]
            memory_host_reader=memory_reader,  # type: ignore[arg-type]
            filesystem_reader=unused,  # type: ignore[arg-type]
            disk_io_reader=unused,  # type: ignore[arg-type]
            network_reader=unused,  # type: ignore[arg-type]
            process_reader=unused,  # type: ignore[arg-type]
            utc_now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        result = collectors.memory.collect()

        self.assertEqual(result.status, ObservationEmissionStatus.EMITTED)
        assert result.event is not None
        self.assertEqual(
            result.event.attributes["observation_type"],
            "linux.host.memory",
        )

    def test_builtin_snapshots_preserve_semantic_order(self) -> None:
        unused = _UnusedReader()
        host = _HostReader()
        collectors = build_builtin_host_collectors(
            cpu_host_reader=host,  # type: ignore[arg-type]
            memory_host_reader=host,  # type: ignore[arg-type]
            filesystem_reader=unused,  # type: ignore[arg-type]
            disk_io_reader=unused,  # type: ignore[arg-type]
            network_reader=unused,  # type: ignore[arg-type]
            process_reader=unused,  # type: ignore[arg-type]
        )

        self.assertEqual(
            tuple(snapshot.collector_name for snapshot in collectors.snapshots()),
            (
                "cpu_load",
                "memory",
                "filesystem",
                "disk_io",
                "network",
                "process",
            ),
        )


if __name__ == "__main__":
    unittest.main()
