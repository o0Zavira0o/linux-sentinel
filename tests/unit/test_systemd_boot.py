"""Tests for typed Linux boot identity helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel_x.systemd.boot import (
    SystemBootIdError,
    normalize_boot_id,
    read_current_boot_id,
)


class SystemBootIdentityTests(unittest.TestCase):
    def test_normalize_accepts_hyphenated_uppercase_boot_id(self) -> None:
        value = "ABCDEF01-2345-6789-ABCD-EF0123456789"
        self.assertEqual(
            normalize_boot_id(value),
            "abcdef0123456789abcdef0123456789",
        )

    def test_normalize_rejects_invalid_type_and_shape(self) -> None:
        with self.assertRaises(SystemBootIdError):
            normalize_boot_id(None)
        with self.assertRaises(SystemBootIdError):
            normalize_boot_id("not-a-boot-id")

    def test_read_current_boot_id_uses_bounded_procfs_text_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "boot_id"
            path.write_text("a" * 32 + "\n", encoding="utf-8")
            with patch("sentinel_x.systemd.boot._BOOT_ID_PATH", path):
                self.assertEqual(read_current_boot_id(), "a" * 32)

    def test_read_current_boot_id_wraps_os_errors(self) -> None:
        path = Path("/definitely/missing/sentinel-x-boot-id")
        with patch("sentinel_x.systemd.boot._BOOT_ID_PATH", path):
            with self.assertRaisesRegex(SystemBootIdError, "failed to read"):
                read_current_boot_id()


if __name__ == "__main__":
    unittest.main()
