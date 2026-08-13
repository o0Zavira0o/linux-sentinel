"""Unit tests for bounded journalctl JSON evidence reads."""

from __future__ import annotations

import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sentinel_x.systemd.journal_reader import (
    JournalctlCommandResult,
    JournalctlServiceReader,
    SystemdJournalCommandError,
    SystemdJournalCommandTimeoutError,
    SystemdJournalCursorUnavailableError,
    SystemdJournalExecutableNotFoundError,
    SystemdJournalProtocolError,
)
from sentinel_x.systemd.journal_models import JournalPriority
from sentinel_x.systemd.models import SystemdUnitNameError

_BOOT_ID = "0123456789abcdef0123456789abcdef"
_CURSOR_A = "s=abc;i=1;b=0123456789abcdef0123456789abcdef;m=10;t=20"
_CURSOR_B = "s=abc;i=2;b=0123456789abcdef0123456789abcdef;m=11;t=21"
_CAPTURED_AT = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def _json_line(
    *,
    cursor: str = _CURSOR_A,
    message: str = "ready",
    priority: str = "6",
) -> bytes:
    return (
        "{"
        f'"__CURSOR":"{cursor}",'
        '"__REALTIME_TIMESTAMP":"2000000",'
        '"__MONOTONIC_TIMESTAMP":"1000000",'
        f'"_BOOT_ID":"{_BOOT_ID}",'
        f'"MESSAGE":"{message}",'
        f'"PRIORITY":"{priority}",'
        '"_SYSTEMD_UNIT":"demo.service",'
        '"_PID":"42"'
        "}\n"
    ).encode()


class RecordingRunner:
    def __init__(self, result: JournalctlCommandResult) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JournalctlCommandResult:
        self.calls.append((argv, timeout_seconds))
        return self.result


class JournalctlServiceReaderTests(unittest.TestCase):
    def _reader(self, runner: RecordingRunner) -> JournalctlServiceReader:
        return JournalctlServiceReader(
            journalctl_path="/usr/bin/journalctl",
            runner=runner,
            clock=lambda: _CAPTURED_AT,
        )

    def test_initial_command_is_current_boot_system_read_only_tail(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))
        reader = self._reader(runner)

        reader.read_service("demo.service", max_entries=7)

        argv, timeout = runner.calls[0]
        self.assertEqual(argv[0], "/usr/bin/journalctl")
        self.assertIn("--system", argv)
        self.assertIn("--no-pager", argv)
        self.assertNotIn("--quiet", argv)
        self.assertIn("--output=json", argv)
        self.assertIn("--boot=0", argv)
        self.assertIn("--unit=demo.service", argv)
        self.assertIn("--lines=7", argv)
        self.assertFalse(any(item.startswith("--after-cursor=") for item in argv))
        self.assertNotIn("--follow", argv)
        self.assertEqual(timeout, 3.0)

    def test_incremental_command_uses_after_cursor_and_oldest_new_entries(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))
        reader = self._reader(runner)

        reader.read_service(
            "demo.service",
            after_cursor=_CURSOR_A,
            max_entries=5,
        )

        argv, _ = runner.calls[0]
        self.assertIn("--after-cursor=" + _CURSOR_A, argv)
        self.assertIn("--lines=+5", argv)

    def test_output_field_allowlist_includes_causal_service_evidence(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))
        reader = self._reader(runner)

        reader.read_service("demo.service")

        argv, _ = runner.calls[0]
        output_option = next(
            item for item in argv if item.startswith("--output-fields=")
        )
        self.assertIn("MESSAGE", output_option)
        self.assertIn("PRIORITY", output_option)
        self.assertIn("_SYSTEMD_INVOCATION_ID", output_option)
        self.assertIn("OBJECT_SYSTEMD_UNIT", output_option)
        self.assertIn("COREDUMP_UNIT", output_option)

    def test_unsafe_unit_is_rejected_before_runner_invocation(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))
        reader = self._reader(runner)

        with self.assertRaises(SystemdUnitNameError):
            reader.read_service("--system.service")

        self.assertEqual(runner.calls, [])

    def test_invalid_cursor_is_rejected_before_runner_invocation(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))
        reader = self._reader(runner)

        with self.assertRaises(SystemdJournalProtocolError):
            reader.read_service("demo.service", after_cursor="bad\ncursor")

        self.assertEqual(runner.calls, [])

    def test_timeout_and_entry_bounds_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            JournalctlServiceReader(
                journalctl_path="/usr/bin/journalctl",
                timeout_seconds=0.01,
            )
        reader = JournalctlServiceReader(journalctl_path="/usr/bin/journalctl")
        for value in (0, 65):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    reader.read_service("demo.service", max_entries=value)

    @patch("sentinel_x.systemd.journal_reader.shutil.which", return_value=None)
    def test_missing_journalctl_executable_is_rejected(self, _which: object) -> None:
        with self.assertRaises(SystemdJournalExecutableNotFoundError):
            JournalctlServiceReader()

    def test_successful_text_entry_builds_typed_batch(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, _json_line(), b""))
        batch = self._reader(runner).read_service("demo.service", max_entries=4)

        self.assertEqual(batch.requested_unit, "demo.service")
        self.assertEqual(batch.next_cursor, _CURSOR_A)
        self.assertEqual(len(batch.entries), 1)
        entry = batch.entries[0]
        self.assertEqual(entry.message_text, "ready")
        self.assertEqual(entry.priority, JournalPriority.INFO)
        self.assertEqual(entry.systemd_unit, "demo.service")
        self.assertEqual(entry.pid, 42)

    def test_multiple_entries_preserve_order_and_advance_last_cursor(self) -> None:
        stdout = _json_line(cursor=_CURSOR_A) + _json_line(cursor=_CURSOR_B)
        runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))

        batch = self._reader(runner).read_service("demo.service", max_entries=2)

        self.assertEqual(
            tuple(entry.cursor for entry in batch.entries),
            (_CURSOR_A, _CURSOR_B),
        )
        self.assertEqual(batch.next_cursor, _CURSOR_B)
        self.assertTrue(batch.entry_limit_reached)

    def test_empty_output_returns_empty_batch_without_inventing_cursor(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))

        batch = self._reader(runner).read_service("demo.service")

        self.assertEqual(batch.entries, ())
        self.assertIsNone(batch.next_cursor)

    def test_empty_incremental_output_preserves_previous_cursor(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))

        batch = self._reader(runner).read_service(
            "demo.service",
            after_cursor=_CURSOR_A,
        )

        self.assertEqual(batch.entries, ())
        self.assertEqual(batch.next_cursor, _CURSOR_A)

    def test_binary_message_is_preserved_as_bytes(self) -> None:
        stdout = (
            "{"
            f'"__CURSOR":"{_CURSOR_A}",'
            '"__REALTIME_TIMESTAMP":"2",'
            '"__MONOTONIC_TIMESTAMP":"1",'
            f'"_BOOT_ID":"{_BOOT_ID}",'
            '"MESSAGE":[97,0,98]'
            "}\n"
        ).encode()
        runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))

        entry = self._reader(runner).read_service("demo.service").entries[0]

        self.assertEqual(entry.message_bytes, b"a\x00b")
        fields_payload = entry.to_dict()["fields"]
        self.assertIsInstance(fields_payload, dict)
        assert isinstance(fields_payload, dict)
        self.assertEqual(fields_payload["MESSAGE"], [97, 0, 98])

    def test_oversized_message_null_is_preserved_as_omitted(self) -> None:
        stdout = (
            "{"
            f'"__CURSOR":"{_CURSOR_A}",'
            '"__REALTIME_TIMESTAMP":"2",'
            '"__MONOTONIC_TIMESTAMP":"1",'
            f'"_BOOT_ID":"{_BOOT_ID}",'
            '"MESSAGE":null'
            "}\n"
        ).encode()
        runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))

        entry = self._reader(runner).read_service("demo.service").entries[0]

        self.assertTrue(entry.message_omitted)

    def test_repeated_mixed_field_values_are_preserved(self) -> None:
        stdout = (
            "{"
            f'"__CURSOR":"{_CURSOR_A}",'
            '"__REALTIME_TIMESTAMP":"2",'
            '"__MONOTONIC_TIMESTAMP":"1",'
            f'"_BOOT_ID":"{_BOOT_ID}",'
            '"MESSAGE":["one",[116,119,111],null]'
            "}\n"
        ).encode()
        runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))

        field = (
            self._reader(runner)
            .read_service("demo.service")
            .entries[0]
            .field("MESSAGE")
        )

        self.assertIsNotNone(field)
        assert field is not None
        self.assertEqual(field.values, ("one", b"two", None))
        self.assertTrue(field.is_multi_valued)

    def test_unknown_future_json_fields_are_ignored(self) -> None:
        stdout = (
            "{"
            f'"__CURSOR":"{_CURSOR_A}",'
            '"__REALTIME_TIMESTAMP":"2",'
            '"__MONOTONIC_TIMESTAMP":"1",'
            f'"_BOOT_ID":"{_BOOT_ID}",'
            '"__FUTURE_ADDRESS":"x",'
            '"FUTURE_FIELD":"y"'
            "}\n"
        ).encode()
        runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))

        entry = self._reader(runner).read_service("demo.service").entries[0]

        self.assertEqual(entry.fields, ())

    def test_malformed_json_and_non_object_records_are_rejected(self) -> None:
        for stdout in (b"{bad}\n", b"[]\n"):
            with self.subTest(stdout=stdout):
                runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))
                with self.assertRaises(SystemdJournalProtocolError):
                    self._reader(runner).read_service("demo.service")

    def test_missing_or_invalid_address_fields_are_rejected(self) -> None:
        cases = (
            b'{"MESSAGE":"x"}\n',
            (
                "{"
                f'"__CURSOR":"{_CURSOR_A}",'
                '"__REALTIME_TIMESTAMP":"bad",'
                '"__MONOTONIC_TIMESTAMP":"1",'
                f'"_BOOT_ID":"{_BOOT_ID}"'
                "}\n"
            ).encode(),
            (
                "{"
                f'"__CURSOR":"{_CURSOR_A}",'
                '"__REALTIME_TIMESTAMP":"2",'
                '"__MONOTONIC_TIMESTAMP":"1",'
                '"_BOOT_ID":"bad"'
                "}\n"
            ).encode(),
        )
        for stdout in cases:
            with self.subTest(stdout=stdout):
                runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))
                with self.assertRaises(SystemdJournalProtocolError):
                    self._reader(runner).read_service("demo.service")

    def test_invalid_binary_value_is_rejected(self) -> None:
        stdout = (
            "{"
            f'"__CURSOR":"{_CURSOR_A}",'
            '"__REALTIME_TIMESTAMP":"2",'
            '"__MONOTONIC_TIMESTAMP":"1",'
            f'"_BOOT_ID":"{_BOOT_ID}",'
            '"MESSAGE":[300]'
            "}\n"
        ).encode()
        runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))

        with self.assertRaises(SystemdJournalProtocolError):
            self._reader(runner).read_service("demo.service")

    def test_more_entries_than_requested_bound_is_rejected(self) -> None:
        stdout = _json_line(cursor=_CURSOR_A) + _json_line(cursor=_CURSOR_B)
        runner = RecordingRunner(JournalctlCommandResult(0, stdout, b""))

        with self.assertRaises(SystemdJournalProtocolError):
            self._reader(runner).read_service("demo.service", max_entries=1)

    def test_successful_stderr_notice_is_preserved_as_diagnostic(self) -> None:
        runner = RecordingRunner(
            JournalctlCommandResult(
                0,
                b"",
                b"Hint: limited journal access\n",
            )
        )

        batch = self._reader(runner).read_service("demo.service")

        self.assertEqual(batch.diagnostic, "Hint: limited journal access")

    def test_nonzero_status_is_bounded_and_rejected(self) -> None:
        runner = RecordingRunner(
            JournalctlCommandResult(
                1,
                b"",
                (b"failure " * 200),
            )
        )

        with self.assertRaises(SystemdJournalCommandError) as context:
            self._reader(runner).read_service("demo.service")

        self.assertLessEqual(len(str(context.exception)), 600)

    def test_incremental_seek_failure_is_classified_as_cursor_unavailable(self) -> None:
        runner = RecordingRunner(
            JournalctlCommandResult(
                1,
                b"",
                b"Failed to seek to cursor: Invalid argument\n",
            )
        )

        with self.assertRaises(SystemdJournalCursorUnavailableError):
            self._reader(runner).read_service(
                "demo.service",
                after_cursor=_CURSOR_A,
            )

    def test_seek_failure_without_incremental_cursor_stays_generic(self) -> None:
        runner = RecordingRunner(
            JournalctlCommandResult(
                1,
                b"",
                b"Failed to seek to cursor: Invalid argument\n",
            )
        )

        with self.assertRaises(SystemdJournalCommandError) as context:
            self._reader(runner).read_service("demo.service")

        self.assertNotIsInstance(
            context.exception,
            SystemdJournalCursorUnavailableError,
        )

    def test_other_incremental_failure_stays_generic(self) -> None:
        runner = RecordingRunner(
            JournalctlCommandResult(1, b"", b"Permission denied\n")
        )

        with self.assertRaises(SystemdJournalCommandError) as context:
            self._reader(runner).read_service(
                "demo.service",
                after_cursor=_CURSOR_A,
            )

        self.assertNotIsInstance(
            context.exception,
            SystemdJournalCursorUnavailableError,
        )

    def test_invalid_utf8_nul_and_oversized_stderr_are_rejected(self) -> None:
        cases = (
            JournalctlCommandResult(0, b"\xff", b""),
            JournalctlCommandResult(0, b"\x00", b""),
            JournalctlCommandResult(0, b"", b"x" * 65_537),
        )
        for result in cases:
            with self.subTest(result=result):
                runner = RecordingRunner(result)
                with self.assertRaises(SystemdJournalProtocolError):
                    self._reader(runner).read_service("demo.service")

    def test_naive_clock_result_is_rejected(self) -> None:
        runner = RecordingRunner(JournalctlCommandResult(0, b"", b""))
        reader = JournalctlServiceReader(
            journalctl_path="/usr/bin/journalctl",
            runner=runner,
            clock=lambda: datetime(2026, 8, 13, 10, 0),
        )

        with self.assertRaises(SystemdJournalProtocolError):
            reader.read_service("demo.service")

    def test_default_runner_uses_shell_false_and_controlled_environment(self) -> None:
        completed = subprocess.CompletedProcess(
            args=("journalctl",),
            returncode=0,
            stdout=b"",
            stderr=b"",
        )
        with patch(
            "sentinel_x.systemd.journal_reader.subprocess.run",
            return_value=completed,
        ) as run:
            reader = JournalctlServiceReader(
                journalctl_path=Path("/usr/bin/journalctl"),
            )
            reader.read_service("demo.service")

        kwargs = run.call_args.kwargs
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(kwargs["env"]["SYSTEMD_COLORS"], "0")
        self.assertEqual(kwargs["env"]["SYSTEMD_PAGER"], "cat")

    def test_default_runner_wraps_subprocess_timeout(self) -> None:
        with patch(
            "sentinel_x.systemd.journal_reader.subprocess.run",
            side_effect=subprocess.TimeoutExpired("journalctl", 3.0),
        ):
            reader = JournalctlServiceReader(
                journalctl_path="/usr/bin/journalctl",
            )
            with self.assertRaises(SystemdJournalCommandTimeoutError):
                reader.read_service("demo.service")


if __name__ == "__main__":
    unittest.main()
