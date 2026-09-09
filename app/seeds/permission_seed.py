"""
=========================================================
Homez OS

File : app/seeds/permission_seed.py
Version : 1.0.0

Permission Seeder
=========================================================
"""

from sqlalchemy.orm import Session

from app.domains.role.model import Role
from app.domains.permission.model import Permission
from app.domains.role_permission.model import RolePermission


class PermissionSeeder:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # =====================================================
    # Seed Admin Permissions
    # =====================================================

    def seed_admin_permissions(self):

        admin_role = (
            self.db.query(Role)
            .filter(
                Role.code == "ADMIN"
            )
            .first()
        )

        if admin_role is None:

            print("ADMIN role not found.")

            return

        permissions = (
            self.db.query(Permission)
            .filter(
                Permission.active.is_(True)
            )
            .all()
        )

        created_count = 0

        for permission in permissions:

            exists = (
                self.db.query(RolePermission)
                .filter(
                    RolePermission.role_id == admin_role.id,
                    RolePermission.permission_id == permission.id,
                )
                .first()
            )

            if exists:
                continue

            self.db.add(
                RolePermission(
                    role_id=admin_role.id,
                    permission_id=permission.id,
                )
            )

            created_count += 1

        self.db.commit()

        print(
            f"[PermissionSeeder] "
            f"{created_count} permissions assigned "
            f"to ADMIN role."
        )