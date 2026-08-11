"""Lifecycle state management for the Sentinel-X agent."""

from __future__ import annotations

from enum import StrEnum
from threading import RLock
from typing import Final


class AgentState(StrEnum):
    """Valid lifecycle states for a Sentinel-X engine instance."""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class InvalidStateTransitionError(RuntimeError):
    """Raised when an invalid agent lifecycle transition is requested."""


_ALLOWED_TRANSITIONS: Final[dict[AgentState, frozenset[AgentState]]] = {
    AgentState.CREATED: frozenset(
        {
            AgentState.STARTING,
            AgentState.STOPPED,
            AgentState.FAILED,
        }
    ),
    AgentState.STARTING: frozenset(
        {
            AgentState.RUNNING,
            AgentState.STOPPING,
            AgentState.FAILED,
        }
    ),
    AgentState.RUNNING: frozenset(
        {
            AgentState.STOPPING,
            AgentState.FAILED,
        }
    ),
    AgentState.STOPPING: frozenset(
        {
            AgentState.STOPPED,
            AgentState.FAILED,
        }
    ),
    AgentState.STOPPED: frozenset(),
    AgentState.FAILED: frozenset(
        {
            AgentState.STOPPING,
            AgentState.STOPPED,
        }
    ),
}


class AgentLifecycle:
    """Thread-safe state machine for the Sentinel-X agent lifecycle."""

    def __init__(self) -> None:
        self._state = AgentState.CREATED
        self._lock = RLock()

    @property
    def state(self) -> AgentState:
        """Return the current lifecycle state."""

        with self._lock:
            return self._state

    def can_transition(
        self,
        target: AgentState,
    ) -> bool:
        """Return whether the current state may transition to target."""

        with self._lock:
            return target in _ALLOWED_TRANSITIONS[self._state]

    def transition(
        self,
        target: AgentState,
    ) -> AgentState:
        """Move to target if the transition is valid.

        Returns the previous state to make state-change reporting
        explicit.
        """

        with self._lock:
            previous = self._state

            if target not in _ALLOWED_TRANSITIONS[previous]:
                raise InvalidStateTransitionError(
                    "invalid Sentinel-X state transition: "
                    f"{previous.value} -> {target.value}"
                )

            self._state = target

            return previous
