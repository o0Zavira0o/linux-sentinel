"""Tests for stateful cursor-aware journald observations."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.core import (
    CollectorDefinition,
    CollectorExecutionPolicy,
    CollectorExecutionSpec,
    CollectorRegistry,
    CollectorRuntime,
    CollectorSchedule,
    EventBus,
    SentinelEvent,
)
from sentinel_x.systemd import (
    JournalField,
    StatefulSystemdJournalCollector,
    SystemdJournalBatch,
    SystemdJournalCollectorContractError,
    SystemdJournalCollectorStateError,
    SystemdJournalCommandError,
    SystemdJournalEntry,
    systemd_journal_batch_to_event,
)

_BOOT_A = "a" * 32
_BOOT_B = "b" * 32
_NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _entry(
    cursor: str,
    *,
    boot_id: str = _BOOT_A,
    message: str | bytes | None = "hello",
) -> SystemdJournalEntry:
    fields: list[JournalField] = [
        JournalField(name="PRIORITY", values=("6",)),
        JournalField(name="_SYSTEMD_UNIT", values=("demo.service",)),
        JournalField(name="_SYSTEMD_INVOCATION_ID", values=("f" * 32,)),
        JournalField(name="_PID", values=("42",)),
        JournalField(name="_UID", values=("1000",)),
        JournalField(name="_COMM", values=("demo",)),
    ]
    fields.append(JournalField(name="MESSAGE", values=(message,)))
    return SystemdJournalEntry(
        cursor=cursor,
        realtime_timestamp_usec=10,
        monotonic_timestamp_usec=5,
        boot_id=boot_id,
        fields=tuple(fields),
    )


def _batch(
    *entries: SystemdJournalEntry,
    after_cursor: str | None = None,
    diagnostic: str | None = None,
    limit: int = 64,
) -> SystemdJournalBatch:
    return SystemdJournalBatch(
        requested_unit="demo.service",
        after_cursor=after_cursor,
        entry_limit=limit,
        entries=tuple(entries),
        diagnostic=diagnostic,
        captured_at=_NOW,
    )


class _QueuedReader:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str | None, int]] = []

    def read_service(
        self,
        unit_name: str,
        *,
        after_cursor: str | None = None,
        max_entries: int = 64,
    ) -> SystemdJournalBatch:
        self.calls.append((unit_name, after_cursor, max_entries))
        if not self.results:
            raise AssertionError("unexpected journal reader call")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        if not isinstance(result, SystemdJournalBatch):
            return result  # type: ignore[return-value]
        return result


class _BootReader:
    def __init__(self, value: str = _BOOT_A) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.value


class _ManualClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, value: int = 100) -> None:
        self.now_ns += value


def _runtime_for_collector(
    collector: StatefulSystemdJournalCollector,
    bus: EventBus,
    clock: _ManualClock,
) -> CollectorRuntime:
    definition = CollectorDefinition(
        enabled=True,
        schedule=CollectorSchedule(name=collector.name, interval_ns=100),
        execution_spec=CollectorExecutionSpec(
            name=collector.name,
            handler=collector.collect,
            policy=CollectorExecutionPolicy(),
        ),
    )
    return CollectorRuntime(bus, CollectorRegistry((definition,)), clock_ns=clock)


class StatefulJournalCollectorTests(unittest.TestCase):
    """Verify cursor commit, retry, boot, and bounded projection invariants."""

    def _collector(
        self,
        reader: _QueuedReader,
        boot: _BootReader | None = None,
    ) -> StatefulSystemdJournalCollector:
        return StatefulSystemdJournalCollector(
            "journal.demo.1234",
            "demo.service",
            reader=reader,
            boot_id_reader=_BootReader() if boot is None else boot,
            max_entries=8,
        )

    def test_nonempty_batch_stays_uncommitted_until_acknowledged(self) -> None:
        reader = _QueuedReader(_batch(_entry("cursor-1"), limit=8))
        collector = self._collector(reader)

        emission = collector.collect()
        before = collector.snapshot()
        emission.commit_publication()
        after = collector.snapshot()

        self.assertIsNotNone(emission.event)
        self.assertTrue(before.pending_publication)
        self.assertIsNone(before.committed_cursor)
        self.assertFalse(after.pending_publication)
        self.assertEqual(after.committed_cursor, "cursor-1")
        self.assertEqual(after.committed_boot_id, _BOOT_A)
        self.assertEqual(after.publication_commit_count, 1)

    def test_rollback_retries_same_event_without_rereading_journal(self) -> None:
        reader = _QueuedReader(_batch(_entry("cursor-1"), limit=8))
        collector = self._collector(reader)
        first = collector.collect()
        first_id = first.event.event_id if first.event is not None else None

        first.rollback_publication()
        retry = collector.collect()
        retry_id = retry.event.event_id if retry.event is not None else None

        self.assertEqual(first_id, retry_id)
        self.assertEqual(len(reader.calls), 1)
        self.assertEqual(collector.snapshot().retry_emission_count, 1)
        self.assertEqual(collector.snapshot().publication_rollback_count, 1)
        retry.commit_publication()
        self.assertEqual(collector.snapshot().committed_cursor, "cursor-1")

    def test_empty_poll_commits_boot_without_publication_transaction(self) -> None:
        reader = _QueuedReader(_batch(limit=8))
        collector = self._collector(reader)

        emission = collector.collect()
        snapshot = collector.snapshot()

        self.assertIsNone(emission.event)
        self.assertEqual(snapshot.committed_boot_id, _BOOT_A)
        self.assertIsNone(snapshot.committed_cursor)
        self.assertEqual(snapshot.empty_poll_count, 1)
        with self.assertRaises(SystemdJournalCollectorStateError):
            emission.commit_publication()

    def test_reader_failure_preserves_committed_cursor(self) -> None:
        reader = _QueuedReader(
            _batch(_entry("cursor-1"), limit=8),
            SystemdJournalCommandError("stale or unavailable"),
        )
        collector = self._collector(reader)
        first = collector.collect()
        first.commit_publication()

        with self.assertRaises(SystemdJournalCommandError):
            collector.collect()

        snapshot = collector.snapshot()
        self.assertEqual(snapshot.committed_cursor, "cursor-1")
        self.assertEqual(snapshot.read_failure_count, 1)
        self.assertEqual(reader.calls[-1][1], "cursor-1")

    def test_boot_change_queries_without_old_cursor_and_commits_after_publish(
        self,
    ) -> None:
        boot = _BootReader(_BOOT_A)
        reader = _QueuedReader(
            _batch(_entry("cursor-a", boot_id=_BOOT_A), limit=8),
            _batch(_entry("cursor-b", boot_id=_BOOT_B), limit=8),
        )
        collector = self._collector(reader, boot)
        first = collector.collect()
        first.commit_publication()
        boot.value = _BOOT_B

        second = collector.collect()
        pending = collector.snapshot()
        second.commit_publication()
        committed = collector.snapshot()

        self.assertIsNone(reader.calls[-1][1])
        self.assertEqual(pending.committed_boot_id, _BOOT_A)
        self.assertEqual(committed.committed_boot_id, _BOOT_B)
        self.assertEqual(committed.committed_cursor, "cursor-b")
        self.assertEqual(committed.boot_reset_count, 1)

    def test_empty_boot_change_resets_old_cursor_after_successful_read(self) -> None:
        boot = _BootReader(_BOOT_A)
        reader = _QueuedReader(
            _batch(_entry("cursor-a", boot_id=_BOOT_A), limit=8),
            _batch(limit=8),
        )
        collector = self._collector(reader, boot)
        collector.collect().commit_publication()
        boot.value = _BOOT_B

        emission = collector.collect()
        snapshot = collector.snapshot()

        self.assertIsNone(emission.event)
        self.assertEqual(snapshot.committed_boot_id, _BOOT_B)
        self.assertIsNone(snapshot.committed_cursor)
        self.assertEqual(snapshot.boot_reset_count, 1)

    def test_cross_boot_entry_is_rejected_without_state_change(self) -> None:
        reader = _QueuedReader(_batch(_entry("cursor-x", boot_id=_BOOT_B), limit=8))
        collector = self._collector(reader)

        with self.assertRaises(SystemdJournalCollectorContractError):
            collector.collect()

        snapshot = collector.snapshot()
        self.assertIsNone(snapshot.committed_cursor)
        self.assertFalse(snapshot.pending_publication)

    def test_wrong_batch_unit_is_rejected(self) -> None:
        wrong = SystemdJournalBatch(
            requested_unit="other.service",
            after_cursor=None,
            entry_limit=8,
            entries=(),
            diagnostic=None,
            captured_at=_NOW,
        )
        collector = self._collector(_QueuedReader(wrong))

        with self.assertRaises(SystemdJournalCollectorContractError):
            collector.collect()

    def test_wrong_reader_result_type_is_rejected(self) -> None:
        collector = self._collector(_QueuedReader(object()))

        with self.assertRaises(SystemdJournalCollectorContractError):
            collector.collect()

    def test_batch_starting_cursor_must_match_requested_cursor(self) -> None:
        reader = _QueuedReader(
            _batch(_entry("cursor-1"), limit=8),
            _batch(after_cursor="wrong-cursor", limit=8),
        )
        collector = self._collector(reader)
        collector.collect().commit_publication()

        with self.assertRaisesRegex(
            SystemdJournalCollectorContractError,
            "wrong starting cursor",
        ):
            collector.collect()

    def test_batch_entry_limit_must_match_requested_bound(self) -> None:
        collector = self._collector(_QueuedReader(_batch(limit=7)))

        with self.assertRaisesRegex(
            SystemdJournalCollectorContractError,
            "wrong entry limit",
        ):
            collector.collect()

    def test_batch_monotonic_timestamps_must_not_regress(self) -> None:
        first = _entry("cursor-1")
        second = SystemdJournalEntry(
            cursor="cursor-2",
            realtime_timestamp_usec=20,
            monotonic_timestamp_usec=4,
            boot_id=_BOOT_A,
            fields=first.fields,
        )
        collector = self._collector(_QueuedReader(_batch(first, second, limit=8)))

        with self.assertRaisesRegex(
            SystemdJournalCollectorContractError,
            "monotonic timestamps must not regress",
        ):
            collector.collect()

    def test_empty_diagnostic_batch_emits_acknowledged_data_quality_event(self) -> None:
        reader = _QueuedReader(_batch(diagnostic="limited journal access", limit=8))
        collector = self._collector(reader)

        emission = collector.collect()

        self.assertIsNotNone(emission.event)
        self.assertEqual(
            emission.event.attributes["diagnostic"],
            "limited journal access",
        )
        self.assertTrue(collector.snapshot().pending_publication)
        emission.commit_publication()
        self.assertEqual(collector.snapshot().committed_boot_id, _BOOT_A)

    def test_text_message_projection_is_bounded(self) -> None:
        event = systemd_journal_batch_to_event(
            _batch(_entry("cursor-1", message="x" * 2000), limit=8),
            collector_name="journal.demo.1234",
            current_boot_id=_BOOT_A,
        )
        projected = event.attributes["entries"]
        self.assertIsInstance(projected, list)
        message = projected[0]["message"]  # type: ignore[index]
        self.assertEqual(message["kind"], "text")  # type: ignore[index]
        self.assertTrue(message["truncated"])  # type: ignore[index]
        self.assertLessEqual(
            len(message["value"]),  # type: ignore[arg-type,index]
            1024,
        )

    def test_binary_message_projection_uses_length_and_hash_not_raw_bytes(
        self,
    ) -> None:
        event = systemd_journal_batch_to_event(
            _batch(_entry("cursor-1", message=b"abc"), limit=8),
            collector_name="journal.demo.1234",
            current_boot_id=_BOOT_A,
        )
        projected = event.attributes["entries"]
        message = projected[0]["message"]  # type: ignore[index]
        self.assertEqual(message["kind"], "binary")  # type: ignore[index]
        self.assertEqual(message["length"], 3)  # type: ignore[index]
        self.assertNotIn("value", message)

    def test_entry_projection_hashes_cursor_instead_of_persisting_every_cursor(
        self,
    ) -> None:
        event = systemd_journal_batch_to_event(
            _batch(_entry("opaque-cursor"), limit=8),
            collector_name="journal.demo.1234",
            current_boot_id=_BOOT_A,
        )
        projected = event.attributes["entries"]
        row = projected[0]  # type: ignore[index]
        self.assertNotIn("cursor", row)
        self.assertEqual(len(row["cursor_sha256"]), 64)
        self.assertEqual(event.attributes["next_cursor"], "opaque-cursor")

    def test_invalid_boot_id_reader_value_is_rejected_before_journal_read(self) -> None:
        boot = _BootReader("not-a-boot-id")
        reader = _QueuedReader(_batch(limit=8))
        collector = self._collector(reader, boot)

        with self.assertRaises(SystemdJournalCollectorContractError):
            collector.collect()

        self.assertEqual(reader.calls, [])

    def test_publication_token_cannot_be_reused_after_commit(self) -> None:
        collector = self._collector(_QueuedReader(_batch(_entry("cursor-1"), limit=8)))
        emission = collector.collect()
        emission.commit_publication()

        with self.assertRaises(SystemdJournalCollectorStateError):
            emission.commit_publication()

    def test_reader_uses_committed_cursor_after_successful_ack(self) -> None:
        reader = _QueuedReader(
            _batch(_entry("cursor-1"), limit=8),
            _batch(after_cursor="cursor-1", limit=8),
        )
        collector = self._collector(reader)
        collector.collect().commit_publication()

        collector.collect()

        self.assertEqual(reader.calls[1][1], "cursor-1")

    def test_core_runtime_commits_cursor_after_successful_publication(self) -> None:
        reader = _QueuedReader(_batch(_entry("cursor-1"), limit=8))
        collector = self._collector(reader)
        bus = EventBus()
        received: list[str] = []
        bus.subscribe(lambda event: received.append(event.event_id))
        runtime = _runtime_for_collector(collector, bus, _ManualClock())

        cycle = runtime.run_due()

        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual(cycle.publish_failure_count, 0)
        self.assertEqual(collector.snapshot().committed_cursor, "cursor-1")
        self.assertEqual(len(received), 1)

    def test_core_runtime_retries_same_event_after_partial_publish_failure(
        self,
    ) -> None:
        reader = _QueuedReader(_batch(_entry("cursor-1"), limit=8))
        collector = self._collector(reader)
        bus = EventBus()
        delivered: list[str] = []
        should_fail = {"value": True}

        bus.subscribe(lambda event: delivered.append(event.event_id))

        def conditional_failure(_: SentinelEvent) -> None:
            if should_fail["value"]:
                raise RuntimeError("temporary subscriber failure")

        bus.subscribe(conditional_failure)
        clock = _ManualClock()
        runtime = _runtime_for_collector(collector, bus, clock)

        first = runtime.run_due()
        first_event_id = delivered[-1]
        self.assertEqual(first.publish_failure_count, 1)
        self.assertIsNone(collector.snapshot().committed_cursor)
        self.assertEqual(len(reader.calls), 1)

        should_fail["value"] = False
        clock.advance()
        second = runtime.run_due()

        self.assertEqual(second.publish_failure_count, 0)
        self.assertEqual(delivered[-1], first_event_id)
        self.assertEqual(collector.snapshot().committed_cursor, "cursor-1")
        self.assertEqual(len(reader.calls), 1)

    def test_snapshot_is_serialization_friendly(self) -> None:
        collector = self._collector(_QueuedReader(_batch(limit=8)))
        collector.collect()

        payload = collector.snapshot().to_dict()

        self.assertEqual(payload["collector_name"], "journal.demo.1234")
        self.assertEqual(payload["unit_name"], "demo.service")
        self.assertEqual(payload["empty_poll_count"], 1)


if __name__ == "__main__":
    unittest.main()
