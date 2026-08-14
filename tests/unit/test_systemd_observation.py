"""Tests for configured systemd service observation collectors."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from sentinel_x.config import SystemdServiceTargetConfig
from sentinel_x.core import CollectorRegistry, CollectorRuntime, EventBus
from sentinel_x.core.events import EventKind
from sentinel_x.systemd import (
    SYSTEMD_SERVICE_OBSERVATION_SOURCE,
    SYSTEMD_SERVICE_OBSERVATION_TYPE,
    ConfiguredSystemdServiceCollectors,
    SystemdServiceCollector,
    SystemdServiceCollectorBindingError,
    SystemdServiceSnapshot,
    systemd_service_snapshot_to_event,
)


_BOOT_ID = "a" * 32


class FakeReader:
    """Deterministic service snapshot reader used by collector tests."""

    def __init__(self, snapshot: SystemdServiceSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    def read_service(self, unit_name: str) -> SystemdServiceSnapshot:
        self.calls.append(unit_name)
        return self.snapshot


class FailingReader:
    """Reader that exposes transport failures to the execution layer."""

    def read_service(self, unit_name: str) -> SystemdServiceSnapshot:
        raise RuntimeError(f"read failed for {unit_name}")


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


class SystemdServiceObservationTests(unittest.TestCase):
    """Validate event conversion and trusted service handler binding."""

    def test_snapshot_converts_to_typed_observation_event(self) -> None:
        event = systemd_service_snapshot_to_event(
            _snapshot(),
            collector_name="systemd.sshd.0123456789ab",
            boot_id=_BOOT_ID,
        )

        self.assertIs(event.kind, EventKind.OBSERVATION)
        self.assertEqual(event.source, SYSTEMD_SERVICE_OBSERVATION_SOURCE)
        self.assertEqual(
            event.attributes["observation_type"],
            SYSTEMD_SERVICE_OBSERVATION_TYPE,
        )
        self.assertEqual(event.attributes["requested_name"], "sshd.service")
        self.assertEqual(event.attributes["active_state"], "active")
        self.assertEqual(event.attributes["boot_id"], _BOOT_ID)
        self.assertEqual(event.occurred_at, _snapshot().captured_at)

    def test_failed_service_state_is_preserved_as_observation(self) -> None:
        snapshot = replace(
            _snapshot(),
            active_state="failed",
            sub_state="failed",
            result="exit-code",
        )
        event = systemd_service_snapshot_to_event(
            snapshot,
            collector_name="systemd.sshd.0123456789ab",
        )

        self.assertEqual(event.attributes["active_state"], "failed")
        self.assertEqual(event.attributes["sub_state"], "failed")
        self.assertEqual(event.attributes["result"], "exit-code")

    def test_not_found_service_state_is_preserved_as_observation(self) -> None:
        snapshot = replace(
            _snapshot(),
            load_state="not-found",
            active_state="inactive",
            sub_state="dead",
            main_pid=None,
        )
        event = systemd_service_snapshot_to_event(
            snapshot,
            collector_name="systemd.missing.0123456789ab",
        )

        self.assertEqual(event.attributes["load_state"], "not-found")
        self.assertEqual(event.attributes["active_state"], "inactive")
        self.assertEqual(event.attributes["sub_state"], "dead")

    def test_collector_reads_exact_requested_unit_and_emits(self) -> None:
        reader = FakeReader(_snapshot())
        collector = SystemdServiceCollector(
            "systemd.sshd.0123456789ab",
            "sshd.service",
            reader=reader,
        )

        emission = collector.collect()

        self.assertEqual(reader.calls, ["sshd.service"])
        self.assertEqual(emission.collector_name, collector.name)
        self.assertEqual(
            emission.event.attributes["collector_name"],
            collector.name,
        )

    def test_emission_serialization_omits_event_attributes(self) -> None:
        reader = FakeReader(_snapshot())
        emission = SystemdServiceCollector(
            "systemd.sshd.0123456789ab",
            "sshd.service",
            reader=reader,
        ).collect()

        payload = emission.to_dict()

        self.assertNotIn("attributes", payload)
        self.assertEqual(payload["event_id"], emission.event.event_id)
        self.assertEqual(payload["event_kind"], "observation")

    def test_configured_set_exposes_exact_trusted_handler_names(self) -> None:
        reader = FakeReader(_snapshot())
        configured = ConfiguredSystemdServiceCollectors(
            (
                ("systemd.sshd.0123456789ab", "sshd.service"),
                (
                    "systemd.journald.abcdef012345",
                    "systemd-journald.service",
                ),
            ),
            reader=reader,
        )

        self.assertEqual(
            tuple(configured.handlers()),
            (
                "systemd.sshd.0123456789ab",
                "systemd.journald.abcdef012345",
            ),
        )

    def test_configured_set_preserves_declared_order(self) -> None:
        configured = ConfiguredSystemdServiceCollectors(
            (
                ("systemd.a.0123456789ab", "a.service"),
                ("systemd.b.abcdef012345", "b.service"),
            ),
            reader=FakeReader(_snapshot()),
        )

        self.assertEqual(
            tuple(collector.unit_name for collector in configured.collectors()),
            ("a.service", "b.service"),
        )

    def test_empty_configured_set_is_valid_without_reader_construction(self) -> None:
        with patch(
            "sentinel_x.systemd.observation.SystemctlServiceReader",
            side_effect=AssertionError("reader must not be constructed"),
        ):
            configured = ConfiguredSystemdServiceCollectors(())

        self.assertEqual(configured.collectors(), ())
        self.assertEqual(dict(configured.handlers()), {})

    def test_duplicate_collector_binding_is_rejected(self) -> None:
        with self.assertRaises(SystemdServiceCollectorBindingError):
            ConfiguredSystemdServiceCollectors(
                (
                    ("systemd.same.0123456789ab", "a.service"),
                    ("systemd.same.0123456789ab", "b.service"),
                ),
                reader=FakeReader(_snapshot()),
            )

    def test_duplicate_unit_binding_is_rejected(self) -> None:
        with self.assertRaises(SystemdServiceCollectorBindingError):
            ConfiguredSystemdServiceCollectors(
                (
                    ("systemd.a.0123456789ab", "a.service"),
                    ("systemd.b.abcdef012345", "a.service"),
                ),
                reader=FakeReader(_snapshot()),
            )

    def test_invalid_collector_identity_is_rejected(self) -> None:
        with self.assertRaises(SystemdServiceCollectorBindingError):
            SystemdServiceCollector(
                "systemd.bad@name",
                "sshd.service",
                reader=FakeReader(_snapshot()),
            )

    def test_reader_failure_propagates_for_executor_isolation(self) -> None:
        collector = SystemdServiceCollector(
            "systemd.sshd.0123456789ab",
            "sshd.service",
            reader=FailingReader(),
        )

        with self.assertRaisesRegex(RuntimeError, "read failed"):
            collector.collect()

    def test_alias_identity_is_preserved_in_event_payload(self) -> None:
        snapshot = replace(
            _snapshot(),
            requested_name="ssh.service",
            canonical_name="sshd.service",
            names=("ssh.service", "sshd.service"),
        )
        event = systemd_service_snapshot_to_event(
            snapshot,
            collector_name="systemd.ssh.0123456789ab",
        )

        self.assertEqual(event.attributes["requested_name"], "ssh.service")
        self.assertEqual(event.attributes["canonical_name"], "sshd.service")
        self.assertEqual(
            event.attributes["names"],
            ["ssh.service", "sshd.service"],
        )

    def test_systemd_target_settings_bind_to_core_registry(self) -> None:
        target = SystemdServiceTargetConfig(
            unit_name="sshd.service",
            initial_delay_seconds=0.0,
        )
        configured = ConfiguredSystemdServiceCollectors(
            ((target.collector_name, target.unit_name),),
            reader=FakeReader(_snapshot()),
        )

        registry = CollectorRegistry.from_settings(
            ((target.collector_name, target),),
            configured.handlers(),
        )

        self.assertEqual(registry.collector_count, 1)
        self.assertEqual(registry.enabled_count, 1)
        self.assertEqual(
            registry.definition(target.collector_name).name,
            target.collector_name,
        )

    def test_disabled_systemd_target_is_not_scheduled(self) -> None:
        target = SystemdServiceTargetConfig(
            unit_name="sshd.service",
            enabled=False,
            initial_delay_seconds=0.0,
        )
        configured = ConfiguredSystemdServiceCollectors(
            ((target.collector_name, target.unit_name),),
            reader=FakeReader(_snapshot()),
        )
        registry = CollectorRegistry.from_settings(
            ((target.collector_name, target),),
            configured.handlers(),
        )

        scheduler = registry.create_scheduler(now_ns=1_000_000_000)

        self.assertEqual(registry.disabled_count, 1)
        self.assertEqual(scheduler.snapshots(), ())

    def test_core_runtime_publishes_systemd_service_observation(self) -> None:
        target = SystemdServiceTargetConfig(
            unit_name="sshd.service",
            initial_delay_seconds=0.0,
        )
        configured = ConfiguredSystemdServiceCollectors(
            ((target.collector_name, target.unit_name),),
            reader=FakeReader(_snapshot()),
            boot_id_reader=lambda: _BOOT_ID,
        )
        registry = CollectorRegistry.from_settings(
            ((target.collector_name, target),),
            configured.handlers(),
        )
        event_bus = EventBus()
        events = []
        event_bus.subscribe(events.append)
        runtime = CollectorRuntime(
            event_bus,
            registry,
            clock_ns=lambda: 1_000_000_000,
        )

        cycle = runtime.run_due()

        self.assertEqual(cycle.claimed_count, 1)
        self.assertEqual(cycle.emitted_count, 1)
        self.assertEqual(cycle.failed_count, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].attributes["observation_type"],
            SYSTEMD_SERVICE_OBSERVATION_TYPE,
        )
        self.assertEqual(events[0].attributes["boot_id"], _BOOT_ID)

    def test_collector_rejects_invalid_boot_identity_at_collection(self) -> None:
        collector = SystemdServiceCollector(
            "systemd.sshd.0123456789ab",
            "sshd.service",
            reader=FakeReader(_snapshot()),
            boot_id_reader=lambda: "invalid",
        )
        with self.assertRaisesRegex(RuntimeError, "boot ID"):
            collector.collect()

    def test_collector_normalizes_hyphenated_boot_identity(self) -> None:
        collector = SystemdServiceCollector(
            "systemd.sshd.0123456789ab",
            "sshd.service",
            reader=FakeReader(_snapshot()),
            boot_id_reader=lambda: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        emission = collector.collect()
        self.assertEqual(emission.event.attributes["boot_id"], _BOOT_ID)


if __name__ == "__main__":
    unittest.main()
