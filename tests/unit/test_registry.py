"""Unit tests for typed Sentinel-X collector registry binding."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from sentinel_x.core import (
    CollectorDefinition,
    CollectorDefinitionValidationError,
    CollectorExecutionPolicy,
    CollectorExecutionSpec,
    CollectorRegistry,
    CollectorRegistryBindingError,
    CollectorSchedule,
    DuplicateCollectorDefinitionError,
    UnknownCollectorDefinitionError,
)


@dataclass(frozen=True, slots=True)
class _Settings:
    enabled: bool = True
    interval_seconds: float = 1.0
    initial_delay_seconds: float = 0.0
    budget_seconds: float | None = 0.25
    failure_backoff_initial_seconds: float = 1.0
    failure_backoff_max_seconds: float = 8.0


class CollectorRegistryTests(unittest.TestCase):
    """Tests for trusted collector definition and registry semantics."""

    def test_definition_from_settings_builds_schedule_and_policy(self) -> None:
        def handler() -> str:
            return "ok"

        definition = CollectorDefinition.from_settings(
            "cpu_load",
            handler,
            _Settings(
                interval_seconds=2.0,
                initial_delay_seconds=0.5,
                budget_seconds=0.75,
                failure_backoff_initial_seconds=2.0,
                failure_backoff_max_seconds=16.0,
            ),
        )

        self.assertEqual(definition.name, "cpu_load")
        self.assertTrue(definition.enabled)
        self.assertEqual(definition.schedule.interval_seconds, 2.0)
        self.assertEqual(definition.schedule.initial_delay_seconds, 0.5)
        self.assertEqual(definition.execution_spec.policy.budget_seconds, 0.75)
        self.assertIs(definition.execution_spec.handler, handler)

    def test_definition_rejects_schedule_and_spec_name_mismatch(self) -> None:
        with self.assertRaises(CollectorDefinitionValidationError):
            CollectorDefinition(
                enabled=True,
                schedule=CollectorSchedule("cpu", interval_ns=100),
                execution_spec=CollectorExecutionSpec("memory", lambda: None),
            )

    def test_definition_rejects_non_boolean_enabled(self) -> None:
        with self.assertRaises(CollectorDefinitionValidationError):
            CollectorDefinition(  # type: ignore[arg-type]
                enabled=1,
                schedule=CollectorSchedule("cpu", interval_ns=100),
                execution_spec=CollectorExecutionSpec("cpu", lambda: None),
            )

    def test_definition_to_dict_omits_handler(self) -> None:
        definition = CollectorDefinition.from_settings(
            "cpu",
            lambda: {"secret": "runtime-only"},
            _Settings(),
        )

        serialized = definition.to_dict()

        self.assertNotIn("handler", serialized)
        self.assertIn("schedule", serialized)
        self.assertIn("execution_policy", serialized)

    def test_registry_rejects_duplicate_definition(self) -> None:
        definition = CollectorDefinition.from_settings(
            "cpu",
            lambda: None,
            _Settings(),
        )
        registry = CollectorRegistry((definition,))

        with self.assertRaises(DuplicateCollectorDefinitionError):
            registry.register(definition)

    def test_registry_returns_definitions_in_name_order(self) -> None:
        registry = CollectorRegistry(
            (
                CollectorDefinition.from_settings(
                    "network",
                    lambda: None,
                    _Settings(),
                ),
                CollectorDefinition.from_settings(
                    "cpu",
                    lambda: None,
                    _Settings(),
                ),
            )
        )

        self.assertEqual(
            tuple(definition.name for definition in registry.definitions()),
            ("cpu", "network"),
        )

    def test_registry_lookup_rejects_unknown_name(self) -> None:
        registry = CollectorRegistry()

        with self.assertRaises(UnknownCollectorDefinitionError):
            registry.definition("missing")

    def test_execution_spec_returns_trusted_handler(self) -> None:
        def handler() -> str:
            return "trusted"

        registry = CollectorRegistry(
            (
                CollectorDefinition.from_settings(
                    "cpu",
                    handler,
                    _Settings(),
                ),
            )
        )

        self.assertIs(registry.execution_spec("cpu").handler, handler)

    def test_enabled_and_disabled_counts_are_reported(self) -> None:
        registry = CollectorRegistry(
            (
                CollectorDefinition.from_settings(
                    "cpu",
                    lambda: None,
                    _Settings(enabled=True),
                ),
                CollectorDefinition.from_settings(
                    "process",
                    lambda: None,
                    _Settings(enabled=False),
                ),
            )
        )

        self.assertEqual(registry.collector_count, 2)
        self.assertEqual(registry.enabled_count, 1)
        self.assertEqual(registry.disabled_count, 1)

    def test_create_scheduler_registers_only_enabled_collectors(self) -> None:
        registry = CollectorRegistry(
            (
                CollectorDefinition.from_settings(
                    "cpu",
                    lambda: None,
                    _Settings(enabled=True),
                ),
                CollectorDefinition.from_settings(
                    "process",
                    lambda: None,
                    _Settings(enabled=False),
                ),
            )
        )

        scheduler = registry.create_scheduler(now_ns=1_000)

        self.assertEqual(
            tuple(snapshot.schedule.name for snapshot in scheduler.snapshots()),
            ("cpu",),
        )

    def test_from_settings_binds_exact_handler_set(self) -> None:
        def cpu() -> str:
            return "cpu"

        def memory() -> str:
            return "memory"

        registry = CollectorRegistry.from_settings(
            (
                ("cpu", _Settings()),
                ("memory", _Settings(interval_seconds=2.0)),
            ),
            {
                "cpu": cpu,
                "memory": memory,
            },
        )

        self.assertIs(registry.execution_spec("cpu").handler, cpu)
        self.assertIs(registry.execution_spec("memory").handler, memory)

    def test_from_settings_rejects_missing_handler(self) -> None:
        with self.assertRaises(CollectorRegistryBindingError):
            CollectorRegistry.from_settings(
                (("cpu", _Settings()), ("memory", _Settings())),
                {"cpu": lambda: None},
            )

    def test_from_settings_rejects_unexpected_handler(self) -> None:
        with self.assertRaises(CollectorRegistryBindingError):
            CollectorRegistry.from_settings(
                (("cpu", _Settings()),),
                {
                    "cpu": lambda: None,
                    "memory": lambda: None,
                },
            )

    def test_from_settings_rejects_duplicate_setting_names(self) -> None:
        with self.assertRaises(CollectorRegistryBindingError):
            CollectorRegistry.from_settings(
                (("cpu", _Settings()), ("cpu", _Settings())),
                {"cpu": lambda: None},
            )

    def test_invalid_settings_are_wrapped_as_definition_error(self) -> None:
        with self.assertRaises(CollectorDefinitionValidationError):
            CollectorDefinition.from_settings(
                "cpu",
                lambda: None,
                _Settings(interval_seconds=0.0),
            )

    def test_registry_metadata_does_not_serialize_handlers(self) -> None:
        registry = CollectorRegistry(
            (
                CollectorDefinition.from_settings(
                    "cpu",
                    lambda: object(),
                    _Settings(),
                ),
            )
        )

        serialized = registry.to_dict()
        collectors = serialized["collectors"]

        self.assertIsInstance(collectors, list)
        self.assertNotIn("handler", collectors[0])

    def test_definition_accepts_explicit_execution_policy(self) -> None:
        schedule = CollectorSchedule("cpu", interval_ns=100)
        policy = CollectorExecutionPolicy(
            budget_ns=50,
            failure_backoff_initial_ns=100,
            failure_backoff_max_ns=400,
        )
        spec = CollectorExecutionSpec("cpu", lambda: None, policy)

        definition = CollectorDefinition(
            enabled=True,
            schedule=schedule,
            execution_spec=spec,
        )

        self.assertIs(definition.execution_spec.policy, policy)


if __name__ == "__main__":
    unittest.main()
