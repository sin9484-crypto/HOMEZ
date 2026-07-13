"""
HOMEZ AI Commerce OS
Build 06

Base Repository

모든 Repository가 상속받는 기본 클래스
"""

from typing import Generic, TypeVar
from sqlalchemy.orm import Session

T = TypeVar("T")


class BaseRepository(Generic[T]):

    def __init__(self, db: Session):
        self.db = db

    def add(self, entity: T) -> T:
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        return entity

    def delete(self, entity: T) -> None:
        self.db.delete(entity)
        self.db.commit()

    def save(self) -> None:
        self.db.commit()

    def refresh(self, entity: T) -> T:
        self.db.refresh(entity)
        return entity