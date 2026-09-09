"""
=========================================================
Homez OS

File : app/domains/permission/policy.py
Version : 2.2.0

Permission Policy
=========================================================
"""

from fastapi import Depends
from fastapi import HTTPException
from fastapi import status

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.core.auth import get_current_user

from app.domains.user.model import User
from app.domains.permission.model import Permission
from app.domains.role.repository import RoleRepository
# RolePermission은 함수 내부(has_permission)에서 지역 import한다 —
# role_permission/model.py가 Permission을 참조하고, 이 파일(policy.py)은
# app/domains/permission/__init__.py를 통해 패키지 초기화 시 로드되므로,
# 모듈 최상단에서 import하면 permission ↔ role_permission 순환
# import가 발생한다(2026-07-30, Desktop 로그인 mapper 오류 조사 중
# 발견). 지역 import로 순환을 끊는다 — 동작은 동일하다.


class PermissionPolicy:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.role_repository = RoleRepository(db)

    # --------------------------------------------------
    # Check Permission
    # --------------------------------------------------

    def has_permission(
        self,
        role_id: int | None,
        permission_code: str,
    ) -> bool:

        if role_id is None:
            return False

        from app.domains.role_permission.model import RolePermission

        stmt = (
            select(Permission)
            .join(
                RolePermission,
                Permission.id == RolePermission.permission_id,
            )
            .where(
                RolePermission.role_id == role_id,
                Permission.code == permission_code,
                Permission.active.is_(True),
            )
        )

        permission = self.db.scalar(stmt)

        return permission is not None

    # --------------------------------------------------
    # Resolve Role ID
    # --------------------------------------------------
    # User.role(str) → Role.code → Role.id
    # User.role_id / migration 없이 RBAC 연결
    # --------------------------------------------------

    def _resolve_role_id(
        self,
        user: User,
    ) -> int | None:

        role_code = getattr(user, "role", None)

        if not role_code:

            return None

        role = self.role_repository.get_by_code(
            role_code,
        )

        if role is None:

            return None

        return role.id

    # --------------------------------------------------
    # Require Permission
    # --------------------------------------------------

    def require(
        self,
        user: User,
        permission_code: str,
    ):

        role_id = self._resolve_role_id(user)

        if role_id is None:

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied.",
            )

        if not self.has_permission(
            role_id,
            permission_code,
        ):

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied.",
            )

        return True


# =====================================================
# Dependency
# =====================================================

def require_permission(
    permission_code: str,
):

    def dependency(

        current_user: User = Depends(
            get_current_user
        ),

        db: Session = Depends(
            get_db
        ),

    ):

        policy = PermissionPolicy(db)

        policy.require(
            current_user,
            permission_code,
        )

        return current_user

    return dependency
