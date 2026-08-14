"""Tests for pure systemd and journald temporal correlation."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.systemd.correlation import (
    SystemdCorrelationContractError,
    SystemdTemporalCorrelator,
)
from sentinel_x.systemd.correlation_models import (
    SystemdCorrelationBasis,
    SystemdTemporalRelation,
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
_CURSOR_HASH = "c" * 64
_NOW = datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)


def _service_event(
    *,
    boot_id: str = _BOOT_A,
    invocation_id: str | None = _INVOCATION_A,
    state_change_monotonic_usec: int | None = 1_000_000,
    requested_name: str = "demo.service",
    canonical_name: str = "demo.service",
    names: list[str] | None = None,
) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_SERVICE_OBSERVATION_SOURCE,
        message="service state",
        occurred_at=_NOW,
        attributes={
            "observation_type": SYSTEMD_SERVICE_OBSERVATION_TYPE,
            "requested_name": requested_name,
            "canonical_name": canonical_name,
            "names": [canonical_name] if names is None else names,
            "boot_id": boot_id,
            "invocation_id": invocation_id,
            "state_change_monotonic_usec": state_change_monotonic_usec,
        },
    )


def _entry(
    *,
    index: int = 0,
    monotonic_timestamp_usec: int = 1_000_100,
    boot_id: str = _BOOT_A,
    systemd_invocation_id: str | None = _INVOCATION_A,
    invocation_id: str | None = None,
    object_systemd_invocation_id: str | None = None,
    systemd_unit: str | None = "demo.service",
    unit: str | None = None,
    message: object | None = None,
) -> dict[str, object]:
    return {
        "index": index,
        "cursor_sha256": _CURSOR_HASH,
        "realtime_timestamp_usec": 2_000_000,
        "monotonic_timestamp_usec": monotonic_timestamp_usec,
        "boot_id": boot_id,
        "priority": 6,
        "message": ({"kind": "text", "value": "hello"} if message is None else message),
        "systemd_unit": systemd_unit,
        "systemd_invocation_id": systemd_invocation_id,
        "invocation_id": invocation_id,
        "unit": unit,
        "object_systemd_unit": None,
        "object_systemd_invocation_id": object_systemd_invocation_id,
        "coredump_unit": None,
    }


def _journal_event(
    *entries: dict[str, object],
    boot_id: str = _BOOT_A,
    requested_unit: str = "demo.service",
) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_JOURNAL_OBSERVATION_SOURCE,
        message="journal evidence",
        occurred_at=_NOW,
        attributes={
            "observation_type": SYSTEMD_JOURNAL_OBSERVATION_TYPE,
            "requested_unit": requested_unit,
            "boot_id": boot_id,
            "entry_count": len(entries),
            "entries": list(entries),
        },
    )


class SystemdTemporalCorrelatorTests(unittest.TestCase):
    """Validate conservative boot-, invocation-, unit-, and time-aware matching."""

    def test_exact_process_invocation_match_is_strongest_basis(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(),
            _journal_event(_entry()),
        )
        self.assertEqual(report.match_count, 1)
        match = report.matches[0]
        self.assertIs(match.basis, SystemdCorrelationBasis.EXACT_INVOCATION)
        self.assertEqual(match.matched_journal_invocation_id, _INVOCATION_A)
        self.assertIs(match.temporal_relation, SystemdTemporalRelation.AFTER)
        self.assertEqual(match.monotonic_delta_usec, 100)

    def test_manager_invocation_id_can_match_service_runtime_cycle(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(),
            _journal_event(
                _entry(
                    systemd_invocation_id=None,
                    invocation_id=_INVOCATION_A,
                )
            ),
        )
        self.assertEqual(report.exact_invocation_count, 1)

    def test_object_invocation_id_can_match_service_runtime_cycle(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(),
            _journal_event(
                _entry(
                    systemd_invocation_id=None,
                    object_systemd_invocation_id=_INVOCATION_A,
                )
            ),
        )
        self.assertEqual(report.exact_invocation_count, 1)

    def test_exact_invocation_match_is_not_rejected_by_time_window(self) -> None:
        report = SystemdTemporalCorrelator(max_state_delta_usec=10).correlate(
            _service_event(),
            _journal_event(_entry(monotonic_timestamp_usec=9_000_000)),
        )
        self.assertEqual(report.exact_invocation_count, 1)

    def test_unit_and_time_fallback_matches_when_invocation_is_missing(self) -> None:
        report = SystemdTemporalCorrelator(max_state_delta_usec=500).correlate(
            _service_event(invocation_id=None),
            _journal_event(
                _entry(
                    monotonic_timestamp_usec=999_800,
                    systemd_invocation_id=None,
                )
            ),
        )
        match = report.matches[0]
        self.assertIs(match.basis, SystemdCorrelationBasis.UNIT_AND_TIME)
        self.assertIs(match.temporal_relation, SystemdTemporalRelation.BEFORE)
        self.assertEqual(match.monotonic_delta_usec, -200)

    def test_unit_and_time_fallback_respects_closed_window_boundary(self) -> None:
        correlator = SystemdTemporalCorrelator(max_state_delta_usec=250)
        report = correlator.correlate(
            _service_event(invocation_id=None),
            _journal_event(
                _entry(
                    monotonic_timestamp_usec=1_000_250,
                    systemd_invocation_id=None,
                )
            ),
        )
        self.assertEqual(report.unit_and_time_count, 1)
        self.assertIs(
            report.matches[0].temporal_relation,
            SystemdTemporalRelation.AFTER,
        )

    def test_entry_outside_time_window_is_not_correlated(self) -> None:
        report = SystemdTemporalCorrelator(max_state_delta_usec=100).correlate(
            _service_event(invocation_id=None),
            _journal_event(
                _entry(
                    monotonic_timestamp_usec=1_000_101,
                    systemd_invocation_id=None,
                )
            ),
        )
        self.assertEqual(report.match_count, 0)

    def test_explicit_invocation_conflict_blocks_time_fallback(self) -> None:
        report = SystemdTemporalCorrelator(max_state_delta_usec=10_000).correlate(
            _service_event(invocation_id=_INVOCATION_A),
            _journal_event(_entry(systemd_invocation_id=_INVOCATION_B)),
        )
        self.assertEqual(report.match_count, 0)

    def test_different_boot_is_never_correlated(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(boot_id=_BOOT_A),
            _journal_event(_entry(boot_id=_BOOT_B), boot_id=_BOOT_B),
        )
        self.assertEqual(report.match_count, 0)

    def test_different_requested_unit_is_never_correlated(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(),
            _journal_event(_entry(), requested_unit="other.service"),
        )
        self.assertEqual(report.match_count, 0)

    def test_service_aliases_allow_requested_alias_correlation(self) -> None:
        service = _service_event(
            requested_name="ssh.service",
            canonical_name="sshd.service",
            names=["ssh.service", "sshd.service"],
        )
        report = SystemdTemporalCorrelator().correlate(
            service,
            _journal_event(
                _entry(systemd_unit="sshd.service"),
                requested_unit="ssh.service",
            ),
        )
        self.assertEqual(report.match_count, 1)
        self.assertEqual(report.canonical_unit, "sshd.service")

    def test_missing_invocation_and_transition_produces_no_guess(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(invocation_id=None, state_change_monotonic_usec=None),
            _journal_event(_entry(systemd_invocation_id=None)),
        )
        self.assertEqual(report.match_count, 0)

    def test_journal_entry_boot_mismatch_is_contract_error(self) -> None:
        with self.assertRaisesRegex(
            SystemdCorrelationContractError,
            "journal entry boot_id",
        ):
            SystemdTemporalCorrelator().correlate(
                _service_event(),
                _journal_event(_entry(boot_id=_BOOT_B)),
            )

    def test_declared_entry_count_mismatch_is_contract_error(self) -> None:
        event = _journal_event(_entry())
        malformed = SentinelEvent(
            kind=event.kind,
            source=event.source,
            message=event.message,
            occurred_at=event.occurred_at,
            attributes={**event.attributes, "entry_count": 2},
        )
        with self.assertRaisesRegex(SystemdCorrelationContractError, "entry_count"):
            SystemdTemporalCorrelator().correlate(_service_event(), malformed)

    def test_wrong_observation_type_is_rejected(self) -> None:
        service = _service_event()
        malformed = SentinelEvent(
            kind=service.kind,
            source=service.source,
            message=service.message,
            occurred_at=service.occurred_at,
            attributes={**service.attributes, "observation_type": "wrong"},
        )
        with self.assertRaisesRegex(
            SystemdCorrelationContractError,
            "observation_type",
        ):
            SystemdTemporalCorrelator().correlate(malformed, _journal_event(_entry()))

    def test_non_text_message_does_not_invent_message_text(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(),
            _journal_event(_entry(message={"kind": "binary", "length": 4})),
        )
        self.assertIsNone(report.matches[0].journal_message)

    def test_report_serialization_preserves_basis_counts(self) -> None:
        report = SystemdTemporalCorrelator().correlate(
            _service_event(),
            _journal_event(_entry()),
        )
        payload = report.to_dict()
        self.assertEqual(payload["match_count"], 1)
        self.assertEqual(payload["exact_invocation_count"], 1)
        self.assertEqual(payload["unit_and_time_count"], 0)
        self.assertEqual(payload["matches"][0]["basis"], "exact_invocation")

    def test_window_validation_is_bounded_and_rejects_boolean(self) -> None:
        with self.assertRaises(TypeError):
            SystemdTemporalCorrelator(max_state_delta_usec=True)
        with self.assertRaises(ValueError):
            SystemdTemporalCorrelator(max_state_delta_usec=-1)
        with self.assertRaises(ValueError):
            SystemdTemporalCorrelator(max_state_delta_usec=300_000_001)


if __name__ == "__main__":
    unittest.main()
