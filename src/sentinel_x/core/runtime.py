"""Scheduled collector runtime orchestration for Sentinel-X."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Final, Protocol, runtime_checkable

from sentinel_x.core.bus import EventBus, PublishReport
from sentinel_x.core.events import (
    EventKind,
    EventSeverity,
    SentinelEvent,
)
from sentinel_x.core.execution import (
    CollectorExecutionOutcome,
    CollectorExecutionSnapshot,
    CollectorExecutionStatus,
    CollectorExecutor,
)
from sentinel_x.core.registry import CollectorRegistry
from sentinel_x.core.scheduling import (
    CollectorScheduleSnapshot,
    CollectorScheduler,
)

_NANOSECONDS_PER_SECOND: Final[int] = 1_000_000_000
_RUNTIME_OBSERVATION_TYPE: Final[str] = "sentinel.runtime.collector_execution"


class CollectorRuntimeError(RuntimeError):
    """Base error raised by scheduled collector runtime orchestration."""


class CollectorRuntimeContractError(CollectorRuntimeError):
    """Raised when a trusted collector violates the runtime result contract."""


class CollectorRuntimeClockError(CollectorRuntimeError):
    """Raised when the injected runtime clock returns an invalid value."""


@runtime_checkable
class CollectorEmissionResult(Protocol):
    """Structural result contract returned by trusted observation collectors."""

    @property
    def collector_name(self) -> str:
        """Return the collector identity that produced this result."""

    @property
    def event(self) -> SentinelEvent | None:
        """Return the emitted event, or None for a successful warm-up."""


@dataclass(frozen=True, slots=True)
class CollectorRuntimeCycle:
    """Bounded result of one due-dispatch polling cycle."""

    claimed_count: int
    emitted_count: int
    warmup_count: int
    failed_count: int
    backoff_skipped_count: int
    budget_exceeded_count: int
    publish_failure_count: int
    outcomes: tuple[CollectorExecutionOutcome, ...]

    def __post_init__(self) -> None:
        """Validate cycle accounting."""

        integer_fields = (
            ("claimed_count", self.claimed_count),
            ("emitted_count", self.emitted_count),
            ("warmup_count", self.warmup_count),
            ("failed_count", self.failed_count),
            ("backoff_skipped_count", self.backoff_skipped_count),
            ("budget_exceeded_count", self.budget_exceeded_count),
            ("publish_failure_count", self.publish_failure_count),
        )
        for field_name, field_value in integer_fields:
            _require_nonnegative_int(field_name, field_value)

        if self.claimed_count != len(self.outcomes):
            raise ValueError("claimed_count must equal the number of outcomes")

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly cycle metadata."""

        return {
            "claimed_count": self.claimed_count,
            "emitted_count": self.emitted_count,
            "warmup_count": self.warmup_count,
            "failed_count": self.failed_count,
            "backoff_skipped_count": self.backoff_skipped_count,
            "budget_exceeded_count": self.budget_exceeded_count,
            "publish_failure_count": self.publish_failure_count,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


@dataclass(frozen=True, slots=True)
class CollectorRuntimeSnapshot:
    """Read-only aggregate state for the scheduled collector runtime."""

    configured_count: int
    enabled_count: int
    disabled_count: int
    cycle_count: int
    claimed_count: int
    emitted_count: int
    warmup_count: int
    failed_count: int
    backoff_skipped_count: int
    budget_exceeded_count: int
    publish_failure_count: int
    schedules: tuple[CollectorScheduleSnapshot, ...]
    executions: tuple[CollectorExecutionSnapshot, ...]

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly runtime state."""

        return {
            "configured_count": self.configured_count,
            "enabled_count": self.enabled_count,
            "disabled_count": self.disabled_count,
            "cycle_count": self.cycle_count,
            "claimed_count": self.claimed_count,
            "emitted_count": self.emitted_count,
            "warmup_count": self.warmup_count,
            "failed_count": self.failed_count,
            "backoff_skipped_count": self.backoff_skipped_count,
            "budget_exceeded_count": self.budget_exceeded_count,
            "publish_failure_count": self.publish_failure_count,
            "schedules": [snapshot.to_dict() for snapshot in self.schedules],
            "executions": [snapshot.to_dict() for snapshot in self.executions],
        }


class CollectorRuntime:
    """Coordinate scheduling, execution, publication, and runtime accounting."""

    SOURCE: Final[str] = "sentinel_x.core.collector_runtime"

    def __init__(
        self,
        event_bus: EventBus,
        registry: CollectorRegistry,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not isinstance(event_bus, EventBus):
            raise TypeError("event_bus must be an EventBus")
        if not isinstance(registry, CollectorRegistry):
            raise TypeError("registry must be a CollectorRegistry")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")

        self._event_bus = event_bus
        self._registry = registry
        self._clock_ns = clock_ns
        initialized_at_ns = self._read_clock_ns()
        self._scheduler: CollectorScheduler = registry.create_scheduler(
            now_ns=initialized_at_ns
        )
        self._executor = CollectorExecutor(
            self._scheduler,
            clock_ns=clock_ns,
        )
        self._lock = RLock()
        self._cycle_count = 0
        self._claimed_count = 0
        self._emitted_count = 0
        self._warmup_count = 0
        self._failed_count = 0
        self._backoff_skipped_count = 0
        self._budget_exceeded_count = 0
        self._publish_failure_count = 0

    def run_due(self) -> CollectorRuntimeCycle:
        """Claim and synchronously execute all collectors due at the current time."""

        now_ns = self._read_clock_ns()
        dispatches = self._scheduler.claim_due(now_ns=now_ns)
        outcomes: list[CollectorExecutionOutcome] = []
        emitted_count = 0
        warmup_count = 0
        failed_count = 0
        backoff_skipped_count = 0
        budget_exceeded_count = 0
        publish_failure_count = 0

        for dispatch in dispatches:
            execution_spec = self._registry.execution_spec(dispatch.collector_name)
            outcome = self._executor.execute(dispatch, execution_spec)
            outcomes.append(outcome)

            if outcome.status is CollectorExecutionStatus.SUCCESS:
                emitted, warmed_up, publication_failures = self._handle_success(outcome)
                emitted_count += emitted
                warmup_count += warmed_up
                publish_failure_count += publication_failures

            elif outcome.status is CollectorExecutionStatus.FAILED:
                failed_count += 1
                publish_failure_count += len(
                    self._publish_execution_diagnostic(outcome).failures
                )

            elif outcome.status is CollectorExecutionStatus.BACKOFF_SKIPPED:
                backoff_skipped_count += 1
                publish_failure_count += len(
                    self._publish_execution_diagnostic(outcome).failures
                )

            if outcome.budget_exceeded:
                budget_exceeded_count += 1
                if outcome.status is CollectorExecutionStatus.SUCCESS:
                    publish_failure_count += len(
                        self._publish_execution_diagnostic(outcome).failures
                    )

        cycle = CollectorRuntimeCycle(
            claimed_count=len(dispatches),
            emitted_count=emitted_count,
            warmup_count=warmup_count,
            failed_count=failed_count,
            backoff_skipped_count=backoff_skipped_count,
            budget_exceeded_count=budget_exceeded_count,
            publish_failure_count=publish_failure_count,
            outcomes=tuple(outcomes),
        )
        self._record_cycle(cycle)
        return cycle

    def next_wakeup_delay_seconds(
        self,
        *,
        maximum_seconds: float,
    ) -> float:
        """Return the earlier of the next collector deadline and wake ceiling."""

        maximum = _validate_positive_seconds(
            "maximum_seconds",
            maximum_seconds,
        )
        now_ns = self._read_clock_ns()
        delay_ns = self._scheduler.next_wakeup_delay_ns(now_ns=now_ns)

        if delay_ns is None:
            return maximum

        return min(delay_ns / _NANOSECONDS_PER_SECOND, maximum)

    def snapshot(self) -> CollectorRuntimeSnapshot:
        """Return a thread-safe aggregate runtime snapshot."""

        with self._lock:
            return CollectorRuntimeSnapshot(
                configured_count=self._registry.collector_count,
                enabled_count=self._registry.enabled_count,
                disabled_count=self._registry.disabled_count,
                cycle_count=self._cycle_count,
                claimed_count=self._claimed_count,
                emitted_count=self._emitted_count,
                warmup_count=self._warmup_count,
                failed_count=self._failed_count,
                backoff_skipped_count=self._backoff_skipped_count,
                budget_exceeded_count=self._budget_exceeded_count,
                publish_failure_count=self._publish_failure_count,
                schedules=self._scheduler.snapshots(),
                executions=self._executor.snapshots(),
            )

    def _handle_success(
        self,
        outcome: CollectorExecutionOutcome,
    ) -> tuple[int, int, int]:
        """Validate one successful handler result and publish its event."""

        result = outcome.result
        if not isinstance(result, CollectorEmissionResult):
            raise CollectorRuntimeContractError(
                "successful collector execution must return an emission result"
            )
        if result.collector_name != outcome.dispatch.collector_name:
            raise CollectorRuntimeContractError(
                "collector emission identity does not match its dispatch"
            )

        event = result.event
        if event is None:
            return 0, 1, 0
        if not isinstance(event, SentinelEvent):
            raise CollectorRuntimeContractError(
                "collector emission event must be a SentinelEvent or None"
            )

        report = self._event_bus.publish(event)
        return 1, 0, len(report.failures)

    def _publish_execution_diagnostic(
        self,
        outcome: CollectorExecutionOutcome,
    ) -> PublishReport:
        """Publish bounded metadata for non-nominal collector execution."""

        if outcome.status is CollectorExecutionStatus.FAILED:
            severity = EventSeverity.ERROR
            message = "Scheduled collector execution failed."
        elif outcome.status is CollectorExecutionStatus.BACKOFF_SKIPPED:
            severity = EventSeverity.WARNING
            message = "Scheduled collector execution skipped during failure backoff."
        elif outcome.budget_exceeded:
            severity = EventSeverity.WARNING
            message = "Scheduled collector exceeded its execution budget."
        else:
            raise CollectorRuntimeContractError(
                "execution diagnostic requested for a nominal outcome"
            )

        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source=self.SOURCE,
            message=message,
            severity=severity,
            attributes={
                "observation_type": _RUNTIME_OBSERVATION_TYPE,
                "collector_name": outcome.dispatch.collector_name,
                "execution": outcome.to_dict(),
            },
        )
        return self._event_bus.publish(event)

    def _record_cycle(self, cycle: CollectorRuntimeCycle) -> None:
        """Accumulate bounded runtime counters."""

        with self._lock:
            self._cycle_count += 1
            self._claimed_count += cycle.claimed_count
            self._emitted_count += cycle.emitted_count
            self._warmup_count += cycle.warmup_count
            self._failed_count += cycle.failed_count
            self._backoff_skipped_count += cycle.backoff_skipped_count
            self._budget_exceeded_count += cycle.budget_exceeded_count
            self._publish_failure_count += cycle.publish_failure_count

    def _read_clock_ns(self) -> int:
        """Read and validate the injected monotonic clock."""

        value = self._clock_ns()
        if isinstance(value, bool) or not isinstance(value, int):
            raise CollectorRuntimeClockError(
                "collector runtime clock must return an integer"
            )
        if value < 0:
            raise CollectorRuntimeClockError(
                "collector runtime clock must not return a negative value"
            )
        return value


def _validate_positive_seconds(name: str, value: object) -> float:
    """Validate one finite strictly positive duration."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return normalized


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer accounting value."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
