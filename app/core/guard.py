"""
=========================================================
Homez OS

File : app/core/guard.py
Version : 5.0.0

Authorization Guard
=========================================================
"""

from fastapi import Depends

from app.core.auth import get_current_user
from app.core.authorization import (
    UserRole,
    require_admin,
    require_manager,
    require_role,
    require_staff,
)

from app.domains.user.model import User


def AdminGuard(
    current_user: User = Depends(get_current_user),
) -> User:

    return require_admin(current_user)


# Domain Router 호환 alias (snake_case import 계약)
admin_guard = AdminGuard


def ManagerGuard(
    current_user: User = Depends(get_current_user),
) -> User:

    return require_manager(current_user)


def StaffGuard(
    current_user: User = Depends(get_current_user),
) -> User:

    return require_staff(current_user)
# --------------------------------------------------
# Role Guards
# --------------------------------------------------

def SellerGuard(
    current_user: User = Depends(get_current_user),
) -> User:

    return require_role(
        current_user,
        UserRole.SELLER,
        UserRole.MANAGER,
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
    )


def CustomerGuard(
    current_user: User = Depends(get_current_user),
) -> User:

    return require_role(
        current_user,
        UserRole.CUSTOMER,
        UserRole.SELLER,
        UserRole.STAFF,
        UserRole.MANAGER,
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
    )


def SuperAdminGuard(
    current_user: User = Depends(get_current_user),
) -> User:

    return require_role(
        current_user,
        UserRole.SUPER_ADMIN,
    )


# Domain Router 호환 alias (snake_case import 계약)
require_super_admin = SuperAdminGuard


def AdminOrManagerGuard(
    current_user: User = Depends(get_current_user),
) -> User:

    return require_role(
        current_user,
        UserRole.ADMIN,
        UserRole.MANAGER,
        UserRole.SUPER_ADMIN,
    )
# --------------------------------------------------
# Generic Guard Factory
# --------------------------------------------------

def RoleGuard(
    *roles: UserRole,
):

    def dependency(
        current_user: User = Depends(get_current_user),
    ) -> User:

        return require_role(
            current_user,
            *roles,
        )

    return dependency


__all__ = [
    "AdminGuard",
    "admin_guard",
    "ManagerGuard",
    "StaffGuard",
    "SellerGuard",
    "CustomerGuard",
    "SuperAdminGuard",
    "require_super_admin",
    "AdminOrManagerGuard",
    "RoleGuard",
]