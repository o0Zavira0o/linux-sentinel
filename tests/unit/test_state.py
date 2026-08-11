"""Unit tests for Sentinel-X lifecycle state management."""

from __future__ import annotations

import unittest

from sentinel_x.core.state import (
    AgentLifecycle,
    AgentState,
    InvalidStateTransitionError,
)


class AgentLifecycleTests(unittest.TestCase):
    """Tests for the Sentinel-X lifecycle state machine."""

    def test_initial_state_is_created(self) -> None:
        lifecycle = AgentLifecycle()

        self.assertIs(
            lifecycle.state,
            AgentState.CREATED,
        )

    def test_valid_startup_transitions(self) -> None:
        lifecycle = AgentLifecycle()

        previous = lifecycle.transition(AgentState.STARTING)

        self.assertIs(
            previous,
            AgentState.CREATED,
        )

        lifecycle.transition(AgentState.RUNNING)

        self.assertIs(
            lifecycle.state,
            AgentState.RUNNING,
        )

    def test_valid_shutdown_transitions(self) -> None:
        lifecycle = AgentLifecycle()

        lifecycle.transition(AgentState.STARTING)

        lifecycle.transition(AgentState.RUNNING)

        lifecycle.transition(AgentState.STOPPING)

        lifecycle.transition(AgentState.STOPPED)

        self.assertIs(
            lifecycle.state,
            AgentState.STOPPED,
        )

    def test_invalid_transition_is_rejected(self) -> None:
        lifecycle = AgentLifecycle()

        with self.assertRaises(InvalidStateTransitionError):
            lifecycle.transition(AgentState.RUNNING)

    def test_stopped_state_is_terminal(self) -> None:
        lifecycle = AgentLifecycle()

        lifecycle.transition(AgentState.STOPPED)

        self.assertFalse(lifecycle.can_transition(AgentState.STARTING))


if __name__ == "__main__":
    unittest.main()
