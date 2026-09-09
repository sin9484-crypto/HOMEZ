"""
=========================================================
Homez OS

File : app/domains/role_permission/admin_router.py

Gate T(2026-08-10) — 역할별 Permission 편집 + 감사 이력 조회 API.
전부 SUPER_ADMIN 전용이다(app/domains/role_permission/admin_service.py
의 설계 노트 참고 — SUPER_ADMIN 역할 자체의 행은 편집 대상에서
제외된다). 사용자 목록/생성/활성화는 app/core/account_admin.py에
이미 있다 — 이 라우터는 그 화면의 "역할·Permission" 탭에서만 쓴다.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.domains.role.model import Role
from app.domains.role_permission.admin_service import (
    RolePermissionUpdateError,
    get_role_permission_codes,
    request_permission_edit_nonce,
    update_role_permissions,
)
from app.domains.role_permission.permission_catalog import build_permission_catalog
from app.domains.user.model import User

router = APIRouter(prefix="/admin", tags=["Role Permission Admin"])

_ERROR_MESSAGES = {
    RolePermissionUpdateError.SUPER_ADMIN_NOT_EDITABLE.value:
        "SUPER_ADMIN 역할은 Permission을 개별 편집할 수 없습니다(항상 모든 권한을 가집니다).",
    RolePermissionUpdateError.RECENT_AUTH_REQUIRED.value:
        "이 작업 전에 현재 비밀번호로 다시 확인해야 합니다.",
    RolePermissionUpdateError.NONCE_INVALID.value:
        "현재 목록을 다시 조회한 뒤 다시 시도하세요(만료되었거나 이미 사용된 요청입니다).",
    RolePermissionUpdateError.UNKNOWN_PERMISSION_CODE.value:
        "존재하지 않거나 비활성화된 Permission 코드가 포함되어 있습니다.",
}


class RoleSummary(BaseModel):

    id: int
    code: str
    name: str


class RolePermissionsResponse(BaseModel):

    role_id: int
    role_code: str
    codes: list[str]
    edit_nonce: str
    edit_nonce_expires_at: str


class RolePermissionsUpdateRequest(BaseModel):

    expected_codes: list[str]
    new_codes: list[str]
    nonce: str


class AuditLogEntry(BaseModel):

    id: int
    action: str
    entity: str
    entity_id: str
    description: str


# --------------------------------------------------
# 역할 목록
# --------------------------------------------------

@router.get("/roles", response_model=list[RoleSummary])
def list_roles(
    _: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    rows = db.query(Role).order_by(Role.id.asc()).all()

    return [RoleSummary(id=r.id, code=r.code, name=r.name) for r in rows]


# --------------------------------------------------
# Permission 카탈로그(그룹화 메타데이터)
# --------------------------------------------------

@router.get("/permissions/catalog")
def get_permission_catalog(
    _: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    return build_permission_catalog(db)


# --------------------------------------------------
# 역할의 현재 Permission 목록 조회 — 조회할 때마다 새 편집 nonce 발급
# --------------------------------------------------

@router.get("/roles/{role_id}/permissions", response_model=RolePermissionsResponse)
def get_role_permissions_endpoint(
    role_id: int,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    try:
        codes = get_role_permission_codes(db, role_id)
        nonce, expires_at = request_permission_edit_nonce(db, role_id=role_id, current_user=current_user)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="역할을 찾을 수 없습니다.")
    except ValueError as exc:
        code = exc.args[0] if exc.args else None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_ERROR_MESSAGES.get(code, "요청을 처리할 수 없습니다."),
        )

    role = db.query(Role).filter(Role.id == role_id).first()

    return RolePermissionsResponse(
        role_id=role_id,
        role_code=role.code,
        codes=sorted(codes),
        edit_nonce=nonce,
        edit_nonce_expires_at=expires_at.isoformat(),
    )


# --------------------------------------------------
# 역할의 Permission 집합 수정 — recent-auth + nonce + 낙관적 동시성
# --------------------------------------------------

@router.put("/roles/{role_id}/permissions", response_model=RolePermissionsResponse)
def update_role_permissions_endpoint(
    role_id: int,
    data: RolePermissionsUpdateRequest,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(default=None, alias="X-Recent-Auth-Token"),
):

    try:
        new_codes = update_role_permissions(
            db,
            role_id=role_id,
            current_user=current_user,
            expected_codes=set(data.expected_codes),
            new_codes=set(data.new_codes),
            nonce=data.nonce,
            recent_auth_token=recent_auth_token,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="역할을 찾을 수 없습니다.")
    except ValueError as exc:
        code = exc.args[0] if exc.args else None

        if code == RolePermissionUpdateError.VERSION_CONFLICT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="다른 관리자가 이미 이 역할의 Permission을 변경했습니다. 목록을 다시 불러오세요.",
            )

        if code == RolePermissionUpdateError.RECENT_AUTH_REQUIRED.value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_ERROR_MESSAGES[code],
                headers={"X-Auth-Error-Code": "RECENT_AUTH_FAILED"},
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_ERROR_MESSAGES.get(code, "요청을 처리할 수 없습니다."),
        )

    role = db.query(Role).filter(Role.id == role_id).first()

    return RolePermissionsResponse(
        role_id=role_id,
        role_code=role.code,
        codes=sorted(new_codes),
        edit_nonce="",
        edit_nonce_expires_at="",
    )


# --------------------------------------------------
# 감사 이력 조회
# --------------------------------------------------

@router.get("/audit-logs", response_model=list[AuditLogEntry])
def list_audit_logs(
    entity: str | None = None,
    entity_id: str | None = None,
    limit: int = 50,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):
    """
    2026-08-15 Gate 1(V6.5 최종 계약 감사) — 이전에는 회사 필터가 전혀
    없어, 이 회사의 SUPER_ADMIN이 다른 회사의 사용자 계정 이벤트
    (비밀번호 재설정, 초대 생성/폐기, 등록 승인/거절 등)까지 그대로
    열람할 수 있었다(Critical, 테넌트 격리 우회 — 코드는
    `app/core/audit_db.py::write_audit_log()`의 company_id NULL 고정
    INSERT가 원인이었고 그 자체도 이번에 함께 고쳤다). `role_permissions`
    entity(역할별 Permission 편집 이력)는 원래부터 회사와 무관한 전역
    플랫폼 설정이라 company_id가 없다(app/domains/decision 등에서
    `DecisionPolicy`/`CoupangPolicySet`을 전역으로 유지하는 것과 동일한
    기존 설계 결정) — 그런 행과, 아직 어떤 회사인지 식별할 수 없었던
    극소수 익명 이벤트(예: 존재하지 않는 계정의 아이디 찾기 시도)만
    company_id가 NULL로 계속 남는다. 이 두 경우를 제외한 나머지는
    반드시 요청자 자신의 회사 것만 보여준다.
    """

    capped_limit = max(1, min(limit, 200))

    query = (
        "SELECT id, action, entity, entity_id, description FROM audit_logs "
        "WHERE (company_id = :company_id OR company_id IS NULL)"
    )
    params: dict = {"company_id": current_user.company_id}

    if entity:
        query += " AND entity = :entity"
        params["entity"] = entity

    if entity_id:
        query += " AND entity_id = :entity_id"
        params["entity_id"] = entity_id

    query += " ORDER BY id DESC LIMIT :limit"
    params["limit"] = capped_limit

    rows = db.execute(text(query), params).fetchall()

    return [
        AuditLogEntry(id=row[0], action=row[1], entity=row[2], entity_id=row[3], description=row[4])
        for row in rows
    ]


__all__ = ["router"]
