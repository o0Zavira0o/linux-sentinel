"""Tests for acknowledged collector publication transactions."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from sentinel_x.core import (
    CollectorDefinition,
    CollectorExecutionPolicy,
    CollectorExecutionSpec,
    CollectorRegistry,
    CollectorRuntime,
    CollectorRuntimeContractError,
    CollectorSchedule,
    EventBus,
    EventKind,
    SentinelEvent,
)


class _ManualClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, value: int = 100) -> None:
        self.now_ns += value


@dataclass(frozen=True, slots=True)
class _Emission:
    collector_name: str
    event: SentinelEvent | None


class _TransactionalEmission:
    def __init__(self, collector_name: str, event: SentinelEvent) -> None:
        self.collector_name = collector_name
        self.event = event
        self.commits = 0
        self.rollbacks = 0
        self.raise_on_commit = False
        self.raise_on_rollback = False

    def commit_publication(self) -> None:
        if self.raise_on_commit:
            raise RuntimeError("commit failed")
        self.commits += 1

    def rollback_publication(self) -> None:
        if self.raise_on_rollback:
            raise RuntimeError("rollback failed")
        self.rollbacks += 1


def _event() -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="test.publication",
        message="transactional observation",
    )


def _runtime(handler: object, bus: EventBus, clock: _ManualClock) -> CollectorRuntime:
    if not callable(handler):
        raise TypeError("handler must be callable")
    definition = CollectorDefinition(
        enabled=True,
        schedule=CollectorSchedule(name="probe", interval_ns=100),
        execution_spec=CollectorExecutionSpec(
            name="probe",
            handler=handler,
            policy=CollectorExecutionPolicy(),
        ),
    )
    return CollectorRuntime(bus, CollectorRegistry((definition,)), clock_ns=clock)


class CollectorPublicationTransactionTests(unittest.TestCase):
    """Verify backward-compatible publication acknowledgment behavior."""

    def test_successful_publication_commits_transaction(self) -> None:
        clock = _ManualClock()
        bus = EventBus()
        received: list[str] = []
        bus.subscribe(lambda event: received.append(event.event_id))
        emission = _TransactionalEmission("probe", _event())
        runtime = _runtime(lambda: emission, bus, clock)

        cycle = runtime.run_due()

        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual(cycle.publish_failure_count, 0)
        self.assertEqual(emission.commits, 1)
        self.assertEqual(emission.rollbacks, 0)
        self.assertEqual(received, [emission.event.event_id])

    def test_any_subscriber_failure_rolls_back_transaction(self) -> None:
        clock = _ManualClock()
        bus = EventBus()
        delivered: list[str] = []
        bus.subscribe(lambda event: delivered.append(event.event_id))

        def fail(_: SentinelEvent) -> None:
            raise RuntimeError("subscriber unavailable")

        bus.subscribe(fail)
        emission = _TransactionalEmission("probe", _event())
        runtime = _runtime(lambda: emission, bus, clock)

        cycle = runtime.run_due()

        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual(cycle.publish_failure_count, 1)
        self.assertEqual(emission.commits, 0)
        self.assertEqual(emission.rollbacks, 1)
        self.assertEqual(delivered, [emission.event.event_id])

    def test_nontransactional_emission_remains_backward_compatible(self) -> None:
        clock = _ManualClock()
        bus = EventBus()
        emission = _Emission("probe", _event())
        runtime = _runtime(lambda: emission, bus, clock)

        cycle = runtime.run_due()

        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual(cycle.publish_failure_count, 0)

    def test_eventless_result_does_not_invoke_transaction_hooks(self) -> None:
        class EventlessTransaction:
            collector_name = "probe"
            event = None

            def commit_publication(self) -> None:
                raise AssertionError("commit must not run for eventless result")

            def rollback_publication(self) -> None:
                raise AssertionError("rollback must not run for eventless result")

        clock = _ManualClock()
        runtime = _runtime(EventlessTransaction, EventBus(), clock)

        cycle = runtime.run_due()

        self.assertEqual(cycle.warmup_count, 1)
        self.assertEqual(cycle.emitted_count, 0)

    def test_commit_hook_failure_is_a_runtime_contract_error(self) -> None:
        clock = _ManualClock()
        emission = _TransactionalEmission("probe", _event())
        emission.raise_on_commit = True
        runtime = _runtime(lambda: emission, EventBus(), clock)

        with self.assertRaisesRegex(
            CollectorRuntimeContractError,
            "failed to commit",
        ):
            runtime.run_due()

    def test_rollback_hook_failure_is_a_runtime_contract_error(self) -> None:
        clock = _ManualClock()
        bus = EventBus()

        def fail(_: SentinelEvent) -> None:
            raise RuntimeError("subscriber unavailable")

        bus.subscribe(fail)
        emission = _TransactionalEmission("probe", _event())
        emission.raise_on_rollback = True
        runtime = _runtime(lambda: emission, bus, clock)

        with self.assertRaisesRegex(
            CollectorRuntimeContractError,
            "failed to rollback",
        ):
            runtime.run_due()


if __name__ == "__main__":
    unittest.main()
