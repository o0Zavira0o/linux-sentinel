"""Core runtime engine for Sentinel-X."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, RLock
from typing import Final

from sentinel_x.core.bus import EventBus, PublishReport
from sentinel_x.core.events import (
    EventKind,
    EventSeverity,
    SentinelEvent,
)
from sentinel_x.core.registry import CollectorRegistry
from sentinel_x.core.runtime import CollectorRuntime, CollectorRuntimeSnapshot
from sentinel_x.core.state import (
    AgentLifecycle,
    AgentState,
    InvalidStateTransitionError,
)


class EngineRunConflictError(RuntimeError):
    """Raised when the same engine is run concurrently."""


@dataclass(
    frozen=True,
    slots=True,
)
class EngineSnapshot:
    """Read-only snapshot of engine control-plane state."""

    instance_name: str
    state: AgentState
    stop_requested: bool
    stop_reason: str | None
    collector_runtime: CollectorRuntimeSnapshot | None


class SentinelEngine:
    """Lifecycle coordinator for the scheduled Sentinel-X runtime.

    The engine owns lifecycle and interruptible waiting while an optional
    collector runtime owns scheduling, execution, and observation publication.
    """

    SOURCE: Final[str] = "sentinel_x.core.engine"

    def __init__(
        self,
        event_bus: EventBus,
        *,
        instance_name: str = "sentinel-x",
        collector_registry: CollectorRegistry | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not isinstance(instance_name, str):
            raise TypeError("instance_name must be a string")

        normalized_instance_name = instance_name.strip()

        if not normalized_instance_name:
            raise ValueError("instance_name must not be empty")
        if collector_registry is not None and not isinstance(
            collector_registry, CollectorRegistry
        ):
            raise TypeError("collector_registry must be a CollectorRegistry or None")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")

        self._event_bus = event_bus
        self._instance_name = normalized_instance_name
        self._lifecycle = AgentLifecycle()
        self._stop_event = Event()
        self._run_lock = Lock()
        self._control_lock = RLock()
        self._stop_reason: str | None = None
        self._collector_registry = collector_registry
        self._clock_ns = clock_ns
        self._collector_runtime: CollectorRuntime | None = None

    @property
    def state(self) -> AgentState:
        """Return current engine lifecycle state."""

        return self._lifecycle.state

    @property
    def instance_name(self) -> str:
        """Return the configured Sentinel-X instance name."""

        return self._instance_name

    def snapshot(self) -> EngineSnapshot:
        """Return a thread-safe control-state snapshot."""

        with self._control_lock:
            return EngineSnapshot(
                instance_name=self._instance_name,
                state=self._lifecycle.state,
                stop_requested=self._stop_event.is_set(),
                stop_reason=self._stop_reason,
                collector_runtime=(
                    None
                    if self._collector_runtime is None
                    else self._collector_runtime.snapshot()
                ),
            )

    def start(self) -> None:
        """Transition engine from CREATED to RUNNING."""

        self._lifecycle.transition(AgentState.STARTING)

        try:
            if self._collector_registry is not None:
                self._collector_runtime = CollectorRuntime(
                    self._event_bus,
                    self._collector_registry,
                    clock_ns=self._clock_ns,
                )
            self._lifecycle.transition(AgentState.RUNNING)

        except Exception:
            self._transition_to_failed()
            raise

        self._publish(
            SentinelEvent(
                kind=EventKind.AGENT_STARTED,
                source=self.SOURCE,
                message=("Sentinel-X engine entered the running state."),
                attributes={
                    "instance_name": self._instance_name,
                    "state": AgentState.RUNNING.value,
                    "collector_runtime_enabled": (self._collector_runtime is not None),
                },
            )
        )

    def request_stop(
        self,
        reason: str,
    ) -> bool:
        """Request a graceful stop.

        This method does not perform shutdown inline.

        It only signals the main runtime loop that shutdown
        should begin.

        Returns True only for the first stop request.
        Repeated requests are idempotent and return False.
        """

        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ValueError("stop reason must not be empty")

        with self._control_lock:
            if self._stop_event.is_set():
                return False

            self._stop_reason = normalized_reason
            self._stop_event.set()

        self._publish(
            SentinelEvent(
                kind=EventKind.AGENT_STOP_REQUESTED,
                source=self.SOURCE,
                message="Graceful shutdown requested.",
                severity=EventSeverity.INFO,
                attributes={
                    "instance_name": self._instance_name,
                    "reason": normalized_reason,
                },
            )
        )

        return True

    def stop(
        self,
        reason: str | None = None,
    ) -> None:
        """Move the engine into STOPPED safely."""

        with self._control_lock:
            if reason is not None:
                normalized_reason = reason.strip()

                if not normalized_reason:
                    raise ValueError("stop reason must not be empty")

                if self._stop_reason is None:
                    self._stop_reason = normalized_reason

            effective_reason = self._stop_reason or "engine stop requested"

        current = self._lifecycle.state

        if current is AgentState.STOPPED:
            return

        if current is AgentState.CREATED:
            self._lifecycle.transition(AgentState.STOPPED)

        else:
            if current in {
                AgentState.STARTING,
                AgentState.RUNNING,
                AgentState.FAILED,
            }:
                self._lifecycle.transition(AgentState.STOPPING)

            if self._lifecycle.state is AgentState.STOPPING:
                self._lifecycle.transition(AgentState.STOPPED)

        self._publish(
            SentinelEvent(
                kind=EventKind.AGENT_STOPPED,
                source=self.SOURCE,
                message="Sentinel-X engine stopped.",
                attributes={
                    "instance_name": self._instance_name,
                    "state": AgentState.STOPPED.value,
                    "reason": effective_reason,
                },
            )
        )

    def run_forever(
        self,
        *,
        tick_interval: float = 0.5,
    ) -> None:
        """Run until a graceful stop is requested.

        tick_interval is a maximum wake ceiling. Collector deadlines may
        wake the engine sooner when a scheduled runtime is configured.
        """

        tick_interval = _validate_tick_interval(tick_interval)

        if not self._run_lock.acquire(blocking=False):
            raise EngineRunConflictError(
                "this SentinelEngine instance is already running"
            )

        try:
            self.start()

            while not self._stop_event.is_set():
                runtime = self._collector_runtime
                if runtime is None:
                    if self._stop_event.wait(timeout=tick_interval):
                        break
                    self._tick()
                    continue

                runtime.run_due()
                if self._stop_event.is_set():
                    break

                wait_seconds = runtime.next_wakeup_delay_seconds(
                    maximum_seconds=tick_interval
                )
                if self._stop_event.wait(timeout=wait_seconds):
                    break

        except Exception as exc:
            self._transition_to_failed()

            self._publish(
                SentinelEvent(
                    kind=EventKind.AGENT_FAILED,
                    source=self.SOURCE,
                    message="Sentinel-X engine failed.",
                    severity=EventSeverity.CRITICAL,
                    attributes={
                        "instance_name": self._instance_name,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            )

            raise

        finally:
            if self._lifecycle.state is not AgentState.STOPPED:
                self.stop()

            self._run_lock.release()

    def _tick(self) -> None:
        """Perform one runtime iteration.

        This compatibility hook is used only when no collector runtime is bound.
        """

    def _transition_to_failed(self) -> None:
        """Move to FAILED when lifecycle permits it."""

        current = self._lifecycle.state

        if current in {
            AgentState.CREATED,
            AgentState.STARTING,
            AgentState.RUNNING,
            AgentState.STOPPING,
        }:
            try:
                self._lifecycle.transition(AgentState.FAILED)

            except InvalidStateTransitionError:
                return

    def _publish(
        self,
        event: SentinelEvent,
    ) -> PublishReport:
        """Publish one control-plane event."""

        return self._event_bus.publish(event)


def _validate_tick_interval(value: object) -> float:
    """Validate the maximum engine wake interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("tick_interval must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError("tick_interval must be finite and greater than zero")
    return normalized
