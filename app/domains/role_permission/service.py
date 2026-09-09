"""
=========================================================
Homez OS

File : app/domains/role_permission/service.py
Version : 2.1.0

Role Permission Service
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.base_service import BaseService

from app.domains.role_permission.model import (
    RolePermission,
)
from app.domains.role_permission.repository import (
    RolePermissionRepository,
)
from app.domains.role_permission.schema import (
    RolePermissionCreate,
    RolePermissionUpdate,
)


class RolePermissionService(
    BaseService[RolePermissionRepository]
):

    def __init__(
        self,
        db: Session,
    ):
        super().__init__(
            db=db,
            repository=RolePermissionRepository(db),
        )

    # --------------------------------------------------
    # Create
    # --------------------------------------------------

    def create(
        self,
        data: RolePermissionCreate,
    ) -> RolePermission:

        if self.repository.exists(
            data.role_id,
            data.permission_id,
        ):
            raise ValueError(
                "RolePermission already exists."
            )

        entity = RolePermission(
            role_id=data.role_id,
            permission_id=data.permission_id,
        )

        return self.repository.create(entity)

    # --------------------------------------------------
    # Read
    # --------------------------------------------------

    def get(
        self,
        role_permission_id: int,
    ) -> RolePermission | None:

        return self.repository.get_by_id(
            role_permission_id
        )

    def get_all(
        self,
    ) -> list[RolePermission]:

        return self.repository.get_all()

    def get_by_role(
        self,
        role_id: int,
    ) -> list[RolePermission]:

        return self.repository.get_by_role(
            role_id
        )

    def get_by_permission(
        self,
        permission_id: int,
    ) -> list[RolePermission]:

        return self.repository.get_by_permission(
            permission_id
        )

    # --------------------------------------------------
    # Update
    # --------------------------------------------------

    def update(
        self,
        role_permission_id: int,
        data: RolePermissionUpdate,
    ) -> RolePermission | None:

        entity = self.repository.get_by_id(
            role_permission_id
        )

        if entity is None:
            return None

        values = data.model_dump(
            exclude_unset=True
        )

        if (
            "role_id" in values
            or "permission_id" in values
        ):

            role_id = values.get(
                "role_id",
                entity.role_id,
            )

            permission_id = values.get(
                "permission_id",
                entity.permission_id,
            )

            duplicate = self.repository.exists(
                role_id,
                permission_id,
            )

            if duplicate:

                same_entity = (
                    entity.role_id == role_id
                    and entity.permission_id
                    == permission_id
                )

                if not same_entity:
                    raise ValueError(
                        "RolePermission already exists."
                    )

        for key, value in values.items():
            setattr(
                entity,
                key,
                value,
            )

        return self.repository.update(
            entity
        )

    # --------------------------------------------------
    # Delete
    # --------------------------------------------------

    def delete(
        self,
        role_permission_id: int,
    ) -> bool:

        entity = self.repository.get_by_id(
            role_permission_id
        )

        if entity is None:
            return False

        self.repository.delete(
            entity
        )

        return True

    # --------------------------------------------------
    # Bulk Replace Permissions
    # --------------------------------------------------

    def replace_permissions(
        self,
        role_id: int,
        permission_ids: list[int],
    ) -> dict:

        permission_ids = list(
            dict.fromkeys(permission_ids)
        )

        try:

            self.repository.delete_by_role(
                role_id
            )

            self.repository.bulk_create(
                role_id,
                permission_ids,
            )

            self.repository.commit()

        except Exception:

            self.repository.rollback()

            raise

        return {
            "success": True,
            "role_id": role_id,
            "permission_count": len(
                permission_ids
            ),
            "permission_ids": permission_ids,
            "message": "Permissions updated successfully.",
        }