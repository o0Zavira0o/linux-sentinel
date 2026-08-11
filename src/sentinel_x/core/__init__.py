"""Core domain types used throughout Sentinel-X."""

from __future__ import annotations

from sentinel_x.core.events import (
    EventKind,
    EventSeverity,
    SentinelEvent,
)


__all__ = [
    "EventKind",
    "EventSeverity",
    "SentinelEvent",
]
