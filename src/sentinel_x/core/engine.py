"""Core runtime engine for Sentinel-X."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, RLock
from typing import Final

from sentinel_x.core.bus import EventBus, PublishReport
from sentinel_x.core.events import (
    EventKind,
    EventSeverity,
    SentinelEvent,
)
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


class SentinelEngine:
    """Lifecycle coordinator for Sentinel-X.

    Phase 0 deliberately keeps the runtime loop operationally
    empty.

    Later phases will attach collectors, detection pipelines,
    diagnosis modules, and remediation logic without changing
    the lifecycle contract established here.
    """

    SOURCE: Final[str] = "sentinel_x.core.engine"

    def __init__(
        self,
        event_bus: EventBus,
        *,
        instance_name: str = "sentinel-x",
    ) -> None:
        if not isinstance(instance_name, str):
            raise TypeError("instance_name must be a string")

        normalized_instance_name = instance_name.strip()

        if not normalized_instance_name:
            raise ValueError("instance_name must not be empty")

        self._event_bus = event_bus
        self._instance_name = normalized_instance_name
        self._lifecycle = AgentLifecycle()
        self._stop_event = Event()
        self._run_lock = Lock()
        self._control_lock = RLock()
        self._stop_reason: str | None = None

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
            )

    def start(self) -> None:
        """Transition engine from CREATED to RUNNING."""

        self._lifecycle.transition(AgentState.STARTING)

        try:
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

        tick_interval controls how frequently the dormant
        Phase-0 runtime wakes.

        Future phases will replace the empty tick with real
        scheduling and operational workloads.
        """

        if tick_interval <= 0:
            raise ValueError("tick_interval must be greater than zero")

        if not self._run_lock.acquire(blocking=False):
            raise EngineRunConflictError(
                "this SentinelEngine instance is already running"
            )

        try:
            self.start()

            while not self._stop_event.wait(timeout=tick_interval):
                self._tick()

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

        Phase 0 intentionally has no operational workload.
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
