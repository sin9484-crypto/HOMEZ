"""
=========================================================
Homez OS

File : app/core/permission_check.py

세부 Permission 기반 인가 — 기존 `roles`/`permissions`/`role_permissions`
스키마는 이미 존재했지만(app/database/seed_role_permission.py가
`ROLE_PERMISSION_MAP`을 정의) 실제로 이를 조회해 요청을 막는 코드가
어디에도 없었다(app/core/authorization.py는 순수 역할 문자열 비교만
한다) — 이번 회원가입 승인 워크플로에서 처음으로 이 스키마를 실제
인가 결정에 사용한다.

새 `SUPERVISOR` 역할을 하드코딩하지 않는다 — "슈퍼바이저"는 특정
역할이 아니라 "USER_ACCESS_APPROVE 등 세부 Permission이 부여된
사용자"라는 개념이다. SUPER_ADMIN은 role_permissions에 무엇이 실제로
채워져 있는지와 무관하게 모든 Permission을 항상 통과한다(기존
`ROLE_PERMISSION_MAP["SUPER_ADMIN"] = ["*"]` 관례와 일치).
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.authorization import authorization_exception
from app.core.authorization import is_super_admin
from app.domains.permission.model import Permission
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User

# --------------------------------------------------
# 역할 등급 — "슈퍼바이저는 자신이 가진 권한(역할)보다 높은 역할을
# 부여할 수 없음"을 판정하기 위한 순서. SUPER_ADMIN은 별도 규칙(기존
# 활성 SUPER_ADMIN만 부여/해제 가능)으로 처리하므로 이 랭크 비교에는
# 참여하지 않는다.
# --------------------------------------------------

ROLE_RANK = {
    "VIEWER": 10,
    "STAFF": 20,
    "MANAGER": 30,
    "ADMIN": 40,
}

MINIMUM_APPROVAL_ROLE_CODE = "VIEWER"


def role_rank(role_code: str | None) -> int:

    if not role_code:
        return 0

    return ROLE_RANK.get(role_code.upper(), 0)


def get_permission_codes_for_role(db: Session, role_id: int | None) -> set[str]:

    if role_id is None:
        return set()

    rows = (
        db.query(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == role_id, Permission.active.is_(True))
        .all()
    )

    return {row[0] for row in rows}


def has_permission(db: Session, user: User | None, code: str) -> bool:

    if user is None:
        return False

    if is_super_admin(user):
        return True

    codes = get_permission_codes_for_role(db, user.role_id)

    return code in codes


def require_permission(db: Session, user: User | None, code: str) -> User:

    if not has_permission(db, user, code):
        raise authorization_exception()

    return user


def can_assign_role_code(granter: User, target_role_code: str) -> bool:
    """
    승인/역할변경 시 부여하려는 역할이 granter의 권한 범위 안인지
    판정한다.

    - SUPER_ADMIN 부여/해제는 오직 기존 활성 SUPER_ADMIN만 가능.
    - 그 외 역할은 SUPER_ADMIN이면 항상 허용, 아니면 granter 자신의
      역할 등급 이상은 부여 불가(자기 등급까지만 — "자신이 가진
      권한보다 높은 역할을 부여할 수 없음").
    """

    target_code = (target_role_code or "").upper()

    if target_code == "SUPER_ADMIN":
        return is_super_admin(granter)

    if is_super_admin(granter):
        return True

    return role_rank(granter.role) >= role_rank(target_code)


__all__ = [
    "ROLE_RANK",
    "MINIMUM_APPROVAL_ROLE_CODE",
    "role_rank",
    "get_permission_codes_for_role",
    "has_permission",
    "require_permission",
    "can_assign_role_code",
]
