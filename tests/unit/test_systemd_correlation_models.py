"""Tests for typed systemd temporal-correlation result models."""

from __future__ import annotations

import unittest

from sentinel_x.systemd.correlation_models import (
    SystemdCorrelationBasis,
    SystemdCorrelationMatch,
    SystemdCorrelationModelError,
    SystemdTemporalCorrelationReport,
    SystemdTemporalRelation,
)

_BOOT = "a" * 32
_HASH = "b" * 64


def _match(
    *,
    service_event_id: str = "service-event",
    journal_event_id: str = "journal-event",
    journal_entry_index: int = 0,
    basis: SystemdCorrelationBasis = SystemdCorrelationBasis.EXACT_INVOCATION,
    matched_journal_invocation_id: str | None = "1" * 32,
    journal_priority: int | None = 6,
) -> SystemdCorrelationMatch:
    return SystemdCorrelationMatch(
        service_event_id=service_event_id,
        journal_event_id=journal_event_id,
        journal_entry_index=journal_entry_index,
        requested_unit="demo.service",
        canonical_unit="demo.service",
        boot_id=_BOOT,
        basis=basis,
        temporal_relation=SystemdTemporalRelation.AFTER,
        monotonic_delta_usec=10,
        service_invocation_id="1" * 32,
        matched_journal_invocation_id=matched_journal_invocation_id,
        journal_cursor_sha256=_HASH,
        journal_priority=journal_priority,
        journal_message="ready",
    )


class SystemdCorrelationModelTests(unittest.TestCase):
    def test_match_serializes_enum_values(self) -> None:
        payload = _match().to_dict()
        self.assertEqual(payload["basis"], "exact_invocation")
        self.assertEqual(payload["temporal_relation"], "after")

    def test_match_rejects_negative_entry_index(self) -> None:
        with self.assertRaises(SystemdCorrelationModelError):
            _match(journal_entry_index=-1)

    def test_match_rejects_priority_outside_syslog_range(self) -> None:
        with self.assertRaises(SystemdCorrelationModelError):
            _match(journal_priority=8)

    def test_report_counts_each_correlation_basis(self) -> None:
        exact = _match()
        fallback = _match(
            journal_entry_index=1,
            basis=SystemdCorrelationBasis.UNIT_AND_TIME,
            matched_journal_invocation_id=None,
        )
        report = SystemdTemporalCorrelationReport(
            service_event_id="service-event",
            journal_event_id="journal-event",
            requested_unit="demo.service",
            canonical_unit="demo.service",
            boot_id=_BOOT,
            evaluated_entry_count=2,
            matches=(exact, fallback),
        )
        self.assertEqual(report.match_count, 2)
        self.assertEqual(report.exact_invocation_count, 1)
        self.assertEqual(report.unit_and_time_count, 1)

    def test_report_rejects_match_from_different_service_event(self) -> None:
        with self.assertRaises(SystemdCorrelationModelError):
            SystemdTemporalCorrelationReport(
                service_event_id="service-event",
                journal_event_id="journal-event",
                requested_unit="demo.service",
                canonical_unit="demo.service",
                boot_id=_BOOT,
                evaluated_entry_count=1,
                matches=(_match(service_event_id="different"),),
            )

    def test_report_serialization_is_json_friendly(self) -> None:
        report = SystemdTemporalCorrelationReport(
            service_event_id="service-event",
            journal_event_id="journal-event",
            requested_unit="demo.service",
            canonical_unit="demo.service",
            boot_id=_BOOT,
            evaluated_entry_count=1,
            matches=(_match(),),
        )
        payload = report.to_dict()
        self.assertEqual(payload["match_count"], 1)
        self.assertIsInstance(payload["matches"], list)


if __name__ == "__main__":
    unittest.main()
