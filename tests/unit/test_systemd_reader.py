"""Tests for constrained read-only systemctl service inspection."""

from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence
from datetime import datetime, timezone
from unittest.mock import patch

from sentinel_x.systemd import (
    SystemctlCommandResult,
    SystemctlServiceReader,
    SystemdCommandError,
    SystemdCommandTimeoutError,
    SystemdExecutableNotFoundError,
    SystemdProtocolError,
)

_CAPTURED_AT = datetime(2026, 8, 13, 9, 30, tzinfo=timezone.utc)


def _valid_output(**overrides: str) -> bytes:
    values = {
        "Id": "sshd.service",
        "Names": "sshd.service ssh.service",
        "Description": "OpenSSH server daemon",
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "UnitFileState": "enabled",
        "FragmentPath": "/usr/lib/systemd/system/sshd.service",
        "SourcePath": "",
        "DropInPaths": "",
        "Requires": "sysinit.target system.slice",
        "Wants": "network-online.target",
        "After": "network.target sysinit.target",
        "Before": "shutdown.target",
        "CanStart": "yes",
        "CanStop": "yes",
        "CanReload": "yes",
        "ControlGroup": "/system.slice/sshd.service",
        "InvocationID": "0123456789abcdef",
        "StateChangeTimestampMonotonic": "1000",
        "ActiveEnterTimestampMonotonic": "900",
        "InactiveEnterTimestampMonotonic": "0",
        "Type": "notify",
        "Restart": "on-failure",
        "MainPID": "123",
        "ExecMainCode": "1",
        "ExecMainStatus": "0",
        "Result": "success",
        "NRestarts": "2",
        "ExecMainStartTimestampMonotonic": "850",
        "ExecMainExitTimestampMonotonic": "0",
    }
    values.update(overrides)
    output = "\n".join(f"{key}={value}" for key, value in values.items())
    return (output + "\n").encode()


class RecordingRunner:
    """Capture one explicit argv and return a controlled result."""

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


class SystemctlServiceReaderTests(unittest.TestCase):
    """Validate command containment, parsing, and failure behavior."""

    def _reader(
        self,
        result: SystemctlCommandResult,
    ) -> tuple[SystemctlServiceReader, RecordingRunner]:
        runner = RecordingRunner(result)
        reader = SystemctlServiceReader(
            systemctl_path="/usr/bin/systemctl",
            timeout_seconds=2.5,
            runner=runner,
            clock=lambda: _CAPTURED_AT,
        )
        return reader, runner

    def test_successful_read_builds_typed_service_snapshot(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(0, _valid_output(), b""),
        )
        snapshot = reader.read_service("sshd.service")
        self.assertEqual(snapshot.canonical_name, "sshd.service")
        self.assertEqual(snapshot.names, ("sshd.service", "ssh.service"))
        self.assertTrue(snapshot.is_active)
        self.assertEqual(snapshot.main_pid, 123)
        self.assertEqual(snapshot.restart_count, 2)
        self.assertEqual(snapshot.exec_main_status, 0)
        self.assertIsNone(snapshot.inactive_enter_monotonic_usec)
        self.assertEqual(snapshot.captured_at, _CAPTURED_AT)

    def test_command_is_explicit_system_read_only_show(self) -> None:
        reader, runner = self._reader(
            SystemctlCommandResult(0, _valid_output(), b""),
        )
        reader.read_service("sshd.service")
        argv, timeout_seconds = runner.calls[0]
        self.assertEqual(argv[0], "/usr/bin/systemctl")
        self.assertEqual(argv[1:4], ("--system", "--no-pager", "--no-ask-password"))
        self.assertIn("show", argv)
        self.assertIn("--all", argv)
        self.assertTrue(any(item.startswith("--property=") for item in argv))
        self.assertEqual(argv[-1], "sshd.service")
        self.assertNotIn("start", argv)
        self.assertNotIn("stop", argv)
        self.assertNotIn("restart", argv)
        self.assertEqual(timeout_seconds, 2.5)

    def test_unsafe_unit_name_is_rejected_before_runner_invocation(self) -> None:
        reader, runner = self._reader(
            SystemctlCommandResult(0, _valid_output(), b""),
        )
        with self.assertRaises(ValueError):
            reader.read_service("--system.service")
        self.assertEqual(runner.calls, [])

    def test_default_runner_uses_shell_false_and_controlled_environment(self) -> None:
        completed = subprocess.CompletedProcess(
            args=("systemctl",),
            returncode=0,
            stdout=_valid_output(),
            stderr=b"",
        )
        with patch(
            "sentinel_x.systemd.reader.subprocess.run",
            return_value=completed,
        ) as run_mock:
            reader = SystemctlServiceReader(
                systemctl_path="/usr/bin/systemctl",
                clock=lambda: _CAPTURED_AT,
            )
            reader.read_service("sshd.service")

        call = run_mock.call_args
        self.assertFalse(call.kwargs["shell"])
        self.assertIs(call.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(call.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(call.kwargs["env"]["SYSTEMD_PAGER"], "cat")

    def test_alias_request_preserves_requested_and_canonical_identity(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(
                0,
                _valid_output(Id="sshd.service", Names="sshd.service ssh.service"),
                b"",
            )
        )
        snapshot = reader.read_service("ssh.service")
        self.assertEqual(snapshot.requested_name, "ssh.service")
        self.assertEqual(snapshot.canonical_name, "sshd.service")

    def test_empty_optional_properties_map_to_none_or_empty_tuples(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(
                0,
                _valid_output(
                    UnitFileState="",
                    FragmentPath="",
                    SourcePath="",
                    DropInPaths="",
                    InvocationID="",
                    ControlGroup="",
                    Type="",
                    Restart="",
                    Result="",
                ),
                b"",
            )
        )
        snapshot = reader.read_service("sshd.service")
        self.assertIsNone(snapshot.unit_file_state)
        self.assertIsNone(snapshot.fragment_path)
        self.assertIsNone(snapshot.invocation_id)
        self.assertEqual(snapshot.drop_in_paths, ())

    def test_zero_main_pid_and_exec_code_become_none(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(
                0,
                _valid_output(MainPID="0", ExecMainCode="0", ExecMainStatus="9"),
                b"",
            )
        )
        snapshot = reader.read_service("sshd.service")
        self.assertIsNone(snapshot.main_pid)
        self.assertIsNone(snapshot.exec_main_code)
        self.assertIsNone(snapshot.exec_main_status)

    def test_missing_names_falls_back_to_canonical_name(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(0, _valid_output(Names=""), b""),
        )
        snapshot = reader.read_service("sshd.service")
        self.assertEqual(snapshot.names, ("sshd.service",))

    def test_missing_required_property_is_rejected(self) -> None:
        output = _valid_output().decode().replace("ActiveState=active\n", "")
        reader, _ = self._reader(
            SystemctlCommandResult(0, output.encode(), b""),
        )
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")

    def test_duplicate_property_is_rejected(self) -> None:
        output = _valid_output() + b"ActiveState=failed\n"
        reader, _ = self._reader(SystemctlCommandResult(0, output, b""))
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")

    def test_malformed_property_line_is_rejected(self) -> None:
        output = _valid_output() + b"not-a-property\n"
        reader, _ = self._reader(SystemctlCommandResult(0, output, b""))
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")

    def test_invalid_boolean_property_is_rejected(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(0, _valid_output(CanStart="true"), b""),
        )
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")

    def test_invalid_integer_property_is_rejected(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(0, _valid_output(MainPID="abc"), b""),
        )
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")

    def test_negative_integer_property_is_rejected(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(0, _valid_output(NRestarts="-1"), b""),
        )
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")

    def test_nonzero_systemctl_status_is_bounded_and_rejected(self) -> None:
        stderr = ("permission denied " * 100).encode()
        reader, _ = self._reader(SystemctlCommandResult(1, b"", stderr))
        with self.assertRaises(SystemdCommandError) as context:
            reader.read_service("sshd.service")
        self.assertLessEqual(len(str(context.exception)), 620)

    def test_oversized_stdout_is_rejected_before_parsing(self) -> None:
        reader, _ = self._reader(
            SystemctlCommandResult(0, b"x" * 65_537, b""),
        )
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")

    def test_nul_and_invalid_utf8_output_are_rejected(self) -> None:
        for output in (b"Id=sshd.service\x00\n", b"\xff"):
            with self.subTest(output=output):
                reader, _ = self._reader(SystemctlCommandResult(0, output, b""))
                with self.assertRaises(SystemdProtocolError):
                    reader.read_service("sshd.service")

    def test_timeout_bounds_are_validated(self) -> None:
        for timeout in (0.01, 31.0):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    SystemctlServiceReader(
                        systemctl_path="/usr/bin/systemctl",
                        timeout_seconds=timeout,
                    )

    def test_missing_systemctl_executable_is_rejected(self) -> None:
        with patch("sentinel_x.systemd.reader.shutil.which", return_value=None):
            with self.assertRaises(SystemdExecutableNotFoundError):
                SystemctlServiceReader()

    def test_default_runner_wraps_subprocess_timeout(self) -> None:
        with patch(
            "sentinel_x.systemd.reader.subprocess.run",
            side_effect=subprocess.TimeoutExpired("systemctl", 1.0),
        ):
            reader = SystemctlServiceReader(
                systemctl_path="/usr/bin/systemctl",
                timeout_seconds=1.0,
                clock=lambda: _CAPTURED_AT,
            )
            with self.assertRaises(SystemdCommandTimeoutError):
                reader.read_service("sshd.service")

    def test_naive_clock_result_is_rejected(self) -> None:
        runner = RecordingRunner(SystemctlCommandResult(0, _valid_output(), b""))
        reader = SystemctlServiceReader(
            systemctl_path="/usr/bin/systemctl",
            runner=runner,
            clock=lambda: datetime(2026, 8, 13, 9, 30),
        )
        with self.assertRaises(SystemdProtocolError):
            reader.read_service("sshd.service")


if __name__ == "__main__":
    unittest.main()
