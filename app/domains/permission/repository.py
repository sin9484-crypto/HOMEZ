"""
=========================================================
Homez OS

File : app/domains/permission/repository.py
Version : 1.0.0

Permission Repository
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository

from app.domains.permission.model import Permission


class PermissionRepository(BaseRepository[Permission]):

    def __init__(self, db: Session) -> None:

        super().__init__(db, Permission)

    def create(self, obj: Permission) -> Permission:

        return super().create(obj)

    def get(self, object_id: int) -> Permission | None:

        return super().get(object_id)

    def get_all(self) -> list[Permission]:

        return super().get_all()

    def update(self, obj: Permission) -> Permission:

        return super().update(obj)

    def delete(self, obj: Permission) -> None:

        self.db.delete(obj)
        self.db.commit()

    def get_by_code(self, code: str) -> Permission | None:

        return (
            self.db.query(Permission)
            .filter(Permission.code == code)
            .first()
        )
