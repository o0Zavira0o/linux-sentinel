"""Unit tests for scheduled collector runtime orchestration."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from sentinel_x.core import (
    CollectorDefinition,
    CollectorExecutionPolicy,
    CollectorExecutionSpec,
    CollectorExecutionStatus,
    CollectorRegistry,
    CollectorRuntime,
    CollectorRuntimeClockError,
    CollectorRuntimeContractError,
    CollectorSchedule,
    EventBus,
    EventKind,
    EventSeverity,
    SentinelEvent,
)


@dataclass(frozen=True, slots=True)
class _Emission:
    collector_name: str
    event: SentinelEvent | None


class _ManualClock:
    def __init__(self, now_ns: int = 0) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, nanoseconds: int) -> None:
        self.now_ns += nanoseconds


def _definition(
    name: str,
    handler: object,
    *,
    interval_ns: int = 100,
    initial_delay_ns: int = 0,
    budget_ns: int | None = None,
    backoff_initial_ns: int = 0,
    backoff_max_ns: int = 0,
    enabled: bool = True,
) -> CollectorDefinition:
    if not callable(handler):
        raise TypeError("handler must be callable")
    return CollectorDefinition(
        enabled=enabled,
        schedule=CollectorSchedule(
            name=name,
            interval_ns=interval_ns,
            initial_delay_ns=initial_delay_ns,
        ),
        execution_spec=CollectorExecutionSpec(
            name=name,
            handler=handler,
            policy=CollectorExecutionPolicy(
                budget_ns=budget_ns,
                failure_backoff_initial_ns=backoff_initial_ns,
                failure_backoff_max_ns=backoff_max_ns,
            ),
        ),
    )


class CollectorRuntimeTests(unittest.TestCase):
    """Tests for scheduler-to-executor-to-event-bus orchestration."""

    def test_constructor_rejects_invalid_dependencies(self) -> None:
        with self.assertRaises(TypeError):
            CollectorRuntime(object(), CollectorRegistry())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            CollectorRuntime(EventBus(), object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            CollectorRuntime(
                EventBus(),
                CollectorRegistry(),
                clock_ns=object(),  # type: ignore[arg-type]
            )

    def test_invalid_clock_value_is_rejected(self) -> None:
        with self.assertRaises(CollectorRuntimeClockError):
            CollectorRuntime(
                EventBus(),
                CollectorRegistry(),
                clock_ns=lambda: -1,
            )
        with self.assertRaises(CollectorRuntimeClockError):
            CollectorRuntime(
                EventBus(),
                CollectorRegistry(),
                clock_ns=lambda: True,
            )

    def test_empty_registry_uses_wake_ceiling(self) -> None:
        clock = _ManualClock()
        runtime = CollectorRuntime(EventBus(), CollectorRegistry(), clock_ns=clock)

        delay = runtime.next_wakeup_delay_seconds(maximum_seconds=0.75)

        self.assertEqual(delay, 0.75)

    def test_wakeup_delay_uses_earlier_collector_deadline(self) -> None:
        clock = _ManualClock()

        def handler() -> _Emission:
            return _Emission("probe", None)

        registry = CollectorRegistry(
            (
                _definition(
                    "probe",
                    handler,
                    initial_delay_ns=100_000_000,
                ),
            )
        )
        runtime = CollectorRuntime(EventBus(), registry, clock_ns=clock)

        delay = runtime.next_wakeup_delay_seconds(maximum_seconds=0.75)

        self.assertAlmostEqual(delay, 0.1)

    def test_wakeup_delay_is_bounded_by_maximum(self) -> None:
        clock = _ManualClock()

        def handler() -> _Emission:
            return _Emission("probe", None)

        registry = CollectorRegistry(
            (
                _definition(
                    "probe",
                    handler,
                    initial_delay_ns=2_000_000_000,
                ),
            )
        )
        runtime = CollectorRuntime(EventBus(), registry, clock_ns=clock)

        delay = runtime.next_wakeup_delay_seconds(maximum_seconds=0.75)

        self.assertEqual(delay, 0.75)

    def test_invalid_wakeup_ceiling_is_rejected(self) -> None:
        runtime = CollectorRuntime(EventBus(), CollectorRegistry(), clock_ns=lambda: 0)
        for value in (0, -1, float("nan"), True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    runtime.next_wakeup_delay_seconds(maximum_seconds=value)

    def test_successful_warmup_records_no_event(self) -> None:
        clock = _ManualClock()

        def handler() -> _Emission:
            return _Emission("probe", None)

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry((_definition("probe", handler),)),
            clock_ns=clock,
        )

        cycle = runtime.run_due()
        snapshot = runtime.snapshot()

        self.assertEqual(cycle.claimed_count, 1)
        self.assertEqual(cycle.warmup_count, 1)
        self.assertEqual(cycle.emitted_count, 0)
        self.assertEqual(snapshot.warmup_count, 1)

    def test_successful_emission_is_published(self) -> None:
        clock = _ManualClock()
        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="test.collector",
            message="Collected observation.",
        )

        def handler() -> _Emission:
            return _Emission("probe", event)

        bus = EventBus()
        events: list[SentinelEvent] = []
        bus.subscribe(events.append)
        runtime = CollectorRuntime(
            bus,
            CollectorRegistry((_definition("probe", handler),)),
            clock_ns=clock,
        )

        cycle = runtime.run_due()

        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual([item.event_id for item in events], [event.event_id])

    def test_collector_failure_isolated_and_diagnostic_is_published(self) -> None:
        clock = _ManualClock()

        def fail() -> object:
            raise RuntimeError("expected failure")

        def succeed() -> _Emission:
            return _Emission(
                "good",
                SentinelEvent(
                    kind=EventKind.OBSERVATION,
                    source="test.good",
                    message="Good collector observation.",
                ),
            )

        bus = EventBus()
        events: list[SentinelEvent] = []
        bus.subscribe(events.append)
        registry = CollectorRegistry(
            (
                _definition("bad", fail),
                _definition("good", succeed),
            )
        )
        runtime = CollectorRuntime(bus, registry, clock_ns=clock)

        cycle = runtime.run_due()

        self.assertEqual(cycle.claimed_count, 2)
        self.assertEqual(cycle.failed_count, 1)
        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual(
            [outcome.dispatch.collector_name for outcome in cycle.outcomes],
            ["bad", "good"],
        )
        diagnostic = next(event for event in events if event.source == runtime.SOURCE)
        self.assertEqual(diagnostic.severity, EventSeverity.ERROR)
        self.assertEqual(diagnostic.attributes["collector_name"], "bad")

    def test_backoff_skip_is_reported_without_reinvoking_handler(self) -> None:
        clock = _ManualClock()
        calls = 0

        def fail() -> object:
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry(
                (
                    _definition(
                        "probe",
                        fail,
                        interval_ns=10,
                        backoff_initial_ns=100,
                        backoff_max_ns=100,
                    ),
                )
            ),
            clock_ns=clock,
        )

        first = runtime.run_due()
        clock.advance(10)
        second = runtime.run_due()

        self.assertEqual(first.failed_count, 1)
        self.assertEqual(second.backoff_skipped_count, 1)
        self.assertEqual(calls, 1)
        self.assertIs(
            second.outcomes[0].status,
            CollectorExecutionStatus.BACKOFF_SKIPPED,
        )

    def test_budget_overrun_publishes_warning_without_losing_observation(self) -> None:
        clock = _ManualClock()
        observation = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="test.slow",
            message="Slow collector observation.",
        )

        def slow() -> _Emission:
            clock.advance(20)
            return _Emission("probe", observation)

        bus = EventBus()
        events: list[SentinelEvent] = []
        bus.subscribe(events.append)
        runtime = CollectorRuntime(
            bus,
            CollectorRegistry(
                (
                    _definition(
                        "probe",
                        slow,
                        budget_ns=10,
                    ),
                )
            ),
            clock_ns=clock,
        )

        cycle = runtime.run_due()

        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual(cycle.budget_exceeded_count, 1)
        self.assertIn(observation.event_id, [event.event_id for event in events])
        diagnostic = next(event for event in events if event.source == runtime.SOURCE)
        self.assertEqual(diagnostic.severity, EventSeverity.WARNING)

    def test_publish_failure_is_counted_without_stopping_later_handlers(self) -> None:
        clock = _ManualClock()
        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="test.publish",
            message="Publish failure observation.",
        )

        def handler() -> _Emission:
            return _Emission("probe", event)

        def broken(_event: SentinelEvent) -> None:
            raise RuntimeError("subscriber failed")

        received: list[SentinelEvent] = []
        bus = EventBus()
        bus.subscribe(broken)
        bus.subscribe(received.append)
        runtime = CollectorRuntime(
            bus,
            CollectorRegistry((_definition("probe", handler),)),
            clock_ns=clock,
        )

        cycle = runtime.run_due()

        self.assertEqual(cycle.publish_failure_count, 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(runtime.snapshot().publish_failure_count, 1)

    def test_success_result_must_satisfy_emission_contract(self) -> None:
        def handler() -> object:
            return object()

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry((_definition("probe", handler),)),
            clock_ns=lambda: 0,
        )

        with self.assertRaises(CollectorRuntimeContractError):
            runtime.run_due()

    def test_emission_identity_must_match_dispatch(self) -> None:
        def handler() -> _Emission:
            return _Emission("other", None)

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry((_definition("probe", handler),)),
            clock_ns=lambda: 0,
        )

        with self.assertRaises(CollectorRuntimeContractError):
            runtime.run_due()

    def test_emission_event_must_be_typed(self) -> None:
        @dataclass(frozen=True, slots=True)
        class InvalidEmission:
            collector_name: str
            event: object

        def handler() -> InvalidEmission:
            return InvalidEmission("probe", object())

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry((_definition("probe", handler),)),
            clock_ns=lambda: 0,
        )

        with self.assertRaises(CollectorRuntimeContractError):
            runtime.run_due()

    def test_disabled_collector_is_not_scheduled(self) -> None:
        calls = 0

        def handler() -> _Emission:
            nonlocal calls
            calls += 1
            return _Emission("probe", None)

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry((_definition("probe", handler, enabled=False),)),
            clock_ns=lambda: 0,
        )

        cycle = runtime.run_due()
        snapshot = runtime.snapshot()

        self.assertEqual(cycle.claimed_count, 0)
        self.assertEqual(calls, 0)
        self.assertEqual(snapshot.configured_count, 1)
        self.assertEqual(snapshot.enabled_count, 0)
        self.assertEqual(snapshot.disabled_count, 1)

    def test_runtime_snapshot_accumulates_multiple_cycles(self) -> None:
        clock = _ManualClock()

        def handler() -> _Emission:
            return _Emission("probe", None)

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry(
                (
                    _definition(
                        "probe",
                        handler,
                        interval_ns=100,
                    ),
                )
            ),
            clock_ns=clock,
        )

        runtime.run_due()
        clock.advance(100)
        runtime.run_due()
        snapshot = runtime.snapshot()

        self.assertEqual(snapshot.cycle_count, 2)
        self.assertEqual(snapshot.claimed_count, 2)
        self.assertEqual(snapshot.warmup_count, 2)
        self.assertEqual(len(snapshot.schedules), 1)
        self.assertEqual(len(snapshot.executions), 1)

    def test_cycle_serialization_omits_arbitrary_handler_result(self) -> None:
        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="test.serialize",
            message="Serialization observation.",
        )

        def handler() -> _Emission:
            return _Emission("probe", event)

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry((_definition("probe", handler),)),
            clock_ns=lambda: 0,
        )

        cycle = runtime.run_due()
        payload = cycle.to_dict()

        self.assertNotIn("result", repr(payload))
        self.assertNotIn(event.event_id, repr(payload))

    def test_runtime_snapshot_is_serialization_friendly(self) -> None:
        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry(),
            clock_ns=lambda: 0,
        )

        payload = runtime.snapshot().to_dict()

        self.assertEqual(payload["configured_count"], 0)
        self.assertEqual(payload["schedules"], [])
        self.assertEqual(payload["executions"], [])

    def test_due_collectors_execute_in_deterministic_name_order(self) -> None:
        clock = _ManualClock()
        order: list[str] = []

        def alpha() -> _Emission:
            order.append("alpha")
            return _Emission("alpha", None)

        def beta() -> _Emission:
            order.append("beta")
            return _Emission("beta", None)

        runtime = CollectorRuntime(
            EventBus(),
            CollectorRegistry(
                (
                    _definition("beta", beta),
                    _definition("alpha", alpha),
                )
            ),
            clock_ns=clock,
        )

        runtime.run_due()

        self.assertEqual(order, ["alpha", "beta"])

    def test_failure_diagnostic_contains_bounded_execution_metadata(self) -> None:
        def fail() -> object:
            raise RuntimeError("x" * 1_000)

        events: list[SentinelEvent] = []
        bus = EventBus()
        bus.subscribe(events.append)
        runtime = CollectorRuntime(
            bus,
            CollectorRegistry((_definition("probe", fail),)),
            clock_ns=lambda: 0,
        )

        runtime.run_due()
        diagnostic = next(event for event in events if event.source == runtime.SOURCE)
        execution = diagnostic.attributes["execution"]

        self.assertIsInstance(execution, dict)
        self.assertNotIn("result", execution)
        self.assertEqual(
            diagnostic.attributes["observation_type"],
            "sentinel.runtime.collector_execution",
        )


if __name__ == "__main__":
    unittest.main()
