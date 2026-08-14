"""Unit tests for the constrained Phase 3C systemd fault injector."""

from __future__ import annotations

import stat
import subprocess
import unittest
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from sentinel_x.lab import (
    FaultExperimentManifest,
    FaultMode,
    FaultScenario,
    SystemdLabFixtureSpec,
    build_systemd_lab_fixture,
)
from sentinel_x.lab.injector import (
    FaultLabCommandError,
    FaultLabPreconditionError,
    FaultLabProtocolError,
    FaultLabRecoveryError,
    LabFaultOperation,
    SystemctlMutationResult,
    SystemdLabFaultInjector,
    SystemdLabFaultPlan,
    SystemdLabServiceState,
    verify_installed_lab_fixture,
)


class _TickingClock:
    def __init__(self, start: float = 10.0) -> None:
        self.value = start

    def __call__(self) -> float:
        current = self.value
        self.value += 0.1
        return current


class _WallClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=100)
        return current


class _FakeSystemctlRunner:
    def __init__(self) -> None:
        self.state = "active"
        self.calls: list[tuple[str, ...]] = []
        self.fail_on: str | None = None
        self.show_override: bytes | None = None

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> SystemctlMutationResult:
        del timeout_seconds
        call = tuple(argv)
        self.calls.append(call)
        operation = self._operation(call)
        if self.fail_on == operation:
            return SystemctlMutationResult(1, b"", b"synthetic failure")

        if operation == "show":
            if self.show_override is not None:
                return SystemctlMutationResult(0, self.show_override, b"")
            return SystemctlMutationResult(0, self._show_payload(call), b"")
        if operation == "stop":
            self.state = "inactive"
        elif operation == "kill":
            self.state = "failed"
        elif operation == "reset-failed":
            if self.state == "inactive":
                return SystemctlMutationResult(
                    1,
                    b"",
                    b"Unit not loaded",
                )
        elif operation == "start":
            self.state = "active"
        return SystemctlMutationResult(0, b"", b"")

    @staticmethod
    def _operation(call: tuple[str, ...]) -> str:
        for candidate in ("show", "stop", "kill", "reset-failed", "start"):
            if candidate in call:
                return candidate
        raise AssertionError(f"unknown command: {call!r}")

    def _show_payload(self, call: tuple[str, ...]) -> bytes:
        unit = call[-1]
        if self.state == "active":
            active_state = "active"
            sub_state = "running"
            main_pid = "4242"
            result = "success"
        elif self.state == "inactive":
            active_state = "inactive"
            sub_state = "dead"
            main_pid = "0"
            result = "success"
        else:
            active_state = "failed"
            sub_state = "failed"
            main_pid = "0"
            result = "signal"
        return (
            f"Id={unit}\n"
            "LoadState=loaded\n"
            f"ActiveState={active_state}\n"
            f"SubState={sub_state}\n"
            f"MainPID={main_pid}\n"
            f"Result={result}\n"
        ).encode()


class _Verifier:
    def __init__(self) -> None:
        self.calls = 0
        self.error: BaseException | None = None

    def __call__(self, artifact: object) -> None:
        del artifact
        self.calls += 1
        if self.error is not None:
            raise self.error


class FaultLabInjectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.artifact = build_systemd_lab_fixture(
            SystemdLabFixtureSpec("injector-01", runtime_max_seconds=120)
        )
        self.scenario = FaultScenario(
            scenario_id="inactive-01",
            description="Controlled inactive fault for the dedicated lab fixture.",
            target_unit=self.artifact.unit_name,
            fault_mode=FaultMode.SERVICE_INACTIVE,
            evidence_grace_seconds=0.1,
        )
        self.manifest = FaultExperimentManifest(
            self.scenario,
            experiment_id="exp-0123456789abcdef0123456789abcdef",
            created_at=datetime(2026, 8, 14, 9, 59, tzinfo=timezone.utc),
        )
        self.runner = _FakeSystemctlRunner()
        self.verifier = _Verifier()
        self.monotonic = _TickingClock()
        self.wall_clock = _WallClock()
        self.sleeps: list[float] = []
        self.injector = SystemdLabFaultInjector(
            runner=self.runner,
            install_verifier=self.verifier,
            wall_clock=self.wall_clock,
            monotonic_clock=self.monotonic,
            sleeper=self.sleeps.append,
            poll_interval_seconds=0.05,
        )

    def _failed_manifest(self) -> FaultExperimentManifest:
        scenario = FaultScenario(
            scenario_id="failed-01",
            description="Controlled failed-state fault for the dedicated lab fixture.",
            target_unit=self.artifact.unit_name,
            fault_mode=FaultMode.SERVICE_FAILED,
            evidence_grace_seconds=0.1,
        )
        return FaultExperimentManifest(
            scenario,
            experiment_id="exp-fedcba9876543210fedcba9876543210",
            created_at=datetime(2026, 8, 14, 9, 59, tzinfo=timezone.utc),
        )

    def test_mutation_result_rejects_invalid_primitives(self) -> None:
        with self.assertRaises(TypeError):
            SystemctlMutationResult(cast(int, True), b"", b"")
        with self.assertRaises(TypeError):
            SystemctlMutationResult(0, cast(bytes, ""), b"")
        with self.assertRaises(TypeError):
            SystemctlMutationResult(0, b"", cast(bytes, ""))

    def test_service_state_requires_lab_identity_and_valid_pid(self) -> None:
        with self.assertRaises(ValueError):
            SystemdLabServiceState(
                "sshd.service", "loaded", "active", "running", 1, "success"
            )
        with self.assertRaises(FaultLabProtocolError):
            SystemdLabServiceState(
                self.artifact.unit_name,
                "loaded",
                "active",
                "running",
                -1,
                "success",
            )

    def test_healthy_state_requires_loaded_running_and_live_pid(self) -> None:
        state = SystemdLabServiceState(
            self.artifact.unit_name,
            "loaded",
            "active",
            "running",
            9,
            "success",
        )
        self.assertTrue(state.is_healthy)
        self.assertTrue(state.to_dict()["is_healthy"])

    def test_plan_is_recovery_first_and_serialization_friendly(self) -> None:
        plan = self.injector.prepare(self.manifest, self.artifact)
        self.assertEqual(plan.operation, LabFaultOperation.STOP_SERVICE)
        self.assertEqual(plan.recovery_operations, ("start", "reset-failed"))
        self.assertEqual(plan.expected_fault_active_states, ("inactive",))
        self.assertEqual(plan.to_dict()["fault_mode"], "service_inactive")

    def test_failed_mode_prepares_abort_main_process_only(self) -> None:
        plan = self.injector.prepare(self._failed_manifest(), self.artifact)
        self.assertEqual(plan.operation, LabFaultOperation.ABORT_MAIN_PROCESS)
        self.assertEqual(plan.expected_fault_active_states, ("failed",))

    def test_prepare_rejects_non_lab_or_mismatched_target(self) -> None:
        scenario = FaultScenario(
            scenario_id="mismatch-01",
            description="Mismatch is rejected before any mutation can occur.",
            target_unit="sentinel-x-lab-other-01.service",
            fault_mode=FaultMode.SERVICE_INACTIVE,
        )
        manifest = FaultExperimentManifest(scenario)
        with self.assertRaises(FaultLabPreconditionError):
            self.injector.prepare(manifest, self.artifact)
        self.assertEqual(self.runner.calls, [])

    def test_prepare_verifies_installed_artifact_before_state_read(self) -> None:
        self.injector.prepare(self.manifest, self.artifact)
        self.assertEqual(self.verifier.calls, 1)
        self.assertIn("show", self.runner.calls[0])

    def test_prepare_rejects_verifier_failure_before_systemctl(self) -> None:
        self.verifier.error = FaultLabPreconditionError("bad installed bytes")
        with self.assertRaises(FaultLabPreconditionError):
            self.injector.prepare(self.manifest, self.artifact)
        self.assertEqual(self.runner.calls, [])

    def test_prepare_requires_healthy_baseline(self) -> None:
        self.runner.state = "inactive"
        with self.assertRaises(FaultLabPreconditionError):
            self.injector.prepare(self.manifest, self.artifact)

    def test_inactive_execution_uses_exact_stop_and_recovery_commands(self) -> None:
        outcome = self.injector.execute(self.manifest, self.artifact)
        operations = [
            _FakeSystemctlRunner._operation(call) for call in self.runner.calls
        ]
        self.assertIn("stop", operations)
        self.assertIn("reset-failed", operations)
        self.assertIn("start", operations)
        self.assertLess(
            operations.index("start"),
            operations.index("reset-failed"),
        )
        stop_call = next(call for call in self.runner.calls if "stop" in call)
        self.assertEqual(
            stop_call,
            (
                "/usr/bin/systemctl",
                "--system",
                "--no-ask-password",
                "--no-pager",
                "stop",
                self.artifact.unit_name,
            ),
        )
        self.assertEqual(outcome.fault_state.active_state, "inactive")
        self.assertTrue(outcome.recovered_state.is_healthy)

    def test_failed_execution_uses_exact_main_process_sigkill(self) -> None:
        outcome = self.injector.execute(self._failed_manifest(), self.artifact)
        kill_call = next(call for call in self.runner.calls if "kill" in call)
        self.assertEqual(
            kill_call,
            (
                "/usr/bin/systemctl",
                "--system",
                "--no-ask-password",
                "--no-pager",
                "kill",
                "--kill-whom=main",
                "--signal=SIGKILL",
                self.artifact.unit_name,
            ),
        )
        self.assertEqual(outcome.fault_state.active_state, "failed")
        self.assertTrue(outcome.recovered_state.is_healthy)

    def test_execute_reverifies_artifact_immediately_before_mutation(self) -> None:
        self.injector.execute(self.manifest, self.artifact)
        self.assertEqual(self.verifier.calls, 2)

    def test_ground_truth_opens_after_fault_and_closes_after_recovery(self) -> None:
        outcome = self.injector.execute(self.manifest, self.artifact)
        truth = outcome.ground_truth
        self.assertFalse(truth.is_open)
        self.assertEqual(truth.experiment_id, self.manifest.experiment_id)
        self.assertEqual(truth.target_unit, self.artifact.unit_name)
        self.assertIsNotNone(truth.duration_usec)
        self.assertGreaterEqual(truth.duration_usec or 0, 0)
        self.assertIn(0.1, self.sleeps)

    def test_outcome_serialization_preserves_plan_truth_and_states(self) -> None:
        outcome = self.injector.execute(self.manifest, self.artifact)
        payload = outcome.to_dict()
        self.assertEqual(payload["plan"]["operation"], "stop_service")
        self.assertFalse(payload["ground_truth"]["is_open"])
        self.assertTrue(payload["baseline_state"]["is_healthy"])
        self.assertTrue(payload["recovered_state"]["is_healthy"])

    def test_fault_command_failure_attempts_recovery_then_reraises(self) -> None:
        self.runner.fail_on = "stop"
        with self.assertRaises(FaultLabCommandError):
            self.injector.execute(self.manifest, self.artifact)
        operations = [
            _FakeSystemctlRunner._operation(call) for call in self.runner.calls
        ]
        self.assertIn("reset-failed", operations)
        self.assertIn("start", operations)
        self.assertEqual(self.runner.state, "active")

    def test_evidence_grace_interruption_attempts_recovery(self) -> None:
        def interrupt(_: float) -> None:
            raise KeyboardInterrupt

        injector = SystemdLabFaultInjector(
            runner=self.runner,
            install_verifier=self.verifier,
            wall_clock=self.wall_clock,
            monotonic_clock=self.monotonic,
            sleeper=interrupt,
        )
        with self.assertRaises(KeyboardInterrupt):
            injector.execute(self.manifest, self.artifact)
        self.assertEqual(self.runner.state, "active")

    def test_recovery_failure_is_explicit_and_not_hidden(self) -> None:
        self.runner.fail_on = "start"
        with self.assertRaises(FaultLabRecoveryError):
            self.injector.execute(self.manifest, self.artifact)

    def test_state_parser_rejects_missing_duplicate_and_wrong_identity(self) -> None:
        unit = self.artifact.unit_name
        self.runner.show_override = b"Id=x\n"
        with self.assertRaises(FaultLabProtocolError):
            self.injector.prepare(self.manifest, self.artifact)
        self.runner.show_override = (
            f"Id={unit}\nId={unit}\nLoadState=loaded\nActiveState=active\n"
            "SubState=running\nMainPID=1\nResult=success\n"
        ).encode()
        with self.assertRaises(FaultLabProtocolError):
            self.injector.prepare(self.manifest, self.artifact)
        self.runner.show_override = (
            "Id=sentinel-x-lab-wrong-01.service\nLoadState=loaded\n"
            "ActiveState=active\nSubState=running\nMainPID=1\nResult=success\n"
        ).encode()
        with self.assertRaises(FaultLabProtocolError):
            self.injector.prepare(self.manifest, self.artifact)

    def test_state_parser_rejects_invalid_main_pid(self) -> None:
        self.runner.show_override = (
            f"Id={self.artifact.unit_name}\nLoadState=loaded\n"
            "ActiveState=active\nSubState=running\nMainPID=nope\nResult=success\n"
        ).encode()
        with self.assertRaises(FaultLabProtocolError):
            self.injector.prepare(self.manifest, self.artifact)

    def test_bounded_capture_rejects_nul_invalid_utf8_and_oversize(self) -> None:
        for payload in (b"Id=x\x00", b"\xff", b"x" * 65_537):
            with self.subTest(payload_length=len(payload)):
                self.runner.show_override = payload
                with self.assertRaises(FaultLabProtocolError):
                    self.injector.prepare(self.manifest, self.artifact)

    def test_nonzero_systemctl_status_is_bounded_and_rejected(self) -> None:
        self.runner.fail_on = "show"
        with self.assertRaisesRegex(FaultLabCommandError, "status 1"):
            self.injector.prepare(self.manifest, self.artifact)

    def test_constructor_rejects_invalid_timeout_and_poll_bounds(self) -> None:
        with self.assertRaises(FaultLabPreconditionError):
            SystemdLabFaultInjector(command_timeout_seconds=True)
        with self.assertRaises(FaultLabPreconditionError):
            SystemdLabFaultInjector(command_timeout_seconds=31)
        with self.assertRaises(FaultLabPreconditionError):
            SystemdLabFaultInjector(poll_interval_seconds=0.001)
        with self.assertRaises(FaultLabPreconditionError):
            SystemdLabFaultInjector(poll_interval_seconds=2)

    def test_invalid_monotonic_clock_is_rejected_during_execution(self) -> None:
        injector = SystemdLabFaultInjector(
            runner=self.runner,
            install_verifier=self.verifier,
            wall_clock=self.wall_clock,
            monotonic_clock=lambda: -1.0,
            sleeper=self.sleeps.append,
        )
        with self.assertRaises(FaultLabRecoveryError):
            injector.execute(self.manifest, self.artifact)
        self.assertEqual(self.runner.state, "active")

    def test_naive_wall_clock_triggers_recovery_before_error_escapes(self) -> None:
        injector = SystemdLabFaultInjector(
            runner=self.runner,
            install_verifier=self.verifier,
            wall_clock=lambda: datetime(2026, 8, 14, 10, 0),
            monotonic_clock=self.monotonic,
            sleeper=self.sleeps.append,
        )
        with self.assertRaises(FaultLabProtocolError):
            injector.execute(self.manifest, self.artifact)
        self.assertEqual(self.runner.state, "active")

    def test_plan_rejects_noncanonical_recovery_sequence(self) -> None:
        with self.assertRaises(FaultLabPreconditionError):
            SystemdLabFaultPlan(
                experiment_id=self.manifest.experiment_id,
                scenario_id=self.scenario.scenario_id,
                target_unit=self.artifact.unit_name,
                fault_mode=self.scenario.fault_mode,
                operation=LabFaultOperation.STOP_SERVICE,
                expected_fault_active_states=("inactive",),
                recovery_operations=("reset-failed", "start"),
                artifact_sha256=self.artifact.sha256,
            )

    def test_default_install_verifier_accepts_exact_root_owned_artifact(self) -> None:
        path_type = type(self.artifact.runtime_install_path)
        payload = self.artifact.unit_text.encode("utf-8")
        metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644,
            st_uid=0,
            st_gid=0,
            st_size=len(payload),
        )
        with (
            patch.object(path_type, "lstat", return_value=metadata),
            patch.object(path_type, "read_bytes", return_value=payload),
        ):
            verify_installed_lab_fixture(self.artifact)

    def test_default_install_verifier_rejects_non_root_owner(self) -> None:
        path_type = type(self.artifact.runtime_install_path)
        payload = self.artifact.unit_text.encode("utf-8")
        metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644,
            st_uid=1000,
            st_gid=1000,
            st_size=len(payload),
        )
        with patch.object(path_type, "lstat", return_value=metadata):
            with self.assertRaises(FaultLabPreconditionError):
                verify_installed_lab_fixture(self.artifact)

    def test_default_install_verifier_rejects_wrong_mode(self) -> None:
        path_type = type(self.artifact.runtime_install_path)
        payload = self.artifact.unit_text.encode("utf-8")
        metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=0,
            st_gid=0,
            st_size=len(payload),
        )
        with patch.object(path_type, "lstat", return_value=metadata):
            with self.assertRaises(FaultLabPreconditionError):
                verify_installed_lab_fixture(self.artifact)

    def test_default_install_verifier_rejects_content_drift(self) -> None:
        path_type = type(self.artifact.runtime_install_path)
        payload = self.artifact.unit_text.encode("utf-8")
        metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644,
            st_uid=0,
            st_gid=0,
            st_size=len(payload),
        )
        with (
            patch.object(path_type, "lstat", return_value=metadata),
            patch.object(path_type, "read_bytes", return_value=b"x" * len(payload)),
        ):
            with self.assertRaises(FaultLabPreconditionError):
                verify_installed_lab_fixture(self.artifact)

    def test_default_runner_uses_shell_false_and_controlled_environment(self) -> None:
        completed = subprocess.CompletedProcess(
            args=(), returncode=0, stdout=b"", stderr=b""
        )
        with patch(
            "sentinel_x.lab.injector.subprocess.run", return_value=completed
        ) as run:
            injector = SystemdLabFaultInjector(
                install_verifier=self.verifier,
                wall_clock=self.wall_clock,
                monotonic_clock=self.monotonic,
                sleeper=self.sleeps.append,
            )
            with self.assertRaises(FaultLabProtocolError):
                injector.prepare(self.manifest, self.artifact)
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["env"]["PATH"], "/usr/bin:/bin")
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
