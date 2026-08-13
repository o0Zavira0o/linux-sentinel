"""TOML configuration loading for Sentinel-X."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from sentinel_x.config.models import (
    BUILTIN_HOST_COLLECTOR_NAMES,
    AgentConfig,
    CollectorRuntimeConfig,
    CollectorsConfig,
    ConfigValidationError,
    SentinelConfig,
    StorageConfig,
    SystemdConfig,
    SystemdServiceTargetConfig,
)

DEFAULT_CONFIG_FILENAME = "sentinel.toml"
MAX_CONFIG_BYTES = 1_048_576


class ConfigError(RuntimeError):
    """Base class for Sentinel-X configuration loading errors."""


class ConfigFileError(ConfigError):
    """Raised when a configuration file cannot be safely read."""


class ConfigParseError(ConfigError):
    """Raised when TOML syntax is invalid."""


class ConfigSchemaError(ConfigError):
    """Raised when parsed configuration violates the schema."""


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """Typed configuration plus information about its source."""

    config: SentinelConfig
    source_path: Path | None

    @property
    def source_label(self) -> str:
        """Return a human-readable configuration source."""

        if self.source_path is None:
            return "<built-in defaults>"
        return str(self.source_path)

    @property
    def base_directory(self) -> Path:
        """Return the base directory for relative config paths."""

        if self.source_path is not None:
            return self.source_path.parent
        return Path.cwd()

    def resolve_path(self, value: str | Path) -> Path:
        """Resolve a path originating from configuration."""

        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.base_directory / candidate

        try:
            return candidate.resolve()
        except OSError:
            return candidate.absolute()


def load_config(path: str | Path | None = None) -> LoadedConfig:
    """Load Sentinel-X configuration from TOML or built-in defaults."""

    if path is None:
        explicit_path = False
        config_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    else:
        explicit_path = True
        config_path = Path(path).expanduser()

    if not config_path.exists():
        if explicit_path:
            raise ConfigFileError(f"configuration file does not exist: {config_path}")
        return LoadedConfig(config=SentinelConfig(), source_path=None)

    if not config_path.is_file():
        raise ConfigFileError(
            f"configuration path is not a regular file: {config_path}"
        )

    try:
        file_size = config_path.stat().st_size
    except OSError as exc:
        raise ConfigFileError(
            f"could not inspect configuration file: {config_path}: {exc}"
        ) from exc

    if file_size > MAX_CONFIG_BYTES:
        raise ConfigFileError(
            "configuration file exceeds the "
            f"{MAX_CONFIG_BYTES}-byte safety limit: {config_path}"
        )

    try:
        with config_path.open("rb") as file_handle:
            raw_config = tomllib.load(file_handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError(
            f"invalid TOML configuration in {config_path}: {exc}"
        ) from exc
    except OSError as exc:
        raise ConfigFileError(
            f"could not read configuration file {config_path}: {exc}"
        ) from exc

    config = _parse_root(raw_config)

    try:
        resolved_path = config_path.resolve()
    except OSError:
        resolved_path = config_path.absolute()

    return LoadedConfig(config=config, source_path=resolved_path)


def _parse_root(raw_config: Mapping[str, Any]) -> SentinelConfig:
    """Parse and validate the root configuration mapping."""

    _reject_unknown_keys(
        mapping=raw_config,
        allowed={"agent", "storage", "collectors", "systemd"},
        context="root",
    )

    raw_agent = raw_config.get("agent", {})
    raw_storage = raw_config.get("storage", {})
    raw_collectors = raw_config.get("collectors", {})
    raw_systemd = raw_config.get("systemd", {})

    if not isinstance(raw_agent, dict):
        raise ConfigSchemaError("[agent] must be a TOML table")
    if not isinstance(raw_storage, dict):
        raise ConfigSchemaError("[storage] must be a TOML table")
    if not isinstance(raw_collectors, dict):
        raise ConfigSchemaError("[collectors] must be a TOML table")
    if not isinstance(raw_systemd, dict):
        raise ConfigSchemaError("[systemd] must be a TOML table")

    return SentinelConfig(
        agent=_parse_agent(raw_agent),
        storage=_parse_storage(raw_storage),
        collectors=_parse_collectors(raw_collectors),
        systemd=_parse_systemd(raw_systemd),
    )


def _parse_agent(raw_agent: Mapping[str, Any]) -> AgentConfig:
    """Parse the [agent] configuration section."""

    _reject_unknown_keys(
        mapping=raw_agent,
        allowed={"instance_name", "tick_interval"},
        context="agent",
    )

    try:
        return AgentConfig(
            instance_name=raw_agent.get("instance_name", "sentinel-x"),
            tick_interval=raw_agent.get("tick_interval", 0.5),
        )
    except ConfigValidationError as exc:
        raise ConfigSchemaError(str(exc)) from exc


def _parse_storage(raw_storage: Mapping[str, Any]) -> StorageConfig:
    """Parse the [storage] configuration section."""

    _reject_unknown_keys(
        mapping=raw_storage,
        allowed={"enabled", "directory", "flush_on_write"},
        context="storage",
    )

    try:
        return StorageConfig(
            enabled=raw_storage.get("enabled", True),
            directory=raw_storage.get(
                "directory",
                "~/.local/state/sentinel-x/events",
            ),
            flush_on_write=raw_storage.get("flush_on_write", True),
        )
    except ConfigValidationError as exc:
        raise ConfigSchemaError(str(exc)) from exc


def _parse_collectors(raw_collectors: Mapping[str, Any]) -> CollectorsConfig:
    """Parse explicit trusted collector runtime configuration."""

    _reject_unknown_keys(
        mapping=raw_collectors,
        allowed=set(BUILTIN_HOST_COLLECTOR_NAMES),
        context="collectors",
    )

    defaults = CollectorsConfig()
    return CollectorsConfig(
        cpu_load=_parse_collector_entry(
            raw_collectors,
            "cpu_load",
            defaults.cpu_load,
        ),
        memory=_parse_collector_entry(
            raw_collectors,
            "memory",
            defaults.memory,
        ),
        filesystem=_parse_collector_entry(
            raw_collectors,
            "filesystem",
            defaults.filesystem,
        ),
        disk_io=_parse_collector_entry(
            raw_collectors,
            "disk_io",
            defaults.disk_io,
        ),
        network=_parse_collector_entry(
            raw_collectors,
            "network",
            defaults.network,
        ),
        process=_parse_collector_entry(
            raw_collectors,
            "process",
            defaults.process,
        ),
    )


def _parse_collector_entry(
    raw_collectors: Mapping[str, Any],
    collector_name: str,
    defaults: CollectorRuntimeConfig,
) -> CollectorRuntimeConfig:
    """Parse one [collectors.<name>] table with typed defaults."""

    raw_entry = raw_collectors.get(collector_name, {})
    context = f"collectors.{collector_name}"

    if not isinstance(raw_entry, dict):
        raise ConfigSchemaError(f"[{context}] must be a TOML table")

    _reject_unknown_keys(
        mapping=raw_entry,
        allowed={
            "enabled",
            "interval_seconds",
            "initial_delay_seconds",
            "budget_seconds",
            "failure_backoff_initial_seconds",
            "failure_backoff_max_seconds",
        },
        context=context,
    )

    try:
        return CollectorRuntimeConfig(
            enabled=raw_entry.get("enabled", defaults.enabled),
            interval_seconds=raw_entry.get(
                "interval_seconds",
                defaults.interval_seconds,
            ),
            initial_delay_seconds=raw_entry.get(
                "initial_delay_seconds",
                defaults.initial_delay_seconds,
            ),
            budget_seconds=raw_entry.get(
                "budget_seconds",
                defaults.budget_seconds,
            ),
            failure_backoff_initial_seconds=raw_entry.get(
                "failure_backoff_initial_seconds",
                defaults.failure_backoff_initial_seconds,
            ),
            failure_backoff_max_seconds=raw_entry.get(
                "failure_backoff_max_seconds",
                defaults.failure_backoff_max_seconds,
            ),
        )
    except ConfigValidationError as exc:
        raise ConfigSchemaError(f"{context}: {exc}") from exc


def _parse_systemd(raw_systemd: Mapping[str, Any]) -> SystemdConfig:
    """Parse bounded read-only systemd service observation targets."""

    _reject_unknown_keys(
        mapping=raw_systemd,
        allowed={"services"},
        context="systemd",
    )
    raw_services = raw_systemd.get("services", [])
    if not isinstance(raw_services, list):
        raise ConfigSchemaError("systemd.services must be an array of tables")

    services: list[SystemdServiceTargetConfig] = []
    for index, raw_service in enumerate(raw_services):
        services.append(_parse_systemd_service_target(raw_service, index=index))

    try:
        return SystemdConfig(services=tuple(services))
    except ConfigValidationError as exc:
        raise ConfigSchemaError(str(exc)) from exc


def _parse_systemd_service_target(
    raw_service: object,
    *,
    index: int,
) -> SystemdServiceTargetConfig:
    """Parse one [[systemd.services]] target with strict keys and defaults."""

    context = f"systemd.services[{index}]"
    if not isinstance(raw_service, dict):
        raise ConfigSchemaError(f"{context} must be a TOML table")
    _reject_unknown_keys(
        mapping=raw_service,
        allowed={
            "unit_name",
            "enabled",
            "interval_seconds",
            "initial_delay_seconds",
            "budget_seconds",
            "failure_backoff_initial_seconds",
            "failure_backoff_max_seconds",
        },
        context=context,
    )

    try:
        return SystemdServiceTargetConfig(
            unit_name=raw_service.get("unit_name", ""),
            enabled=raw_service.get("enabled", True),
            interval_seconds=raw_service.get("interval_seconds", 5.0),
            initial_delay_seconds=raw_service.get(
                "initial_delay_seconds",
                1.0,
            ),
            budget_seconds=raw_service.get("budget_seconds", 0.75),
            failure_backoff_initial_seconds=raw_service.get(
                "failure_backoff_initial_seconds",
                2.0,
            ),
            failure_backoff_max_seconds=raw_service.get(
                "failure_backoff_max_seconds",
                16.0,
            ),
        )
    except ConfigValidationError as exc:
        raise ConfigSchemaError(f"{context}: {exc}") from exc


def _reject_unknown_keys(
    *,
    mapping: Mapping[str, Any],
    allowed: set[str],
    context: str,
) -> None:
    """Reject unknown keys instead of silently ignoring typos."""

    unknown = sorted(set(mapping) - allowed)
    if not unknown:
        return

    names = ", ".join(repr(name) for name in unknown)
    raise ConfigSchemaError(f"unknown configuration key(s) in {context}: {names}")
