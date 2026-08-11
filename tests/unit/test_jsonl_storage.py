"""Unit tests for Sentinel-X JSONL event persistence."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest

from sentinel_x.core import (
    EventKind,
    SentinelEvent,
)
from sentinel_x.storage import (
    EventRecorderClosedError,
    EventSerializationError,
    JsonlEventRecorder,
    RECORD_SCHEMA_VERSION,
)


class JsonlEventRecorderTests(
    unittest.TestCase
):
    """Tests for durable Sentinel-X event recording."""

    def test_event_is_written_as_structured_jsonl(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            event = SentinelEvent(
                kind=EventKind.OBSERVATION,
                source="unit-test",
                message="CPU sample collected",
                attributes={
                    "cpu_percent": 12.5,
                },
            )

            recorder.record(
                event
            )

            event_path = recorder.path
            run_id = recorder.run_id

            recorder.close()

            lines = event_path.read_text(
                encoding="utf-8"
            ).splitlines()

        self.assertEqual(
            len(lines),
            1,
        )

        payload = json.loads(
            lines[0]
        )

        self.assertEqual(
            payload["schema_version"],
            RECORD_SCHEMA_VERSION,
        )

        self.assertEqual(
            payload["run_id"],
            run_id,
        )

        self.assertEqual(
            payload["instance_name"],
            "test-node",
        )

        self.assertEqual(
            payload["event"]["event_id"],
            event.event_id,
        )

        self.assertEqual(
            payload["event"]["kind"],
            "observation",
        )

        self.assertEqual(
            payload["event"]["attributes"]["cpu_percent"],
            12.5,
        )

    def test_multiple_events_create_multiple_lines(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            first = SentinelEvent(
                kind=EventKind.OBSERVATION,
                source="unit-test",
                message="first",
            )

            second = SentinelEvent(
                kind=EventKind.ANOMALY,
                source="unit-test",
                message="second",
            )

            recorder.record(
                first
            )

            recorder.record(
                second
            )

            event_path = recorder.path

            self.assertEqual(
                recorder.records_written,
                2,
            )

            recorder.close()

            lines = event_path.read_text(
                encoding="utf-8"
            ).splitlines()

        self.assertEqual(
            len(lines),
            2,
        )

        first_payload = json.loads(
            lines[0]
        )

        second_payload = json.loads(
            lines[1]
        )

        self.assertEqual(
            first_payload["run_id"],
            second_payload["run_id"],
        )

        self.assertEqual(
            first_payload["event"]["message"],
            "first",
        )

        self.assertEqual(
            second_payload["event"]["message"],
            "second",
        )

    def test_event_file_uses_private_permissions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            mode = stat.S_IMODE(
                recorder.path.stat().st_mode
            )

            recorder.close()

        self.assertEqual(
            mode,
            0o600,
        )

    def test_record_after_close_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            recorder.close()

            event = SentinelEvent(
                kind=EventKind.OBSERVATION,
                source="unit-test",
                message="sample",
            )

            with self.assertRaises(
                EventRecorderClosedError
            ):
                recorder.record(
                    event
                )

    def test_unserializable_attribute_is_rejected_before_write(
        self,
    ) -> None:
        class UnsupportedObject:
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            event = SentinelEvent(
                kind=EventKind.OBSERVATION,
                source="unit-test",
                message="sample",
                attributes={
                    "unsupported": UnsupportedObject(),
                },
            )

            with self.assertRaises(
                EventSerializationError
            ):
                recorder.record(
                    event
                )

            event_path = recorder.path

            self.assertEqual(
                recorder.records_written,
                0,
            )

            recorder.close()

            content = event_path.read_text(
                encoding="utf-8"
            )

        self.assertEqual(
            content,
            "",
        )


if __name__ == "__main__":
    unittest.main()
