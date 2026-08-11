"""Typed configuration models for Sentinel-X."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


class ConfigValidationError(ValueError):
    """Raised when a typed Sentinel-X configuration value is invalid."""


_INSTANCE_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)


def _validate_tick_interval(value: object) -> float:
    """Validate the core loop wake-up interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(
            "agent.tick_interval must be a number"
        )

    normalized = float(value)

    if not math.isfinite(normalized):
        raise ConfigValidationError(
            "agent.tick_interval must be finite"
        )

    if not 0.05 <= normalized <= 60.0:
        raise ConfigValidationError(
            "agent.tick_interval must be between "
            "0.05 and 60.0 seconds"
        )

    return normalized


@dataclass(
    frozen=True,
    slots=True,
)
class AgentConfig:
    """Runtime settings currently consumed by Sentinel-X."""

    instance_name: str = "sentinel-x"
    tick_interval: float = 0.5

    def __post_init__(self) -> None:
        """Normalize and validate agent configuration."""

        if not isinstance(self.instance_name, str):
            raise ConfigValidationError(
                "agent.instance_name must be a string"
            )

        instance_name = self.instance_name.strip()

        if not _INSTANCE_NAME_PATTERN.fullmatch(instance_name):
            raise ConfigValidationError(
                "agent.instance_name must be 1-64 characters "
                "and contain only letters, digits, '.', '_', "
                "or '-', starting with a letter or digit"
            )

        tick_interval = _validate_tick_interval(
            self.tick_interval
        )

        object.__setattr__(
            self,
            "instance_name",
            instance_name,
        )

        object.__setattr__(
            self,
            "tick_interval",
            tick_interval,
        )


@dataclass(
    frozen=True,
    slots=True,
)
class StorageConfig:
    """Durable event-storage settings for Sentinel-X."""

    enabled: bool = True

    directory: str = (
        "~/.local/state/sentinel-x/events"
    )

    flush_on_write: bool = True

    def __post_init__(self) -> None:
        """Normalize and validate storage configuration."""

        if type(self.enabled) is not bool:
            raise ConfigValidationError(
                "storage.enabled must be a boolean"
            )

        if not isinstance(self.directory, str):
            raise ConfigValidationError(
                "storage.directory must be a string"
            )

        directory = self.directory.strip()

        if not directory:
            raise ConfigValidationError(
                "storage.directory must not be empty"
            )

        if "\x00" in directory:
            raise ConfigValidationError(
                "storage.directory must not contain "
                "NUL characters"
            )

        if type(self.flush_on_write) is not bool:
            raise ConfigValidationError(
                "storage.flush_on_write must be a boolean"
            )

        object.__setattr__(
            self,
            "directory",
            directory,
        )


@dataclass(
    frozen=True,
    slots=True,
)
class SentinelConfig:
    """Root typed configuration for Sentinel-X."""

    agent: AgentConfig = field(
        default_factory=AgentConfig
    )

    storage: StorageConfig = field(
        default_factory=StorageConfig
    )
