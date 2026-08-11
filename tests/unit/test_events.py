"""Unit tests for Sentinel-X core events."""

from __future__ import annotations

import unittest
from datetime import datetime

from sentinel_x.core.events import EventKind, SentinelEvent


class SentinelEventTests(unittest.TestCase):
    """Tests for the central Sentinel-X event model."""

    def test_event_normalizes_text_and_serializes(self) -> None:
        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="  unit-test  ",
            message="  CPU sample collected  ",
            attributes={
                "cpu_percent": 12.5,
            },
        )

        payload = event.to_dict()

        self.assertEqual(event.source, "unit-test")
        self.assertEqual(event.message, "CPU sample collected")
        self.assertEqual(payload["kind"], "observation")
        self.assertEqual(payload["attributes"]["cpu_percent"], 12.5)

        timestamp = datetime.fromisoformat(payload["occurred_at"])

        self.assertIsNotNone(timestamp.tzinfo)

    def test_event_rejects_empty_source(self) -> None:
        with self.assertRaises(ValueError):
            SentinelEvent(
                kind=EventKind.OBSERVATION,
                source="   ",
                message="valid message",
            )

    def test_event_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            SentinelEvent(
                kind=EventKind.OBSERVATION,
                source="unit-test",
                message="valid message",
                occurred_at=datetime.now(),
            )

    def test_attributes_mapping_is_read_only(self) -> None:
        event = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="unit-test",
            message="sample",
            attributes={
                "value": 1,
            },
        )

        with self.assertRaises(TypeError):
            event.attributes["value"] = 2  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
