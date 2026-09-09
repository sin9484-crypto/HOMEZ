from __future__ import annotations

from collections import defaultdict

from app.core.event_subscriber import EventSubscriber
from app.core.event_types import Event


class EventDispatcher:

    def __init__(self) -> None:
        self._subscribers: dict[
            str,
            list[EventSubscriber],
        ] = defaultdict(list)

    def subscribe(
        self,
        subscriber: EventSubscriber,
    ) -> None:

        self._subscribers[
            subscriber.event_name
        ].append(subscriber)

    def unsubscribe(
        self,
        subscriber: EventSubscriber,
    ) -> None:

        subscribers = self._subscribers.get(
            subscriber.event_name,
            [],
        )

        if subscriber in subscribers:
            subscribers.remove(subscriber)
    def dispatch(
        self,
        event: Event,
    ) -> None:

        subscribers = list(
            self._subscribers.get(
                event.name,
                [],
            )
        )

        for subscriber in subscribers:
            subscriber.handle(event)

    def dispatch_many(
        self,
        events: list[Event],
    ) -> None:

        for event in events:
            self.dispatch(event)

    def clear(
        self,
        event_name: str | None = None,
    ) -> None:

        if event_name is None:
            self._subscribers.clear()
            return

        self._subscribers.pop(
            event_name,
            None,
        )
    def has_subscribers(
        self,
        event_name: str,
    ) -> bool:

        return bool(
            self._subscribers.get(
                event_name,
            )
        )

    def subscriber_count(
        self,
        event_name: str | None = None,
    ) -> int:

        if event_name is not None:
            return len(
                self._subscribers.get(
                    event_name,
                    [],
                )
            )

        return sum(
            len(subscribers)
            for subscribers in self._subscribers.values()
        )

    @property
    def event_names(
        self,
    ) -> tuple[str, ...]:

        return tuple(
            self._subscribers.keys()
        )
    def reset(self) -> None:
        self._subscribers.clear()

    def __contains__(
        self,
        event_name: str,
    ) -> bool:

        return event_name in self._subscribers

    def __len__(self) -> int:
        return sum(
            len(subscribers)
            for subscribers in self._subscribers.values()
        )


__all__ = [
    "EventDispatcher",
]                