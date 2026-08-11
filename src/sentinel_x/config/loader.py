"""TOML configuration loading for Sentinel-X."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from sentinel_x.config.models import (
    AgentConfig,
    ConfigValidationError,
    SentinelConfig,
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


@dataclass(
    frozen=True,
    slots=True,
)
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


def load_config(
    path: str | Path | None = None,
) -> LoadedConfig:
    """Load Sentinel-X configuration.

    If path is omitted, ``sentinel.toml`` in the current working
    directory is used when present. Otherwise built-in defaults
    are returned.
    """

    explicit_path = path is not None

    config_path = (
        Path(path).expanduser()
        if explicit_path
        else Path.cwd() / DEFAULT_CONFIG_FILENAME
    )

    if not config_path.exists():
        if explicit_path:
            raise ConfigFileError(
                "configuration file does not exist: "
                f"{config_path}"
            )

        return LoadedConfig(
            config=SentinelConfig(),
            source_path=None,
        )

    if not config_path.is_file():
        raise ConfigFileError(
            "configuration path is not a regular file: "
            f"{config_path}"
        )

    try:
        file_size = config_path.stat().st_size
    except OSError as exc:
        raise ConfigFileError(
            "could not inspect configuration file: "
            f"{config_path}: {exc}"
        ) from exc

    if file_size > MAX_CONFIG_BYTES:
        raise ConfigFileError(
            "configuration file exceeds the "
            f"{MAX_CONFIG_BYTES}-byte safety limit: "
            f"{config_path}"
        )

    try:
        with config_path.open("rb") as file_handle:
            raw_config = tomllib.load(
                file_handle
            )
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError(
            "invalid TOML configuration in "
            f"{config_path}: {exc}"
        ) from exc
    except OSError as exc:
        raise ConfigFileError(
            "could not read configuration file "
            f"{config_path}: {exc}"
        ) from exc

    config = _parse_root(
        raw_config
    )

    try:
        resolved_path = (
            config_path.resolve()
        )
    except OSError:
        resolved_path = (
            config_path.absolute()
        )

    return LoadedConfig(
        config=config,
        source_path=resolved_path,
    )


def _parse_root(
    raw_config: Mapping[str, Any],
) -> SentinelConfig:
    """Parse and validate the root configuration mapping."""

    _reject_unknown_keys(
        mapping=raw_config,
        allowed={"agent"},
        context="root",
    )

    raw_agent = raw_config.get(
        "agent",
        {},
    )

    if not isinstance(
        raw_agent,
        dict,
    ):
        raise ConfigSchemaError(
            "[agent] must be a TOML table"
        )

    agent = _parse_agent(
        raw_agent
    )

    return SentinelConfig(
        agent=agent
    )


def _parse_agent(
    raw_agent: Mapping[str, Any],
) -> AgentConfig:
    """Parse the [agent] configuration section."""

    _reject_unknown_keys(
        mapping=raw_agent,
        allowed={
            "instance_name",
            "tick_interval",
        },
        context="agent",
    )

    try:
        return AgentConfig(
            instance_name=raw_agent.get(
                "instance_name",
                "sentinel-x",
            ),
            tick_interval=raw_agent.get(
                "tick_interval",
                0.5,
            ),
        )
    except ConfigValidationError as exc:
        raise ConfigSchemaError(
            str(exc)
        ) from exc


def _reject_unknown_keys(
    *,
    mapping: Mapping[str, Any],
    allowed: set[str],
    context: str,
) -> None:
    """Reject unknown keys instead of silently ignoring typos."""

    unknown = sorted(
        set(mapping) - allowed
    )

    if not unknown:
        return

    names = ", ".join(
        repr(name)
        for name in unknown
    )

    raise ConfigSchemaError(
        f"unknown configuration key(s) "
        f"in {context}: {names}"
    )
