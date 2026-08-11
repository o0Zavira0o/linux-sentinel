"""Unit tests for the Sentinel-X EventBus."""

from __future__ import annotations

import unittest

from sentinel_x.core.bus import EventBus
from sentinel_x.core.events import (
    EventKind,
    SentinelEvent,
)


class EventBusTests(
    unittest.TestCase
):
    """Tests for Sentinel-X event publication."""

    def test_publish_delivers_event(
        self,
    ) -> None:
        bus = EventBus()

        received: list[
            SentinelEvent
        ] = []

        bus.subscribe(
            received.append
        )

        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="unit-test",
            message="sample",
        )

        report = bus.publish(
            event
        )

        self.assertEqual(
            received,
            [event],
        )

        self.assertEqual(
            report.delivered,
            1,
        )

        self.assertTrue(
            report.succeeded
        )

    def test_unsubscribe_stops_delivery(
        self,
    ) -> None:
        bus = EventBus()

        received: list[
            SentinelEvent
        ] = []

        subscription = bus.subscribe(
            received.append
        )

        removed = bus.unsubscribe(
            subscription
        )

        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="unit-test",
            message="sample",
        )

        report = bus.publish(
            event
        )

        self.assertTrue(
            removed
        )

        self.assertEqual(
            received,
            [],
        )

        self.assertEqual(
            report.delivered,
            0,
        )

    def test_failing_handler_does_not_block_later_handlers(
        self,
    ) -> None:
        bus = EventBus()

        received: list[
            SentinelEvent
        ] = []

        def failing_handler(
            _event: SentinelEvent,
        ) -> None:
            raise RuntimeError(
                "intentional test failure"
            )

        bus.subscribe(
            failing_handler
        )

        bus.subscribe(
            received.append
        )

        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="unit-test",
            message="sample",
        )

        report = bus.publish(
            event
        )

        self.assertEqual(
            received,
            [event],
        )

        self.assertEqual(
            report.delivered,
            1,
        )

        self.assertEqual(
            len(
                report.failures
            ),
            1,
        )

        self.assertFalse(
            report.succeeded
        )

        self.assertEqual(
            report.failures[
                0
            ].error_type,
            "RuntimeError",
        )

    def test_subscriber_count_tracks_active_handlers(
        self,
    ) -> None:
        bus = EventBus()

        subscription = bus.subscribe(
            lambda _event: None
        )

        self.assertEqual(
            bus.subscriber_count,
            1,
        )

        bus.unsubscribe(
            subscription
        )

        self.assertEqual(
            bus.subscriber_count,
            0,
        )


if __name__ == "__main__":
    unittest.main()
