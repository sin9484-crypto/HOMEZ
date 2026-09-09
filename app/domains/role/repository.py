"""
=========================================================
Homez OS

File : app/domains/role/repository.py
Version : 1.0.0

Role Repository
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository

from app.domains.role.model import Role


class RoleRepository(BaseRepository[Role]):

    def __init__(self, db: Session) -> None:

        super().__init__(db, Role)

    def create(self, obj: Role) -> Role:

        return super().create(obj)

    def get(self, object_id: int) -> Role | None:

        return super().get(object_id)

    def get_all(self) -> list[Role]:

        return super().get_all()

    def update(self, obj: Role) -> Role:

        return super().update(obj)

    def delete(self, obj: Role) -> None:

        self.db.delete(obj)
        self.db.commit()

    def get_by_code(self, code: str) -> Role | None:

        return (
            self.db.query(Role)
            .filter(Role.code == code)
            .first()
        )

    def get_by_name(self, name: str) -> Role | None:

        return (
            self.db.query(Role)
            .filter(Role.name == name)
            .first()
        )
