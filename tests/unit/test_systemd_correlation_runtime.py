"""Tests for bounded stateful systemd/journald correlation fusion."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.systemd.correlation import SystemdTemporalCorrelator
from sentinel_x.systemd.correlation_runtime import (
    SYSTEMD_CORRELATION_OBSERVATION_SOURCE,
    SYSTEMD_CORRELATION_OBSERVATION_TYPE,
    SystemdCorrelationTracker,
    systemd_correlation_report_to_event,
)
from sentinel_x.systemd.journal_observation import (
    SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
    SYSTEMD_JOURNAL_OBSERVATION_TYPE,
)
from sentinel_x.systemd.observation import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
)

_BOOT_A = "a" * 32
_BOOT_B = "b" * 32
_INVOCATION_A = "1" * 32
_INVOCATION_B = "2" * 32
_NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


def _service_event(
    *,
    event_id: str,
    boot_id: str = _BOOT_A,
    invocation_id: str | None = _INVOCATION_A,
    transition: int | None = 1_000_000,
    requested_name: str = "demo.service",
) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_SERVICE_OBSERVATION_SOURCE,
        message="service state",
        event_id=event_id,
        occurred_at=_NOW,
        attributes={
            "observation_type": SYSTEMD_SERVICE_OBSERVATION_TYPE,
            "requested_name": requested_name,
            "canonical_name": requested_name,
            "names": [requested_name],
            "boot_id": boot_id,
            "invocation_id": invocation_id,
            "state_change_monotonic_usec": transition,
        },
    )


def _entry(
    *,
    index: int,
    cursor_character: str,
    monotonic: int,
    invocation_id: str | None = _INVOCATION_A,
    boot_id: str = _BOOT_A,
    unit_name: str = "demo.service",
) -> dict[str, object]:
    return {
        "index": index,
        "cursor_sha256": cursor_character * 64,
        "realtime_timestamp_usec": 2_000_000 + index,
        "monotonic_timestamp_usec": monotonic,
        "boot_id": boot_id,
        "priority": 6,
        "message": {"kind": "text", "value": f"message-{index}"},
        "systemd_unit": unit_name,
        "systemd_invocation_id": invocation_id,
        "invocation_id": None,
        "unit": None,
        "object_systemd_unit": None,
        "object_systemd_invocation_id": None,
        "coredump_unit": None,
    }


def _journal_event(
    *,
    event_id: str,
    entries: list[dict[str, object]],
    boot_id: str = _BOOT_A,
    unit_name: str = "demo.service",
) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
        message="journal evidence",
        event_id=event_id,
        occurred_at=_NOW,
        attributes={
            "observation_type": SYSTEMD_JOURNAL_OBSERVATION_TYPE,
            "requested_unit": unit_name,
            "boot_id": boot_id,
            "entry_count": len(entries),
            "entries": entries,
        },
    )


class SystemdCorrelationTrackerTests(unittest.TestCase):
    """Validate bounded order-independent fusion and deduplication."""

    def test_service_then_journal_emits_strong_correlation(self) -> None:
        tracker = SystemdCorrelationTracker()
        service = _service_event(event_id="service-1")
        journal = _journal_event(
            event_id="journal-1",
            entries=[
                _entry(index=0, cursor_character="a", monotonic=1_000_100),
            ],
        )
        self.assertEqual(tracker.ingest(service), ())
        emitted = tracker.ingest(journal)
        self.assertEqual(len(emitted), 1)
        event = emitted[0]
        self.assertEqual(event.source, SYSTEMD_CORRELATION_OBSERVATION_SOURCE)
        self.assertEqual(
            event.attributes["observation_type"],
            SYSTEMD_CORRELATION_OBSERVATION_TYPE,
        )
        self.assertEqual(event.attributes["evidence_strength"], "strong")
        self.assertEqual(event.attributes["match_count"], 1)
        self.assertEqual(event.attributes["exact_invocation_count"], 1)

    def test_journal_then_service_is_order_independent(self) -> None:
        tracker = SystemdCorrelationTracker()
        journal = _journal_event(
            event_id="journal-1",
            entries=[
                _entry(index=0, cursor_character="b", monotonic=1_000_100),
            ],
        )
        self.assertEqual(tracker.ingest(journal), ())
        emitted = tracker.ingest(_service_event(event_id="service-1"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].attributes["match_count"], 1)

    def test_duplicate_input_event_is_ignored(self) -> None:
        tracker = SystemdCorrelationTracker()
        service = _service_event(event_id="service-1")
        tracker.ingest(service)
        self.assertEqual(tracker.ingest(service), ())
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot.service_events_ingested, 1)
        self.assertEqual(snapshot.duplicate_input_events_ignored, 1)

    def test_repeated_service_snapshot_same_invocation_does_not_reemit(self) -> None:
        tracker = SystemdCorrelationTracker()
        journal = _journal_event(
            event_id="journal-1",
            entries=[
                _entry(index=0, cursor_character="c", monotonic=1_000_100),
            ],
        )
        tracker.ingest(journal)
        first = tracker.ingest(_service_event(event_id="service-1"))
        second = tracker.ingest(_service_event(event_id="service-2"))
        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())
        self.assertEqual(tracker.snapshot().duplicate_matches_suppressed, 1)

    def test_same_journal_cursor_is_not_reemitted_for_another_cycle(self) -> None:
        tracker = SystemdCorrelationTracker()
        first_journal = _journal_event(
            event_id="journal-1",
            entries=[
                _entry(index=0, cursor_character="d", monotonic=1_000_100),
            ],
        )
        tracker.ingest(_service_event(event_id="service-1"))
        first = tracker.ingest(first_journal)
        self.assertEqual(len(first), 1)

        tracker.ingest(
            _service_event(
                event_id="service-2",
                invocation_id=_INVOCATION_B,
                transition=2_000_000,
            )
        )
        repeated_cursor = _journal_event(
            event_id="journal-2",
            entries=[
                _entry(
                    index=0,
                    cursor_character="d",
                    monotonic=2_000_100,
                    invocation_id=_INVOCATION_B,
                ),
            ],
        )
        self.assertEqual(tracker.ingest(repeated_cursor), ())

    def test_exact_evidence_upgrades_provisional_match(self) -> None:
        tracker = SystemdCorrelationTracker(
            correlator=SystemdTemporalCorrelator(max_state_delta_usec=1_000)
        )
        journal = _journal_event(
            event_id="journal-1",
            entries=[
                _entry(
                    index=0,
                    cursor_character="9",
                    monotonic=1_000_100,
                    invocation_id=None,
                )
            ],
        )
        tracker.ingest(journal)
        provisional = tracker.ingest(
            _service_event(
                event_id="service-fallback",
                invocation_id=None,
                transition=1_000_000,
            )
        )
        self.assertEqual(len(provisional), 1)
        self.assertEqual(
            provisional[0].attributes["evidence_strength"],
            "provisional",
        )

        stronger_journal = _journal_event(
            event_id="journal-2",
            entries=[
                _entry(
                    index=0,
                    cursor_character="9",
                    monotonic=1_000_100,
                    invocation_id=_INVOCATION_A,
                )
            ],
        )
        tracker.ingest(stronger_journal)
        upgraded = tracker.ingest(
            _service_event(
                event_id="service-exact",
                invocation_id=_INVOCATION_A,
                transition=9_000_000,
            )
        )
        self.assertEqual(len(upgraded), 1)
        event = upgraded[0]
        self.assertEqual(event.attributes["evidence_strength"], "strong")
        self.assertEqual(event.attributes["revision_kind"], "upgrade")
        self.assertEqual(
            event.attributes["supersedes_event_ids"],
            [provisional[0].event_id],
        )
        self.assertEqual(tracker.snapshot().correlation_upgrades_emitted, 1)

    def test_exact_invocation_wins_over_time_fallback(self) -> None:
        tracker = SystemdCorrelationTracker(
            correlator=SystemdTemporalCorrelator(max_state_delta_usec=10_000)
        )
        tracker.ingest(
            _service_event(
                event_id="fallback",
                invocation_id=None,
                transition=1_000_010,
            )
        )
        tracker.ingest(
            _service_event(
                event_id="exact",
                invocation_id=_INVOCATION_A,
                transition=9_000_000,
            )
        )
        emitted = tracker.ingest(
            _journal_event(
                event_id="journal-1",
                entries=[
                    _entry(index=0, cursor_character="e", monotonic=1_000_000),
                ],
            )
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].attributes["service_event_id"], "exact")
        self.assertEqual(emitted[0].attributes["evidence_strength"], "strong")

    def test_nearest_time_fallback_wins_between_state_transitions(self) -> None:
        tracker = SystemdCorrelationTracker(
            correlator=SystemdTemporalCorrelator(max_state_delta_usec=1_000)
        )
        tracker.ingest(
            _service_event(
                event_id="farther",
                invocation_id=None,
                transition=999_500,
            )
        )
        tracker.ingest(
            _service_event(
                event_id="nearer",
                invocation_id=None,
                transition=999_900,
            )
        )
        emitted = tracker.ingest(
            _journal_event(
                event_id="journal-1",
                entries=[
                    _entry(
                        index=0,
                        cursor_character="f",
                        monotonic=1_000_000,
                        invocation_id=None,
                    ),
                ],
            )
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].attributes["service_event_id"], "nearer")
        self.assertEqual(
            emitted[0].attributes["evidence_strength"],
            "provisional",
        )

    def test_cross_boot_and_wrong_unit_do_not_emit(self) -> None:
        tracker = SystemdCorrelationTracker()
        tracker.ingest(_service_event(event_id="service-1"))
        cross_boot = _journal_event(
            event_id="journal-boot",
            boot_id=_BOOT_B,
            entries=[
                _entry(
                    index=0,
                    cursor_character="1",
                    monotonic=1_000_100,
                    boot_id=_BOOT_B,
                )
            ],
        )
        wrong_unit = _journal_event(
            event_id="journal-unit",
            unit_name="other.service",
            entries=[
                _entry(
                    index=0,
                    cursor_character="2",
                    monotonic=1_000_100,
                    unit_name="other.service",
                )
            ],
        )
        self.assertEqual(tracker.ingest(cross_boot), ())
        self.assertEqual(tracker.ingest(wrong_unit), ())

    def test_unrelated_event_is_ignored_without_retention(self) -> None:
        tracker = SystemdCorrelationTracker()
        unrelated = SentinelEvent(
            kind=EventKind.OBSERVATION,
            source="sentinel_x.host.cpu",
            message="cpu",
            attributes={"observation_type": "linux.host.cpu"},
        )
        self.assertEqual(tracker.ingest(unrelated), ())
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot.unrelated_events_ignored, 1)
        self.assertEqual(snapshot.retained_service_events, 0)
        self.assertEqual(snapshot.retained_journal_events, 0)

    def test_retention_is_bounded(self) -> None:
        tracker = SystemdCorrelationTracker(
            max_retained_service_events=2,
            max_retained_journal_events=2,
        )
        for index in range(3):
            tracker.ingest(_service_event(event_id=f"service-{index}"))
        for index in range(3):
            tracker.ingest(
                _journal_event(
                    event_id=f"journal-{index}",
                    boot_id=_BOOT_B,
                    entries=[],
                )
            )
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot.retained_service_events, 2)
        self.assertEqual(snapshot.retained_journal_events, 2)

    def test_emitted_match_key_cache_is_bounded(self) -> None:
        tracker = SystemdCorrelationTracker(max_emitted_match_keys=2)
        tracker.ingest(_service_event(event_id="service-1"))
        for index, character in enumerate(("3", "4", "5")):
            emitted = tracker.ingest(
                _journal_event(
                    event_id=f"journal-{index}",
                    entries=[
                        _entry(
                            index=0,
                            cursor_character=character,
                            monotonic=1_000_100 + index,
                        )
                    ],
                )
            )
            self.assertEqual(len(emitted), 1)
        self.assertEqual(tracker.snapshot().emitted_match_keys, 2)

    def test_snapshot_is_serialization_friendly(self) -> None:
        tracker = SystemdCorrelationTracker()
        tracker.ingest(_service_event(event_id="service-1"))
        payload = tracker.snapshot().to_dict()
        self.assertEqual(payload["service_events_ingested"], 1)
        self.assertEqual(payload["retained_service_events"], 1)
        self.assertTrue(all(isinstance(value, int) for value in payload.values()))

    def test_constructor_bounds_are_strict(self) -> None:
        with self.assertRaises(TypeError):
            SystemdCorrelationTracker(max_retained_service_events=True)
        with self.assertRaises(ValueError):
            SystemdCorrelationTracker(max_retained_journal_events=0)
        with self.assertRaises(ValueError):
            SystemdCorrelationTracker(max_emitted_match_keys=4097)

    def test_empty_report_projection_is_rejected(self) -> None:
        correlator = SystemdTemporalCorrelator()
        report = correlator.correlate(
            _service_event(event_id="service-1", boot_id=_BOOT_A),
            _journal_event(
                event_id="journal-1",
                boot_id=_BOOT_B,
                entries=[],
            ),
        )
        with self.assertRaises(ValueError):
            systemd_correlation_report_to_event(report)


if __name__ == "__main__":
    unittest.main()
