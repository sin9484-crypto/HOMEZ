"""
=========================================================
Homez OS

File : app/core/account_admin.py

HOMEZ Desktop 로그인 후 "설정 → 계정 및 보안" 화면 — SUPER_ADMIN 전용
계정 관리 API. (본인 비밀번호 변경·세션 전체 폐기는 app/domains/auth/
router.py에 이미 구현됨.)

원칙:
  - UI뿐 아니라 API 자체가 SUPER_ADMIN 권한을 검사한다
    (app/core/guard.py::SuperAdminGuard 재사용).
  - 비밀번호 원문·해시는 어떤 응답에도 포함하지 않는다.
  - 마지막 활성 SUPER_ADMIN은 비활성화할 수 없다(전역 — SUPER_ADMIN은
    회사 소속과 무관하게 시스템 전체의 최종 관리 권한이므로).
  - 본인 계정은 스스로 비활성화할 수 없다(재확인 UI 대신 서버에서
    아예 차단 — 실수로 자신을 잠그는 사고를 구조적으로 막는다).
  - Gate T(2026-08-10) 검토: "회사의 마지막 관리자 비활성화 차단"을
    별도로 추가할지 검토했으나, 이 라우터의 모든 엔드포인트가
    SuperAdminGuard(호출자=활성 SUPER_ADMIN) + _own_company_user_or_404
    (대상이 호출자와 같은 회사)를 이미 강제하므로, 호출자 자신이 항상
    그 회사의 활성 관리자 1명으로 카운트된다 — 대상이 호출자 본인이
    아닌 한(이미 위에서 차단됨) 그 회사의 관리자 수가 0이 되는 경로가
    존재하지 않는다. 그래서 도달 불가능한 방어 코드를 추가하지 않았다
    (검증: tests/test_role_permission_admin.py 참고).
  - Gate T(2026-08-10) 추가: 모든 조회/수정은 current_user.company_id로
    스코프한다 — 다른 회사 사용자를 조회/수정하면 404(정보 노출 방지,
    발견된 결함을 이번에 보강 — 기존에는 전역 조회였다).
  - 계정 삭제 API는 만들지 않는다(비활성화만 제공).
  - 모든 변경을 audit_logs에 기록한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.config import settings
from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.core.password_policy import validate_password_policy
from app.core.permission_check import get_permission_codes_for_role
from app.core.security import hash_password
from app.domains.role.service import find_role_by_code_ci
from app.domains.session.service import count_active_sessions
from app.domains.session.service import get_last_login_at
from app.domains.session.service import revoke_all_sessions_for_user
from app.domains.user.model import User

router = APIRouter(prefix="/admin/users", tags=["Account Admin"])


# --------------------------------------------------
# Schema
# --------------------------------------------------

class AdminUserSummary(BaseModel):

    id: int
    username: str
    email: str
    name: str | None
    role: str | None
    is_active: bool
    company_id: int | None
    last_login_at: str | None = None
    # 2026-09-09 Phase 1(로그인 무차별 대입 방어) — 잠금 여부는
    # `locked_until > 지금`으로 판단한다. `failed_login_count`는 아직
    # 잠기지 않았어도(임계치 미도달) 참고용으로 함께 노출한다.
    is_locked: bool = False
    failed_login_count: int = 0
    locked_until: str | None = None


class AdminSecurityEvent(BaseModel):

    action: str
    description: str
    created_at: str | None = None


class AdminUserDetail(AdminUserSummary):

    active_session_count: int | None = None
    permission_codes: list[str] = Field(default_factory=list)
    recent_security_events: list[AdminSecurityEvent] = Field(default_factory=list)


class AdminUserCreateRequest(BaseModel):

    username: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    password: str
    password_confirmation: str
    role_code: str
    name: str | None = None


class AdminUserActiveUpdateRequest(BaseModel):

    active: bool


class AdminMessage(BaseModel):

    message: str


def _to_summary(db: Session, user: User) -> AdminUserSummary:

    last_login = get_last_login_at(db, user.id)

    is_locked = (
        user.locked_until is not None
        and user.locked_until > datetime.utcnow()
    )

    return AdminUserSummary(
        id=user.id, username=user.username, email=user.email,
        name=user.name, role=user.role, is_active=user.is_active,
        company_id=user.company_id,
        last_login_at=last_login.isoformat() if last_login is not None else None,
        is_locked=is_locked,
        failed_login_count=user.failed_login_count,
        locked_until=user.locked_until.isoformat() if user.locked_until else None,
    )


def _count_active_super_admins(db: Session) -> int:

    count = 0

    for user in db.query(User).filter(User.is_active.is_(True)).all():
        if (user.role or "").strip().upper() == "SUPER_ADMIN":
            count += 1

    return count


def _own_company_user_or_404(db: Session, user_id: int, current_user: User) -> User:

    target = db.query(User).filter(User.id == user_id).first()

    if target is None or target.company_id != current_user.company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.",
        )

    return target


# --------------------------------------------------
# 목록 / 상세
# --------------------------------------------------

@router.get("", response_model=list[AdminUserSummary])
def list_users(
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    rows = (
        db.query(User)
        .filter(User.company_id == current_user.company_id)
        .order_by(User.id.asc())
        .all()
    )

    return [_to_summary(db, u) for u in rows]


@router.get("/{user_id}", response_model=AdminUserDetail)
def get_user_detail(
    user_id: int,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    target = _own_company_user_or_404(db, user_id, current_user)

    permission_codes = sorted(get_permission_codes_for_role(db, target.role_id))

    events = db.execute(
        text(
            "SELECT action, description, id FROM audit_logs "
            "WHERE entity = 'users' AND entity_id = :entity_id "
            "ORDER BY id DESC LIMIT 20",
        ),
        {"entity_id": str(target.id)},
    ).fetchall()

    summary = _to_summary(db, target)

    return AdminUserDetail(
        **summary.model_dump(),
        active_session_count=count_active_sessions(db, target.id),
        permission_codes=permission_codes,
        recent_security_events=[
            AdminSecurityEvent(action=row[0], description=row[1], created_at=None)
            for row in events
        ],
    )


# --------------------------------------------------
# 신규 사용자 생성
# --------------------------------------------------

@router.post("", response_model=AdminUserSummary)
def create_user(
    data: AdminUserCreateRequest,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    username = data.username.strip()
    email = data.email.strip().lower()

    if not username or "@" not in email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username 또는 email 형식이 올바르지 않습니다.",
        )

    if data.password != data.password_confirmation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호 확인이 일치하지 않습니다.",
        )

    policy_errors = validate_password_policy(data.password, settings)

    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=policy_errors,
        )

    role = find_role_by_code_ci(db, data.role_code)

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"role_code '{data.role_code}'가 존재하지 않습니다.",
        )

    if db.query(User).filter(User.username == username).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 존재하는 username입니다.",
        )

    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 존재하는 email입니다.",
        )

    new_user = User(
        username=username, email=email,
        password_hash=hash_password(data.password),
        name=data.name, role_id=role.id, is_active=True,
        company_id=current_user.company_id,
    )
    db.add(new_user)
    db.flush()

    write_audit_log(
        db,
        user_id=current_user.id,
        action="CREATE_USER",
        entity="users",
        entity_id=str(new_user.id),
        description=f"SUPER_ADMIN(id={current_user.id}) created user "
        f"(id={new_user.id}, role={role.code}).",
        company_id=current_user.company_id,
    )
    db.commit()
    db.refresh(new_user)

    return _to_summary(db, new_user)


# --------------------------------------------------
# 활성화 / 비활성화
# --------------------------------------------------

@router.patch("/{user_id}/active", response_model=AdminUserSummary)
def update_user_active_status(
    user_id: int,
    data: AdminUserActiveUpdateRequest,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    target = _own_company_user_or_404(db, user_id, current_user)

    if not data.active:

        if target.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="본인 계정은 스스로 비활성화할 수 없습니다.",
            )

        is_target_super_admin = (target.role or "").strip().upper() == "SUPER_ADMIN"

        if (
            is_target_super_admin
            and target.is_active
            and _count_active_super_admins(db) <= 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="마지막 활성 SUPER_ADMIN은 비활성화할 수 없습니다.",
            )

    target.is_active = data.active
    db.add(target)

    if not data.active:
        # 비활성화는 기존에 발급된 세션으로도 계속 접근할 수 있으면
        # 실질적인 효과가 없다 — 즉시 전 세션을 폐기한다.
        revoke_all_sessions_for_user(db, target.id, reason="account_deactivated")

    write_audit_log(
        db,
        user_id=current_user.id,
        action="ACTIVATE_USER" if data.active else "DEACTIVATE_USER",
        entity="users",
        entity_id=str(target.id),
        description=f"SUPER_ADMIN(id={current_user.id}) set user(id={target.id}) "
        f"active={data.active}.",
        company_id=current_user.company_id,
    )
    db.commit()
    db.refresh(target)

    return _to_summary(db, target)


# --------------------------------------------------
# 로그인 잠금 수동 해제 (2026-09-09 Phase 1)
# --------------------------------------------------

@router.post("/{user_id}/unlock", response_model=AdminUserSummary)
def unlock_user(
    user_id: int,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):
    """
    HOMEZ_USER_OPERATION_SETTINGS.md 11번 로그인 잠금은 시간이
    지나면 자동으로 풀리지만(locked_until 경과), 정당한 사용자가
    비밀번호를 여러 번 틀려 급하게 잠긴 경우 관리자가 즉시 풀어줄
    수 있어야 한다. 이 엔드포인트는 실패 횟수와 잠금을 초기화만
    한다 — 비밀번호는 바꾸지 않는다.
    """

    target = _own_company_user_or_404(db, user_id, current_user)

    was_locked = target.failed_login_count > 0 or target.locked_until is not None

    target.failed_login_count = 0
    target.locked_until = None
    db.add(target)

    write_audit_log(
        db,
        user_id=current_user.id,
        action="UNLOCK_USER",
        entity="users",
        entity_id=str(target.id),
        description=f"SUPER_ADMIN(id={current_user.id})이 user(id={target.id})의 "
        f"로그인 잠금을 수동으로 해제했습니다(잠겨 있었음={was_locked}).",
        company_id=current_user.company_id,
    )
    db.commit()
    db.refresh(target)

    return _to_summary(db, target)


__all__ = ["router"]
