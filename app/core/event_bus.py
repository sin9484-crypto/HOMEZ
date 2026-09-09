from __future__ import annotations

from app.core.event_dispatcher import EventDispatcher
from app.core.event_subscriber import EventSubscriber
from app.core.event_types import Event


class EventBus:
    """
    Homez 전역 Event Bus
    """

    def __init__(self) -> None:
        self._dispatcher = EventDispatcher()

    def subscribe(
        self,
        subscriber: EventSubscriber,
    ) -> None:

        self._dispatcher.subscribe(subscriber)

    def unsubscribe(
        self,
        subscriber: EventSubscriber,
    ) -> None:

        self._dispatcher.unsubscribe(subscriber)

    def publish(
        self,
        event: Event,
    ) -> None:

        self._dispatcher.dispatch(event)
    def publish_many(
        self,
        events: list[Event],
    ) -> None:

        for event in events:
            self.publish(event)

    def clear(
        self,
    ) -> None:

        self._dispatcher.clear()

    @property
    def subscriber_count(
        self,
    ) -> int:

        return self._dispatcher.subscriber_count
    @property
    def dispatcher(
        self,
    ) -> EventDispatcher:

        return self._dispatcher


    def has_subscribers(
        self,
    ) -> bool:

        return self.subscriber_count > 0


    def reset(
        self,
    ) -> None:

        self.clear()
__all__ = [
    "EventBus",
]       