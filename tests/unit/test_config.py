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


class ConfigLoaderTests(unittest.TestCase):
    """Tests for typed TOML configuration loading."""

    def test_missing_implicit_config_uses_defaults(self) -> None:
        original_cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                os.chdir(tmpdir)

                loaded = load_config()

            finally:
                os.chdir(original_cwd)

        self.assertIsNone(loaded.source_path)

        self.assertEqual(
            loaded.config.agent.instance_name,
            "sentinel-x",
        )

        self.assertEqual(
            loaded.config.agent.tick_interval,
            0.5,
        )

        self.assertTrue(loaded.config.storage.enabled)

        self.assertEqual(
            loaded.config.storage.directory,
            "~/.local/state/sentinel-x/events",
        )

        self.assertTrue(loaded.config.storage.flush_on_write)

    def test_valid_explicit_config_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "custom.toml"

            config_path.write_text(
                (
                    "[agent]\n"
                    'instance_name = "lab-node-01"\n'
                    "tick_interval = 1.25\n"
                    "\n"
                    "[storage]\n"
                    "enabled = false\n"
                    'directory = "./events"\n'
                    "flush_on_write = false\n"
                ),
                encoding="utf-8",
            )

            loaded = load_config(config_path)

        self.assertEqual(
            loaded.config.agent.instance_name,
            "lab-node-01",
        )

        self.assertEqual(
            loaded.config.agent.tick_interval,
            1.25,
        )

        self.assertFalse(loaded.config.storage.enabled)

        self.assertEqual(
            loaded.config.storage.directory,
            "./events",
        )

        self.assertFalse(loaded.config.storage.flush_on_write)

        self.assertIsNotNone(loaded.source_path)

    def test_invalid_toml_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "broken.toml"

            config_path.write_text(
                "[agent\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigParseError):
                load_config(config_path)

    def test_unknown_agent_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "unknown.toml"

            config_path.write_text(
                ("[agent]\ntick_intervall = 1.0\n"),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_invalid_tick_interval_type_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "wrong-type.toml"

            config_path.write_text(
                ('[agent]\ntick_interval = "fast"\n'),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_missing_explicit_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "missing.toml"

            with self.assertRaises(ConfigFileError):
                load_config(config_path)

    def test_directory_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ConfigFileError):
                load_config(Path(tmpdir))

    def test_invalid_storage_boolean_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "invalid-storage.toml"

            config_path.write_text(
                ('[storage]\nenabled = "yes"\n'),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_unknown_storage_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "unknown-storage.toml"

            config_path.write_text(
                ("[storage]\nflush_every_time = true\n"),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_default_collector_configuration_is_typed_and_staggered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                loaded = load_config()
            finally:
                os.chdir(original_cwd)

        collectors = loaded.config.collectors
        self.assertEqual(collectors.cpu_load.interval_seconds, 1.0)
        self.assertEqual(collectors.memory.interval_seconds, 2.0)
        self.assertEqual(collectors.filesystem.interval_seconds, 10.0)
        self.assertEqual(collectors.disk_io.interval_seconds, 1.0)
        self.assertEqual(collectors.network.interval_seconds, 1.0)
        self.assertEqual(collectors.process.interval_seconds, 5.0)
        self.assertLess(
            collectors.cpu_load.initial_delay_seconds,
            collectors.process.initial_delay_seconds,
        )

    def test_explicit_collector_overrides_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collectors.toml"
            config_path.write_text(
                (
                    "[collectors.process]\n"
                    "enabled = false\n"
                    "interval_seconds = 15.0\n"
                    "initial_delay_seconds = 2.0\n"
                    "budget_seconds = 2.5\n"
                    "failure_backoff_initial_seconds = 3.0\n"
                    "failure_backoff_max_seconds = 12.0\n"
                ),
                encoding="utf-8",
            )

            loaded = load_config(config_path)

        process = loaded.config.collectors.process
        self.assertFalse(process.enabled)
        self.assertEqual(process.interval_seconds, 15.0)
        self.assertEqual(process.initial_delay_seconds, 2.0)
        self.assertEqual(process.budget_seconds, 2.5)
        self.assertEqual(process.failure_backoff_initial_seconds, 3.0)
        self.assertEqual(process.failure_backoff_max_seconds, 12.0)

    def test_partial_collector_override_keeps_other_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "partial.toml"
            config_path.write_text(
                "[collectors.network]\ninterval_seconds = 3.0\n",
                encoding="utf-8",
            )

            loaded = load_config(config_path)

        self.assertEqual(loaded.config.collectors.network.interval_seconds, 3.0)
        self.assertEqual(loaded.config.collectors.network.budget_seconds, 0.25)
        self.assertEqual(loaded.config.collectors.cpu_load.interval_seconds, 1.0)

    def test_unknown_collector_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "unknown-collector.toml"
            config_path.write_text(
                "[collectors.gpu]\ninterval_seconds = 1.0\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_unknown_collector_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "unknown-collector-key.toml"
            config_path.write_text(
                "[collectors.cpu_load]\ninterval = 1.0\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_non_table_collector_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collector-not-table.toml"
            config_path.write_text(
                "collectors = { process = 1 }\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_invalid_collector_enabled_type_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collector-enabled.toml"
            config_path.write_text(
                '[collectors.memory]\nenabled = "yes"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_too_fast_collector_interval_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collector-fast.toml"
            config_path.write_text(
                "[collectors.cpu_load]\ninterval_seconds = 0.001\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_nonpositive_collector_budget_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collector-budget.toml"
            config_path.write_text(
                "[collectors.process]\nbudget_seconds = 0.0\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_invalid_collector_backoff_pair_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collector-backoff.toml"
            config_path.write_text(
                (
                    "[collectors.disk_io]\n"
                    "failure_backoff_initial_seconds = 4.0\n"
                    "failure_backoff_max_seconds = 2.0\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_collectors_root_must_be_a_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collectors-root.toml"
            config_path.write_text(
                "collectors = 1\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()
