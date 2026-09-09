"""
=========================================================
Homez OS

File : base_service.py
Version : 5.0.0

Base Service
=========================================================
"""

from typing import Generic
from typing import TypeVar

from app.core.base_repository import BaseRepository

ModelType = TypeVar("ModelType")


class BaseService(Generic[ModelType]):

    def __init__(
        self,
        repository: BaseRepository[ModelType],
    ):

        self.repository = repository

    def get(
        self,
        object_id: int,
    ) -> ModelType | None:

        return self.repository.get(object_id)

    def get_all(
        self,
    ) -> list[ModelType]:

        return self.repository.get_all()
    def first(
        self,
    ) -> ModelType | None:

        return self.repository.first()


    def create(
        self,
        obj: ModelType,
    ) -> ModelType:

        return self.repository.create(obj)


    def update(
        self,
        obj: ModelType,
    ) -> ModelType:

        return self.repository.update(obj)


    def delete(
        self,
        obj: ModelType,
    ) -> None:

        self.repository.delete(obj)
    def exists(
        self,
        object_id: int,
    ) -> bool:

        return self.repository.exists(object_id)


    def count(
        self,
    ) -> int:

        return self.repository.count()


    def save(
        self,
        obj: ModelType,
    ) -> ModelType:

        return self.repository.save(obj)


    def refresh(
        self,
        obj: ModelType,
    ) -> None:

        self.repository.refresh(obj)


    def flush(
        self,
    ) -> None:

        self.repository.flush()
    def commit(
        self,
    ) -> None:

        self.repository.commit()


    def rollback(
        self,
    ) -> None:

        self.repository.rollback()


    def close(
        self,
    ) -> None:

        self.repository.close()


__all__ = [
    "BaseService",
    "ModelType",
]