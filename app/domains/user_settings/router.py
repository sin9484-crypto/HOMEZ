"""
=========================================================
Homez OS

File : app/domains/user_settings/router.py

2026-08-14 Gate F-2 — 사용자 설정 API. 항상 "내 설정"만 다룬다 — 다른
사용자의 user_id를 경로/바디로 받는 엔드포인트를 두지 않는다(다른
사용자 설정이 섞이거나 노출될 여지 자체를 없앤다). company_id/user_id
둘 다 인증된 현재 사용자(get_current_user)에서만 가져오며, 클라이언트가
보낸 값을 신뢰하지 않는다.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.exceptions import ConflictException
from app.database.session import get_db
from app.domains.user.model import User
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.schema import UserSettingResponse
from app.domains.user_settings.schema import UserSettingUpdateRequest
from app.domains.user_settings.service import UserSettingService

router = APIRouter(prefix="/user-settings", tags=["User Settings"])


def get_service(db: Session = Depends(get_db)) -> UserSettingService:

    return UserSettingService(UserSettingRepository(db))


def _require_company_id(current_user: User) -> int:
    """
    회사 복구(company_recovery_setup)가 아직 끝나지 않아
    `current_user.company_id`가 NULL인 극히 드문 상태에서는 이 API를
    409로 명확히 거부한다 — None을 그대로 격리 컬럼에 흘려보내지
    않는다.
    """

    if current_user.company_id is None:
        raise ConflictException(
            "회사 정보가 아직 설정되지 않았습니다. 회사 설정을 먼저 "
            "완료하세요.",
        )

    return current_user.company_id


@router.get("/{key}", response_model=UserSettingResponse)
def get_user_setting(
    key: str,
    current_user: User = Depends(get_current_user),
    service: UserSettingService = Depends(get_service),
):

    company_id = _require_company_id(current_user)

    return service.get(current_user.id, company_id, key)


@router.put("/{key}", response_model=UserSettingResponse)
def put_user_setting(
    key: str,
    body: UserSettingUpdateRequest,
    current_user: User = Depends(get_current_user),
    service: UserSettingService = Depends(get_service),
):

    company_id = _require_company_id(current_user)

    return service.put(
        current_user.id,
        company_id,
        key,
        body.value,
        body.expected_version,
        body.schema_version,
    )


__all__ = ["router"]
