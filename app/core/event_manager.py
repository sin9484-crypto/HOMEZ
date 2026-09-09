from __future__ import annotations

from app.core.event_dispatcher import EventDispatcher
from app.core.event_subscriber import EventSubscriber
from app.core.event_types import Event


class EventManager:

    def __init__(self) -> None:
        self._dispatcher = EventDispatcher()

    @property
    def dispatcher(
        self,
    ) -> EventDispatcher:
        return self._dispatcher

    def subscribe(
        self,
        subscriber: EventSubscriber,
    ) -> None:

        self._dispatcher.subscribe(
            subscriber,
        )

    def unsubscribe(
        self,
        subscriber: EventSubscriber,
    ) -> None:

        self._dispatcher.unsubscribe(
            subscriber,
        )

    def publish(
        self,
        event: Event,
    ) -> None:

        self._dispatcher.dispatch(
            event,
        )
    def publish_many(
        self,
        events: list[Event],
    ) -> None:

        for event in events:
            self.publish(event)

    def has_subscribers(
        self,
        event_name: str,
    ) -> bool:

        return self._dispatcher.has_subscribers(
            event_name,
        )

    def subscriber_count(
        self,
        event_name: str | None = None,
    ) -> int:

        return self._dispatcher.subscriber_count(
            event_name,
        )

    @property
    def event_names(
        self,
    ) -> tuple[str, ...]:

        return self._dispatcher.event_names
    def clear(
        self,
        event_name: str | None = None,
    ) -> None:

        self._dispatcher.clear(
            event_name,
        )

    def reset(
        self,
    ) -> None:

        self._dispatcher.reset()

    def __contains__(
        self,
        event_name: str,
    ) -> bool:

        return (
            event_name
            in self._dispatcher
        )

    def __len__(
        self,
    ) -> int:

        return len(
            self._dispatcher
        )
    @property
    def subscribers(
        self,
    ) -> EventDispatcher:

        return self._dispatcher


__all__ = [
    "EventManager",
]           