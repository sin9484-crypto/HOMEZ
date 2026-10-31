"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/ai_settings_router.py

2026-10-31 — AI 통합 계획 2단계 준비: **AI 사용 설정**(모델·월 한도·단가·API 키)을 위저드 밖에서 보고 저장한다.
지금까지 설정은 위저드 AI 초안 화면(`/listing-wizards/{id}/ai-draft/*`)에만 있어, 고객 응대 답변 초안·반려 사유 설명처럼 위저드와 무관한
AI 기능을 쓰려 해도 위저드를 하나 열어야 했다. 저장 규칙·저장 위치는 `AiDraftService`를 그대로 쓴다(같은 설정, 같은 키 저장소).

  - 보기(GET): 관리자. 설정·이번 달 사용액·사용 기록. 키는 저장 여부만 알려 준다. 바깥 호출 없음.
  - 저장(PUT): 최고 관리자 + 비밀번호 재확인(위저드 경로와 같다). 키는 쓰기 전용 — 응답·감사 기록에 남기지 않는다.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from pydantic import BaseModel
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import AdminGuard
from app.core.guard import SuperAdminGuard
from app.core.recent_auth import consume_recent_auth_token
from app.core.windows_credential_store import CredentialStore
from app.domains.user.model import User

router = APIRouter(prefix="/ai-settings", tags=["ai-settings"])


def get_ai_credential_store() -> CredentialStore:
    """AI 키 저장소 — 항상 실제 Windows 자격증명 저장소. 테스트는 이 의존성을 덮어쓴다."""

    from app.core.windows_credential_store import WindowsCredentialStore

    return WindowsCredentialStore()


class AiSettingsRequest(BaseModel):
    """API 키는 쓰기 전용이다. 비워 두면 기존 키를 유지한다."""

    model: str
    monthly_limit_krw: int
    in_price: float
    out_price: float
    api_key: str | None = None

    model_config = ConfigDict(extra="forbid", protected_namespaces=())


def _service(db: Session, store: CredentialStore):
    from app.domains.marketplace_listing.ai_draft_service import AiDraftService

    return AiDraftService(db, store)


@router.get("")
def read_ai_settings(
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_ai_credential_store),
):
    """설정·이번 달 사용량·사용 기록(읽기 전용, 바깥 호출 없음)."""

    service = _service(db, credential_store)
    return {**service.status(current_user.id, current_user.company_id), "records": service.month_records()}


@router.put("")
def save_ai_settings(
    data: AiSettingsRequest,
    current_user: User = Depends(SuperAdminGuard),
    recent_auth_token: str | None = Header(default=None, alias="X-Recent-Auth-Token"),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_ai_credential_store),
):
    """모델·월 한도·단가·API 키 저장. 비용과 비밀값을 다루므로 비밀번호 재확인이 먼저다."""

    from app.core.audit_db import write_audit_log
    from app.core.exceptions import ForbiddenException

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise ForbiddenException("현재 비밀번호 재확인이 필요합니다.")
    result = _service(db, credential_store).save_settings(
        current_user.id, current_user.company_id, model=data.model, monthly_limit_krw=data.monthly_limit_krw,
        in_price=data.in_price, out_price=data.out_price, api_key=data.api_key,
    )
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id, action="AI_DRAFT_SETTINGS_SAVED",
        entity="ai_draft", entity_id="SETTINGS",
        description=f"AI 사용 설정 저장(모델 {result['model']}, 월 한도 {result['monthly_limit_krw']}원, 키 {'교체' if data.api_key else '유지'})",
    )
    db.commit()
    return result


__all__ = ["router", "get_ai_credential_store", "AiSettingsRequest"]
