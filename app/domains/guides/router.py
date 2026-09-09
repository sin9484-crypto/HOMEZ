"""
=========================================================
Homez OS

File : app/domains/guides/router.py

HOMEZ V6.5 사용자 가이드 API. admin_guard가 아니라 get_current_user다
— 가이드는 모든 역할(Manager/Staff/Viewer 포함)이 읽을 수 있어야
한다는 요구사항 그대로다(notification_center/router.py와 동일한
근거). 네 엔드포인트(Gate AI-F2에서 /ask 추가) 전부 읽기 전용 — 어떤
데이터도 변경하지 않는다. /ask도 질문 문자열을 GET 쿼리 파라미터로만
받는다(요청 바디 없음, 다른 세 엔드포인트와 동일한 GET-only 계약을
그대로 따른다).

`/guides/files/{file_path:path}`는 반드시 `/guides/{guide_id}`보다
먼저 등록한다 — FastAPI가 경로를 등록 순서대로 매칭하므로, path
컨버터 라우트를 뒤에 두면 일부 클라이언트/프록시 조합에서 의도치
않게 먼저 매칭될 수 있다는 FastAPI 공식 권고를 따른다.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import FileResponse

from app.core.auth import get_current_user
from app.domains.guides import service
from app.domains.guides.constants import MEDIA_TYPE_BY_EXTENSION
from app.domains.guides.interactive_guidance_service import (
    InteractiveGuidanceService,
)
from app.domains.guides.schema import GuidanceAskResponse
from app.domains.guides.schema import GuideListResponse
from app.domains.guides.schema import GuideSummary
from app.domains.user.model import User

router = APIRouter(
    prefix="/guides",
    tags=["Guides"],
)


@router.get(
    "/files/{file_path:path}",
    include_in_schema=False,
)
def get_guide_file(
    file_path: str,
    _: User = Depends(get_current_user),
):
    """
    `docs/guides/` 아래 실제 파일만 서빙한다(경로 탈출·확장자
    allowlist는 service.resolve_safe_path가 전담). 존재하지 않거나
    허용되지 않은 요청은 전부 동일하게 404다 — 경로 조작 시도와
    단순 오타를 구분해 추가 정보를 주지 않는다.
    """

    resolved = service.resolve_safe_path(file_path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="not found")

    media_type = MEDIA_TYPE_BY_EXTENSION.get(resolved.suffix.lower())
    return FileResponse(resolved, media_type=media_type)


@router.get(
    "",
    response_model=GuideListResponse,
)
def list_guides(
    _: User = Depends(get_current_user),
):
    """5개 가이드 목록 — 영상/문서/PDF/자막 가용 상태를 실제 파일
    존재 여부 기준으로 계산해 함께 반환한다."""

    return GuideListResponse(
        version="6.5",
        guides=service.list_guides(),
    )


@router.get(
    "/ask",
    response_model=GuidanceAskResponse,
)
def ask_guidance(
    query: str = Query(default=""),
    locale: str = Query(default="ko-KR"),
    _: User = Depends(get_current_user),
):
    """Gate AI-F2(2026-08-22) — 정적 키워드 매칭 기반 가이드 추천.
    다른 세 엔드포인트와 동일하게 GET + 쿼리 파라미터만 쓴다(요청
    바디 없음 — 가이드 화면은 데이터를 변경하지 않는다는 이 라우터의
    기존 계약을 그대로 따른다). `/{guide_id}`보다 먼저 등록해야 한다
    (위 파일 헤더의 경로 순서 원칙과 동일한 이유 — 그렇지 않으면
    "ask"가 guide_id로 오인될 수 있다)."""

    suggested, envelope = InteractiveGuidanceService().ask(
        query, locale=locale,
    )

    return GuidanceAskResponse(
        suggested_guides=suggested,
        ai_result=envelope.model_dump(mode="json"),
    )


@router.get(
    "/{guide_id}",
    response_model=GuideSummary,
)
def get_guide(
    guide_id: str,
    _: User = Depends(get_current_user),
):

    summary = service.get_guide(guide_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="guide not found")

    return summary


__all__ = ["router"]
