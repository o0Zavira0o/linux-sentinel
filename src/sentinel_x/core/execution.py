"""Failure-isolated collector execution primitives for Sentinel-X."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock

from sentinel_x.core.scheduling import (
    CollectorDispatch,
    CollectorScheduler,
)

_NANOSECONDS_PER_SECOND = 1_000_000_000
_MAX_ERROR_MESSAGE_CHARS = 512
_COLLECTOR_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class CollectorExecutionError(RuntimeError):
    """Base error for collector execution operations."""


class CollectorExecutionValidationError(ValueError):
    """Raised when execution configuration is invalid."""


class CollectorSpecMismatchError(CollectorExecutionError):
    """Raised when a dispatch is paired with the wrong collector spec."""


class ExecutionClockError(CollectorExecutionError):
    """Raised when the execution clock violates monotonic invariants."""


class UnknownExecutionStateError(CollectorExecutionError):
    """Raised when execution state has not been observed for a collector."""


class CollectorExecutionStatus(str, Enum):
    """Outcome status for one scheduler dispatch."""

    SUCCESS = "success"
    FAILED = "failed"
    BACKOFF_SKIPPED = "backoff_skipped"


@dataclass(frozen=True, slots=True)
class CollectorExecutionFailure:
    """Bounded exception metadata captured from one collector invocation."""

    error_type: str
    error_message: str

    def __post_init__(self) -> None:
        """Validate failure metadata."""

        if not isinstance(self.error_type, str) or not self.error_type:
            raise ValueError("error_type must be a non-empty string")
        if not isinstance(self.error_message, str):
            raise ValueError("error_message must be a string")
        if len(self.error_message) > _MAX_ERROR_MESSAGE_CHARS:
            raise ValueError("error_message exceeds the bounded message limit")

    @classmethod
    def from_exception(cls, exc: Exception) -> CollectorExecutionFailure:
        """Create bounded metadata without retaining traceback objects."""

        error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
        message = str(exc)

        if len(message) > _MAX_ERROR_MESSAGE_CHARS:
            message = message[: _MAX_ERROR_MESSAGE_CHARS - 3] + "..."

        return cls(
            error_type=error_type,
            error_message=message,
        )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly failure metadata."""

        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class CollectorExecutionPolicy:
    """Execution budget and bounded exponential failure-backoff policy."""

    budget_ns: int | None = None
    failure_backoff_initial_ns: int = 0
    failure_backoff_max_ns: int = 0

    def __post_init__(self) -> None:
        """Validate execution policy values."""

        if self.budget_ns is not None:
            _require_positive_int("budget_ns", self.budget_ns)

        _require_nonnegative_int(
            "failure_backoff_initial_ns",
            self.failure_backoff_initial_ns,
        )
        _require_nonnegative_int(
            "failure_backoff_max_ns",
            self.failure_backoff_max_ns,
        )

        if self.failure_backoff_initial_ns == 0:
            if self.failure_backoff_max_ns != 0:
                raise CollectorExecutionValidationError(
                    "failure_backoff_max_ns must be zero when backoff is disabled"
                )
        elif self.failure_backoff_max_ns < self.failure_backoff_initial_ns:
            raise CollectorExecutionValidationError(
                "failure_backoff_max_ns must be at least the initial backoff"
            )

    @classmethod
    def from_seconds(
        cls,
        *,
        budget_seconds: object | None = None,
        failure_backoff_initial_seconds: object = 0.0,
        failure_backoff_max_seconds: object = 0.0,
    ) -> CollectorExecutionPolicy:
        """Build a nanosecond policy from validated second-based values."""

        budget_ns = None
        if budget_seconds is not None:
            budget_ns = _seconds_to_nanoseconds(
                budget_seconds,
                field_name="budget_seconds",
                allow_zero=False,
            )

        initial_ns = _seconds_to_nanoseconds(
            failure_backoff_initial_seconds,
            field_name="failure_backoff_initial_seconds",
            allow_zero=True,
        )
        maximum_ns = _seconds_to_nanoseconds(
            failure_backoff_max_seconds,
            field_name="failure_backoff_max_seconds",
            allow_zero=True,
        )

        return cls(
            budget_ns=budget_ns,
            failure_backoff_initial_ns=initial_ns,
            failure_backoff_max_ns=maximum_ns,
        )

    @property
    def budget_seconds(self) -> float | None:
        """Return execution budget in seconds when configured."""

        if self.budget_ns is None:
            return None
        return self.budget_ns / _NANOSECONDS_PER_SECOND

    @property
    def backoff_enabled(self) -> bool:
        """Return whether failure backoff is enabled."""

        return self.failure_backoff_initial_ns > 0

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly policy metadata."""

        return {
            "budget_ns": self.budget_ns,
            "budget_seconds": self.budget_seconds,
            "failure_backoff_initial_ns": self.failure_backoff_initial_ns,
            "failure_backoff_max_ns": self.failure_backoff_max_ns,
            "backoff_enabled": self.backoff_enabled,
        }


@dataclass(frozen=True, slots=True)
class CollectorExecutionSpec:
    """Executable collector callable and its execution policy."""

    name: str
    handler: Callable[[], object]
    policy: CollectorExecutionPolicy = field(default_factory=CollectorExecutionPolicy)

    def __post_init__(self) -> None:
        """Validate collector identity and callable contract."""

        if not isinstance(self.name, str):
            raise CollectorExecutionValidationError("collector name must be a string")

        normalized_name = self.name.strip()
        if not _COLLECTOR_NAME_PATTERN.fullmatch(normalized_name):
            raise CollectorExecutionValidationError(
                "collector name must match scheduler collector naming rules"
            )

        if not callable(self.handler):
            raise CollectorExecutionValidationError("handler must be callable")

        if not isinstance(self.policy, CollectorExecutionPolicy):
            raise CollectorExecutionValidationError(
                "policy must be a CollectorExecutionPolicy"
            )

        object.__setattr__(self, "name", normalized_name)


@dataclass(frozen=True, slots=True)
class CollectorExecutionOutcome:
    """Measured and failure-isolated outcome for one scheduler dispatch."""

    dispatch: CollectorDispatch
    status: CollectorExecutionStatus
    started_at_ns: int
    finished_at_ns: int
    queue_delay_ns: int
    duration_ns: int
    completion_lateness_ns: int
    budget_ns: int | None
    budget_exceeded: bool
    consecutive_failure_count: int
    backoff_until_ns: int | None
    failure: CollectorExecutionFailure | None
    result: object | None = None

    def __post_init__(self) -> None:
        """Validate execution outcome invariants."""

        _require_nonnegative_int("started_at_ns", self.started_at_ns)
        _require_nonnegative_int("finished_at_ns", self.finished_at_ns)
        _require_nonnegative_int("queue_delay_ns", self.queue_delay_ns)
        _require_nonnegative_int("duration_ns", self.duration_ns)
        _require_nonnegative_int(
            "completion_lateness_ns",
            self.completion_lateness_ns,
        )
        _require_nonnegative_int(
            "consecutive_failure_count",
            self.consecutive_failure_count,
        )

        if self.started_at_ns < self.dispatch.claimed_at_ns:
            raise ValueError("started_at_ns must not precede dispatch claim time")
        if self.finished_at_ns < self.started_at_ns:
            raise ValueError("finished_at_ns must not precede started_at_ns")
        if self.queue_delay_ns != self.started_at_ns - self.dispatch.claimed_at_ns:
            raise ValueError("queue_delay_ns does not match execution timestamps")
        if self.duration_ns != self.finished_at_ns - self.started_at_ns:
            raise ValueError("duration_ns does not match execution timestamps")
        if self.completion_lateness_ns != (
            self.finished_at_ns - self.dispatch.scheduled_for_ns
        ):
            raise ValueError(
                "completion_lateness_ns does not match dispatch completion time"
            )

        if self.budget_ns is not None:
            _require_positive_int("budget_ns", self.budget_ns)
            if self.budget_exceeded != (self.duration_ns > self.budget_ns):
                raise ValueError("budget_exceeded does not match execution duration")
        elif self.budget_exceeded:
            raise ValueError("budget_exceeded requires a configured budget")

        if self.backoff_until_ns is not None:
            _require_nonnegative_int("backoff_until_ns", self.backoff_until_ns)

        if self.status is CollectorExecutionStatus.SUCCESS:
            if self.failure is not None:
                raise ValueError(
                    "successful execution must not contain failure metadata"
                )
            if self.consecutive_failure_count != 0:
                raise ValueError("successful execution must reset failure count")
            if self.backoff_until_ns is not None:
                raise ValueError("successful execution must clear failure backoff")
        elif self.status is CollectorExecutionStatus.FAILED:
            if self.failure is None:
                raise ValueError("failed execution requires failure metadata")
            if self.result is not None:
                raise ValueError("failed execution must not expose a result")
            if self.consecutive_failure_count <= 0:
                raise ValueError("failed execution requires a positive failure count")
        elif self.status is CollectorExecutionStatus.BACKOFF_SKIPPED:
            if self.failure is not None or self.result is not None:
                raise ValueError("backoff-skipped execution has no result or failure")
            if self.duration_ns != 0:
                raise ValueError("backoff-skipped execution must have zero duration")
            if self.budget_exceeded:
                raise ValueError("backoff-skipped execution cannot exceed a budget")
            if self.consecutive_failure_count <= 0:
                raise ValueError("backoff skip requires an active failure streak")
            if self.backoff_until_ns is None:
                raise ValueError("backoff skip requires backoff_until_ns")
            if self.started_at_ns >= self.backoff_until_ns:
                raise ValueError("backoff skip must occur before backoff_until_ns")

    @property
    def queue_delay_seconds(self) -> float:
        """Return delay between scheduler claim and handler start."""

        return self.queue_delay_ns / _NANOSECONDS_PER_SECOND

    @property
    def duration_seconds(self) -> float:
        """Return measured handler execution duration."""

        return self.duration_ns / _NANOSECONDS_PER_SECOND

    @property
    def completion_lateness_seconds(self) -> float:
        """Return completion lateness relative to scheduled deadline."""

        return self.completion_lateness_ns / _NANOSECONDS_PER_SECOND

    def to_dict(self) -> dict[str, object]:
        """Return bounded metadata without serializing arbitrary handler results."""

        return {
            "dispatch": self.dispatch.to_dict(),
            "status": self.status.value,
            "started_at_ns": self.started_at_ns,
            "finished_at_ns": self.finished_at_ns,
            "queue_delay_ns": self.queue_delay_ns,
            "queue_delay_seconds": self.queue_delay_seconds,
            "duration_ns": self.duration_ns,
            "duration_seconds": self.duration_seconds,
            "completion_lateness_ns": self.completion_lateness_ns,
            "completion_lateness_seconds": self.completion_lateness_seconds,
            "budget_ns": self.budget_ns,
            "budget_exceeded": self.budget_exceeded,
            "consecutive_failure_count": self.consecutive_failure_count,
            "backoff_until_ns": self.backoff_until_ns,
            "failure": None if self.failure is None else self.failure.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CollectorExecutionSnapshot:
    """Read-only execution-layer state for one collector."""

    collector_name: str
    dispatch_count: int
    handler_invocation_count: int
    success_count: int
    failure_count: int
    backoff_skipped_count: int
    budget_exceeded_count: int
    consecutive_failure_count: int
    backoff_until_ns: int | None
    last_status: CollectorExecutionStatus | None
    last_started_at_ns: int | None
    last_finished_at_ns: int | None
    last_duration_ns: int | None
    last_failure: CollectorExecutionFailure | None

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly execution-layer state."""

        return {
            "collector_name": self.collector_name,
            "dispatch_count": self.dispatch_count,
            "handler_invocation_count": self.handler_invocation_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "backoff_skipped_count": self.backoff_skipped_count,
            "budget_exceeded_count": self.budget_exceeded_count,
            "consecutive_failure_count": self.consecutive_failure_count,
            "backoff_until_ns": self.backoff_until_ns,
            "last_status": None if self.last_status is None else self.last_status.value,
            "last_started_at_ns": self.last_started_at_ns,
            "last_finished_at_ns": self.last_finished_at_ns,
            "last_duration_ns": self.last_duration_ns,
            "last_failure": (
                None if self.last_failure is None else self.last_failure.to_dict()
            ),
        }


@dataclass(slots=True)
class _CollectorExecutionState:
    """Mutable internal execution statistics for one collector."""

    collector_name: str
    dispatch_count: int = 0
    handler_invocation_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    backoff_skipped_count: int = 0
    budget_exceeded_count: int = 0
    consecutive_failure_count: int = 0
    backoff_until_ns: int | None = None
    last_status: CollectorExecutionStatus | None = None
    last_started_at_ns: int | None = None
    last_finished_at_ns: int | None = None
    last_duration_ns: int | None = None
    last_failure: CollectorExecutionFailure | None = None

    def snapshot(self) -> CollectorExecutionSnapshot:
        """Return an immutable copy of execution state."""

        return CollectorExecutionSnapshot(
            collector_name=self.collector_name,
            dispatch_count=self.dispatch_count,
            handler_invocation_count=self.handler_invocation_count,
            success_count=self.success_count,
            failure_count=self.failure_count,
            backoff_skipped_count=self.backoff_skipped_count,
            budget_exceeded_count=self.budget_exceeded_count,
            consecutive_failure_count=self.consecutive_failure_count,
            backoff_until_ns=self.backoff_until_ns,
            last_status=self.last_status,
            last_started_at_ns=self.last_started_at_ns,
            last_finished_at_ns=self.last_finished_at_ns,
            last_duration_ns=self.last_duration_ns,
            last_failure=self.last_failure,
        )


class CollectorExecutor:
    """Run claimed collector dispatches with failure and backoff isolation."""

    def __init__(
        self,
        scheduler: CollectorScheduler,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not isinstance(scheduler, CollectorScheduler):
            raise TypeError("scheduler must be a CollectorScheduler")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")

        self._scheduler = scheduler
        self._clock_ns = clock_ns
        self._lock = RLock()
        self._states: dict[str, _CollectorExecutionState] = {}

    def execute(
        self,
        dispatch: CollectorDispatch,
        spec: CollectorExecutionSpec,
    ) -> CollectorExecutionOutcome:
        """Execute one claimed dispatch and always release scheduler state."""

        if not isinstance(dispatch, CollectorDispatch):
            raise TypeError("dispatch must be a CollectorDispatch")
        try:
            validated_spec = _require_execution_spec(spec)
        except TypeError:
            self._release_dispatch(dispatch, finished_at_ns=dispatch.claimed_at_ns)
            raise

        spec = validated_spec
        if dispatch.collector_name != spec.name:
            self._release_dispatch(dispatch, finished_at_ns=dispatch.claimed_at_ns)
            raise CollectorSpecMismatchError(
                "dispatch collector does not match execution spec: "
                f"{dispatch.collector_name} != {spec.name}"
            )

        try:
            started_at_ns = self._read_clock_ns()
        except ExecutionClockError:
            self._release_dispatch(dispatch, finished_at_ns=dispatch.claimed_at_ns)
            raise

        if started_at_ns < dispatch.claimed_at_ns:
            self._release_dispatch(dispatch, finished_at_ns=dispatch.claimed_at_ns)
            raise ExecutionClockError(
                "execution clock precedes scheduler claim time: "
                f"{started_at_ns} < {dispatch.claimed_at_ns}"
            )

        state = self._get_or_create_state(spec.name)
        with self._lock:
            backoff_until_ns = state.backoff_until_ns
            consecutive_failures = state.consecutive_failure_count

        if backoff_until_ns is not None and started_at_ns < backoff_until_ns:
            self._scheduler.complete(dispatch, finished_at_ns=started_at_ns)
            outcome = self._build_outcome(
                dispatch=dispatch,
                status=CollectorExecutionStatus.BACKOFF_SKIPPED,
                started_at_ns=started_at_ns,
                finished_at_ns=started_at_ns,
                policy=spec.policy,
                consecutive_failure_count=consecutive_failures,
                backoff_until_ns=backoff_until_ns,
                failure=None,
                result=None,
            )
            self._record_outcome(spec.name, outcome, handler_invoked=False)
            return outcome

        result: object | None = None
        failure: CollectorExecutionFailure | None = None
        status = CollectorExecutionStatus.SUCCESS

        try:
            result = spec.handler()
        except Exception as exc:
            status = CollectorExecutionStatus.FAILED
            failure = CollectorExecutionFailure.from_exception(exc)
        except BaseException:
            finished_at_ns = self._read_finish_or_release(
                dispatch,
                started_at_ns=started_at_ns,
            )
            self._scheduler.complete(dispatch, finished_at_ns=finished_at_ns)
            raise

        finished_at_ns = self._read_finish_or_release(
            dispatch,
            started_at_ns=started_at_ns,
        )

        if status is CollectorExecutionStatus.FAILED:
            consecutive_failures += 1
            backoff_until_ns = _backoff_until_ns(
                policy=spec.policy,
                consecutive_failure_count=consecutive_failures,
                finished_at_ns=finished_at_ns,
            )
            result = None
        else:
            consecutive_failures = 0
            backoff_until_ns = None

        outcome = self._build_outcome(
            dispatch=dispatch,
            status=status,
            started_at_ns=started_at_ns,
            finished_at_ns=finished_at_ns,
            policy=spec.policy,
            consecutive_failure_count=consecutive_failures,
            backoff_until_ns=backoff_until_ns,
            failure=failure,
            result=result,
        )

        # Record policy state while the scheduler still marks this collector
        # RUNNING. This closes the completion-to-next-claim race for backoff.
        self._record_outcome(spec.name, outcome, handler_invoked=True)
        self._scheduler.complete(dispatch, finished_at_ns=finished_at_ns)
        return outcome

    def snapshot(self, collector_name: str) -> CollectorExecutionSnapshot:
        """Return execution state for one collector observed by the executor."""

        if not isinstance(collector_name, str):
            raise TypeError("collector_name must be a string")

        with self._lock:
            state = self._states.get(collector_name)
            if state is None:
                raise UnknownExecutionStateError(
                    f"collector execution state is unknown: {collector_name}"
                )
            return state.snapshot()

    def snapshots(self) -> tuple[CollectorExecutionSnapshot, ...]:
        """Return execution states in deterministic collector-name order."""

        with self._lock:
            return tuple(self._states[name].snapshot() for name in sorted(self._states))

    def _build_outcome(
        self,
        *,
        dispatch: CollectorDispatch,
        status: CollectorExecutionStatus,
        started_at_ns: int,
        finished_at_ns: int,
        policy: CollectorExecutionPolicy,
        consecutive_failure_count: int,
        backoff_until_ns: int | None,
        failure: CollectorExecutionFailure | None,
        result: object | None,
    ) -> CollectorExecutionOutcome:
        """Build one validated execution outcome."""

        duration_ns = finished_at_ns - started_at_ns
        budget_exceeded = (
            policy.budget_ns is not None and duration_ns > policy.budget_ns
        )

        return CollectorExecutionOutcome(
            dispatch=dispatch,
            status=status,
            started_at_ns=started_at_ns,
            finished_at_ns=finished_at_ns,
            queue_delay_ns=started_at_ns - dispatch.claimed_at_ns,
            duration_ns=duration_ns,
            completion_lateness_ns=finished_at_ns - dispatch.scheduled_for_ns,
            budget_ns=policy.budget_ns,
            budget_exceeded=budget_exceeded,
            consecutive_failure_count=consecutive_failure_count,
            backoff_until_ns=backoff_until_ns,
            failure=failure,
            result=result,
        )

    def _record_outcome(
        self,
        collector_name: str,
        outcome: CollectorExecutionOutcome,
        *,
        handler_invoked: bool,
    ) -> None:
        """Update bounded execution statistics for one collector."""

        state = self._get_or_create_state(collector_name)
        with self._lock:
            state.dispatch_count += 1
            if handler_invoked:
                state.handler_invocation_count += 1
            if outcome.status is CollectorExecutionStatus.SUCCESS:
                state.success_count += 1
            elif outcome.status is CollectorExecutionStatus.FAILED:
                state.failure_count += 1
            else:
                state.backoff_skipped_count += 1

            if outcome.budget_exceeded:
                state.budget_exceeded_count += 1

            state.consecutive_failure_count = outcome.consecutive_failure_count
            state.backoff_until_ns = outcome.backoff_until_ns
            state.last_status = outcome.status
            state.last_started_at_ns = outcome.started_at_ns
            state.last_finished_at_ns = outcome.finished_at_ns
            state.last_duration_ns = outcome.duration_ns
            if outcome.failure is not None:
                state.last_failure = outcome.failure

    def _get_or_create_state(self, collector_name: str) -> _CollectorExecutionState:
        """Return mutable execution state, creating it when first observed."""

        with self._lock:
            state = self._states.get(collector_name)
            if state is None:
                state = _CollectorExecutionState(collector_name=collector_name)
                self._states[collector_name] = state
            return state

    def _read_clock_ns(self) -> int:
        """Read and validate the injected execution monotonic clock."""

        value = self._clock_ns()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExecutionClockError(
                "execution clock must return a non-negative integer nanosecond value"
            )
        return value

    def _read_finish_or_release(
        self,
        dispatch: CollectorDispatch,
        *,
        started_at_ns: int,
    ) -> int:
        """Read finish time while guaranteeing scheduler claim release on error."""

        try:
            finished_at_ns = self._read_clock_ns()
        except ExecutionClockError:
            self._release_dispatch(dispatch, finished_at_ns=started_at_ns)
            raise

        if finished_at_ns < started_at_ns:
            self._release_dispatch(dispatch, finished_at_ns=started_at_ns)
            raise ExecutionClockError(
                "execution clock moved backwards during collector run: "
                f"{finished_at_ns} < {started_at_ns}"
            )

        return finished_at_ns

    def _release_dispatch(
        self,
        dispatch: CollectorDispatch,
        *,
        finished_at_ns: int,
    ) -> None:
        """Release scheduler state after an executor-side validation failure."""

        self._scheduler.complete(dispatch, finished_at_ns=finished_at_ns)


def _require_execution_spec(value: object) -> CollectorExecutionSpec:
    """Return a validated execution specification for runtime callers."""

    if not isinstance(value, CollectorExecutionSpec):
        raise TypeError("spec must be a CollectorExecutionSpec")
    return value


def _backoff_until_ns(
    *,
    policy: CollectorExecutionPolicy,
    consecutive_failure_count: int,
    finished_at_ns: int,
) -> int | None:
    """Return bounded exponential retry suppression deadline."""

    if not policy.backoff_enabled:
        return None

    initial = policy.failure_backoff_initial_ns
    maximum = policy.failure_backoff_max_ns
    ratio = maximum // initial
    max_doublings = max(ratio.bit_length(), 1)
    doublings = min(consecutive_failure_count - 1, max_doublings)
    delay_ns = min(initial * (1 << doublings), maximum)
    return finished_at_ns + delay_ns


def _seconds_to_nanoseconds(
    value: object,
    *,
    field_name: str,
    allow_zero: bool,
) -> int:
    """Convert a finite second value to integer nanoseconds."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectorExecutionValidationError(f"{field_name} must be a number")

    normalized = float(value)
    if not math.isfinite(normalized):
        raise CollectorExecutionValidationError(f"{field_name} must be finite")

    if normalized < 0.0 or (not allow_zero and normalized == 0.0):
        comparison = "non-negative" if allow_zero else "greater than zero"
        raise CollectorExecutionValidationError(f"{field_name} must be {comparison}")

    nanoseconds = round(normalized * _NANOSECONDS_PER_SECOND)
    if not allow_zero and nanoseconds <= 0:
        raise CollectorExecutionValidationError(
            f"{field_name} is below the supported one-nanosecond resolution"
        )
    if allow_zero and nanoseconds < 0:
        raise CollectorExecutionValidationError(f"{field_name} must be non-negative")
    return nanoseconds


def _require_positive_int(name: str, value: int) -> None:
    """Require a positive non-boolean integer."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CollectorExecutionValidationError(f"{name} must be a positive integer")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative non-boolean integer."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CollectorExecutionValidationError(
            f"{name} must be a non-negative integer"
        )
