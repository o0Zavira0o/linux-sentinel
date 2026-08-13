"""Deterministic monotonic collector scheduling primitives for Sentinel-X."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from threading import RLock

_NANOSECONDS_PER_SECOND = 1_000_000_000
_COLLECTOR_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class SchedulingError(RuntimeError):
    """Base error for collector scheduling operations."""


class ScheduleValidationError(ValueError):
    """Raised when a collector schedule is invalid."""


class DuplicateScheduleError(SchedulingError):
    """Raised when a collector name is registered more than once."""


class UnknownScheduleError(SchedulingError):
    """Raised when a collector schedule cannot be found."""


class ScheduleStateError(SchedulingError):
    """Raised when a collector execution transition is invalid."""


class SchedulerClockError(SchedulingError):
    """Raised when scheduler polling time moves backwards."""


class CollectorExecutionState(str, Enum):
    """Execution state tracked for one scheduled collector."""

    IDLE = "idle"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class CollectorSchedule:
    """Fixed-cadence schedule expressed on a monotonic nanosecond timeline."""

    name: str
    interval_ns: int
    initial_delay_ns: int = 0

    def __post_init__(self) -> None:
        """Validate schedule identity and timing values."""

        if not isinstance(self.name, str):
            raise ScheduleValidationError("collector name must be a string")

        normalized_name = self.name.strip()

        if not _COLLECTOR_NAME_PATTERN.fullmatch(normalized_name):
            raise ScheduleValidationError(
                "collector name must be 1-64 characters and contain only "
                "letters, digits, '.', '_', or '-', starting with a letter "
                "or digit"
            )

        _require_positive_int("interval_ns", self.interval_ns)
        _require_nonnegative_int("initial_delay_ns", self.initial_delay_ns)

        object.__setattr__(self, "name", normalized_name)

    @classmethod
    def from_seconds(
        cls,
        name: str,
        *,
        interval_seconds: object,
        initial_delay_seconds: object = 0.0,
    ) -> CollectorSchedule:
        """Build a nanosecond schedule from validated second-based values."""

        interval_ns = _seconds_to_nanoseconds(
            interval_seconds,
            field_name="interval_seconds",
            allow_zero=False,
        )
        initial_delay_ns = _seconds_to_nanoseconds(
            initial_delay_seconds,
            field_name="initial_delay_seconds",
            allow_zero=True,
        )

        return cls(
            name=name,
            interval_ns=interval_ns,
            initial_delay_ns=initial_delay_ns,
        )

    @property
    def interval_seconds(self) -> float:
        """Return the collector cadence in seconds."""

        return self.interval_ns / _NANOSECONDS_PER_SECOND

    @property
    def initial_delay_seconds(self) -> float:
        """Return the initial scheduling delay in seconds."""

        return self.initial_delay_ns / _NANOSECONDS_PER_SECOND

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly schedule metadata."""

        return {
            "name": self.name,
            "interval_ns": self.interval_ns,
            "interval_seconds": self.interval_seconds,
            "initial_delay_ns": self.initial_delay_ns,
            "initial_delay_seconds": self.initial_delay_seconds,
        }


@dataclass(frozen=True, slots=True)
class CollectorDispatch:
    """One no-overlap collector execution claim."""

    collector_name: str
    dispatch_sequence: int
    scheduled_for_ns: int
    claimed_at_ns: int
    lateness_ns: int
    missed_intervals_before_dispatch: int

    def __post_init__(self) -> None:
        """Validate dispatch timing invariants."""

        if not isinstance(self.collector_name, str) or not self.collector_name:
            raise ValueError("collector_name must be a non-empty string")

        _require_positive_int("dispatch_sequence", self.dispatch_sequence)
        _require_nonnegative_int("scheduled_for_ns", self.scheduled_for_ns)
        _require_nonnegative_int("claimed_at_ns", self.claimed_at_ns)
        _require_nonnegative_int("lateness_ns", self.lateness_ns)
        _require_nonnegative_int(
            "missed_intervals_before_dispatch",
            self.missed_intervals_before_dispatch,
        )

        if self.claimed_at_ns < self.scheduled_for_ns:
            raise ValueError("claimed_at_ns must not precede scheduled_for_ns")

        if self.lateness_ns != self.claimed_at_ns - self.scheduled_for_ns:
            raise ValueError("lateness_ns must equal claim time minus scheduled time")

    @property
    def lateness_seconds(self) -> float:
        """Return dispatch lateness in seconds."""

        return self.lateness_ns / _NANOSECONDS_PER_SECOND

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly dispatch metadata."""

        return {
            "collector_name": self.collector_name,
            "dispatch_sequence": self.dispatch_sequence,
            "scheduled_for_ns": self.scheduled_for_ns,
            "claimed_at_ns": self.claimed_at_ns,
            "lateness_ns": self.lateness_ns,
            "lateness_seconds": self.lateness_seconds,
            "missed_intervals_before_dispatch": (self.missed_intervals_before_dispatch),
        }


@dataclass(frozen=True, slots=True)
class CollectorScheduleSnapshot:
    """Read-only scheduler state for one collector."""

    schedule: CollectorSchedule
    state: CollectorExecutionState
    next_deadline_ns: int
    active_dispatch_sequence: int | None
    dispatch_count: int
    completion_count: int
    missed_interval_count: int
    overlap_skipped_count: int
    last_scheduled_for_ns: int | None
    last_claimed_at_ns: int | None
    last_finished_at_ns: int | None

    @property
    def total_skipped_count(self) -> int:
        """Return all cadence slots intentionally skipped by the scheduler."""

        return self.missed_interval_count + self.overlap_skipped_count

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly scheduler state."""

        return {
            "schedule": self.schedule.to_dict(),
            "state": self.state.value,
            "next_deadline_ns": self.next_deadline_ns,
            "active_dispatch_sequence": self.active_dispatch_sequence,
            "dispatch_count": self.dispatch_count,
            "completion_count": self.completion_count,
            "missed_interval_count": self.missed_interval_count,
            "overlap_skipped_count": self.overlap_skipped_count,
            "total_skipped_count": self.total_skipped_count,
            "last_scheduled_for_ns": self.last_scheduled_for_ns,
            "last_claimed_at_ns": self.last_claimed_at_ns,
            "last_finished_at_ns": self.last_finished_at_ns,
        }


@dataclass(slots=True)
class _CollectorScheduleState:
    """Mutable internal scheduler state for one collector."""

    schedule: CollectorSchedule
    state: CollectorExecutionState
    next_deadline_ns: int
    active_dispatch_sequence: int | None = None
    dispatch_count: int = 0
    completion_count: int = 0
    missed_interval_count: int = 0
    overlap_skipped_count: int = 0
    last_scheduled_for_ns: int | None = None
    last_claimed_at_ns: int | None = None
    last_finished_at_ns: int | None = None

    def snapshot(self) -> CollectorScheduleSnapshot:
        """Return an immutable copy of internal scheduler state."""

        return CollectorScheduleSnapshot(
            schedule=self.schedule,
            state=self.state,
            next_deadline_ns=self.next_deadline_ns,
            active_dispatch_sequence=self.active_dispatch_sequence,
            dispatch_count=self.dispatch_count,
            completion_count=self.completion_count,
            missed_interval_count=self.missed_interval_count,
            overlap_skipped_count=self.overlap_skipped_count,
            last_scheduled_for_ns=self.last_scheduled_for_ns,
            last_claimed_at_ns=self.last_claimed_at_ns,
            last_finished_at_ns=self.last_finished_at_ns,
        )


class CollectorScheduler:
    """Fixed-cadence, skip-missed, no-overlap collector scheduler.

    The scheduler is intentionally execution-agnostic. A runtime claims due
    dispatches, runs collector work elsewhere, then completes each dispatch.
    Cadence is anchored to monotonic deadlines rather than completion time so
    collector duration and scheduler lateness do not accumulate phase drift.
    """

    def __init__(self) -> None:
        self._states: dict[str, _CollectorScheduleState] = {}
        self._lock = RLock()
        self._last_poll_ns: int | None = None

    def register(
        self,
        schedule: CollectorSchedule,
        *,
        now_ns: int,
    ) -> None:
        """Register one collector schedule relative to the current clock."""

        if not isinstance(schedule, CollectorSchedule):
            raise TypeError("schedule must be a CollectorSchedule")

        with self._lock:
            normalized_now = self._observe_poll_time_locked(now_ns)

            if schedule.name in self._states:
                raise DuplicateScheduleError(
                    f"collector schedule already registered: {schedule.name}"
                )

            self._states[schedule.name] = _CollectorScheduleState(
                schedule=schedule,
                state=CollectorExecutionState.IDLE,
                next_deadline_ns=normalized_now + schedule.initial_delay_ns,
            )

    def claim_due(self, *, now_ns: int) -> tuple[CollectorDispatch, ...]:
        """Claim due collectors without catch-up bursts or overlap."""

        with self._lock:
            normalized_now = self._observe_poll_time_locked(now_ns)
            ordered_states = sorted(
                self._states.values(),
                key=lambda state: (
                    state.next_deadline_ns,
                    state.schedule.name,
                ),
            )
            dispatches: list[CollectorDispatch] = []

            for state in ordered_states:
                if state.next_deadline_ns > normalized_now:
                    continue

                due_count = (
                    (normalized_now - state.next_deadline_ns)
                    // state.schedule.interval_ns
                ) + 1

                if state.state is CollectorExecutionState.RUNNING:
                    state.overlap_skipped_count += due_count
                    state.next_deadline_ns += due_count * state.schedule.interval_ns
                    continue

                missed_before_dispatch = due_count - 1
                scheduled_for_ns = (
                    state.next_deadline_ns
                    + missed_before_dispatch * state.schedule.interval_ns
                )

                state.missed_interval_count += missed_before_dispatch
                state.next_deadline_ns = scheduled_for_ns + state.schedule.interval_ns
                state.dispatch_count += 1
                state.state = CollectorExecutionState.RUNNING
                state.active_dispatch_sequence = state.dispatch_count
                state.last_scheduled_for_ns = scheduled_for_ns
                state.last_claimed_at_ns = normalized_now

                dispatches.append(
                    CollectorDispatch(
                        collector_name=state.schedule.name,
                        dispatch_sequence=state.dispatch_count,
                        scheduled_for_ns=scheduled_for_ns,
                        claimed_at_ns=normalized_now,
                        lateness_ns=normalized_now - scheduled_for_ns,
                        missed_intervals_before_dispatch=missed_before_dispatch,
                    )
                )

            return tuple(dispatches)

    def complete(
        self,
        dispatch: CollectorDispatch,
        *,
        finished_at_ns: int,
    ) -> None:
        """Complete one active dispatch and skip slots overlapped by its run."""

        if not isinstance(dispatch, CollectorDispatch):
            raise TypeError("dispatch must be a CollectorDispatch")

        normalized_finished = _validate_monotonic_value(
            "finished_at_ns",
            finished_at_ns,
        )

        if normalized_finished < dispatch.claimed_at_ns:
            raise ScheduleStateError(
                "collector completion time must not precede its claim time"
            )

        with self._lock:
            state = self._states.get(dispatch.collector_name)

            if state is None:
                raise UnknownScheduleError(
                    f"collector schedule is not registered: {dispatch.collector_name}"
                )

            if state.state is not CollectorExecutionState.RUNNING:
                raise ScheduleStateError(
                    f"collector is not running: {dispatch.collector_name}"
                )

            if state.active_dispatch_sequence != dispatch.dispatch_sequence:
                raise ScheduleStateError(
                    "dispatch sequence does not match the active collector run"
                )

            if state.next_deadline_ns <= normalized_finished:
                overlapped_count = (
                    (normalized_finished - state.next_deadline_ns)
                    // state.schedule.interval_ns
                ) + 1
                state.overlap_skipped_count += overlapped_count
                state.next_deadline_ns += overlapped_count * state.schedule.interval_ns

            state.state = CollectorExecutionState.IDLE
            state.active_dispatch_sequence = None
            state.completion_count += 1
            state.last_finished_at_ns = normalized_finished

    def next_wakeup_delay_ns(self, *, now_ns: int) -> int | None:
        """Return nanoseconds until the earliest collector deadline."""

        with self._lock:
            normalized_now = self._observe_poll_time_locked(now_ns)

            if not self._states:
                return None

            earliest_deadline = min(
                state.next_deadline_ns for state in self._states.values()
            )

        return max(earliest_deadline - normalized_now, 0)

    def snapshot(self, collector_name: str) -> CollectorScheduleSnapshot:
        """Return scheduler state for one registered collector."""

        if not isinstance(collector_name, str):
            raise TypeError("collector_name must be a string")

        with self._lock:
            state = self._states.get(collector_name)

            if state is None:
                raise UnknownScheduleError(
                    f"collector schedule is not registered: {collector_name}"
                )

            return state.snapshot()

    def snapshots(self) -> tuple[CollectorScheduleSnapshot, ...]:
        """Return all registered schedules in deterministic name order."""

        with self._lock:
            return tuple(self._states[name].snapshot() for name in sorted(self._states))

    def _observe_poll_time_locked(self, now_ns: int) -> int:
        """Validate scheduler-thread monotonic time while holding the lock."""

        normalized_now = _validate_monotonic_value("now_ns", now_ns)

        if self._last_poll_ns is not None and normalized_now < self._last_poll_ns:
            raise SchedulerClockError(
                "scheduler polling time moved backwards: "
                f"{normalized_now} < {self._last_poll_ns}"
            )

        self._last_poll_ns = normalized_now
        return normalized_now


def _seconds_to_nanoseconds(
    value: object,
    *,
    field_name: str,
    allow_zero: bool,
) -> int:
    """Convert a finite second value to integer nanoseconds."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScheduleValidationError(f"{field_name} must be a number")

    normalized = float(value)

    if not math.isfinite(normalized):
        raise ScheduleValidationError(f"{field_name} must be finite")

    if normalized < 0.0 or (not allow_zero and normalized == 0.0):
        comparison = "non-negative" if allow_zero else "greater than zero"
        raise ScheduleValidationError(f"{field_name} must be {comparison}")

    nanoseconds = round(normalized * _NANOSECONDS_PER_SECOND)

    if not allow_zero and nanoseconds <= 0:
        raise ScheduleValidationError(
            f"{field_name} is below the supported one-nanosecond resolution"
        )

    if allow_zero and nanoseconds < 0:
        raise ScheduleValidationError(f"{field_name} must be non-negative")

    return nanoseconds


def _validate_monotonic_value(name: str, value: int) -> int:
    """Validate one non-negative monotonic nanosecond value."""

    _require_nonnegative_int(name, value)
    return value


def _require_positive_int(name: str, value: int) -> None:
    """Require a strictly positive integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ScheduleValidationError(f"{name} must be an integer")

    if value <= 0:
        raise ScheduleValidationError(f"{name} must be greater than zero")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ScheduleValidationError(f"{name} must be an integer")

    if value < 0:
        raise ScheduleValidationError(f"{name} must be non-negative")
