"""
=========================================================
Homez OS

File : app/domains/media_asset/ai_background_router.py

2026-11-10 — **AI 배경(Cloudflare)** 화면용 주소. 서비스 규칙은 `ai_background_service.py`.

  - 설정 보기(GET /ai-background/settings): 관리자. 토큰은 저장 여부만, 계정 ID는 끝 4자리만. 바깥 호출 없음.
  - 설정 저장(PUT): 최고 관리자 + 비밀번호 재확인(AI 사용 설정과 같다). 토큰은 쓰기 전용 — 응답·감사 기록에 남기지 않는다.
  - 컨셉 목록(GET /ai-background/concepts): 관리자. 바깥에 보낼 장면 글까지 보여 준다.
  - 만들기(POST /ai-background/runs): 관리자. 새로 그릴 배경마다 승인 센터 허용 1회(저장된 배경을 다시 쓰면 0회).
=========================================================
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import AdminGuard
from app.core.guard import SuperAdminGuard
from app.core.recent_auth import consume_recent_auth_token
from app.core.windows_credential_store import CredentialStore
from app.domains.user.model import User

router = APIRouter(prefix="/ai-background", tags=["ai-background"])


def get_ai_background_credential_store() -> CredentialStore:
    """AI 배경 토큰 저장소 — 항상 실제 Windows 자격증명 저장소. 시험은 이 의존성을 덮어쓴다."""

    from app.core.windows_credential_store import WindowsCredentialStore

    return WindowsCredentialStore()


class AiBackgroundSettingsRequest(BaseModel):
    """토큰은 쓰기 전용이다. 비워 두면 저장된 값을 그대로 쓴다."""

    account_id: str | None = Field(default=None, max_length=64)
    api_token: str | None = Field(default=None, max_length=300)

    model_config = ConfigDict(extra="forbid")


class AiBackgroundRunRequest(BaseModel):
    cutout_asset_id: int
    concept_codes: list[str] = Field(..., min_length=1, max_length=3)
    purpose: Literal["ADDITIONAL", "DETAIL"] = "ADDITIONAL"
    new_background: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("concept_codes")
    @classmethod
    def _codes(cls, value):
        import re

        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,39}", v) for v in value):
            raise ValueError("컨셉 코드는 영문 대문자·숫자·밑줄 3~40자여야 합니다.")
        return value


def _service(db: Session, store: CredentialStore):
    from app.domains.media_asset.ai_background_service import AiBackgroundService

    return AiBackgroundService(db, store)


@router.get("/settings")
def read_settings(
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_ai_background_credential_store),
):
    return _service(db, credential_store).status()


@router.put("/settings")
def save_settings(
    data: AiBackgroundSettingsRequest,
    current_user: User = Depends(SuperAdminGuard),
    recent_auth_token: str | None = Header(default=None, alias="X-Recent-Auth-Token"),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_ai_background_credential_store),
):
    """계정 ID·API 토큰 저장. 비밀값을 다루므로 비밀번호 재확인이 먼저다."""

    from app.core.audit_db import write_audit_log
    from app.core.exceptions import ForbiddenException
    from app.domains.media_asset.ai_background_service import SETTINGS_ACTION

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise ForbiddenException("현재 비밀번호 재확인이 필요합니다.")
    result = _service(db, credential_store).save_settings(account_id=data.account_id, api_token=data.api_token)
    write_audit_log(
        db, user_id=current_user.id, company_id=current_user.company_id, action=SETTINGS_ACTION, entity="ai_background", entity_id="SETTINGS",
        description=f"AI 배경(Cloudflare) 설정 저장(계정 ID {'교체' if data.account_id else '유지'}, 토큰 {'교체' if data.api_token else '유지'})",
    )
    db.commit()
    return result


@router.get("/concepts")
def list_concepts(current_user: User = Depends(AdminGuard)):
    from app.domains.media_asset import ai_background_concepts as abc
    from app.domains.media_asset.ai_background_service import GENERATED_LABEL_KO

    return {"concepts": [abc.to_summary(c) for c in abc.list_ai_concepts()], "max_pick": abc.MAX_PICK, "generated_label": GENERATED_LABEL_KO}


@router.post("/runs")
def run(
    data: AiBackgroundRunRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_ai_background_credential_store),
):
    """누끼 한 장 + 고른 AI 배경 컨셉(최대 3개) → 컨셉마다 합성·검사·저장. 검사에서 FAIL이 나면 저장하지 않는다. 대표 사진은 정하지 않는다."""

    from app.domains.media_asset.ai_background_service import GENERATED_LABEL_KO
    from app.domains.media_asset.schema import MediaAssetResponse

    outcomes = _service(db, credential_store).run(
        current_user.company_id, current_user.id, data.cutout_asset_id, data.concept_codes, data.purpose, new_background=data.new_background,
    )
    results = []
    for o in outcomes:
        inner = o.outcome
        results.append({
            "concept_code": o.concept_code, "status": o.status, "reason": o.reason, "detail": o.detail,
            "usable": None if inner is None else inner.usable, "checks": [] if inner is None else list(inner.checks),
            "asset": None if inner is None or inner.asset is None else MediaAssetResponse.model_validate(inner.asset).model_dump(mode="json"),
            "background_version": o.background_version, "background_reused": o.background_reused,
        })
    count = lambda status: sum(1 for o in outcomes if o.status == status)  # noqa: E731
    return {
        "results": results, "created": count("CREATED"), "reused": count("REUSED"), "check_failed": count("CHECK_FAILED"), "skipped": count("SKIPPED"),
        "failed": count("FAILED"), "drawn": sum(1 for o in outcomes if o.background_version and not o.background_reused and o.status not in ("FAILED", "SKIPPED")),
        "generated_label": GENERATED_LABEL_KO,
    }


__all__ = ["router", "get_ai_background_credential_store", "AiBackgroundSettingsRequest", "AiBackgroundRunRequest"]
