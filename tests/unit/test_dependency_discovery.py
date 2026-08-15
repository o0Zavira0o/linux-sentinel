"""Tests for Phase 5B bounded Linux-native dependency discovery."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.dependency.discovery import (
    SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
    SYSTEMD_DEPENDENCY_OBSERVATION_TYPE,
    SystemctlDependencyUnitReader,
    SystemdDependencyCommandError,
    SystemdDependencyDiscovery,
    SystemdDependencyDiscoveryCapacityError,
    SystemdDependencyProtocolError,
    SystemdDependencyUnitSnapshot,
    validate_systemd_unit_identity,
)
from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.systemd.reader import SystemctlCommandResult

_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
_BOOT = "a" * 32


def _stdout(
    unit: str,
    *,
    names: str | None = None,
    load_state: str = "loaded",
    requires: str = "",
    wants: str = "",
    after: str = "",
    before: str = "",
) -> bytes:
    names_value = unit if names is None else names
    return (
        f"Id={unit}\n"
        f"Names={names_value}\n"
        f"LoadState={load_state}\n"
        f"Requires={requires}\n"
        f"Wants={wants}\n"
        f"After={after}\n"
        f"Before={before}\n"
    ).encode()


def _snapshot(
    unit: str,
    *,
    requested: str | None = None,
    requires: tuple[str, ...] = (),
    wants: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
) -> SystemdDependencyUnitSnapshot:
    return SystemdDependencyUnitSnapshot(
        requested_name=unit if requested is None else requested,
        canonical_name=unit,
        names=(unit,),
        load_state="loaded",
        requires=requires,
        wants=wants,
        after=after,
        before=before,
        captured_at=_NOW,
    )


class _CaptureRunner:
    def __init__(self, result: SystemctlCommandResult) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> SystemctlCommandResult:
        self.calls.append((tuple(argv), timeout_seconds))
        return self.result


class _FakeReader:
    def __init__(
        self,
        snapshots: dict[str, SystemdDependencyUnitSnapshot],
        *,
        failures: set[str] | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.failures = set() if failures is None else failures
        self.calls: list[str] = []

    def read_unit(self, unit_name: str) -> SystemdDependencyUnitSnapshot:
        self.calls.append(unit_name)
        if unit_name in self.failures:
            raise SystemdDependencyCommandError(f"cannot read {unit_name}")
        return self.snapshots[unit_name]


class _EventIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self) -> str:
        self._next += 1
        return f"dependency-event-{self._next}"


class SystemdDependencyDiscoveryTests(unittest.TestCase):
    def test_generic_unit_identity_accepts_root_mount_and_nonservice_units(
        self,
    ) -> None:
        self.assertEqual(
            validate_systemd_unit_identity("-.mount", field_name="unit"),
            "-.mount",
        )
        self.assertEqual(
            validate_systemd_unit_identity("network.target", field_name="unit"),
            "network.target",
        )
        self.assertEqual(
            validate_systemd_unit_identity("dbus.socket", field_name="unit"),
            "dbus.socket",
        )

    def test_generic_unit_identity_rejects_unsafe_or_untyped_names(self) -> None:
        for value in (
            "",
            " network.target",
            "network.target ",
            "a/b.service",
            '"quoted.service"',
            "'quoted.service'",
            "nosuffix",
        ):
            with self.subTest(value=value):
                with self.assertRaises(SystemdDependencyProtocolError):
                    validate_systemd_unit_identity(value, field_name="unit")

    def test_snapshot_requires_canonical_name_in_names(self) -> None:
        with self.assertRaisesRegex(SystemdDependencyProtocolError, "canonical_name"):
            replace(_snapshot("demo.service"), names=("alias.service",))

    def test_snapshot_rejects_duplicate_dependency_values(self) -> None:
        with self.assertRaisesRegex(SystemdDependencyProtocolError, "duplicates"):
            replace(
                _snapshot("demo.service"),
                requires=("dbus.socket", "dbus.socket"),
            )

    def test_snapshot_preserves_all_four_phase5a_relation_properties(self) -> None:
        snapshot = _snapshot(
            "demo.service",
            requires=("a.socket",),
            wants=("b.target",),
            after=("c.mount",),
            before=("d.service",),
        )
        payload = snapshot.to_dict()
        self.assertEqual(payload["requires"], ["a.socket"])
        self.assertEqual(payload["wants"], ["b.target"])
        self.assertEqual(payload["after"], ["c.mount"])
        self.assertEqual(payload["before"], ["d.service"])

    def test_reader_uses_explicit_system_read_only_show_and_option_terminator(
        self,
    ) -> None:
        runner = _CaptureRunner(
            SystemctlCommandResult(
                returncode=0,
                stdout=_stdout("-.mount"),
                stderr=b"",
            )
        )
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=runner,
            clock=lambda: _NOW,
        )
        snapshot = reader.read_unit("-.mount")
        self.assertEqual(snapshot.canonical_name, "-.mount")
        argv, timeout_seconds = runner.calls[0]
        self.assertEqual(argv[0], "/usr/bin/systemctl")
        self.assertIn("--system", argv)
        self.assertIn("--no-ask-password", argv)
        self.assertIn("show", argv)
        self.assertEqual(argv[-2:], ("--", "-.mount"))
        self.assertGreater(timeout_seconds, 0.0)

    def test_reader_parses_generic_unit_dependencies_and_alias(self) -> None:
        runner = _CaptureRunner(
            SystemctlCommandResult(
                returncode=0,
                stdout=_stdout(
                    "real.service",
                    names="real.service alias.service",
                    requires="dbus.socket -.mount",
                    wants="network-online.target",
                    after="dbus.socket",
                    before="shutdown.target",
                ),
                stderr=b"",
            )
        )
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=runner,
            clock=lambda: _NOW,
        )
        snapshot = reader.read_unit("alias.service")
        self.assertEqual(snapshot.requested_name, "alias.service")
        self.assertEqual(snapshot.canonical_name, "real.service")
        self.assertEqual(snapshot.names, ("real.service", "alias.service"))
        self.assertEqual(snapshot.requires, ("dbus.socket", "-.mount"))

    def test_reader_decodes_systemctl_shell_quoted_unit_names(self) -> None:
        runner = _CaptureRunner(
            SystemctlCommandResult(
                returncode=0,
                stdout=_stdout(
                    "demo.service",
                    after=(
                        r'"blockdev@dev-disk-by\\x2duuid-A.target" '
                        "network.target"
                    ),
                ),
                stderr=b"",
            )
        )
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=runner,
            clock=lambda: _NOW,
        )
        snapshot = reader.read_unit("demo.service")
        self.assertEqual(
            snapshot.after,
            (
                r"blockdev@dev-disk-by\x2duuid-A.target",
                "network.target",
            ),
        )

    def test_reader_rejects_malformed_shell_quoted_unit_names(self) -> None:
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=_CaptureRunner(
                SystemctlCommandResult(
                    returncode=0,
                    stdout=_stdout(
                        "demo.service",
                        after='"unterminated.target',
                    ),
                    stderr=b"",
                )
            ),
            clock=lambda: _NOW,
        )
        with self.assertRaisesRegex(
            SystemdDependencyProtocolError,
            "shell-quoted",
        ):
            reader.read_unit("demo.service")

    def test_reader_rejects_missing_property(self) -> None:
        output = _stdout("demo.service").replace(b"Before=\n", b"")
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=_CaptureRunner(
                SystemctlCommandResult(returncode=0, stdout=output, stderr=b"")
            ),
            clock=lambda: _NOW,
        )
        with self.assertRaisesRegex(SystemdDependencyProtocolError, "missing"):
            reader.read_unit("demo.service")

    def test_reader_rejects_duplicate_property(self) -> None:
        output = _stdout("demo.service") + b"Requires=other.service\n"
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=_CaptureRunner(
                SystemctlCommandResult(returncode=0, stdout=output, stderr=b"")
            ),
            clock=lambda: _NOW,
        )
        with self.assertRaisesRegex(SystemdDependencyProtocolError, "duplicate"):
            reader.read_unit("demo.service")

    def test_reader_rejects_invalid_utf8(self) -> None:
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=_CaptureRunner(
                SystemctlCommandResult(returncode=0, stdout=b"\xff", stderr=b"")
            ),
            clock=lambda: _NOW,
        )
        with self.assertRaisesRegex(SystemdDependencyProtocolError, "UTF-8"):
            reader.read_unit("demo.service")

    def test_reader_rejects_nonzero_systemctl_status_with_bounded_error(self) -> None:
        reader = SystemctlDependencyUnitReader(
            systemctl_path="/usr/bin/systemctl",
            runner=_CaptureRunner(
                SystemctlCommandResult(
                    returncode=1,
                    stdout=b"",
                    stderr=b"unit unavailable",
                )
            ),
            clock=lambda: _NOW,
        )
        with self.assertRaisesRegex(SystemdDependencyCommandError, "unit unavailable"):
            reader.read_unit("demo.service")

    def test_reader_timeout_bounds_are_strict(self) -> None:
        with self.assertRaises(ValueError):
            SystemctlDependencyUnitReader(
                systemctl_path="/usr/bin/systemctl",
                timeout_seconds=0.01,
            )
        with self.assertRaises(TypeError):
            SystemctlDependencyUnitReader(
                systemctl_path="/usr/bin/systemctl",
                timeout_seconds=True,
            )

    def test_discovery_traverses_requires_and_wants_but_not_ordering(self) -> None:
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("required.socket",),
                    wants=("wanted.target",),
                    after=("ordering-only.service",),
                    before=("before-only.service",),
                ),
                "required.socket": _snapshot("required.socket"),
                "wanted.target": _snapshot("wanted.target"),
            }
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        self.assertEqual(
            reader.calls,
            ["root.service", "required.socket", "wanted.target"],
        )
        self.assertNotIn("ordering-only.service", reader.calls)
        self.assertNotIn("before-only.service", reader.calls)
        relations = {item.relation for item in report.units[0].evidence}
        self.assertEqual(
            relations,
            {
                DependencyRelation.REQUIRES,
                DependencyRelation.WANTS,
                DependencyRelation.AFTER,
                DependencyRelation.BEFORE,
            },
        )

    def test_discovery_evidence_is_noncausal_and_manager_provenanced(self) -> None:
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("required.socket",),
                ),
                "required.socket": _snapshot("required.socket"),
            }
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        evidence = report.units[0].evidence[0]
        self.assertIs(evidence.causal_claim, False)
        self.assertEqual(evidence.origin.value, "systemd.manager.property")
        self.assertEqual(
            evidence.configuration_origin.value,
            "manager_merged_unresolved",
        )
        self.assertEqual(evidence.source_event_id, "dependency-event-1")

    def test_discovery_event_contract_is_typed_and_explicitly_noncausal(self) -> None:
        reader = _FakeReader({"root.service": _snapshot("root.service")})
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=0,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        event = report.units[0].source_event
        self.assertEqual(event.source, SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE)
        self.assertEqual(
            event.attributes["observation_type"],
            SYSTEMD_DEPENDENCY_OBSERVATION_TYPE,
        )
        self.assertIs(event.attributes["causal_claims_assigned"], False)

    def test_discovery_deduplicates_same_target_across_requirement_relations(
        self,
    ) -> None:
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("shared.socket",),
                    wants=("shared.socket",),
                ),
                "shared.socket": _snapshot("shared.socket"),
            }
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        self.assertEqual(reader.calls.count("shared.socket"), 1)
        shared_edges = [
            item
            for item in report.units[0].evidence
            if item.object.identity == "shared.socket"
        ]
        self.assertEqual(len(shared_edges), 2)

    def test_discovery_cycle_is_bounded_without_repeated_reads(self) -> None:
        reader = _FakeReader(
            {
                "a.service": _snapshot("a.service", requires=("b.service",)),
                "b.service": _snapshot("b.service", requires=("a.service",)),
            }
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=8,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("a.service")
        self.assertEqual(reader.calls, ["a.service", "b.service"])
        self.assertEqual(len(report.units), 2)
        self.assertTrue(report.complete_within_scope)

    def test_discovery_canonical_alias_is_not_recorded_twice(self) -> None:
        alias_snapshot = replace(
            _snapshot("real.service", requested="alias.service"),
            names=("real.service", "alias.service"),
        )
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("alias.service", "real.service"),
                ),
                "alias.service": alias_snapshot,
                "real.service": _snapshot("real.service"),
            }
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        self.assertEqual(
            [unit.snapshot.canonical_name for unit in report.units],
            ["root.service", "real.service"],
        )

    def test_child_read_failure_is_explicit_instead_of_silent(self) -> None:
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("missing.service",),
                ),
            },
            failures={"missing.service"},
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        self.assertFalse(report.complete_within_scope)
        self.assertEqual(len(report.failures), 1)
        self.assertEqual(report.failures[0].requested_unit, "missing.service")
        self.assertEqual(report.failures[0].depth, 1)

    def test_root_read_failure_is_not_downgraded_to_partial_report(self) -> None:
        reader = _FakeReader({}, failures={"root.service"})
        discovery = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            boot_id_reader=lambda: _BOOT,
        )
        with self.assertRaises(SystemdDependencyCommandError):
            discovery.discover("root.service")

    def test_depth_limit_is_explicit_and_counts_unexpanded_requirements(self) -> None:
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("child.service",),
                )
            }
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=0,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        self.assertTrue(report.truncated_by_depth)
        self.assertEqual(report.unexpanded_requirement_count, 1)
        self.assertFalse(report.complete_within_scope)
        self.assertEqual(reader.calls, ["root.service"])

    def test_capacity_limit_fails_explicitly_without_silent_eviction(self) -> None:
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("a.service", "b.service"),
                ),
                "a.service": _snapshot("a.service"),
                "b.service": _snapshot("b.service"),
            }
        )
        discovery = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            max_units=2,
            boot_id_reader=lambda: _BOOT,
        )
        with self.assertRaisesRegex(
            SystemdDependencyDiscoveryCapacityError,
            "silently evicted",
        ):
            discovery.discover("root.service")

    def test_discovery_bounds_reject_boolean_and_out_of_range_values(self) -> None:
        reader = _FakeReader({"root.service": _snapshot("root.service")})
        for kwargs in (
            {"max_depth": True},
            {"max_depth": 9},
            {"max_units": 0},
            {"max_units": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    SystemdDependencyDiscovery(reader=reader, **kwargs)

    def test_report_serialization_disclaims_comprehensive_scope_and_causality(
        self,
    ) -> None:
        reader = _FakeReader({"root.service": _snapshot("root.service")})
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=0,
            boot_id_reader=lambda: _BOOT,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        payload = report.to_dict()
        self.assertEqual(
            payload["observed_relations"],
            ["requires", "wants", "after", "before"],
        )
        self.assertEqual(payload["traversal_relations"], ["requires", "wants"])
        self.assertIs(payload["comprehensive_systemd_relation_scope"], False)
        self.assertIs(payload["causal_claims_assigned"], False)
        self.assertTrue(payload["complete_within_scope"])

    def test_boot_identity_is_normalized_once_for_the_report_and_evidence(self) -> None:
        hyphenated = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        reader = _FakeReader(
            {
                "root.service": _snapshot(
                    "root.service",
                    requires=("child.service",),
                ),
                "child.service": _snapshot("child.service"),
            }
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=1,
            boot_id_reader=lambda: hyphenated,
            event_id_factory=_EventIds(),
        ).discover("root.service")
        self.assertEqual(report.boot_id, _BOOT)
        self.assertTrue(
            all(
                item.boot_id == _BOOT for unit in report.units for item in unit.evidence
            )
        )


if __name__ == "__main__":
    unittest.main()
