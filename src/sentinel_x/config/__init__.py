"""Configuration subsystem for Sentinel-X."""

from __future__ import annotations

from sentinel_x.config.loader import (
    DEFAULT_CONFIG_FILENAME,
    MAX_CONFIG_BYTES,
    ConfigError,
    ConfigFileError,
    ConfigParseError,
    ConfigSchemaError,
    LoadedConfig,
    load_config,
)
from sentinel_x.config.models import (
    BUILTIN_HOST_COLLECTOR_NAMES,
    MAX_SYSTEMD_JOURNAL_ENTRIES,
    MAX_SYSTEMD_SERVICE_TARGETS,
    AgentConfig,
    CollectorRuntimeConfig,
    CollectorsConfig,
    ConfigValidationError,
    SentinelConfig,
    StorageConfig,
    SystemdConfig,
    SystemdServiceTargetConfig,
)

__all__ = [
    "AgentConfig",
    "BUILTIN_HOST_COLLECTOR_NAMES",
    "MAX_SYSTEMD_JOURNAL_ENTRIES",
    "MAX_SYSTEMD_SERVICE_TARGETS",
    "CollectorRuntimeConfig",
    "CollectorsConfig",
    "ConfigError",
    "ConfigFileError",
    "ConfigParseError",
    "ConfigSchemaError",
    "ConfigValidationError",
    "DEFAULT_CONFIG_FILENAME",
    "LoadedConfig",
    "MAX_CONFIG_BYTES",
    "SentinelConfig",
    "StorageConfig",
    "SystemdConfig",
    "SystemdServiceTargetConfig",
    "load_config",
]
