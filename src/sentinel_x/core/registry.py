"""Typed collector registry and runtime binding primitives for Sentinel-X."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from sentinel_x.core.execution import (
    CollectorExecutionPolicy,
    CollectorExecutionSpec,
    CollectorExecutionValidationError,
)
from sentinel_x.core.scheduling import (
    CollectorSchedule,
    CollectorScheduler,
    ScheduleValidationError,
)


class CollectorRegistryError(RuntimeError):
    """Base error for collector registry operations."""


class CollectorDefinitionValidationError(ValueError):
    """Raised when a collector definition is internally inconsistent."""


class DuplicateCollectorDefinitionError(CollectorRegistryError):
    """Raised when a collector name is registered more than once."""


class UnknownCollectorDefinitionError(CollectorRegistryError):
    """Raised when a collector definition cannot be found."""


class CollectorRegistryBindingError(CollectorRegistryError):
    """Raised when configured collectors and runtime handlers do not match."""


class CollectorRuntimeSettings(Protocol):
    """Read-only runtime settings consumed by collector definition binding."""

    @property
    def enabled(self) -> bool:
        """Return whether the collector is enabled."""

        ...

    @property
    def interval_seconds(self) -> float:
        """Return the collector cadence in seconds."""

        ...

    @property
    def initial_delay_seconds(self) -> float:
        """Return the initial scheduling delay in seconds."""

        ...

    @property
    def budget_seconds(self) -> float | None:
        """Return the optional execution budget in seconds."""

        ...

    @property
    def failure_backoff_initial_seconds(self) -> float:
        """Return the initial failure backoff in seconds."""

        ...

    @property
    def failure_backoff_max_seconds(self) -> float:
        """Return the maximum failure backoff in seconds."""

        ...


@dataclass(frozen=True, slots=True)
class CollectorDefinition:
    """One validated collector schedule, handler, and execution policy."""

    enabled: bool
    schedule: CollectorSchedule
    execution_spec: CollectorExecutionSpec

    def __post_init__(self) -> None:
        """Validate definition consistency without inspecting handler internals."""

        if type(self.enabled) is not bool:
            raise CollectorDefinitionValidationError("enabled must be a boolean")
        if not isinstance(self.schedule, CollectorSchedule):
            raise CollectorDefinitionValidationError(
                "schedule must be a CollectorSchedule"
            )
        if not isinstance(self.execution_spec, CollectorExecutionSpec):
            raise CollectorDefinitionValidationError(
                "execution_spec must be a CollectorExecutionSpec"
            )
        if self.schedule.name != self.execution_spec.name:
            raise CollectorDefinitionValidationError(
                "schedule and execution spec must use the same collector name"
            )

    @classmethod
    def from_settings(
        cls,
        name: str,
        handler: Callable[[], object],
        settings: CollectorRuntimeSettings,
    ) -> CollectorDefinition:
        """Build one definition from structurally compatible runtime settings."""

        if type(settings.enabled) is not bool:
            raise CollectorDefinitionValidationError(
                "collector settings enabled must be a boolean"
            )

        try:
            schedule = CollectorSchedule.from_seconds(
                name,
                interval_seconds=settings.interval_seconds,
                initial_delay_seconds=settings.initial_delay_seconds,
            )
            policy = CollectorExecutionPolicy.from_seconds(
                budget_seconds=settings.budget_seconds,
                failure_backoff_initial_seconds=(
                    settings.failure_backoff_initial_seconds
                ),
                failure_backoff_max_seconds=settings.failure_backoff_max_seconds,
            )
            execution_spec = CollectorExecutionSpec(
                name=name,
                handler=handler,
                policy=policy,
            )
        except (ScheduleValidationError, CollectorExecutionValidationError) as exc:
            raise CollectorDefinitionValidationError(str(exc)) from exc

        return cls(
            enabled=settings.enabled,
            schedule=schedule,
            execution_spec=execution_spec,
        )

    @property
    def name(self) -> str:
        """Return the collector identity shared by schedule and execution spec."""

        return self.schedule.name

    def to_dict(self) -> dict[str, object]:
        """Return metadata without serializing the executable handler."""

        return {
            "name": self.name,
            "enabled": self.enabled,
            "schedule": self.schedule.to_dict(),
            "execution_policy": self.execution_spec.policy.to_dict(),
        }


class CollectorRegistry:
    """Thread-safe registry for validated collector runtime definitions."""

    def __init__(
        self,
        definitions: Iterable[CollectorDefinition] = (),
    ) -> None:
        self._definitions: dict[str, CollectorDefinition] = {}
        self._lock = RLock()

        for definition in definitions:
            self.register(definition)

    @classmethod
    def from_settings(
        cls,
        settings: Iterable[tuple[str, CollectorRuntimeSettings]],
        handlers: Mapping[str, Callable[[], object]],
    ) -> CollectorRegistry:
        """Bind configured collectors to an exact set of trusted handlers."""

        configured = tuple(settings)
        configured_names = tuple(name for name, _ in configured)

        if len(set(configured_names)) != len(configured_names):
            raise CollectorRegistryBindingError(
                "collector settings contain duplicate collector names"
            )

        expected = set(configured_names)
        provided = set(handlers)
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)

        if missing or extra:
            parts: list[str] = []
            if missing:
                parts.append("missing handlers: " + ", ".join(missing))
            if extra:
                parts.append("unexpected handlers: " + ", ".join(extra))
            raise CollectorRegistryBindingError("; ".join(parts))

        definitions = tuple(
            CollectorDefinition.from_settings(
                name,
                handlers[name],
                runtime_settings,
            )
            for name, runtime_settings in configured
        )
        return cls(definitions)

    def register(self, definition: CollectorDefinition) -> None:
        """Register one immutable collector definition."""

        if not isinstance(definition, CollectorDefinition):
            raise TypeError("definition must be a CollectorDefinition")

        with self._lock:
            if definition.name in self._definitions:
                raise DuplicateCollectorDefinitionError(
                    f"collector definition already registered: {definition.name}"
                )
            self._definitions[definition.name] = definition

    def definition(self, collector_name: str) -> CollectorDefinition:
        """Return one collector definition by exact name."""

        if not isinstance(collector_name, str):
            raise TypeError("collector_name must be a string")

        with self._lock:
            definition = self._definitions.get(collector_name)
            if definition is None:
                raise UnknownCollectorDefinitionError(
                    f"collector definition is not registered: {collector_name}"
                )
            return definition

    def execution_spec(self, collector_name: str) -> CollectorExecutionSpec:
        """Return the trusted execution spec for one registered collector."""

        return self.definition(collector_name).execution_spec

    def definitions(self) -> tuple[CollectorDefinition, ...]:
        """Return all definitions in deterministic collector-name order."""

        with self._lock:
            return tuple(self._definitions[name] for name in sorted(self._definitions))

    def enabled_definitions(self) -> tuple[CollectorDefinition, ...]:
        """Return enabled definitions in deterministic collector-name order."""

        return tuple(
            definition for definition in self.definitions() if definition.enabled
        )

    @property
    def collector_count(self) -> int:
        """Return the number of configured collector definitions."""

        with self._lock:
            return len(self._definitions)

    @property
    def enabled_count(self) -> int:
        """Return the number of enabled collector definitions."""

        return len(self.enabled_definitions())

    @property
    def disabled_count(self) -> int:
        """Return the number of disabled collector definitions."""

        return self.collector_count - self.enabled_count

    def create_scheduler(self, *, now_ns: int) -> CollectorScheduler:
        """Create a fresh scheduler containing only enabled collectors."""

        scheduler = CollectorScheduler()
        for definition in self.enabled_definitions():
            scheduler.register(definition.schedule, now_ns=now_ns)
        return scheduler

    def to_dict(self) -> dict[str, object]:
        """Return bounded metadata without executable handler objects."""

        definitions = self.definitions()
        return {
            "collector_count": len(definitions),
            "enabled_count": sum(1 for definition in definitions if definition.enabled),
            "disabled_count": sum(
                1 for definition in definitions if not definition.enabled
            ),
            "collectors": [definition.to_dict() for definition in definitions],
        }
