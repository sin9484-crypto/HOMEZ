from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from app.core.event_manager import EventManager


class Kernel:

    def __init__(
        self,
        app: FastAPI,
    ) -> None:

        self.app = app
        self.events = EventManager()
        self._startup_tasks: list[
            Callable[..., Any]
        ] = []
        self._shutdown_tasks: list[
            Callable[..., Any]
        ] = []

    def add_startup_task(
        self,
        task: Callable[..., Any],
    ) -> None:

        self._startup_tasks.append(task)

    def add_shutdown_task(
        self,
        task: Callable[..., Any],
    ) -> None:

        self._shutdown_tasks.append(task)
    async def startup(
        self,
    ) -> None:

        for task in self._startup_tasks:

            result = task()

            if hasattr(
                result,
                "__await__",
            ):
                await result

    async def shutdown(
        self,
    ) -> None:

        for task in self._shutdown_tasks:

            result = task()

            if hasattr(
                result,
                "__await__",
            ):
                await result

    def publish(
        self,
        event,
    ) -> None:

        self.events.publish(
            event,
        )
    def subscribe(
        self,
        subscriber,
    ) -> None:

        self.events.subscribe(
            subscriber,
        )

    def unsubscribe(
        self,
        subscriber,
    ) -> None:

        self.events.unsubscribe(
            subscriber,
        )

    def clear_events(
        self,
    ) -> None:

        self.events.reset()

    @property
    def startup_tasks(
        self,
    ) -> tuple[
        Callable[..., Any],
        ...,
    ]:

        return tuple(
            self._startup_tasks
        )

    @property
    def shutdown_tasks(
        self,
    ) -> tuple[
        Callable[..., Any],
        ...,
    ]:

        return tuple(
            self._shutdown_tasks
        )
    def __len__(
        self,
    ) -> int:

        return len(
            self.events
        )

    def __contains__(
        self,
        event_name: str,
    ) -> bool:

        return (
            event_name
            in self.events
        )


__all__ = [
    "Kernel",
]                    