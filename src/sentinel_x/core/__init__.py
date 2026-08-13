"""Core domain types used throughout Sentinel-X."""

from __future__ import annotations

from sentinel_x.core.bus import (
    EventBus,
    HandlerFailure,
    PublishReport,
    Subscription,
)
from sentinel_x.core.engine import (
    EngineRunConflictError,
    EngineSnapshot,
    SentinelEngine,
)
from sentinel_x.core.events import (
    EventKind,
    EventSeverity,
    SentinelEvent,
)
from sentinel_x.core.scheduling import (
    CollectorDispatch,
    CollectorExecutionState,
    CollectorSchedule,
    CollectorScheduler,
    CollectorScheduleSnapshot,
    DuplicateScheduleError,
    ScheduleStateError,
    ScheduleValidationError,
    SchedulerClockError,
    SchedulingError,
    UnknownScheduleError,
)
from sentinel_x.core.state import (
    AgentLifecycle,
    AgentState,
    InvalidStateTransitionError,
)

__all__ = [
    "AgentLifecycle",
    "AgentState",
    "CollectorDispatch",
    "CollectorExecutionState",
    "CollectorSchedule",
    "CollectorScheduler",
    "CollectorScheduleSnapshot",
    "DuplicateScheduleError",
    "EngineRunConflictError",
    "EngineSnapshot",
    "EventBus",
    "EventKind",
    "EventSeverity",
    "HandlerFailure",
    "InvalidStateTransitionError",
    "PublishReport",
    "ScheduleStateError",
    "ScheduleValidationError",
    "SchedulerClockError",
    "SchedulingError",
    "SentinelEngine",
    "SentinelEvent",
    "Subscription",
    "UnknownScheduleError",
]
