"""
=========================================================
Homez OS

File : app/domains/update/router.py

Gate Y-4(2026-08-12) — 업데이트 공지 API. 공지 등록/취소는
admin_guard, 현재 업데이트 상태 확인은 모든 인증된 사용자가 할 수
있다(로그인 후 화면에서 "새 버전이 있습니다" 배지를 보여주는 데
쓴다).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.update.schema import UpdateNoticeCreateRequest
from app.domains.update.schema import UpdateNoticeResponse
from app.domains.update.schema import UpdateStatusResponse
from app.domains.update.service import UpdateNoticeService
from app.domains.user.model import User

router = APIRouter(
    prefix="/updates",
    tags=["Update"],
)


@router.get(
    "/status",
    response_model=UpdateStatusResponse,
)
def get_update_status(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """현재 앱 버전 기준으로 새 업데이트 공지가 있는지 확인한다."""

    service = UpdateNoticeService(db)

    return service.get_update_status()


@router.get(
    "/notices",
    response_model=list[UpdateNoticeResponse],
)
def list_notices(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """등록된 업데이트 공지 전체 이력(비활성 포함)을 조회한다."""

    service = UpdateNoticeService(db)

    return service.list_notices()


@router.post(
    "/notices",
    response_model=UpdateNoticeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_notice(
    data: UpdateNoticeCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """새 버전 공지를 등록한다(서버가 외부에서 자동으로 확인하지 않는다 — 관리자가 직접 등록)."""

    service = UpdateNoticeService(db)

    return service.create_notice(
        version=data.version,
        title=data.title,
        message=data.message,
        severity=data.severity,
        release_notes_url=data.release_notes_url,
        published_by_user_id=current_user.id,
    )


@router.post(
    "/notices/{notice_id}/deactivate",
    response_model=UpdateNoticeResponse,
)
def deactivate_notice(
    notice_id: int,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """잘못 등록했거나 더 이상 유효하지 않은 공지를 취소한다."""

    service = UpdateNoticeService(db)

    return service.deactivate_notice(notice_id)


__all__ = ["router"]
