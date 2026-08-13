from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import cast

from sentinel_x.core import (
    CollectorDispatch,
    CollectorExecutionPolicy,
    CollectorExecutionSpec,
    CollectorExecutionStatus,
    CollectorExecutionValidationError,
    CollectorExecutor,
    CollectorSchedule,
    CollectorScheduler,
    CollectorSpecMismatchError,
    ExecutionClockError,
    UnknownExecutionStateError,
)


class _Clock:
    def __init__(self, *values: int) -> None:
        self._values = list(values)

    def __call__(self) -> int:
        if not self._values:
            raise AssertionError("fake clock exhausted")
        return self._values.pop(0)


class CollectorExecutorTests(unittest.TestCase):
    def _claimed(
        self,
        name: str = "cpu",
        *,
        interval_ns: int = 100,
        claim_at_ns: int = 1_000,
    ) -> tuple[CollectorScheduler, CollectorDispatch]:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule(name, interval_ns=interval_ns),
            now_ns=claim_at_ns,
        )
        dispatch = scheduler.claim_due(now_ns=claim_at_ns)[0]
        return scheduler, dispatch

    def test_policy_from_seconds_converts_budget_and_backoff(self) -> None:
        policy = CollectorExecutionPolicy.from_seconds(
            budget_seconds=0.5,
            failure_backoff_initial_seconds=1.0,
            failure_backoff_max_seconds=8.0,
        )

        self.assertEqual(policy.budget_ns, 500_000_000)
        self.assertEqual(policy.failure_backoff_initial_ns, 1_000_000_000)
        self.assertEqual(policy.failure_backoff_max_ns, 8_000_000_000)
        self.assertTrue(policy.backoff_enabled)

    def test_invalid_policy_values_are_rejected(self) -> None:
        invalid_builders: tuple[Callable[[], object], ...] = (
            lambda: CollectorExecutionPolicy(budget_ns=0),
            lambda: CollectorExecutionPolicy(
                failure_backoff_initial_ns=0,
                failure_backoff_max_ns=1,
            ),
            lambda: CollectorExecutionPolicy(
                failure_backoff_initial_ns=10,
                failure_backoff_max_ns=9,
            ),
            lambda: CollectorExecutionPolicy.from_seconds(budget_seconds=float("nan")),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(CollectorExecutionValidationError):
                    builder()

    def test_execution_spec_validates_name_and_handler(self) -> None:
        with self.assertRaises(CollectorExecutionValidationError):
            CollectorExecutionSpec("bad name", lambda: None)

        with self.assertRaises(CollectorExecutionValidationError):
            CollectorExecutionSpec(
                "cpu",
                cast(Callable[[], object], object()),
            )

    def test_successful_execution_measures_timing_and_completes_scheduler(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_020, 1_080),
        )
        token = object()

        outcome = executor.execute(
            dispatch,
            CollectorExecutionSpec("cpu", lambda: token),
        )

        self.assertIs(outcome.status, CollectorExecutionStatus.SUCCESS)
        self.assertIs(outcome.result, token)
        self.assertEqual(outcome.queue_delay_ns, 20)
        self.assertEqual(outcome.duration_ns, 60)
        self.assertEqual(outcome.completion_lateness_ns, 80)
        self.assertEqual(scheduler.snapshot("cpu").completion_count, 1)

    def test_collector_exception_is_captured_and_does_not_escape(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_050),
        )

        def fail() -> object:
            raise RuntimeError("collector failed")

        outcome = executor.execute(
            dispatch,
            CollectorExecutionSpec("cpu", fail),
        )

        self.assertIs(outcome.status, CollectorExecutionStatus.FAILED)
        self.assertIsNone(outcome.result)
        self.assertIsNotNone(outcome.failure)
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.error_type, "builtins.RuntimeError")
        self.assertEqual(outcome.failure.error_message, "collector failed")
        self.assertEqual(scheduler.snapshot("cpu").completion_count, 1)

    def test_failure_message_is_bounded(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_010),
        )

        def fail() -> object:
            raise RuntimeError("x" * 2_000)

        outcome = executor.execute(
            dispatch,
            CollectorExecutionSpec("cpu", fail),
        )

        assert outcome.failure is not None
        self.assertEqual(len(outcome.failure.error_message), 512)
        self.assertTrue(outcome.failure.error_message.endswith("..."))

    def test_budget_overrun_is_reported_without_turning_success_into_failure(
        self,
    ) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_075),
        )
        policy = CollectorExecutionPolicy(budget_ns=50)

        outcome = executor.execute(
            dispatch,
            CollectorExecutionSpec("cpu", lambda: "ok", policy),
        )

        self.assertIs(outcome.status, CollectorExecutionStatus.SUCCESS)
        self.assertTrue(outcome.budget_exceeded)
        self.assertEqual(executor.snapshot("cpu").budget_exceeded_count, 1)

    def test_duration_equal_to_budget_is_not_overrun(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_050),
        )

        outcome = executor.execute(
            dispatch,
            CollectorExecutionSpec(
                "cpu",
                lambda: None,
                CollectorExecutionPolicy(budget_ns=50),
            ),
        )

        self.assertFalse(outcome.budget_exceeded)

    def test_failure_backoff_skips_handler_and_completes_dispatch(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )
        first = scheduler.claim_due(now_ns=1_000)[0]
        calls = 0

        def fail() -> object:
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        policy = CollectorExecutionPolicy(
            failure_backoff_initial_ns=500,
            failure_backoff_max_ns=2_000,
        )
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_010, 1_100),
        )
        spec = CollectorExecutionSpec("cpu", fail, policy)

        first_outcome = executor.execute(first, spec)
        second = scheduler.claim_due(now_ns=1_100)[0]
        second_outcome = executor.execute(second, spec)

        self.assertIs(first_outcome.status, CollectorExecutionStatus.FAILED)
        self.assertIs(
            second_outcome.status,
            CollectorExecutionStatus.BACKOFF_SKIPPED,
        )
        self.assertEqual(calls, 1)
        self.assertEqual(scheduler.snapshot("cpu").completion_count, 2)

    def test_consecutive_failures_use_bounded_exponential_backoff(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=1_000),
            now_ns=1_000,
        )
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(
                1_000,
                1_010,
                2_000,
                2_010,
                4_000,
                4_010,
                8_000,
                8_010,
            ),
        )
        policy = CollectorExecutionPolicy(
            failure_backoff_initial_ns=100,
            failure_backoff_max_ns=250,
        )

        def fail() -> object:
            raise RuntimeError("boom")

        spec = CollectorExecutionSpec("cpu", fail, policy)
        backoff_deadlines: list[int | None] = []

        for claim_time in (1_000, 2_000, 4_000, 8_000):
            dispatch = scheduler.claim_due(now_ns=claim_time)[0]
            outcome = executor.execute(dispatch, spec)
            backoff_deadlines.append(outcome.backoff_until_ns)

        self.assertEqual(
            backoff_deadlines,
            [1_110, 2_210, 4_260, 8_260],
        )

    def test_success_resets_failure_streak_and_backoff(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=1_000),
            now_ns=1_000,
        )
        responses = iter((RuntimeError("boom"), "ok"))

        def handler() -> object:
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_010, 2_000, 2_010),
        )
        spec = CollectorExecutionSpec(
            "cpu",
            handler,
            CollectorExecutionPolicy(
                failure_backoff_initial_ns=100,
                failure_backoff_max_ns=100,
            ),
        )

        first = executor.execute(scheduler.claim_due(now_ns=1_000)[0], spec)
        second = executor.execute(scheduler.claim_due(now_ns=2_000)[0], spec)

        self.assertEqual(first.consecutive_failure_count, 1)
        self.assertIs(second.status, CollectorExecutionStatus.SUCCESS)
        self.assertEqual(second.consecutive_failure_count, 0)
        self.assertIsNone(second.backoff_until_ns)
        self.assertEqual(executor.snapshot("cpu").consecutive_failure_count, 0)

    def test_failure_in_one_collector_does_not_block_another(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(CollectorSchedule("cpu", interval_ns=100), now_ns=1_000)
        scheduler.register(
            CollectorSchedule("memory", interval_ns=100),
            now_ns=1_000,
        )
        dispatches = {
            dispatch.collector_name: dispatch
            for dispatch in scheduler.claim_due(now_ns=1_000)
        }
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_010, 1_010, 1_020),
        )

        def fail() -> object:
            raise RuntimeError("cpu failed")

        cpu = executor.execute(
            dispatches["cpu"],
            CollectorExecutionSpec("cpu", fail),
        )
        memory = executor.execute(
            dispatches["memory"],
            CollectorExecutionSpec("memory", lambda: "memory-ok"),
        )

        self.assertIs(cpu.status, CollectorExecutionStatus.FAILED)
        self.assertIs(memory.status, CollectorExecutionStatus.SUCCESS)
        self.assertEqual(memory.result, "memory-ok")

    def test_spec_mismatch_releases_dispatch_before_raising(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(scheduler, clock_ns=_Clock())

        with self.assertRaises(CollectorSpecMismatchError):
            executor.execute(
                dispatch,
                CollectorExecutionSpec("memory", lambda: None),
            )

        snapshot = scheduler.snapshot("cpu")
        self.assertEqual(snapshot.completion_count, 1)
        self.assertIsNone(snapshot.active_dispatch_sequence)

    def test_invalid_start_clock_releases_dispatch_before_raising(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=lambda: -1,
        )

        with self.assertRaises(ExecutionClockError):
            executor.execute(
                dispatch,
                CollectorExecutionSpec("cpu", lambda: None),
            )

        self.assertEqual(scheduler.snapshot("cpu").completion_count, 1)

    def test_clock_before_claim_releases_dispatch_before_raising(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(999),
        )

        with self.assertRaises(ExecutionClockError):
            executor.execute(
                dispatch,
                CollectorExecutionSpec("cpu", lambda: None),
            )

        self.assertEqual(scheduler.snapshot("cpu").completion_count, 1)

    def test_clock_regression_during_handler_releases_dispatch(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_010, 1_009),
        )

        with self.assertRaises(ExecutionClockError):
            executor.execute(
                dispatch,
                CollectorExecutionSpec("cpu", lambda: None),
            )

        self.assertEqual(scheduler.snapshot("cpu").completion_count, 1)

    def test_base_exception_propagates_but_dispatch_is_completed(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_010),
        )

        def stop() -> object:
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            executor.execute(
                dispatch,
                CollectorExecutionSpec("cpu", stop),
            )

        self.assertEqual(scheduler.snapshot("cpu").completion_count, 1)

    def test_outcome_serialization_omits_arbitrary_handler_result(self) -> None:
        scheduler, dispatch = self._claimed()
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_010),
        )
        outcome = executor.execute(
            dispatch,
            CollectorExecutionSpec("cpu", lambda: object()),
        )

        payload = outcome.to_dict()

        self.assertNotIn("result", payload)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["duration_ns"], 10)

    def test_snapshot_tracks_execution_statistics(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(CollectorSchedule("cpu", interval_ns=100), now_ns=1_000)
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_060),
        )
        outcome = executor.execute(
            scheduler.claim_due(now_ns=1_000)[0],
            CollectorExecutionSpec(
                "cpu",
                lambda: "ok",
                CollectorExecutionPolicy(budget_ns=50),
            ),
        )

        snapshot = executor.snapshot("cpu")

        self.assertEqual(snapshot.dispatch_count, 1)
        self.assertEqual(snapshot.handler_invocation_count, 1)
        self.assertEqual(snapshot.success_count, 1)
        self.assertEqual(snapshot.failure_count, 0)
        self.assertEqual(snapshot.budget_exceeded_count, 1)
        self.assertEqual(snapshot.last_duration_ns, outcome.duration_ns)
        self.assertIs(snapshot.last_status, CollectorExecutionStatus.SUCCESS)

    def test_unknown_execution_snapshot_is_rejected(self) -> None:
        scheduler = CollectorScheduler()
        executor = CollectorExecutor(scheduler)

        with self.assertRaises(UnknownExecutionStateError):
            executor.snapshot("missing")

    def test_snapshots_are_returned_in_deterministic_name_order(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(CollectorSchedule("network", interval_ns=100), now_ns=1_000)
        scheduler.register(CollectorSchedule("cpu", interval_ns=100), now_ns=1_000)
        dispatches = {
            dispatch.collector_name: dispatch
            for dispatch in scheduler.claim_due(now_ns=1_000)
        }
        executor = CollectorExecutor(
            scheduler,
            clock_ns=_Clock(1_000, 1_010, 1_010, 1_020),
        )

        executor.execute(
            dispatches["network"],
            CollectorExecutionSpec("network", lambda: None),
        )
        executor.execute(
            dispatches["cpu"],
            CollectorExecutionSpec("cpu", lambda: None),
        )

        self.assertEqual(
            [snapshot.collector_name for snapshot in executor.snapshots()],
            ["cpu", "network"],
        )


if __name__ == "__main__":
    unittest.main()
