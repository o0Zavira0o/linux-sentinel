"""In-process event bus for Sentinel-X core components."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from sentinel_x.core.events import SentinelEvent


EventHandler = Callable[[SentinelEvent], None]


@dataclass(
    frozen=True,
    slots=True,
)
class Subscription:
    """Opaque handle identifying one EventBus subscription."""

    subscription_id: str


@dataclass(
    frozen=True,
    slots=True,
)
class HandlerFailure:
    """Description of a subscriber failure during publication."""

    subscription_id: str
    handler_name: str
    error_type: str
    error_message: str


@dataclass(
    frozen=True,
    slots=True,
)
class PublishReport:
    """Result of publishing one event to current subscribers."""

    event_id: str
    delivered: int
    failures: tuple[HandlerFailure, ...]

    @property
    def succeeded(self) -> bool:
        """Return True when every selected subscriber succeeded."""

        return not self.failures


class EventBus:
    """Thread-safe, failure-isolating in-process event bus.

    Subscriber exceptions are captured in a PublishReport instead
    of being allowed to prevent delivery to later subscribers.
    """

    def __init__(self) -> None:
        self._handlers: dict[
            str,
            EventHandler,
        ] = {}

        self._lock = RLock()

    @property
    def subscriber_count(self) -> int:
        """Return the number of active subscribers."""

        with self._lock:
            return len(self._handlers)

    def subscribe(
        self,
        handler: EventHandler,
    ) -> Subscription:
        """Register handler and return its subscription handle."""

        if not callable(handler):
            raise TypeError(
                "event handler must be callable"
            )

        subscription = Subscription(
            subscription_id=str(uuid4())
        )

        with self._lock:
            self._handlers[
                subscription.subscription_id
            ] = handler

        return subscription

    def unsubscribe(
        self,
        subscription: Subscription,
    ) -> bool:
        """Remove a subscription.

        Returns True if the subscription existed and was removed.
        """

        with self._lock:
            return (
                self._handlers.pop(
                    subscription.subscription_id,
                    None,
                )
                is not None
            )

    def publish(
        self,
        event: SentinelEvent,
    ) -> PublishReport:
        """Publish event to a stable snapshot of subscribers."""

        if not isinstance(
            event,
            SentinelEvent,
        ):
            raise TypeError(
                "EventBus.publish() requires "
                "a SentinelEvent"
            )

        with self._lock:
            handlers = tuple(
                self._handlers.items()
            )

        failures: list[
            HandlerFailure
        ] = []

        delivered = 0

        for (
            subscription_id,
            handler,
        ) in handlers:
            try:
                handler(event)

            except Exception as exc:
                failures.append(
                    HandlerFailure(
                        subscription_id=(
                            subscription_id
                        ),
                        handler_name=(
                            self._handler_name(
                                handler
                            )
                        ),
                        error_type=(
                            type(exc).__name__
                        ),
                        error_message=str(exc),
                    )
                )

            else:
                delivered += 1

        return PublishReport(
            event_id=event.event_id,
            delivered=delivered,
            failures=tuple(failures),
        )

    @staticmethod
    def _handler_name(
        handler: EventHandler,
    ) -> str:
        """Return a useful display name for a handler."""

        return getattr(
            handler,
            "__qualname__",
            handler.__class__.__qualname__,
        )
