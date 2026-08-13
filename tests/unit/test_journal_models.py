"""Unit tests for typed journald evidence models."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from sentinel_x.systemd.journal_models import (
    JournalField,
    JournalPriority,
    SystemdJournalBatch,
    SystemdJournalCursorError,
    SystemdJournalEntry,
    SystemdJournalFieldError,
    SystemdJournalModelError,
    validate_journal_cursor,
)

_BOOT_ID = "0123456789abcdef0123456789abcdef"
_CURSOR_A = "s=abc;i=1;b=0123456789abcdef0123456789abcdef;m=10;t=20"
_CURSOR_B = "s=abc;i=2;b=0123456789abcdef0123456789abcdef;m=11;t=21"
_CAPTURED_AT = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def _entry(
    *,
    cursor: str = _CURSOR_A,
    fields: tuple[JournalField, ...] = (),
) -> SystemdJournalEntry:
    return SystemdJournalEntry(
        cursor=cursor,
        realtime_timestamp_usec=2_000_000,
        monotonic_timestamp_usec=1_000_000,
        boot_id=_BOOT_ID,
        fields=fields,
    )


class SystemdJournalModelTests(unittest.TestCase):
    def test_cursor_validation_preserves_opaque_text(self) -> None:
        self.assertEqual(
            validate_journal_cursor(_CURSOR_A, field_name="cursor"),
            _CURSOR_A,
        )

    def test_cursor_validation_rejects_empty_or_control_characters(self) -> None:
        for value in ("", "cursor\nnext", "cursor\x00tail"):
            with self.subTest(value=value):
                with self.assertRaises(SystemdJournalCursorError):
                    validate_journal_cursor(value, field_name="cursor")

    def test_field_preserves_text_binary_null_and_repeated_values(self) -> None:
        text = JournalField(name="MESSAGE", values=("hello",))
        binary = JournalField(name="MESSAGE", values=(b"a\x00b",))
        omitted = JournalField(name="MESSAGE", values=(None,))
        repeated = JournalField(
            name="MESSAGE",
            values=("one", b"two", None),
        )

        self.assertEqual(text.single_text, "hello")
        self.assertEqual(binary.single_bytes, b"a\x00b")
        self.assertTrue(omitted.single_omitted)
        self.assertTrue(repeated.is_multi_valued)
        self.assertEqual(repeated.to_json_value(), ["one", [116, 119, 111], None])

    def test_field_rejects_invalid_name_or_empty_values(self) -> None:
        with self.assertRaises(SystemdJournalFieldError):
            JournalField(name="message", values=("x",))
        with self.assertRaises(SystemdJournalFieldError):
            JournalField(name="MESSAGE", values=())

    def test_entry_exposes_common_typed_helpers_without_losing_raw_fields(self) -> None:
        entry = _entry(
            fields=(
                JournalField(name="MESSAGE", values=("ready",)),
                JournalField(name="PRIORITY", values=("4",)),
                JournalField(name="_SYSTEMD_UNIT", values=("demo.service",)),
                JournalField(
                    name="_SYSTEMD_INVOCATION_ID",
                    values=("abcdef",),
                ),
                JournalField(name="_PID", values=("42",)),
            )
        )

        self.assertEqual(entry.message_text, "ready")
        self.assertEqual(entry.priority, JournalPriority.WARNING)
        self.assertEqual(entry.systemd_unit, "demo.service")
        self.assertEqual(entry.systemd_invocation_id, "abcdef")
        self.assertEqual(entry.pid, 42)

    def test_entry_binary_and_omitted_message_helpers_are_explicit(self) -> None:
        binary = _entry(fields=(JournalField(name="MESSAGE", values=(b"x\x00",)),))
        omitted = _entry(fields=(JournalField(name="MESSAGE", values=(None,)),))

        self.assertEqual(binary.message_bytes, b"x\x00")
        self.assertIsNone(binary.message_text)
        self.assertTrue(omitted.message_omitted)

    def test_entry_ambiguous_or_invalid_typed_fields_return_none(self) -> None:
        entry = _entry(
            fields=(
                JournalField(name="PRIORITY", values=("9",)),
                JournalField(name="_PID", values=("12", "13")),
            )
        )

        self.assertIsNone(entry.priority)
        self.assertIsNone(entry.pid)

    def test_entry_rejects_invalid_address_metadata(self) -> None:
        with self.assertRaises(SystemdJournalModelError):
            SystemdJournalEntry(
                cursor=_CURSOR_A,
                realtime_timestamp_usec=-1,
                monotonic_timestamp_usec=1,
                boot_id=_BOOT_ID,
                fields=(),
            )
        with self.assertRaises(SystemdJournalModelError):
            SystemdJournalEntry(
                cursor=_CURSOR_A,
                realtime_timestamp_usec=1,
                monotonic_timestamp_usec=1,
                boot_id="not-an-id",
                fields=(),
            )

    def test_entry_rejects_duplicate_field_names(self) -> None:
        with self.assertRaises(SystemdJournalModelError):
            _entry(
                fields=(
                    JournalField(name="MESSAGE", values=("one",)),
                    JournalField(name="MESSAGE", values=("two",)),
                )
            )

    def test_entry_serialization_is_json_friendly_and_binary_safe(self) -> None:
        entry = _entry(
            fields=(
                JournalField(name="MESSAGE", values=(b"a\x00b",)),
                JournalField(name="PRIORITY", values=("6",)),
            )
        )

        payload = entry.to_dict()
        fields_payload = payload["fields"]
        self.assertIsInstance(fields_payload, dict)
        assert isinstance(fields_payload, dict)
        self.assertEqual(fields_payload["MESSAGE"], [97, 0, 98])
        json.dumps(payload)

    def test_batch_advances_to_last_cursor_and_reports_limit(self) -> None:
        batch = SystemdJournalBatch(
            requested_unit="demo.service",
            after_cursor=None,
            entry_limit=2,
            entries=(
                _entry(cursor=_CURSOR_A),
                _entry(cursor=_CURSOR_B),
            ),
            diagnostic=None,
            captured_at=_CAPTURED_AT,
        )

        self.assertEqual(batch.next_cursor, _CURSOR_B)
        self.assertTrue(batch.entry_limit_reached)
        self.assertEqual(batch.to_dict()["entry_count"], 2)

    def test_empty_incremental_batch_preserves_existing_cursor(self) -> None:
        batch = SystemdJournalBatch(
            requested_unit="demo.service",
            after_cursor=_CURSOR_A,
            entry_limit=4,
            entries=(),
            diagnostic=None,
            captured_at=_CAPTURED_AT,
        )

        self.assertEqual(batch.next_cursor, _CURSOR_A)
        self.assertFalse(batch.entry_limit_reached)

    def test_batch_rejects_duplicate_entry_cursors(self) -> None:
        with self.assertRaises(SystemdJournalModelError):
            SystemdJournalBatch(
                requested_unit="demo.service",
                after_cursor=None,
                entry_limit=2,
                entries=(_entry(), _entry()),
                diagnostic=None,
                captured_at=_CAPTURED_AT,
            )

    def test_batch_rejects_naive_capture_time_or_oversized_entry_set(self) -> None:
        with self.assertRaises(SystemdJournalModelError):
            SystemdJournalBatch(
                requested_unit="demo.service",
                after_cursor=None,
                entry_limit=1,
                entries=(),
                diagnostic=None,
                captured_at=datetime(2026, 8, 13, 10, 0),
            )
        with self.assertRaises(SystemdJournalModelError):
            SystemdJournalBatch(
                requested_unit="demo.service",
                after_cursor=None,
                entry_limit=1,
                entries=(_entry(cursor=_CURSOR_A), _entry(cursor=_CURSOR_B)),
                diagnostic=None,
                captured_at=_CAPTURED_AT,
            )

    def test_batch_rejects_unbounded_diagnostic(self) -> None:
        with self.assertRaises(SystemdJournalModelError):
            SystemdJournalBatch(
                requested_unit="demo.service",
                after_cursor=None,
                entry_limit=1,
                entries=(),
                diagnostic="x" * 513,
                captured_at=_CAPTURED_AT,
            )


if __name__ == "__main__":
    unittest.main()
