"""
=========================================================
Homez OS

File : app/domains/role_permission/repository.py
Version : 2.1.0

Role Permission Repository
=========================================================
"""

from sqlalchemy.orm import Session

from app.core.base_repository import BaseRepository

from app.domains.role_permission.model import (
    RolePermission,
)


class RolePermissionRepository(
    BaseRepository[RolePermission]
):

    def __init__(
        self,
        db: Session,
    ):
        super().__init__(
            db=db,
            model=RolePermission,
        )

    # --------------------------------------------------
    # Read
    # --------------------------------------------------

    def get_by_id(
        self,
        role_permission_id: int,
    ) -> RolePermission | None:

        return (
            self.db.query(RolePermission)
            .filter(
                RolePermission.id
                == role_permission_id
            )
            .first()
        )

    def get_by_role(
        self,
        role_id: int,
    ) -> list[RolePermission]:

        return (
            self.db.query(RolePermission)
            .filter(
                RolePermission.role_id == role_id
            )
            .all()
        )

    def get_by_permission(
        self,
        permission_id: int,
    ) -> list[RolePermission]:

        return (
            self.db.query(RolePermission)
            .filter(
                RolePermission.permission_id
                == permission_id
            )
            .all()
        )

    # --------------------------------------------------
    # Exists
    # --------------------------------------------------

    def exists(
        self,
        role_id: int,
        permission_id: int,
    ) -> bool:

        return (
            self.db.query(RolePermission)
            .filter(
                RolePermission.role_id == role_id,
                RolePermission.permission_id
                == permission_id,
            )
            .first()
            is not None
        )

    # --------------------------------------------------
    # Bulk
    # --------------------------------------------------

    def delete_by_role(
        self,
        role_id: int,
    ):

        (
            self.db.query(RolePermission)
            .filter(
                RolePermission.role_id == role_id
            )
            .delete(
                synchronize_session=False
            )
        )

    def bulk_create(
        self,
        role_id: int,
        permission_ids: list[int],
    ):

        entities = [
            RolePermission(
                role_id=role_id,
                permission_id=permission_id,
            )
            for permission_id
            in permission_ids
        ]

        self.db.bulk_save_objects(
            entities
        )

    # --------------------------------------------------
    # Transaction
    # --------------------------------------------------

    def commit(self):

        self.db.commit()

    def rollback(self):

        self.db.rollback()