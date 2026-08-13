"""Unit tests for the Sentinel-X core runtime engine."""

from __future__ import annotations

import threading
import unittest
from dataclasses import dataclass

from sentinel_x.core import (
    AgentState,
    CollectorDefinition,
    CollectorExecutionPolicy,
    CollectorExecutionSpec,
    CollectorRegistry,
    CollectorRuntime,
    CollectorSchedule,
    EventBus,
    EventKind,
    SentinelEngine,
    SentinelEvent,
)


@dataclass(frozen=True, slots=True)
class _Emission:
    collector_name: str
    event: SentinelEvent | None


def _registry(handler: object) -> CollectorRegistry:
    if not callable(handler):
        raise TypeError("handler must be callable")
    return CollectorRegistry(
        (
            CollectorDefinition(
                enabled=True,
                schedule=CollectorSchedule(
                    name="probe",
                    interval_ns=10_000_000,
                ),
                execution_spec=CollectorExecutionSpec(
                    name="probe",
                    handler=handler,
                    policy=CollectorExecutionPolicy(),
                ),
            ),
        )
    )


class SentinelEngineTests(unittest.TestCase):
    """Tests for lifecycle, scheduling, and graceful shutdown behavior."""

    def test_start_moves_engine_to_running(self) -> None:
        bus = EventBus()
        events: list[SentinelEvent] = []
        bus.subscribe(events.append)
        engine = SentinelEngine(event_bus=bus)

        engine.start()

        self.assertIs(engine.state, AgentState.RUNNING)
        self.assertEqual(events[-1].kind, EventKind.AGENT_STARTED)
        engine.stop(reason="unit test cleanup")

    def test_stop_request_is_idempotent(self) -> None:
        engine = SentinelEngine(event_bus=EventBus())
        first = engine.request_stop(reason="first request")
        second = engine.request_stop(reason="second request")
        snapshot = engine.snapshot()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(snapshot.stop_requested)
        self.assertEqual(snapshot.stop_reason, "first request")

    def test_run_forever_exits_after_stop_request(self) -> None:
        bus = EventBus()
        events: list[SentinelEvent] = []
        started = threading.Event()

        def collect(event: SentinelEvent) -> None:
            events.append(event)
            if event.kind is EventKind.AGENT_STARTED:
                started.set()

        bus.subscribe(collect)
        engine = SentinelEngine(event_bus=bus)
        worker = threading.Thread(
            target=engine.run_forever,
            kwargs={"tick_interval": 0.01},
            daemon=True,
        )
        worker.start()

        self.assertTrue(
            started.wait(timeout=1.0),
            "engine did not reach RUNNING in time",
        )
        engine.request_stop(reason="unit test shutdown")
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive(), "engine did not stop in time")
        self.assertIs(engine.state, AgentState.STOPPED)
        kinds = [event.kind for event in events]
        self.assertIn(EventKind.AGENT_STARTED, kinds)
        self.assertIn(EventKind.AGENT_STOP_REQUESTED, kinds)
        self.assertIn(EventKind.AGENT_STOPPED, kinds)

    def test_invalid_tick_interval_is_rejected(self) -> None:
        engine = SentinelEngine(event_bus=EventBus())
        with self.assertRaises(ValueError):
            engine.run_forever(tick_interval=0)
        with self.assertRaises(ValueError):
            engine.run_forever(tick_interval=float("nan"))

    def test_start_initializes_collector_runtime_snapshot(self) -> None:
        def handler() -> _Emission:
            return _Emission("probe", None)

        engine = SentinelEngine(
            event_bus=EventBus(),
            collector_registry=_registry(handler),
        )

        engine.start()
        snapshot = engine.snapshot()
        engine.stop(reason="unit test cleanup")

        self.assertIsNotNone(snapshot.collector_runtime)
        assert snapshot.collector_runtime is not None
        self.assertEqual(snapshot.collector_runtime.configured_count, 1)
        self.assertEqual(snapshot.collector_runtime.enabled_count, 1)
        self.assertEqual(snapshot.collector_runtime.cycle_count, 0)

    def test_runtime_emission_is_published_and_can_request_stop(self) -> None:
        observation = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="test.engine.collector",
            message="Engine runtime observation.",
        )

        def handler() -> _Emission:
            return _Emission("probe", observation)

        bus = EventBus()
        events: list[SentinelEvent] = []
        engine = SentinelEngine(
            event_bus=bus,
            collector_registry=_registry(handler),
        )

        def collect(event: SentinelEvent) -> None:
            events.append(event)
            if event.event_id == observation.event_id:
                engine.request_stop(reason="observation received")

        bus.subscribe(collect)
        engine.run_forever(tick_interval=0.5)

        self.assertIs(engine.state, AgentState.STOPPED)
        self.assertIn(observation.event_id, [event.event_id for event in events])
        runtime = engine.snapshot().collector_runtime
        assert runtime is not None
        self.assertEqual(runtime.emitted_count, 1)
        self.assertEqual(runtime.failed_count, 0)

    def test_collector_failure_isolated_without_failing_engine(self) -> None:
        def handler() -> object:
            raise RuntimeError("expected collector failure")

        bus = EventBus()
        events: list[SentinelEvent] = []
        engine = SentinelEngine(
            event_bus=bus,
            collector_registry=_registry(handler),
        )

        def collect(event: SentinelEvent) -> None:
            events.append(event)
            if event.source == CollectorRuntime.SOURCE:
                engine.request_stop(reason="failure observed")

        bus.subscribe(collect)
        engine.run_forever(tick_interval=0.5)

        self.assertIs(engine.state, AgentState.STOPPED)
        runtime_events = [
            event for event in events if event.source == CollectorRuntime.SOURCE
        ]
        self.assertEqual(len(runtime_events), 1)
        runtime = engine.snapshot().collector_runtime
        assert runtime is not None
        self.assertEqual(runtime.failed_count, 1)

    def test_started_event_reports_runtime_binding(self) -> None:
        def handler() -> _Emission:
            return _Emission("probe", None)

        bus = EventBus()
        events: list[SentinelEvent] = []
        bus.subscribe(events.append)
        engine = SentinelEngine(
            event_bus=bus,
            collector_registry=_registry(handler),
        )

        engine.start()
        engine.stop(reason="unit test cleanup")

        started = next(
            event for event in events if event.kind is EventKind.AGENT_STARTED
        )
        self.assertTrue(started.attributes["collector_runtime_enabled"])

    def test_engine_without_registry_preserves_dormant_compatibility(self) -> None:
        engine = SentinelEngine(event_bus=EventBus())
        engine.start()
        snapshot = engine.snapshot()
        engine.stop(reason="unit test cleanup")

        self.assertIsNone(snapshot.collector_runtime)

    def test_constructor_rejects_invalid_runtime_dependencies(self) -> None:
        with self.assertRaises(TypeError):
            SentinelEngine(
                event_bus=EventBus(),
                collector_registry=object(),  # type: ignore[arg-type]
            )
        with self.assertRaises(TypeError):
            SentinelEngine(
                event_bus=EventBus(),
                clock_ns=object(),  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
