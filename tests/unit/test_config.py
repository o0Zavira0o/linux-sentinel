"""Unit tests for the Sentinel-X configuration subsystem."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sentinel_x.config import (
    ConfigFileError,
    ConfigParseError,
    ConfigSchemaError,
    load_config,
)


class ConfigLoaderTests(
    unittest.TestCase
):
    """Tests for typed TOML configuration loading."""

    def test_missing_implicit_config_uses_defaults(
        self,
    ) -> None:
        original_cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                os.chdir(tmpdir)

                loaded = load_config()

            finally:
                os.chdir(original_cwd)

        self.assertIsNone(
            loaded.source_path
        )

        self.assertEqual(
            loaded.config.agent.instance_name,
            "sentinel-x",
        )

        self.assertEqual(
            loaded.config.agent.tick_interval,
            0.5,
        )

    def test_valid_explicit_config_is_loaded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = (
                Path(tmpdir)
                / "custom.toml"
            )

            config_path.write_text(
                (
                    "[agent]\n"
                    'instance_name = "lab-node-01"\n'
                    "tick_interval = 1.25\n"
                ),
                encoding="utf-8",
            )

            loaded = load_config(
                config_path
            )

        self.assertEqual(
            loaded.config.agent.instance_name,
            "lab-node-01",
        )

        self.assertEqual(
            loaded.config.agent.tick_interval,
            1.25,
        )

        self.assertIsNotNone(
            loaded.source_path
        )

    def test_invalid_toml_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = (
                Path(tmpdir)
                / "broken.toml"
            )

            config_path.write_text(
                "[agent\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                ConfigParseError
            ):
                load_config(
                    config_path
                )

    def test_unknown_key_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = (
                Path(tmpdir)
                / "unknown.toml"
            )

            config_path.write_text(
                (
                    "[agent]\n"
                    "tick_intervall = 1.0\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaises(
                ConfigSchemaError
            ):
                load_config(
                    config_path
                )

    def test_invalid_value_type_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = (
                Path(tmpdir)
                / "wrong-type.toml"
            )

            config_path.write_text(
                (
                    "[agent]\n"
                    'tick_interval = "fast"\n'
                ),
                encoding="utf-8",
            )

            with self.assertRaises(
                ConfigSchemaError
            ):
                load_config(
                    config_path
                )

    def test_missing_explicit_file_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = (
                Path(tmpdir)
                / "missing.toml"
            )

            with self.assertRaises(
                ConfigFileError
            ):
                load_config(
                    config_path
                )

    def test_directory_path_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(
                ConfigFileError
            ):
                load_config(
                    Path(tmpdir)
                )


if __name__ == "__main__":
    unittest.main()
