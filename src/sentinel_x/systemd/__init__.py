"""Read-only systemd integration primitives for Sentinel-X."""

from sentinel_x.systemd.models import (
    SystemdActiveState,
    SystemdModelError,
    SystemdServiceSnapshot,
    SystemdUnitNameError,
    validate_service_unit_name,
)
from sentinel_x.systemd.reader import (
    SystemctlCommandResult,
    SystemctlCommandRunner,
    SystemctlServiceReader,
    SystemdCommandError,
    SystemdCommandTimeoutError,
    SystemdExecutableNotFoundError,
    SystemdProtocolError,
    SystemdReadError,
)

__all__ = [
    "SystemctlCommandResult",
    "SystemctlCommandRunner",
    "SystemctlServiceReader",
    "SystemdActiveState",
    "SystemdCommandError",
    "SystemdCommandTimeoutError",
    "SystemdExecutableNotFoundError",
    "SystemdModelError",
    "SystemdProtocolError",
    "SystemdReadError",
    "SystemdServiceSnapshot",
    "SystemdUnitNameError",
    "validate_service_unit_name",
]
