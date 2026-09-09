from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

StartupCallable = Callable[
    ...,
    Any | Awaitable[Any],
]


class StartupManager:

    def __init__(self) -> None:
        self._tasks: list[
            StartupCallable
        ] = []

    def register(
        self,
        task: StartupCallable,
    ) -> StartupCallable:

        self._tasks.append(task)
        return task

    async def run(
        self,
    ) -> None:

        for task in self._tasks:

            result = task()

            if hasattr(
                result,
                "__await__",
            ):
                await result

    def clear(
        self,
    ) -> None:

        self._tasks.clear()

    @property
    def tasks(
        self,
    ) -> tuple[
        StartupCallable,
        ...,
    ]:

        return tuple(
            self._tasks
        )

    def __len__(
        self,
    ) -> int:

        return len(
            self._tasks
        )


startup_manager = StartupManager()


def startup() -> None:
    """
    애플리케이션 동기 기동 진입점.

    main.lifespan에서 호출한다.
    등록된 동기 작업만 실행하고,
    awaitable은 이후 async 경로로 확장한다.
    """

    for task in startup_manager.tasks:

        result = task()

        if hasattr(
            result,
            "__await__",
        ):
            continue


__all__ = [
    "StartupCallable",
    "StartupManager",
    "startup_manager",
    "startup",
]
