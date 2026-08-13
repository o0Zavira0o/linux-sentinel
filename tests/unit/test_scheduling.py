"""Unit tests for Sentinel-X monotonic collector scheduling primitives."""

from __future__ import annotations

import math
import unittest

from sentinel_x.core import (
    CollectorDispatch,
    CollectorExecutionState,
    CollectorSchedule,
    CollectorScheduler,
    DuplicateScheduleError,
    ScheduleStateError,
    ScheduleValidationError,
    SchedulerClockError,
    UnknownScheduleError,
)


class CollectorSchedulingTests(unittest.TestCase):
    """Tests for fixed-cadence, skip-missed, no-overlap scheduling."""

    def test_schedule_from_seconds_normalizes_to_nanoseconds(self) -> None:
        schedule = CollectorSchedule.from_seconds(
            "cpu_load",
            interval_seconds=1.25,
            initial_delay_seconds=0.5,
        )

        self.assertEqual(schedule.name, "cpu_load")
        self.assertEqual(schedule.interval_ns, 1_250_000_000)
        self.assertEqual(schedule.initial_delay_ns, 500_000_000)
        self.assertEqual(schedule.interval_seconds, 1.25)
        self.assertEqual(schedule.initial_delay_seconds, 0.5)

    def test_schedule_rejects_invalid_identity_and_timing(self) -> None:
        with self.assertRaises(ScheduleValidationError):
            CollectorSchedule("bad name", interval_ns=1)

        with self.assertRaises(ScheduleValidationError):
            CollectorSchedule("cpu", interval_ns=0)

        with self.assertRaises(ScheduleValidationError):
            CollectorSchedule("cpu", interval_ns=1, initial_delay_ns=-1)

    def test_second_conversion_rejects_invalid_values(self) -> None:
        invalid_intervals: tuple[object, ...] = (
            True,
            0,
            -1.0,
            math.inf,
            math.nan,
            "1",
        )

        for value in invalid_intervals:
            with self.subTest(value=value):
                with self.assertRaises(ScheduleValidationError):
                    CollectorSchedule.from_seconds(
                        "cpu",
                        interval_seconds=value,
                    )

    def test_registered_zero_delay_schedule_is_immediately_due(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )

        dispatches = scheduler.claim_due(now_ns=1_000)

        self.assertEqual(len(dispatches), 1)
        dispatch = dispatches[0]
        self.assertEqual(dispatch.collector_name, "cpu")
        self.assertEqual(dispatch.scheduled_for_ns, 1_000)
        self.assertEqual(dispatch.claimed_at_ns, 1_000)
        self.assertEqual(dispatch.lateness_ns, 0)
        self.assertEqual(dispatch.missed_intervals_before_dispatch, 0)

    def test_initial_delay_defers_first_dispatch(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule(
                "memory",
                interval_ns=100,
                initial_delay_ns=50,
            ),
            now_ns=1_000,
        )

        self.assertEqual(scheduler.claim_due(now_ns=1_049), ())
        dispatches = scheduler.claim_due(now_ns=1_050)

        self.assertEqual(len(dispatches), 1)
        self.assertEqual(dispatches[0].scheduled_for_ns, 1_050)

    def test_due_dispatches_use_deterministic_deadline_then_name_order(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("network", interval_ns=100),
            now_ns=1_000,
        )
        scheduler.register(
            CollectorSchedule("disk", interval_ns=100),
            now_ns=1_000,
        )
        scheduler.register(
            CollectorSchedule("memory", interval_ns=100, initial_delay_ns=10),
            now_ns=1_000,
        )

        dispatches = scheduler.claim_due(now_ns=1_010)

        self.assertEqual(
            [dispatch.collector_name for dispatch in dispatches],
            ["disk", "network", "memory"],
        )

    def test_late_claim_skips_old_slots_without_catch_up_burst(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )

        dispatches = scheduler.claim_due(now_ns=1_350)

        self.assertEqual(len(dispatches), 1)
        dispatch = dispatches[0]
        self.assertEqual(dispatch.scheduled_for_ns, 1_300)
        self.assertEqual(dispatch.lateness_ns, 50)
        self.assertEqual(dispatch.missed_intervals_before_dispatch, 3)

        snapshot = scheduler.snapshot("cpu")
        self.assertEqual(snapshot.next_deadline_ns, 1_400)
        self.assertEqual(snapshot.missed_interval_count, 3)

    def test_deadline_phase_does_not_drift_after_late_dispatches(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )

        first = scheduler.claim_due(now_ns=1_035)[0]
        scheduler.complete(first, finished_at_ns=1_040)

        second = scheduler.claim_due(now_ns=1_145)[0]
        scheduler.complete(second, finished_at_ns=1_150)

        self.assertEqual(first.scheduled_for_ns, 1_000)
        self.assertEqual(second.scheduled_for_ns, 1_100)
        self.assertEqual(scheduler.snapshot("cpu").next_deadline_ns, 1_200)

    def test_completion_skips_slots_overlapped_by_long_running_dispatch(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("process", interval_ns=100),
            now_ns=1_000,
        )

        dispatch = scheduler.claim_due(now_ns=1_000)[0]
        scheduler.complete(dispatch, finished_at_ns=1_250)

        snapshot = scheduler.snapshot("process")
        self.assertIs(snapshot.state, CollectorExecutionState.IDLE)
        self.assertEqual(snapshot.overlap_skipped_count, 2)
        self.assertEqual(snapshot.next_deadline_ns, 1_300)
        self.assertEqual(scheduler.claim_due(now_ns=1_299), ())

    def test_polling_running_collector_skips_due_overlap_slots(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("process", interval_ns=100),
            now_ns=1_000,
        )

        dispatch = scheduler.claim_due(now_ns=1_000)[0]
        self.assertEqual(scheduler.claim_due(now_ns=1_250), ())

        running = scheduler.snapshot("process")
        self.assertEqual(running.overlap_skipped_count, 2)
        self.assertEqual(running.next_deadline_ns, 1_300)

        scheduler.complete(dispatch, finished_at_ns=1_260)
        finished = scheduler.snapshot("process")
        self.assertEqual(finished.overlap_skipped_count, 2)

    def test_completion_requires_active_dispatch_sequence(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )
        active = scheduler.claim_due(now_ns=1_000)[0]
        stale = CollectorDispatch(
            collector_name="cpu",
            dispatch_sequence=2,
            scheduled_for_ns=1_000,
            claimed_at_ns=1_000,
            lateness_ns=0,
            missed_intervals_before_dispatch=0,
        )

        with self.assertRaises(ScheduleStateError):
            scheduler.complete(stale, finished_at_ns=1_010)

        self.assertIs(
            scheduler.snapshot("cpu").state,
            CollectorExecutionState.RUNNING,
        )
        scheduler.complete(active, finished_at_ns=1_020)

    def test_duplicate_completion_is_rejected(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )
        dispatch = scheduler.claim_due(now_ns=1_000)[0]
        scheduler.complete(dispatch, finished_at_ns=1_010)

        with self.assertRaises(ScheduleStateError):
            scheduler.complete(dispatch, finished_at_ns=1_020)

    def test_early_completion_is_rejected_without_state_change(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )
        dispatch = scheduler.claim_due(now_ns=1_010)[0]

        with self.assertRaises(ScheduleStateError):
            scheduler.complete(dispatch, finished_at_ns=1_009)

        self.assertIs(
            scheduler.snapshot("cpu").state,
            CollectorExecutionState.RUNNING,
        )

    def test_duplicate_schedule_registration_is_rejected(self) -> None:
        scheduler = CollectorScheduler()
        schedule = CollectorSchedule("cpu", interval_ns=100)
        scheduler.register(schedule, now_ns=1_000)

        with self.assertRaises(DuplicateScheduleError):
            scheduler.register(schedule, now_ns=1_000)

    def test_unknown_schedule_lookup_is_rejected(self) -> None:
        scheduler = CollectorScheduler()

        with self.assertRaises(UnknownScheduleError):
            scheduler.snapshot("missing")

    def test_next_wakeup_delay_tracks_earliest_deadline(self) -> None:
        scheduler = CollectorScheduler()

        self.assertIsNone(scheduler.next_wakeup_delay_ns(now_ns=1_000))

        scheduler.register(
            CollectorSchedule("slow", interval_ns=500, initial_delay_ns=200),
            now_ns=1_000,
        )
        scheduler.register(
            CollectorSchedule("fast", interval_ns=100, initial_delay_ns=50),
            now_ns=1_000,
        )

        self.assertEqual(scheduler.next_wakeup_delay_ns(now_ns=1_020), 30)
        self.assertEqual(scheduler.next_wakeup_delay_ns(now_ns=1_060), 0)

    def test_scheduler_poll_time_regression_is_rejected(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )
        scheduler.claim_due(now_ns=1_010)

        with self.assertRaises(SchedulerClockError):
            scheduler.claim_due(now_ns=1_009)

    def test_snapshot_tracks_execution_and_skip_accounting(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )

        dispatch = scheduler.claim_due(now_ns=1_250)[0]
        running = scheduler.snapshot("cpu")

        self.assertIs(running.state, CollectorExecutionState.RUNNING)
        self.assertEqual(running.dispatch_count, 1)
        self.assertEqual(running.completion_count, 0)
        self.assertEqual(running.missed_interval_count, 2)
        self.assertEqual(running.total_skipped_count, 2)
        self.assertEqual(running.active_dispatch_sequence, 1)

        scheduler.complete(dispatch, finished_at_ns=1_350)
        finished = scheduler.snapshot("cpu")

        self.assertIs(finished.state, CollectorExecutionState.IDLE)
        self.assertEqual(finished.completion_count, 1)
        self.assertEqual(finished.overlap_skipped_count, 1)
        self.assertEqual(finished.total_skipped_count, 3)
        self.assertIsNone(finished.active_dispatch_sequence)
        self.assertEqual(finished.last_finished_at_ns, 1_350)

    def test_all_snapshots_are_returned_in_name_order(self) -> None:
        scheduler = CollectorScheduler()
        scheduler.register(
            CollectorSchedule("network", interval_ns=100),
            now_ns=1_000,
        )
        scheduler.register(
            CollectorSchedule("cpu", interval_ns=100),
            now_ns=1_000,
        )

        snapshots = scheduler.snapshots()

        self.assertEqual(
            [snapshot.schedule.name for snapshot in snapshots],
            ["cpu", "network"],
        )


if __name__ == "__main__":
    unittest.main()
