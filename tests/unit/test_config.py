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

    def test_default_systemd_service_targets_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                loaded = load_config()
            finally:
                os.chdir(original_cwd)

        self.assertEqual(loaded.config.systemd.services, ())
        self.assertEqual(loaded.config.systemd.items(), ())
        self.assertEqual(loaded.config.systemd.bindings(), ())

    def test_explicit_systemd_service_target_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "systemd-journald.service"\n'
                    "enabled = true\n"
                    "interval_seconds = 7.0\n"
                    "initial_delay_seconds = 1.5\n"
                    "budget_seconds = 0.9\n"
                    "failure_backoff_initial_seconds = 3.0\n"
                    "failure_backoff_max_seconds = 12.0\n"
                ),
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        target = loaded.config.systemd.services[0]
        self.assertEqual(target.unit_name, "systemd-journald.service")
        self.assertTrue(target.enabled)
        self.assertEqual(target.interval_seconds, 7.0)
        self.assertEqual(target.initial_delay_seconds, 1.5)
        self.assertEqual(target.budget_seconds, 0.9)
        self.assertEqual(target.failure_backoff_initial_seconds, 3.0)
        self.assertEqual(target.failure_backoff_max_seconds, 12.0)

    def test_systemd_service_targets_preserve_declared_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-order.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "\n"
                    "[[systemd.services]]\n"
                    'unit_name = "systemd-journald.service"\n'
                ),
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        self.assertEqual(
            tuple(target.unit_name for target in loaded.config.systemd.services),
            ("sshd.service", "systemd-journald.service"),
        )
        collector_names = tuple(name for name, _ in loaded.config.systemd.items())
        self.assertEqual(len(set(collector_names)), 2)

    def test_duplicate_systemd_service_targets_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-duplicate.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "\n"
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_unsafe_systemd_service_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-unsafe.toml"
            config_path.write_text(
                ('[[systemd.services]]\nunit_name = "../evil.service"\n'),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_too_fast_systemd_service_interval_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-fast.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "interval_seconds = 0.5\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_unknown_systemd_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-key.toml"
            config_path.write_text(
                '[systemd]\nmode = "unsafe"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_systemd_services_must_be_array_of_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-shape.toml"
            config_path.write_text(
                '[systemd]\nservices = "sshd.service"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_systemd_service_entry_must_be_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-entry.toml"
            config_path.write_text(
                '[systemd]\nservices = ["sshd.service"]\n',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_unknown_systemd_service_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-service-key.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    'command = "restart"\n'
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_systemd_service_target_limit_is_enforced(self) -> None:
        entries = []
        for index in range(17):
            entries.append(
                f'[[systemd.services]]\nunit_name = "sentinel-test-{index}.service"\n'
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-limit.toml"
            config_path.write_text("\n".join(entries), encoding="utf-8")
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_disabled_systemd_service_target_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-disabled.toml"
            config_path.write_text(
                ('[[systemd.services]]\nunit_name = "sshd.service"\nenabled = false\n'),
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        target = loaded.config.systemd.services[0]
        self.assertFalse(target.enabled)
        self.assertEqual(len(loaded.config.systemd.items()), 1)

    def test_templated_systemd_unit_gets_scheduler_safe_collector_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-template.toml"
            config_path.write_text(
                ('[[systemd.services]]\nunit_name = "getty@tty1.service"\n'),
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        collector_name = loaded.config.systemd.services[0].collector_name
        self.assertLessEqual(len(collector_name), 64)
        self.assertNotIn("@", collector_name)
        self.assertTrue(collector_name.startswith("systemd."))

    def test_default_systemd_journal_targets_are_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-default-journal.toml"
            config_path.write_text(
                '[[systemd.services]]\nunit_name = "sshd.service"\n',
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        target = loaded.config.systemd.services[0]
        self.assertFalse(target.journal_enabled)
        self.assertEqual(target.journal_max_entries, 32)
        self.assertEqual(loaded.config.systemd.journal_target_count, 0)
        self.assertEqual(loaded.config.systemd.journal_items(), ())
        self.assertEqual(loaded.config.systemd.journal_bindings(), ())

    def test_explicit_systemd_journal_policy_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "systemd-journald.service"\n'
                    "journal_enabled = true\n"
                    "journal_interval_seconds = 7.0\n"
                    "journal_initial_delay_seconds = 1.75\n"
                    "journal_budget_seconds = 1.5\n"
                    "journal_failure_backoff_initial_seconds = 3.0\n"
                    "journal_failure_backoff_max_seconds = 12.0\n"
                    "journal_max_entries = 24\n"
                ),
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        target = loaded.config.systemd.services[0]
        self.assertTrue(target.journal_enabled)
        self.assertEqual(target.journal_interval_seconds, 7.0)
        self.assertEqual(target.journal_initial_delay_seconds, 1.75)
        self.assertEqual(target.journal_budget_seconds, 1.5)
        self.assertEqual(target.journal_failure_backoff_initial_seconds, 3.0)
        self.assertEqual(target.journal_failure_backoff_max_seconds, 12.0)
        self.assertEqual(target.journal_max_entries, 24)
        self.assertEqual(loaded.config.systemd.journal_target_count, 1)

    def test_journal_and_state_enable_flags_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-only.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "enabled = false\n"
                    "journal_enabled = true\n"
                ),
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        target = loaded.config.systemd.services[0]
        self.assertFalse(target.enabled)
        self.assertTrue(target.journal_enabled)
        self.assertFalse(loaded.config.systemd.items()[0][1].enabled)
        self.assertTrue(loaded.config.systemd.journal_items()[0][1].enabled)

    def test_journal_binding_uses_deterministic_safe_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-template.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "getty@tty1.service"\n'
                    "journal_enabled = true\n"
                    "journal_max_entries = 8\n"
                ),
                encoding="utf-8",
            )
            loaded = load_config(config_path)

        target = loaded.config.systemd.services[0]
        self.assertLessEqual(len(target.journal_collector_name), 64)
        self.assertNotIn("@", target.journal_collector_name)
        self.assertTrue(target.journal_collector_name.startswith("journal."))
        self.assertEqual(
            loaded.config.systemd.journal_bindings(),
            ((target.journal_collector_name, "getty@tty1.service", 8),),
        )

    def test_too_fast_systemd_journal_interval_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-fast.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "journal_enabled = true\n"
                    "journal_interval_seconds = 0.5\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_invalid_systemd_journal_enabled_type_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-enabled.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    'journal_enabled = "yes"\n'
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_zero_systemd_journal_max_entries_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-zero.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "journal_max_entries = 0\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_systemd_journal_max_entries_above_bound_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-large.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "journal_max_entries = 65\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_systemd_journal_max_entries_boolean_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-bool.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "journal_max_entries = true\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)

    def test_invalid_systemd_journal_backoff_pair_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "systemd-journal-backoff.toml"
            config_path.write_text(
                (
                    "[[systemd.services]]\n"
                    'unit_name = "sshd.service"\n'
                    "journal_failure_backoff_initial_seconds = 8.0\n"
                    "journal_failure_backoff_max_seconds = 4.0\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigSchemaError):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()
