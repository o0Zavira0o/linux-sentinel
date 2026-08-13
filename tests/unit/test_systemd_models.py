"""Tests for typed read-only systemd service state models."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.systemd import (
    SystemdActiveState,
    SystemdModelError,
    SystemdServiceSnapshot,
    SystemdUnitNameError,
    validate_service_unit_name,
)


def _snapshot() -> SystemdServiceSnapshot:
    return SystemdServiceSnapshot(
        requested_name="sshd.service",
        canonical_name="sshd.service",
        names=("sshd.service",),
        description="OpenSSH server daemon",
        load_state="loaded",
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        service_type="notify",
        restart_policy="on-failure",
        result="success",
        invocation_id="0123456789abcdef",
        control_group="/system.slice/sshd.service",
        fragment_path="/usr/lib/systemd/system/sshd.service",
        source_path=None,
        drop_in_paths=(),
        requires=("sysinit.target",),
        wants=(),
        after=("network.target",),
        before=("shutdown.target",),
        can_start=True,
        can_stop=True,
        can_reload=True,
        main_pid=123,
        exec_main_code=1,
        exec_main_status=0,
        restart_count=2,
        state_change_monotonic_usec=1_000,
        active_enter_monotonic_usec=900,
        inactive_enter_monotonic_usec=None,
        exec_main_start_monotonic_usec=850,
        exec_main_exit_monotonic_usec=None,
        captured_at=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
    )


class SystemdServiceModelTests(unittest.TestCase):
    """Validate forward-compatible service snapshot invariants."""

    def test_service_name_validation_accepts_safe_service_names(self) -> None:
        for name in (
            "sshd.service",
            "systemd-journald.service",
            "getty@tty1.service",
            "dbus-:1.2-org.example.service",
        ):
            self.assertEqual(
                validate_service_unit_name(name, field_name="unit"),
                name,
            )

    def test_service_name_validation_rejects_unsafe_or_nonservice_names(self) -> None:
        for name in (
            "",
            " sshd.service",
            "sshd.service ",
            "sshd",
            "multi-user.target",
            "foo/bar.service",
            "foo service.service",
            "--system.service",
        ):
            with self.subTest(name=name):
                with self.assertRaises(SystemdUnitNameError):
                    validate_service_unit_name(name, field_name="unit")

    def test_snapshot_exposes_known_active_state_helpers(self) -> None:
        snapshot = _snapshot()
        self.assertIs(snapshot.known_active_state, SystemdActiveState.ACTIVE)
        self.assertTrue(snapshot.is_active)
        self.assertFalse(snapshot.is_failed)

    def test_unknown_future_active_state_is_preserved(self) -> None:
        snapshot = replace(_snapshot(), active_state="future-state")
        self.assertIsNone(snapshot.known_active_state)
        self.assertEqual(snapshot.active_state, "future-state")

    def test_failed_state_helper_is_explicit(self) -> None:
        snapshot = replace(_snapshot(), active_state="failed", sub_state="failed")
        self.assertTrue(snapshot.is_failed)
        self.assertFalse(snapshot.is_active)

    def test_names_must_include_canonical_name(self) -> None:
        with self.assertRaises(SystemdModelError):
            replace(
                _snapshot(),
                names=("alias.service",),
            )

    def test_negative_restart_count_is_rejected(self) -> None:
        with self.assertRaises(SystemdModelError):
            replace(_snapshot(), restart_count=-1)

    def test_naive_capture_time_is_rejected(self) -> None:
        with self.assertRaises(SystemdModelError):
            replace(
                _snapshot(),
                captured_at=datetime(2026, 8, 13, 9, 0),
            )

    def test_serialization_is_json_friendly_and_preserves_raw_state(self) -> None:
        payload = _snapshot().to_dict()
        self.assertEqual(payload["active_state"], "active")
        self.assertEqual(payload["known_active_state"], "active")
        self.assertEqual(payload["names"], ["sshd.service"])
        self.assertEqual(payload["restart_count"], 2)
        self.assertEqual(
            payload["captured_at"],
            "2026-08-13T09:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
