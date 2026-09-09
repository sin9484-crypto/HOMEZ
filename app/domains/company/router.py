"""
=========================================================
Homez OS

Company Router

2026-08-02 보안 감사(Gate 6 계정 복구 작업 중 발견): 이 라우터는 원래
인증·권한 검사가 전혀 없이 app/main.py에 마운트되어 있어, 누구나
`PUT /companies/{id}`로 회사명을 바꾸거나 `DELETE /companies/{id}`로
회사를 삭제할 수 있는 Critical 결함이었다. 이번 수정으로 전 엔드포인트에
로그인+SUPER_ADMIN 검사와 company_id 격리를 강제한다.

- 목록/조회/수정/삭제 전부 로그인 필수 + SUPER_ADMIN 전용.
- 자기 회사(current_user.company_id) 외 다른 company_id는 존재 여부를
  드러내지 않기 위해 403이 아니라 404로 응답한다.
- 신규 Company 생성(POST)은 이 라우터에서 완전히 제거했다 — Company
  생성은 이미 원자적 Transaction(Company+User+audit_log)을 보장하는
  app/core/desktop_setup.py(최초 관리자) / app/core/company_recovery_setup.py
  (기존 관리자 복구) 두 경로로만 허용된다. 이 CRUD 라우터로 임의
  생성을 허용하면 그 원자성·단일 회사 불변식이 깨진다.
- 삭제는 (a) 이 회사가 마지막 회사이거나 (b) 연결된 사용자가 1명이라도
  있으면 차단한다 — 이 시스템은 항상 정확히 이 두 조건을 만족하므로
  실질적으로 삭제 API는 항상 거부되지만, API 자체는 남겨 향후 다회사
  지원 시 재사용할 수 있게 한다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import status

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.guard import SuperAdminGuard
from app.core.recent_auth import consume_recent_auth_token
from app.database.session import get_db

from app.domains.company.model import Company
from app.domains.company.service import CompanyService
from app.domains.company.schema import (
    CompanyResponse,
    CompanyUpdate,
)
from app.domains.user.model import User


router = APIRouter()


def _own_company_or_404(
    company_id: int,
    current_user: User,
) -> None:
    """다른 회사 ID는 403이 아니라 404로 응답해 존재 여부를 숨긴다."""

    if current_user.company_id is None or company_id != current_user.company_id:

        raise HTTPException(
            status_code=404,
            detail="Company not found",
        )


@router.get(
    "/",
    response_model=list[CompanyResponse],
)
def get_companies(
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    service = CompanyService(db)

    if current_user.company_id is None:
        return []

    company = service.get(current_user.company_id)

    return [company] if company is not None else []


@router.get(
    "/{company_id}",
    response_model=CompanyResponse,
)
def get_company(
    company_id: int,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    _own_company_or_404(company_id, current_user)

    service = CompanyService(db)

    company = service.get(company_id)

    if company is None:

        raise HTTPException(
            status_code=404,
            detail="Company not found",
        )

    return company


@router.put(
    "/{company_id}",
    response_model=CompanyResponse,
)
def update_company(
    company_id: int,
    company: CompanyUpdate,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(default=None, alias="X-Recent-Auth-Token"),
):
    """
    2026-08-04 V6 Gate 1A: 회사명(`name`)을 바꾸는 요청은 존재 여부
    확인(404 우선) 이후, 현재 비밀번호로 방금 재확인했다는 증거
    (`X-Recent-Auth-Token`, /auth/recent-auth에서 발급, 5분·1회용)가
    없으면 거부한다 — "기존 PUT을 그대로 연결했을 뿐 재인증이 없다"는
    지적을 반영. 감사 로그에는 회사명 값 자체가 아니라 company_id와
    결과만 남긴다.
    """

    _own_company_or_404(company_id, current_user)

    changing_name = company.name is not None

    if changing_name:

        if not consume_recent_auth_token(recent_auth_token, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="회사명 변경 전 현재 비밀번호로 다시 확인해야 합니다.",
            )

    service = CompanyService(db)

    result = service.update(
        company_id,
        company,
    )

    if result is None:

        raise HTTPException(
            status_code=404,
            detail="Company not found",
        )

    if changing_name:

        write_audit_log(
            db,
            user_id=current_user.id,
            action="COMPANY_NAME_UPDATED",
            entity="companies",
            entity_id=str(company_id),
            description="Company name updated via recent-auth-verified request.",
            company_id=current_user.company_id,
        )
        db.commit()

    return result


@router.delete(
    "/{company_id}",
)
def delete_company(
    company_id: int,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):

    _own_company_or_404(company_id, current_user)

    total_companies = db.query(Company).count()

    if total_companies <= 1:
        raise HTTPException(
            status_code=400,
            detail="마지막 남은 회사는 삭제할 수 없습니다.",
        )

    linked_users = db.query(User).filter(User.company_id == company_id).count()

    if linked_users > 0:
        raise HTTPException(
            status_code=400,
            detail="연결된 사용자가 있는 회사는 삭제할 수 없습니다.",
        )

    service = CompanyService(db)

    if not service.delete(company_id):

        raise HTTPException(
            status_code=404,
            detail="Company not found",
        )

    return {
        "message": "Company deleted"
    }