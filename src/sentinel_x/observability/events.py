"""Event adapters for Sentinel-X host observations."""

from __future__ import annotations

from typing import Final

from sentinel_x.core import EventKind, SentinelEvent
from sentinel_x.observability.filesystem import FilesystemObservation
from sentinel_x.observability.models import HostObservation, MemoryObservation


HOST_OBSERVATION_SOURCE: Final[str] = "sentinel_x.observability.host"
HOST_OBSERVATION_TYPE: Final[str] = "linux.host.cpu_load"

MEMORY_OBSERVATION_SOURCE: Final[str] = "sentinel_x.observability.memory"
MEMORY_OBSERVATION_TYPE: Final[str] = "linux.host.memory"

FILESYSTEM_OBSERVATION_SOURCE: Final[str] = "sentinel_x.observability.filesystem"
FILESYSTEM_OBSERVATION_TYPE: Final[str] = "linux.host.filesystem"


def host_observation_to_event(
    observation: HostObservation,
) -> SentinelEvent:
    """Convert one sampled host observation into a SentinelEvent."""

    attributes = observation.to_attributes()
    attributes["observation_type"] = HOST_OBSERVATION_TYPE

    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=HOST_OBSERVATION_SOURCE,
        message="Linux host CPU/load observation collected.",
        attributes=attributes,
        occurred_at=observation.captured_at,
    )


def memory_observation_to_event(
    observation: MemoryObservation,
) -> SentinelEvent:
    """Convert one memory observation into a SentinelEvent."""

    attributes = observation.to_attributes()
    attributes["observation_type"] = MEMORY_OBSERVATION_TYPE

    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=MEMORY_OBSERVATION_SOURCE,
        message="Linux host memory observation collected.",
        attributes=attributes,
        occurred_at=observation.captured_at,
    )


def filesystem_observation_to_event(
    observation: FilesystemObservation,
) -> SentinelEvent:
    """Convert one filesystem observation into a SentinelEvent."""

    attributes = observation.to_attributes()
    attributes["observation_type"] = FILESYSTEM_OBSERVATION_TYPE

    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=FILESYSTEM_OBSERVATION_SOURCE,
        message="Linux host filesystem observation collected.",
        attributes=attributes,
        occurred_at=observation.captured_at,
    )
