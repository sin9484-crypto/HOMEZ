"""
=========================================================
Homez OS

File : app/domains/orchestration/router.py

읽기 전용 projection API — admin_guard로 보호, company 격리.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from pydantic import BaseModel
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.orchestration.dashboard_service import DashboardService
from app.domains.orchestration.priority_service import (
    OperationsPriorityService,
)
from app.domains.orchestration.projection_service import (
    OrchestrationProjectionService,
)
from app.domains.user.model import User

router = APIRouter(prefix="/orchestration", tags=["Orchestration"])


@router.get("/order-items/{order_item_id}/stage")
def get_order_item_stage(
    order_item_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = OrchestrationProjectionService(db)
    stage = service.project_order_item_stage(
        order_item_id, current_user.company_id,
    )

    return {"order_item_id": order_item_id, "stage": stage}


@router.get("/dashboard/summary")
def get_dashboard_summary(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DashboardService(db)

    return service.get_summary(current_user.company_id)


@router.get("/priorities")
def get_operations_priorities(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """Gate AI-F2(2026-08-22) — 운영 우선순위 읽기 전용 집계. 위
    /dashboard/summary(단순 COUNT)와 완전히 별개다 — AI Capability
    상태와 무관하게 그 엔드포인트는 항상 그대로 동작한다."""

    service = OperationsPriorityService(db)
    items, envelope = service.analyze(current_user.company_id)

    return {
        "items": [
            {
                "category": i.category,
                "urgency": i.urgency,
                "label": i.label,
                "count": i.count,
                "source_capability": i.source_capability,
            }
            for i in items
        ],
        "ai_result": envelope.model_dump(mode="json"),
    }


@router.get("/assistant/answer")
def get_assistant_answer(
    question_id: str,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """2026-11-01 AI 통합 계획 5단계(비용 0) — 비서에게 묻기. 정해진 질문 번호에 실제 기록을 규칙으로 센 답(읽기 전용,
    바깥 호출 없음). 사용자가 적은 문장은 받지 않는다 — 화면이 질문 목록과 맞춘 번호만 온다."""

    from app.core.exceptions import BadRequestException
    from app.domains.orchestration.assistant_qa_service import AssistantQaService

    try:
        return AssistantQaService(db).answer(current_user.company_id, current_user.id, question_id)
    except KeyError:
        raise BadRequestException("모르는 질문입니다.") from None


def get_assistant_ai_credential_store():
    """AI 키 저장소 — 항상 실제 Windows 자격증명 저장소. 테스트는 이 의존성을 덮어쓴다."""

    from app.core.windows_credential_store import WindowsCredentialStore

    return WindowsCredentialStore()


class AssistantAskRequest(BaseModel):
    text: str = Field(..., max_length=500)


@router.post("/assistant/ask-ai")
def ask_assistant_ai(
    data: AssistantAskRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    credential_store=Depends(get_assistant_ai_credential_store),
):
    """2026-11-01 AI 통합 계획 5단계(Claude 부분) — 정해진 질문에 맞지 않는 문장을 사용자가 확인했을 때만 AI(Claude)가 답한다
    (월 한도·승인 센터 허용 `AI_ADVICE` 필요). 숫자는 서버가 규칙으로 센 답에서만 오고, 개인정보로 보이는 것은 보내기 전에 가린다.
    질문 글과 답은 저장하지 않는다."""

    from app.domains.marketplace_listing.ai_draft_service import AiDraftService
    from app.domains.orchestration.assistant_ask_ai_service import AssistantAskAiService

    return AssistantAskAiService(db, AiDraftService(db, credential_store)).ask(current_user.company_id, current_user.id, data.text)


__all__ = [
    "router",
]
