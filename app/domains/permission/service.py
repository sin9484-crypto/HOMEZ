"""
=========================================================
Homez OS

File : app/domains/permission/service.py
Version : 1.0.0

Permission Service
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.permission.model import Permission
from app.domains.permission.repository import (
    PermissionRepository,
)
from app.domains.permission.schema import PermissionCreate
from app.domains.permission.schema import PermissionUpdate


class PermissionService:

    def __init__(self, db: Session) -> None:

        self.db = db
        self.repository = PermissionRepository(db)

    def create_permission(
        self,
        data: PermissionCreate,
    ) -> Permission:

        permission = Permission(
            name=data.name,
            code=data.code,
            description=data.description,
            active=data.active,
        )

        return self.repository.create(permission)

    def get_permission(
        self,
        permission_id: int,
    ) -> Permission | None:

        return self.repository.get(permission_id)

    def get_permissions(self) -> list[Permission]:

        return self.repository.get_all()

    def update_permission(
        self,
        permission_id: int,
        data: PermissionUpdate,
    ) -> Permission | None:

        permission = self.repository.get(
            permission_id,
        )

        if permission is None:

            return None

        values = data.model_dump(exclude_unset=True)

        for key, value in values.items():

            setattr(permission, key, value)

        return self.repository.update(permission)

    def delete_permission(
        self,
        permission_id: int,
    ) -> bool:

        permission = self.repository.get(
            permission_id,
        )

        if permission is None:

            return False

        self.repository.delete(permission)

        return True
