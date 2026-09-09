"""
=========================================================
Homez OS

File : base_repository.py
Version : 5.0.0

Base Repository
=========================================================
"""

from typing import Generic
from typing import TypeVar

from sqlalchemy.orm import Session

ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):

    def __init__(
        self,
        db: Session,
        model: type[ModelType],
    ):

        self.db = db
        self.model = model

    def get(
        self,
        object_id: int,
    ) -> ModelType | None:

        return (
            self.db.query(self.model)
            .filter(self.model.id == object_id)
            .first()
        )

    def get_all(
        self,
    ) -> list[ModelType]:

        return (
            self.db.query(self.model)
            .all()
        )
    def first(
        self,
    ) -> ModelType | None:

        return (
            self.db.query(self.model)
            .first()
        )


    def create(
        self,
        obj: ModelType,
    ) -> ModelType:

        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)

        return obj


    def update(
        self,
        obj: ModelType,
    ) -> ModelType:

        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)

        return obj


    def delete(
        self,
        obj: ModelType,
    ) -> None:

        self.db.delete(obj)
        self.db.commit()
    def exists(
        self,
        object_id: int,
    ) -> bool:

        return (
            self.db.query(self.model)
            .filter(self.model.id == object_id)
            .first()
            is not None
        )


    def count(
        self,
    ) -> int:

        return (
            self.db.query(self.model)
            .count()
        )


    def save(
        self,
        obj: ModelType,
    ) -> ModelType:

        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)

        return obj


    def flush(
        self,
    ) -> None:

        self.db.flush()


    def refresh(
        self,
        obj: ModelType,
    ) -> None:

        self.db.refresh(obj)
    def rollback(
        self,
    ) -> None:

        self.db.rollback()


    def commit(
        self,
    ) -> None:

        self.db.commit()


    def expunge(
        self,
        obj: ModelType,
    ) -> None:

        self.db.expunge(obj)


    def close(
        self,
    ) -> None:

        self.db.close()


__all__ = [
    "BaseRepository",
    "ModelType",
]    