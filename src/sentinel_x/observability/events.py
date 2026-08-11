"""Event adapters for Sentinel-X host observations."""

from __future__ import annotations

from typing import Final

from sentinel_x.core import EventKind, SentinelEvent
from sentinel_x.observability.models import HostObservation


HOST_OBSERVATION_SOURCE: Final[str] = "sentinel_x.observability.host"
HOST_OBSERVATION_TYPE: Final[str] = "linux.host.cpu_load"


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
