"""Unit tests for the Sentinel-X core runtime engine."""

from __future__ import annotations

import threading
import unittest

from sentinel_x.core import (
    AgentState,
    EventBus,
    EventKind,
    SentinelEngine,
    SentinelEvent,
)


class SentinelEngineTests(
    unittest.TestCase
):
    """Tests for lifecycle and graceful shutdown behavior."""

    def test_start_moves_engine_to_running(
        self,
    ) -> None:
        bus = EventBus()

        events: list[
            SentinelEvent
        ] = []

        bus.subscribe(
            events.append
        )

        engine = SentinelEngine(
            event_bus=bus
        )

        engine.start()

        self.assertIs(
            engine.state,
            AgentState.RUNNING,
        )

        self.assertEqual(
            events[-1].kind,
            EventKind.AGENT_STARTED,
        )

        engine.stop(
            reason="unit test cleanup"
        )

    def test_stop_request_is_idempotent(
        self,
    ) -> None:
        engine = SentinelEngine(
            event_bus=EventBus()
        )

        first = engine.request_stop(
            reason="first request"
        )

        second = engine.request_stop(
            reason="second request"
        )

        snapshot = engine.snapshot()

        self.assertTrue(
            first
        )

        self.assertFalse(
            second
        )

        self.assertTrue(
            snapshot.stop_requested
        )

        self.assertEqual(
            snapshot.stop_reason,
            "first request",
        )

    def test_run_forever_exits_after_stop_request(
        self,
    ) -> None:
        bus = EventBus()

        events: list[
            SentinelEvent
        ] = []

        started = threading.Event()

        def collect(
            event: SentinelEvent,
        ) -> None:
            events.append(
                event
            )

            if (
                event.kind
                is EventKind.AGENT_STARTED
            ):
                started.set()

        bus.subscribe(
            collect
        )

        engine = SentinelEngine(
            event_bus=bus
        )

        worker = threading.Thread(
            target=engine.run_forever,
            kwargs={
                "tick_interval": 0.01,
            },
            daemon=True,
        )

        worker.start()

        self.assertTrue(
            started.wait(
                timeout=1.0
            ),
            "engine did not reach "
            "RUNNING in time",
        )

        engine.request_stop(
            reason="unit test shutdown"
        )

        worker.join(
            timeout=1.0
        )

        self.assertFalse(
            worker.is_alive(),
            "engine did not stop in time",
        )

        self.assertIs(
            engine.state,
            AgentState.STOPPED,
        )

        kinds = [
            event.kind
            for event in events
        ]

        self.assertIn(
            EventKind.AGENT_STARTED,
            kinds,
        )

        self.assertIn(
            EventKind.AGENT_STOP_REQUESTED,
            kinds,
        )

        self.assertIn(
            EventKind.AGENT_STOPPED,
            kinds,
        )

    def test_invalid_tick_interval_is_rejected(
        self,
    ) -> None:
        engine = SentinelEngine(
            event_bus=EventBus()
        )

        with self.assertRaises(
            ValueError
        ):
            engine.run_forever(
                tick_interval=0,
            )


if __name__ == "__main__":
    unittest.main()
