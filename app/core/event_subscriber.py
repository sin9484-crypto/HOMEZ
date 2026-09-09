from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from app.core.event_types import Event


class EventSubscriber(ABC):

    def __init__(
        self,
        event_name: str,
    ) -> None:
        self._event_name = event_name

    @property
    def event_name(
        self,
    ) -> str:
        return self._event_name

    @abstractmethod
    def handle(
        self,
        event: Event,
    ) -> None:
        raise NotImplementedError
    def supports(
        self,
        event: Event,
    ) -> bool:

        return (
            event.name
            == self._event_name
        )

    def __call__(
        self,
        event: Event,
    ) -> None:

        if self.supports(event):
            self.handle(event)

    def __repr__(
        self,
    ) -> str:

        return (
            f"{self.__class__.__name__}"
            f"(event_name='{self._event_name}')"
        )
    def __eq__(
        self,
        other: object,
    ) -> bool:

        if not isinstance(
            other,
            EventSubscriber,
        ):
            return False

        return (
            self.__class__ is other.__class__
            and self._event_name == other._event_name
        )

    def __hash__(
        self,
    ) -> int:

        return hash(
            (
                self.__class__,
                self._event_name,
            )
        )
    def __str__(
        self,
    ) -> str:

        return (
            f"{self.__class__.__name__}"
            f"[{self._event_name}]"
        )


__all__ = [
    "EventSubscriber",
]           