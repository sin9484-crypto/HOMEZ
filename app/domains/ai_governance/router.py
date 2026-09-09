"""
=========================================================
Homez OS

File : app/domains/ai_governance/router.py

AI Capability Registry — 읽기 전용 조회 API. UI가 "이 기능은 규칙
기반/계산/외부 생성형 AI/Fake 중 무엇인가"를 명확히 구분해 보여줄 수
있도록 한다(CA-5 요구 — UI 구분 표시).
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import Query
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.guard import AdminGuard
from app.domains.ai_governance.constants import ProposedActionStatus
from app.domains.automation_safety.service import SafetyService
from app.domains.ai_governance.proposed_action_service import (
    ProposedActionService,
)
from app.domains.ai_governance.schema import ApproveProposedActionRequest
from app.domains.ai_governance.schema import CapabilityContractResponse
from app.domains.ai_governance.schema import ProposedActionResponse
from app.domains.ai_governance.schema import RejectProposedActionRequest
from app.domains.ai_governance.service import get_capability
from app.domains.ai_governance.service import list_capabilities
from app.domains.user.model import User

router = APIRouter(prefix="/ai-governance", tags=["AI Governance"])


@router.get("/capabilities", response_model=list[CapabilityContractResponse])
def list_ai_capabilities(
    current_user: User = Depends(AdminGuard),
):

    return list_capabilities()


@router.get(
    "/capabilities/{capability_code}",
    response_model=CapabilityContractResponse,
)
def get_ai_capability(
    capability_code: str,
    current_user: User = Depends(AdminGuard),
):
    """관리자 조회 전용 — 비활성 항목도 그대로 보여준다(왜
    비활성인지 확인할 수 있어야 하므로 require_active_capability()
    가 아니라 get_capability()를 쓴다 — 그 강제 자체는 각 도메인
    호출 지점의 책임이다)."""

    contract = get_capability(capability_code)
    if contract is None:
        raise NotFoundException(
            f"등록된 AI capability가 아닙니다: {capability_code}",
        )
    return contract


# --------------------------------------------------
# Gate AI-F(2026-08-22) — ProposedAction 사용자 진입점. AdminGuard로
# 통제한다(가격·재고·주문 등 실행 관련 제안이라 pricing/inventory
# Router와 동일한 민감도로 취급 — app/domains/inventory/router.py의
# admin_guard 전역 컨벤션과 동일한 이유).
# --------------------------------------------------

@router.get(
    "/proposed-actions", response_model=list[ProposedActionResponse],
)
def list_proposed_actions(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """상태 필터(status=REVIEW_REQUIRED 등) — 생략하면 전체. 회사
    격리는 Service의 company_id 스코프 쿼리가 강제한다."""

    if status_filter is not None and status_filter not in ProposedActionStatus.ALL:
        raise NotFoundException(f"알 수 없는 상태값입니다: {status_filter}")

    service = ProposedActionService(db)
    actions = service.list_for_company(
        current_user.company_id, status=status_filter,
    )

    return [ProposedActionResponse.from_model(a) for a in actions]


@router.get(
    "/proposed-actions/{action_id}", response_model=ProposedActionResponse,
)
def get_proposed_action(
    action_id: int,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = ProposedActionService(db)
    action = service.repository.get_for_company(
        action_id, current_user.company_id,
    )
    if action is None:
        raise NotFoundException("ProposedAction을 찾을 수 없습니다.")

    return ProposedActionResponse.from_model(action)


@router.post(
    "/proposed-actions/{action_id}/approve",
    response_model=ProposedActionResponse,
)
def approve_proposed_action(
    action_id: int,
    data: ApproveProposedActionRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """
    승인자는 항상 current_user.id — 요청 바디에 승인자 필드 자체가
    없다(클라이언트가 자칭할 수 없다). 고위험 action_type은
    ProposedActionService.approve()가 recent_auth_token 없이는
    거부한다(`X-Recent-Auth-Token` 헤더 — app/domains/company/
    router.py·app/core/migration_approval.py와 동일한 기존 관례,
    요청 바디에 두지 않는다). EStop이 활성화되어 있으면 승인 자체를
    차단한다 — 승인은 실행을 향한 단계이지 단순 조회가 아니다.
    """

    if SafetyService(db).is_emergency_stop_active():
        raise BadRequestException(
            "Emergency Stop이 활성화되어 있어 제안을 승인할 수 "
            "없습니다.",
        )

    service = ProposedActionService(db)
    action = service.approve(
        action_id, current_user.company_id,
        approved_by=current_user.id,
        current_payload_for_refingerprint=(
            data.current_payload_for_refingerprint
        ),
        decision_reason=data.decision_reason,
        recent_auth_token=recent_auth_token,
    )

    return ProposedActionResponse.from_model(action)


@router.post(
    "/proposed-actions/{action_id}/reject",
    response_model=ProposedActionResponse,
)
def reject_proposed_action(
    action_id: int,
    data: RejectProposedActionRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = ProposedActionService(db)
    action = service.reject(
        action_id, current_user.company_id,
        rejected_by=current_user.id, reason=data.reason,
    )

    return ProposedActionResponse.from_model(action)


__all__ = ["router"]
