"""Tests for configured journald collector bindings."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sentinel_x.systemd import (
    ConfiguredSystemdJournalCollectors,
    SystemdJournalBatch,
    SystemdJournalCheckpoint,
    SystemdJournalCollectorBindingError,
)

_BOOT_ID = "a" * 32
_NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


class _RecordingReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, int]] = []

    def read_service(
        self,
        unit_name: str,
        *,
        after_cursor: str | None = None,
        max_entries: int = 64,
    ) -> SystemdJournalBatch:
        self.calls.append((unit_name, after_cursor, max_entries))
        return SystemdJournalBatch(
            requested_unit=unit_name,
            after_cursor=after_cursor,
            entry_limit=max_entries,
            entries=(),
            diagnostic=None,
            captured_at=_NOW,
        )


class _BootReader:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return _BOOT_ID


class _CheckpointStore:
    def __init__(self) -> None:
        self.values: dict[str, SystemdJournalCheckpoint] = {}
        self.loads: list[tuple[str, str]] = []
        self.saves: list[str] = []

    def load(
        self,
        collector_name: str,
        unit_name: str,
    ) -> SystemdJournalCheckpoint | None:
        self.loads.append((collector_name, unit_name))
        return self.values.get(collector_name)

    def save(self, checkpoint: SystemdJournalCheckpoint) -> None:
        self.values[checkpoint.collector_name] = checkpoint
        self.saves.append(checkpoint.collector_name)


class ConfiguredJournalCollectorTests(unittest.TestCase):
    """Tests for trusted config-to-journal collector construction."""

    def test_empty_bindings_do_not_construct_default_reader(self) -> None:
        with patch(
            "sentinel_x.systemd.journal_binding.JournalctlServiceReader",
            side_effect=AssertionError("reader must not be constructed"),
        ):
            configured = ConfiguredSystemdJournalCollectors(())

        self.assertEqual(configured.collectors(), ())
        self.assertEqual(dict(configured.handlers()), {})

    def test_collectors_preserve_declared_binding_order(self) -> None:
        configured = ConfiguredSystemdJournalCollectors(
            (
                ("journal.alpha.aaaaaaaaaaaa", "alpha.service", 4),
                ("journal.beta.bbbbbbbbbbbb", "beta.service", 8),
            ),
            reader=_RecordingReader(),
        )

        self.assertEqual(
            tuple(collector.name for collector in configured.collectors()),
            (
                "journal.alpha.aaaaaaaaaaaa",
                "journal.beta.bbbbbbbbbbbb",
            ),
        )

    def test_handlers_expose_exact_trusted_names(self) -> None:
        configured = ConfiguredSystemdJournalCollectors(
            (("journal.demo.aaaaaaaaaaaa", "demo.service", 4),),
            reader=_RecordingReader(),
        )

        self.assertEqual(
            tuple(configured.handlers()),
            ("journal.demo.aaaaaaaaaaaa",),
        )

    def test_shared_injected_reader_is_used_by_all_collectors(self) -> None:
        reader = _RecordingReader()
        boot_reader = _BootReader()
        configured = ConfiguredSystemdJournalCollectors(
            (
                ("journal.alpha.aaaaaaaaaaaa", "alpha.service", 4),
                ("journal.beta.bbbbbbbbbbbb", "beta.service", 8),
            ),
            reader=reader,
            boot_id_reader=boot_reader,
        )

        for collector in configured.collectors():
            emission = collector.collect()
            self.assertIsNone(emission.event)

        self.assertEqual(
            reader.calls,
            [
                ("alpha.service", None, 4),
                ("beta.service", None, 8),
            ],
        )
        self.assertEqual(boot_reader.calls, 2)

    def test_binding_max_entries_is_applied_per_collector(self) -> None:
        reader = _RecordingReader()
        configured = ConfiguredSystemdJournalCollectors(
            (("journal.demo.aaaaaaaaaaaa", "demo.service", 7),),
            reader=reader,
            boot_id_reader=_BootReader(),
        )

        configured.collectors()[0].collect()

        self.assertEqual(reader.calls, [("demo.service", None, 7)])

    def test_malformed_binding_shape_is_rejected(self) -> None:
        with self.assertRaises(SystemdJournalCollectorBindingError):
            ConfiguredSystemdJournalCollectors(
                (("journal.demo.aaaaaaaaaaaa", "demo.service"),),
                reader=_RecordingReader(),
            )

    def test_duplicate_collector_names_are_rejected(self) -> None:
        with self.assertRaises(SystemdJournalCollectorBindingError):
            ConfiguredSystemdJournalCollectors(
                (
                    ("journal.demo.aaaaaaaaaaaa", "alpha.service", 4),
                    ("journal.demo.aaaaaaaaaaaa", "beta.service", 4),
                ),
                reader=_RecordingReader(),
            )

    def test_duplicate_unit_names_are_rejected(self) -> None:
        with self.assertRaises(SystemdJournalCollectorBindingError):
            ConfiguredSystemdJournalCollectors(
                (
                    ("journal.alpha.aaaaaaaaaaaa", "demo.service", 4),
                    ("journal.beta.bbbbbbbbbbbb", "demo.service", 4),
                ),
                reader=_RecordingReader(),
            )

    def test_invalid_collector_name_is_wrapped_as_binding_error(self) -> None:
        with self.assertRaises(SystemdJournalCollectorBindingError):
            ConfiguredSystemdJournalCollectors(
                (("bad name", "demo.service", 4),),
                reader=_RecordingReader(),
            )

    def test_invalid_unit_name_is_wrapped_as_binding_error(self) -> None:
        with self.assertRaises(SystemdJournalCollectorBindingError):
            ConfiguredSystemdJournalCollectors(
                (("journal.demo.aaaaaaaaaaaa", "../demo.service", 4),),
                reader=_RecordingReader(),
            )

    def test_zero_max_entries_is_wrapped_as_binding_error(self) -> None:
        with self.assertRaises(SystemdJournalCollectorBindingError):
            ConfiguredSystemdJournalCollectors(
                (("journal.demo.aaaaaaaaaaaa", "demo.service", 0),),
                reader=_RecordingReader(),
            )

    def test_max_entries_above_reader_bound_is_wrapped_as_error(self) -> None:
        with self.assertRaises(SystemdJournalCollectorBindingError):
            ConfiguredSystemdJournalCollectors(
                (("journal.demo.aaaaaaaaaaaa", "demo.service", 65),),
                reader=_RecordingReader(),
            )

    def test_noncallable_boot_reader_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            ConfiguredSystemdJournalCollectors(
                (("journal.demo.aaaaaaaaaaaa", "demo.service", 4),),
                reader=_RecordingReader(),
                boot_id_reader=object(),
            )

    def test_shared_checkpoint_store_is_passed_to_all_collectors(self) -> None:
        store = _CheckpointStore()
        configured = ConfiguredSystemdJournalCollectors(
            (
                ("journal.alpha.aaaaaaaaaaaa", "alpha.service", 4),
                ("journal.beta.bbbbbbbbbbbb", "beta.service", 4),
            ),
            reader=_RecordingReader(),
            boot_id_reader=_BootReader(),
            checkpoint_store=store,
        )

        first, second = configured.collectors()
        first.collect()
        second.collect()

        self.assertEqual(
            store.loads,
            [
                ("journal.alpha.aaaaaaaaaaaa", "alpha.service"),
                ("journal.beta.bbbbbbbbbbbb", "beta.service"),
            ],
        )
        self.assertEqual(
            store.saves,
            [
                "journal.alpha.aaaaaaaaaaaa",
                "journal.beta.bbbbbbbbbbbb",
            ],
        )

    def test_empty_bindings_do_not_touch_injected_checkpoint_store(self) -> None:
        store = _CheckpointStore()

        configured = ConfiguredSystemdJournalCollectors(
            (),
            checkpoint_store=store,
        )

        self.assertEqual(configured.collectors(), ())
        self.assertEqual(store.loads, [])
        self.assertEqual(store.saves, [])

    def test_distinct_collectors_keep_independent_cursor_state(self) -> None:
        reader = _RecordingReader()
        configured = ConfiguredSystemdJournalCollectors(
            (
                ("journal.alpha.aaaaaaaaaaaa", "alpha.service", 4),
                ("journal.beta.bbbbbbbbbbbb", "beta.service", 4),
            ),
            reader=reader,
            boot_id_reader=_BootReader(),
        )

        first, second = configured.collectors()
        first.collect()

        self.assertEqual(first.snapshot().committed_boot_id, _BOOT_ID)
        self.assertIsNone(second.snapshot().committed_boot_id)


if __name__ == "__main__":
    unittest.main()
