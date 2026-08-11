"""Typed event model for the Sentinel-X control plane.

Every future subsystem -- collectors, detectors, diagnosis engines,
safety policies, remediation executors, and verifiers -- will
communicate through structured events rather than ad-hoc strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


class EventSeverity(StrEnum):
    """Severity assigned to a Sentinel-X event."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventKind(StrEnum):
    """High-level event categories used by the Sentinel-X pipeline."""

    AGENT_STARTED = "agent.started"
    AGENT_STOPPED = "agent.stopped"

    OBSERVATION = "observation"
    ANOMALY = "anomaly"
    DIAGNOSIS = "diagnosis"

    REMEDIATION_REQUESTED = "remediation.requested"
    REMEDIATION_EXECUTED = "remediation.executed"

    VERIFICATION = "verification"
    SAFETY_BLOCK = "safety.block"


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


@dataclass(
    frozen=True,
    slots=True,
)
class SentinelEvent:
    """Immutable event envelope used throughout Sentinel-X.

    SentinelEvent is the common information unit exchanged between
    collectors, detectors, diagnosis modules, remediation policies,
    executors, and verification components.

    Top-level attributes are copied into a read-only mapping during
    construction to reduce accidental mutation after an event has
    entered the Sentinel-X pipeline.
    """

    kind: EventKind

    source: str

    message: str

    severity: EventSeverity = EventSeverity.INFO

    attributes: Mapping[str, Any] = field(
        default_factory=dict,
    )

    event_id: str = field(
        default_factory=lambda: str(uuid4()),
    )

    occurred_at: datetime = field(
        default_factory=_utc_now,
    )

    def __post_init__(self) -> None:
        """Validate and normalize the event after construction."""

        source = self.source.strip()
        message = self.message.strip()

        if not source:
            raise ValueError(
                "event source must not be empty"
            )

        if not message:
            raise ValueError(
                "event message must not be empty"
            )

        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError(
                "occurred_at must be timezone-aware"
            )

        object.__setattr__(
            self,
            "source",
            source,
        )

        object.__setattr__(
            self,
            "message",
            message,
        )

        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(
                dict(self.attributes)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly event representation."""

        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "kind": self.kind.value,
            "severity": self.severity.value,
            "source": self.source,
            "message": self.message,
            "attributes": dict(self.attributes),
        }
