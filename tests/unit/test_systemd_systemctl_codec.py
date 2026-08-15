"""Tests for exact decoding of systemctl string-array properties."""

from __future__ import annotations

import unittest

from sentinel_x.systemd.systemctl_codec import (
    SystemctlStringArrayDecodeError,
    decode_systemctl_string_array,
)


class SystemctlStringArrayCodecTests(unittest.TestCase):
    def test_empty_property_is_empty_array(self) -> None:
        self.assertEqual(decode_systemctl_string_array(""), ())

    def test_unquoted_entries_preserve_order(self) -> None:
        self.assertEqual(
            decode_systemctl_string_array("a.service b.target"),
            ("a.service", "b.target"),
        )

    def test_quoted_unit_escape_preserves_literal_systemd_backslash_escape(
        self,
    ) -> None:
        self.assertEqual(
            decode_systemctl_string_array(
                r'"blockdev@dev-disk-by\\x2duuid-A.target" network.target'
            ),
            (r"blockdev@dev-disk-by\x2duuid-A.target", "network.target"),
        )

    def test_quoted_path_restores_space_and_shell_special_characters(self) -> None:
        self.assertEqual(
            decode_systemctl_string_array(r'"/tmp/a b\$c\`d"'),
            ("/tmp/a b$c`d",),
        )

    def test_quoted_path_distinguishes_literal_backslash_from_escape(self) -> None:
        self.assertEqual(
            decode_systemctl_string_array(r'"/tmp/a\\\$b"'),
            (r"/tmp/a\$b",),
        )

    def test_named_and_octal_control_escapes_are_decoded(self) -> None:
        self.assertEqual(
            decode_systemctl_string_array(r'"line\nnext\011tab"'),
            ("line\nnext\ttab",),
        )

    def test_malformed_quote_and_escape_are_rejected(self) -> None:
        for value in ('"unterminated', r'"bad\q"'):
            with self.subTest(value=value):
                with self.assertRaises(SystemctlStringArrayDecodeError):
                    decode_systemctl_string_array(value)

    def test_noncanonical_spacing_and_unquoted_shell_syntax_are_rejected(self) -> None:
        for value in ("a.service  b.service", "a.service ", "a$bad.service"):
            with self.subTest(value=value):
                with self.assertRaises(SystemctlStringArrayDecodeError):
                    decode_systemctl_string_array(value)


if __name__ == "__main__":
    unittest.main()
