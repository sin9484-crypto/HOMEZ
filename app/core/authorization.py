"""
=========================================================
Homez OS

File : app/core/authorization.py
Version : 5.0.0

Role Based Authorization
=========================================================
"""

from enum import Enum

from fastapi import HTTPException
from fastapi import status

from app.domains.user.model import User


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MANAGER = "manager"
    STAFF = "staff"
    SELLER = "seller"
    CUSTOMER = "customer"
    GUEST = "guest"


def authorization_exception() -> HTTPException:

    # 2026-08-03: 기존 detail 문자열은 그대로 두고(기존 계약 유지),
    # 클라이언트가 401과 구분해 "로그아웃하지 않고 권한 오류로만
    # 표시"할 수 있도록 헤더로만 안정적인 코드를 추가한다.
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Permission denied",
        headers={"X-Auth-Error-Code": "PERMISSION_DENIED"},
    )


def has_role(
    user: User,
    *roles: UserRole,
) -> bool:
    """
    대소문자를 구분하지 않고 비교한다. 실제 homez.db에 시딩된 역할
    코드(`roles.code`, 예: "ADMIN", "SUPER_ADMIN")는 대문자인데
    UserRole enum 값(예: "admin")은 소문자라 그대로 비교하면 실제
    계정으로는 admin_guard를 절대 통과할 수 없었다(2026-07-30, Desktop
    로그인 흐름 작업 중 발견).
    """

    if user is None:
        return False

    role = getattr(
        user,
        "role",
        None,
    )

    if role is None:
        return False

    return role.upper() in {
        r.value.upper()
        for r in roles
    }
# --------------------------------------------------
# Utilities
# --------------------------------------------------

def is_admin(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.ADMIN,
    )


def is_super_admin(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.SUPER_ADMIN,
    )


def is_manager(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.MANAGER,
    )


def is_seller(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.SELLER,
    )


def is_staff(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.STAFF,
    )


def is_customer(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.CUSTOMER,
    )


def can_manage_users(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.ADMIN,
    )


def can_manage_products(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.MANAGER,
    )


def can_manage_orders(
    user: User,
) -> bool:

    return has_role(
        user,
        UserRole.STAFF,
    )
# --------------------------------------------------
# Require Permission
# --------------------------------------------------

def require_role(
    user: User,
    *roles: UserRole,
) -> User:

    if not has_role(user, *roles):
        raise authorization_exception()

    return user


def require_admin(
    user: User,
) -> User:

    return require_role(
        user,
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
    )


def require_manager(
    user: User,
) -> User:

    return require_role(
        user,
        UserRole.MANAGER,
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
    )


def require_staff(
    user: User,
) -> User:

    return require_role(
        user,
        UserRole.STAFF,
        UserRole.MANAGER,
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
    )


__all__ = [
    "UserRole",
    "authorization_exception",
    "has_role",
    "is_admin",
    "is_super_admin",
    "is_manager",
    "is_staff",
    "is_seller",
    "is_customer",
    "can_manage_users",
    "can_manage_products",
    "can_manage_orders",
    "require_role",
    "require_admin",
    "require_manager",
    "require_staff",
]