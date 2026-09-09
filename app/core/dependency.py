"""
=========================================================
Homez OS

File : app/core/dependency.py
Version : 5.0.0

Dependency Providers
=========================================================
"""

from fastapi import Depends

from app.core.auth import get_current_user, get_current_superuser
from app.database.session import get_db
from app.domains.user.model import User


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:

    if getattr(current_user, "is_active", True):
        return current_user

    raise PermissionError("Inactive user")
# --------------------------------------------------
# Optional Dependencies
# --------------------------------------------------

def get_optional_user(
    current_user: User | None = Depends(get_current_user),
) -> User | None:

    return current_user


def require_user(
    current_user: User = Depends(get_current_user),
) -> User:

    return current_user


def require_active_user(
    current_user: User = Depends(get_current_active_user),
) -> User:

    return current_user


def require_superuser(
    current_user: User = Depends(get_current_superuser),
) -> User:

    return current_user
# --------------------------------------------------
# Optional Dependencies
# --------------------------------------------------

def get_optional_user(
    current_user: User | None = Depends(get_current_user),
) -> User | None:

    return current_user


__all__ = [
    "get_db",
    "CurrentUser",
    "CurrentActiveUser",
    "CurrentSuperUser",
    "get_optional_user",
    "require_user",
    "require_active_user",
    "require_superuser",
]