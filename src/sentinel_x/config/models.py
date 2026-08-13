"""Typed configuration models for Sentinel-X."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

from sentinel_x.systemd.models import (
    SystemdUnitNameError,
    validate_service_unit_name,
)


class ConfigValidationError(ValueError):
    """Raised when a typed Sentinel-X configuration value is invalid."""


_INSTANCE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MIN_COLLECTOR_INTERVAL_SECONDS = 0.05
_MAX_RUNTIME_SECONDS = 86_400.0
_MIN_SYSTEMD_SERVICE_INTERVAL_SECONDS = 1.0
_MIN_SYSTEMD_JOURNAL_INTERVAL_SECONDS = 1.0
MAX_SYSTEMD_SERVICE_TARGETS = 16
MAX_SYSTEMD_JOURNAL_ENTRIES = 64
_SYSTEMD_COLLECTOR_PREFIX = "systemd."
_SYSTEMD_JOURNAL_COLLECTOR_PREFIX = "journal."
_SYSTEMD_COLLECTOR_HASH_HEX_LENGTH = 12

BUILTIN_HOST_COLLECTOR_NAMES = (
    "cpu_load",
    "memory",
    "filesystem",
    "disk_io",
    "network",
    "process",
)


def _validate_tick_interval(value: object) -> float:
    """Validate the core loop wake-up interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError("agent.tick_interval must be a number")

    normalized = float(value)
    if not math.isfinite(normalized):
        raise ConfigValidationError("agent.tick_interval must be finite")
    if not 0.05 <= normalized <= 60.0:
        raise ConfigValidationError(
            "agent.tick_interval must be between 0.05 and 60.0 seconds"
        )
    return normalized


def _validate_runtime_seconds(
    value: object,
    *,
    field_name: str,
    allow_zero: bool,
    minimum_when_positive: float | None = None,
) -> float:
    """Validate one finite operational duration expressed in seconds."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(f"{field_name} must be a number")

    normalized = float(value)
    if not math.isfinite(normalized):
        raise ConfigValidationError(f"{field_name} must be finite")
    if normalized < 0.0 or (not allow_zero and normalized == 0.0):
        comparison = "non-negative" if allow_zero else "greater than zero"
        raise ConfigValidationError(f"{field_name} must be {comparison}")
    if (
        normalized > 0.0
        and minimum_when_positive is not None
        and normalized < minimum_when_positive
    ):
        raise ConfigValidationError(
            f"{field_name} must be at least {minimum_when_positive} seconds"
        )
    if normalized > _MAX_RUNTIME_SECONDS:
        raise ConfigValidationError(
            f"{field_name} must not exceed {_MAX_RUNTIME_SECONDS} seconds"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Runtime settings currently consumed by Sentinel-X."""

    instance_name: str = "sentinel-x"
    tick_interval: float = 0.5

    def __post_init__(self) -> None:
        """Normalize and validate agent configuration."""

        if not isinstance(self.instance_name, str):
            raise ConfigValidationError("agent.instance_name must be a string")

        instance_name = self.instance_name.strip()
        if not _INSTANCE_NAME_PATTERN.fullmatch(instance_name):
            raise ConfigValidationError(
                "agent.instance_name must be 1-64 characters "
                "and contain only letters, digits, '.', '_', "
                "or '-', starting with a letter or digit"
            )

        tick_interval = _validate_tick_interval(self.tick_interval)
        object.__setattr__(self, "instance_name", instance_name)
        object.__setattr__(self, "tick_interval", tick_interval)


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Durable event-storage settings for Sentinel-X."""

    enabled: bool = True
    directory: str = "~/.local/state/sentinel-x/events"
    flush_on_write: bool = True

    def __post_init__(self) -> None:
        """Normalize and validate storage configuration."""

        if type(self.enabled) is not bool:
            raise ConfigValidationError("storage.enabled must be a boolean")
        if not isinstance(self.directory, str):
            raise ConfigValidationError("storage.directory must be a string")

        directory = self.directory.strip()
        if not directory:
            raise ConfigValidationError("storage.directory must not be empty")
        if "\x00" in directory:
            raise ConfigValidationError(
                "storage.directory must not contain NUL characters"
            )
        if type(self.flush_on_write) is not bool:
            raise ConfigValidationError("storage.flush_on_write must be a boolean")

        object.__setattr__(self, "directory", directory)


@dataclass(frozen=True, slots=True)
class CollectorRuntimeConfig:
    """Scheduling and execution policy for one trusted collector."""

    enabled: bool = True
    interval_seconds: float = 1.0
    initial_delay_seconds: float = 0.0
    budget_seconds: float | None = 0.25
    failure_backoff_initial_seconds: float = 1.0
    failure_backoff_max_seconds: float = 8.0

    def __post_init__(self) -> None:
        """Validate operational timing and bounded failure policy."""

        if type(self.enabled) is not bool:
            raise ConfigValidationError("collector.enabled must be a boolean")

        interval = _validate_runtime_seconds(
            self.interval_seconds,
            field_name="collector.interval_seconds",
            allow_zero=False,
            minimum_when_positive=_MIN_COLLECTOR_INTERVAL_SECONDS,
        )
        initial_delay = _validate_runtime_seconds(
            self.initial_delay_seconds,
            field_name="collector.initial_delay_seconds",
            allow_zero=True,
        )

        budget = self.budget_seconds
        if budget is not None:
            budget = _validate_runtime_seconds(
                budget,
                field_name="collector.budget_seconds",
                allow_zero=False,
            )

        backoff_initial = _validate_runtime_seconds(
            self.failure_backoff_initial_seconds,
            field_name="collector.failure_backoff_initial_seconds",
            allow_zero=True,
        )
        backoff_max = _validate_runtime_seconds(
            self.failure_backoff_max_seconds,
            field_name="collector.failure_backoff_max_seconds",
            allow_zero=True,
        )

        if backoff_initial == 0.0:
            if backoff_max != 0.0:
                raise ConfigValidationError(
                    "collector.failure_backoff_max_seconds must be zero "
                    "when failure backoff is disabled"
                )
        elif backoff_max < backoff_initial:
            raise ConfigValidationError(
                "collector.failure_backoff_max_seconds must be at least "
                "collector.failure_backoff_initial_seconds"
            )

        object.__setattr__(self, "interval_seconds", interval)
        object.__setattr__(self, "initial_delay_seconds", initial_delay)
        object.__setattr__(self, "budget_seconds", budget)
        object.__setattr__(
            self,
            "failure_backoff_initial_seconds",
            backoff_initial,
        )
        object.__setattr__(
            self,
            "failure_backoff_max_seconds",
            backoff_max,
        )


def _default_cpu_load() -> CollectorRuntimeConfig:
    return CollectorRuntimeConfig(
        interval_seconds=1.0,
        initial_delay_seconds=0.0,
        budget_seconds=0.25,
    )


def _default_memory() -> CollectorRuntimeConfig:
    return CollectorRuntimeConfig(
        interval_seconds=2.0,
        initial_delay_seconds=0.15,
        budget_seconds=0.10,
    )


def _default_filesystem() -> CollectorRuntimeConfig:
    return CollectorRuntimeConfig(
        interval_seconds=10.0,
        initial_delay_seconds=0.75,
        budget_seconds=0.50,
    )


def _default_disk_io() -> CollectorRuntimeConfig:
    return CollectorRuntimeConfig(
        interval_seconds=1.0,
        initial_delay_seconds=0.30,
        budget_seconds=0.25,
    )


def _default_network() -> CollectorRuntimeConfig:
    return CollectorRuntimeConfig(
        interval_seconds=1.0,
        initial_delay_seconds=0.45,
        budget_seconds=0.25,
    )


def _default_process() -> CollectorRuntimeConfig:
    return CollectorRuntimeConfig(
        interval_seconds=5.0,
        initial_delay_seconds=0.60,
        budget_seconds=1.0,
    )


@dataclass(frozen=True, slots=True)
class CollectorsConfig:
    """Explicit configuration for trusted built-in host collectors."""

    cpu_load: CollectorRuntimeConfig = field(default_factory=_default_cpu_load)
    memory: CollectorRuntimeConfig = field(default_factory=_default_memory)
    filesystem: CollectorRuntimeConfig = field(default_factory=_default_filesystem)
    disk_io: CollectorRuntimeConfig = field(default_factory=_default_disk_io)
    network: CollectorRuntimeConfig = field(default_factory=_default_network)
    process: CollectorRuntimeConfig = field(default_factory=_default_process)

    def __post_init__(self) -> None:
        """Require each built-in entry to use the typed collector model."""

        for name, runtime_config in self.items():
            if not isinstance(runtime_config, CollectorRuntimeConfig):
                raise ConfigValidationError(
                    f"collectors.{name} must be a CollectorRuntimeConfig"
                )

    def items(self) -> tuple[tuple[str, CollectorRuntimeConfig], ...]:
        """Return built-in collector settings in stable semantic order."""

        return (
            ("cpu_load", self.cpu_load),
            ("memory", self.memory),
            ("filesystem", self.filesystem),
            ("disk_io", self.disk_io),
            ("network", self.network),
            ("process", self.process),
        )

    def get(self, collector_name: str) -> CollectorRuntimeConfig:
        """Return one built-in collector configuration by exact name."""

        if not isinstance(collector_name, str):
            raise TypeError("collector_name must be a string")

        for name, runtime_config in self.items():
            if name == collector_name:
                return runtime_config
        raise KeyError(collector_name)


def _systemd_collector_name(unit_name: str, *, prefix: str) -> str:
    """Return a stable scheduler-safe identity for one systemd target."""

    stem = unit_name.removesuffix(".service")
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    digest = hashlib.sha256(unit_name.encode("utf-8")).hexdigest()[
        :_SYSTEMD_COLLECTOR_HASH_HEX_LENGTH
    ]
    max_stem_length = 64 - len(prefix) - 1 - _SYSTEMD_COLLECTOR_HASH_HEX_LENGTH
    safe_stem = safe_stem[:max_stem_length]
    return f"{prefix}{safe_stem}.{digest}"


def _systemd_service_collector_name(unit_name: str) -> str:
    """Return the scheduler identity for one systemd state collector."""

    return _systemd_collector_name(unit_name, prefix=_SYSTEMD_COLLECTOR_PREFIX)


def _systemd_journal_collector_name(unit_name: str) -> str:
    """Return the scheduler identity for one journald evidence collector."""

    return _systemd_collector_name(unit_name, prefix=_SYSTEMD_JOURNAL_COLLECTOR_PREFIX)


@dataclass(frozen=True, slots=True)
class SystemdServiceTargetConfig:
    """Read-only state and journald observation policy for one service."""

    unit_name: str
    enabled: bool = True
    interval_seconds: float = 5.0
    initial_delay_seconds: float = 1.0
    budget_seconds: float | None = 0.75
    failure_backoff_initial_seconds: float = 2.0
    failure_backoff_max_seconds: float = 16.0
    journal_enabled: bool = False
    journal_interval_seconds: float = 5.0
    journal_initial_delay_seconds: float = 1.25
    journal_budget_seconds: float | None = 1.0
    journal_failure_backoff_initial_seconds: float = 2.0
    journal_failure_backoff_max_seconds: float = 16.0
    journal_max_entries: int = 32

    def __post_init__(self) -> None:
        """Validate service identity and independent bounded runtime policies."""

        try:
            unit_name = validate_service_unit_name(
                self.unit_name,
                field_name="systemd service unit_name",
            )
        except SystemdUnitNameError as exc:
            raise ConfigValidationError(str(exc)) from exc

        state_runtime = CollectorRuntimeConfig(
            enabled=self.enabled,
            interval_seconds=self.interval_seconds,
            initial_delay_seconds=self.initial_delay_seconds,
            budget_seconds=self.budget_seconds,
            failure_backoff_initial_seconds=self.failure_backoff_initial_seconds,
            failure_backoff_max_seconds=self.failure_backoff_max_seconds,
        )
        if state_runtime.interval_seconds < _MIN_SYSTEMD_SERVICE_INTERVAL_SECONDS:
            raise ConfigValidationError(
                "systemd service interval_seconds must be at least "
                f"{_MIN_SYSTEMD_SERVICE_INTERVAL_SECONDS} seconds"
            )

        journal_runtime = CollectorRuntimeConfig(
            enabled=self.journal_enabled,
            interval_seconds=self.journal_interval_seconds,
            initial_delay_seconds=self.journal_initial_delay_seconds,
            budget_seconds=self.journal_budget_seconds,
            failure_backoff_initial_seconds=(
                self.journal_failure_backoff_initial_seconds
            ),
            failure_backoff_max_seconds=self.journal_failure_backoff_max_seconds,
        )
        if journal_runtime.interval_seconds < _MIN_SYSTEMD_JOURNAL_INTERVAL_SECONDS:
            raise ConfigValidationError(
                "systemd journal_interval_seconds must be at least "
                f"{_MIN_SYSTEMD_JOURNAL_INTERVAL_SECONDS} seconds"
            )

        if isinstance(self.journal_max_entries, bool) or not isinstance(
            self.journal_max_entries, int
        ):
            raise ConfigValidationError(
                "systemd journal_max_entries must be an integer"
            )
        if not 1 <= self.journal_max_entries <= MAX_SYSTEMD_JOURNAL_ENTRIES:
            raise ConfigValidationError(
                "systemd journal_max_entries must be between 1 and "
                f"{MAX_SYSTEMD_JOURNAL_ENTRIES}"
            )

        object.__setattr__(self, "unit_name", unit_name)
        object.__setattr__(self, "enabled", state_runtime.enabled)
        object.__setattr__(self, "interval_seconds", state_runtime.interval_seconds)
        object.__setattr__(
            self,
            "initial_delay_seconds",
            state_runtime.initial_delay_seconds,
        )
        object.__setattr__(self, "budget_seconds", state_runtime.budget_seconds)
        object.__setattr__(
            self,
            "failure_backoff_initial_seconds",
            state_runtime.failure_backoff_initial_seconds,
        )
        object.__setattr__(
            self,
            "failure_backoff_max_seconds",
            state_runtime.failure_backoff_max_seconds,
        )
        object.__setattr__(self, "journal_enabled", journal_runtime.enabled)
        object.__setattr__(
            self,
            "journal_interval_seconds",
            journal_runtime.interval_seconds,
        )
        object.__setattr__(
            self,
            "journal_initial_delay_seconds",
            journal_runtime.initial_delay_seconds,
        )
        object.__setattr__(
            self,
            "journal_budget_seconds",
            journal_runtime.budget_seconds,
        )
        object.__setattr__(
            self,
            "journal_failure_backoff_initial_seconds",
            journal_runtime.failure_backoff_initial_seconds,
        )
        object.__setattr__(
            self,
            "journal_failure_backoff_max_seconds",
            journal_runtime.failure_backoff_max_seconds,
        )

    @property
    def collector_name(self) -> str:
        """Return the deterministic scheduler identity for state observation."""

        return _systemd_service_collector_name(self.unit_name)

    @property
    def journal_collector_name(self) -> str:
        """Return the deterministic scheduler identity for journald evidence."""

        return _systemd_journal_collector_name(self.unit_name)

    @property
    def journal_runtime_config(self) -> CollectorRuntimeConfig:
        """Return the independent scheduling policy for journald evidence."""

        return CollectorRuntimeConfig(
            enabled=self.journal_enabled,
            interval_seconds=self.journal_interval_seconds,
            initial_delay_seconds=self.journal_initial_delay_seconds,
            budget_seconds=self.journal_budget_seconds,
            failure_backoff_initial_seconds=(
                self.journal_failure_backoff_initial_seconds
            ),
            failure_backoff_max_seconds=self.journal_failure_backoff_max_seconds,
        )


@dataclass(frozen=True, slots=True)
class SystemdConfig:
    """Bounded systemd observation and durable journal-state policy."""

    services: tuple[SystemdServiceTargetConfig, ...] = ()
    journal_checkpoint_enabled: bool = True
    journal_checkpoint_directory: str = "~/.local/state/sentinel-x/journal-checkpoints"

    def __post_init__(self) -> None:
        """Validate bounded targets and durable journal checkpoint policy."""

        if type(self.journal_checkpoint_enabled) is not bool:
            raise ConfigValidationError(
                "systemd.journal_checkpoint_enabled must be a boolean"
            )
        if not isinstance(self.journal_checkpoint_directory, str):
            raise ConfigValidationError(
                "systemd.journal_checkpoint_directory must be a string"
            )
        checkpoint_directory = self.journal_checkpoint_directory.strip()
        if not checkpoint_directory:
            raise ConfigValidationError(
                "systemd.journal_checkpoint_directory must not be empty"
            )
        if "\x00" in checkpoint_directory:
            raise ConfigValidationError(
                "systemd.journal_checkpoint_directory must not contain NUL characters"
            )
        object.__setattr__(
            self,
            "journal_checkpoint_directory",
            checkpoint_directory,
        )

        if not isinstance(self.services, tuple):
            raise ConfigValidationError("systemd.services must be a tuple")
        if len(self.services) > MAX_SYSTEMD_SERVICE_TARGETS:
            raise ConfigValidationError(
                "systemd.services must not contain more than "
                f"{MAX_SYSTEMD_SERVICE_TARGETS} targets"
            )

        unit_names: list[str] = []
        state_collector_names: list[str] = []
        journal_collector_names: list[str] = []
        for service in self.services:
            if not isinstance(service, SystemdServiceTargetConfig):
                raise ConfigValidationError(
                    "systemd.services entries must be SystemdServiceTargetConfig"
                )
            unit_names.append(service.unit_name)
            state_collector_names.append(service.collector_name)
            journal_collector_names.append(service.journal_collector_name)

        if len(set(unit_names)) != len(unit_names):
            raise ConfigValidationError(
                "systemd.services must not contain duplicate unit_name values"
            )
        if len(set(state_collector_names)) != len(state_collector_names):
            raise ConfigValidationError(
                "systemd service collector identities must be unique"
            )
        if len(set(journal_collector_names)) != len(journal_collector_names):
            raise ConfigValidationError(
                "systemd journal collector identities must be unique"
            )

    @property
    def journal_target_count(self) -> int:
        """Return the number of explicitly enabled journald targets."""

        return sum(service.journal_enabled for service in self.services)

    def items(self) -> tuple[tuple[str, SystemdServiceTargetConfig], ...]:
        """Return state-collector scheduler settings in declared order."""

        return tuple((service.collector_name, service) for service in self.services)

    def bindings(self) -> tuple[tuple[str, str], ...]:
        """Return state collector-name to requested-unit bindings."""

        return tuple(
            (service.collector_name, service.unit_name) for service in self.services
        )

    def journal_items(self) -> tuple[tuple[str, CollectorRuntimeConfig], ...]:
        """Return enabled journald scheduler settings in declared order."""

        return tuple(
            (service.journal_collector_name, service.journal_runtime_config)
            for service in self.services
            if service.journal_enabled
        )

    def journal_bindings(self) -> tuple[tuple[str, str, int], ...]:
        """Return enabled journald collector bindings in declared order."""

        return tuple(
            (
                service.journal_collector_name,
                service.unit_name,
                service.journal_max_entries,
            )
            for service in self.services
            if service.journal_enabled
        )


@dataclass(frozen=True, slots=True)
class SentinelConfig:
    """Root typed configuration for Sentinel-X."""

    agent: AgentConfig = field(default_factory=AgentConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    collectors: CollectorsConfig = field(default_factory=CollectorsConfig)
    systemd: SystemdConfig = field(default_factory=SystemdConfig)
