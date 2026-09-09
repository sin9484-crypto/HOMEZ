"""
=========================================================
Homez OS

File : app/domains/settlement/router.py

Marketplace Settlement Router
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.guard import admin_guard
from app.domains.automation_safety.service import SafetyService
from app.domains.settlement.difference_analysis_service import (
    SettlementDifferenceAnalysisService,
)
from app.domains.settlement.schema import SettlementCreate
from app.domains.settlement.schema import SettlementDifferenceAnalysisResult
from app.domains.settlement.schema import (
    SettlementDifferenceReviewActionCreate,
)
from app.domains.settlement.schema import (
    SettlementDifferenceReviewActionResponse,
)
from app.domains.settlement.schema import SettlementDifferenceResponse
from app.domains.settlement.schema import SettlementMemoUpdate
from app.domains.settlement.schema import SettlementResponse
from app.domains.settlement.service import SettlementService
from app.domains.user.model import User


router = APIRouter(
    prefix="/settlements",
    tags=["Settlement"],
)


@router.post(
    "",
    response_model=SettlementResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_settlement(
    data: SettlementCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """마켓 정산 예정 생성 (PENDING)."""

    service = SettlementService(db)

    return service.create(data, current_user.company_id)


@router.get(
    "",
    response_model=list[SettlementResponse],
)
def list_settlements(
    market: str | None = Query(default=None),
    status_filter: str | None = Query(
        default=None,
        alias="status",
    ),
    order_id: int | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SettlementService(db)

    return service.list(
        current_user.company_id,
        market=market,
        status=status_filter,
        order_id=order_id,
        skip=skip,
        limit=limit,
    )



# --------------------------------------------------
# Gate AI-F2(2026-08-22) — 정산 차이 분석(읽기 전용). 반드시 아래
# "/{settlement_id}"보다 먼저 등록한다 — FastAPI/Starlette는 경로를
# 등록 순서대로 매칭하므로, 동적 경로를 앞에 두면 "/settlements/
# difference-analysis"의 "difference-analysis"가 settlement_id로
# 오인되어 422(정수 파싱 실패)가 난다(실제 Browser E2E로 재현·확인
# 후 이 위치로 옮겼다 — app/domains/order/router.py의 동일 수정과
# 같은 원인).
# --------------------------------------------------

@router.get(
    "/difference-analysis",
    response_model=SettlementDifferenceAnalysisResult,
)
def analyze_settlement_differences(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SettlementDifferenceAnalysisService(db)
    differences, envelope = service.analyze(current_user.company_id)

    return SettlementDifferenceAnalysisResult(
        differences=[
            SettlementDifferenceResponse(
                difference_type=d.difference_type, urgency=d.urgency,
                target_entity=d.target_entity, evidence=d.evidence,
                elapsed_hours=d.elapsed_hours,
                recommended_action=d.recommended_action,
                missing_evidence=d.missing_evidence,
                approval_required=d.approval_required,
                execution_allowed=d.execution_allowed,
            )
            for d in differences
        ],
        ai_result=envelope.model_dump(mode="json"),
    )


@router.post(
    "/difference-analysis/review-actions",
    response_model=SettlementDifferenceReviewActionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_settlement_difference_review_action(
    data: SettlementDifferenceReviewActionCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """요청 바디의 difference_type/target_entity로 지금 이 순간의
    analyze() 결과에서 다시 찾은 차이만 사용한다 — 클라이언트가 보낸
    urgency/evidence/execution_allowed는 존재하지 않으므로 신뢰할
    필요조차 없다."""

    if SafetyService(db).is_emergency_stop_active():
        raise BadRequestException(
            "Emergency Stop이 활성화되어 있어 새 검토 제안을 만들 수 "
            "없습니다.",
        )

    service = SettlementDifferenceAnalysisService(db)
    differences, _envelope = service.analyze(current_user.company_id)

    match = next(
        (
            d for d in differences
            if d.difference_type == data.difference_type
            and d.target_entity == data.target_entity
        ),
        None,
    )
    if match is None:
        raise NotFoundException(
            "해당 차이를 다시 찾을 수 없습니다 — 이미 해소되었거나 "
            "상태가 바뀌었을 수 있습니다.",
        )

    action = service.create_review_action(
        current_user.company_id, match,
        idempotency_key=data.idempotency_key, created_by=current_user.id,
    )

    return SettlementDifferenceReviewActionResponse(proposed_action=action)


@router.get(
    "/{settlement_id}",
    response_model=SettlementResponse,
)
def get_settlement(
    settlement_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = SettlementService(db)

    return service.get(settlement_id, current_user.company_id)


@router.post(
    "/{settlement_id}/confirm-deposit",
    response_model=SettlementResponse,
)
def confirm_deposit(
    settlement_id: int,
    data: SettlementMemoUpdate | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    입금 확인 + Funding Add.

    DEPOSITED 재호출 시 Funding Add 없이 기존 반환.
    """

    service = SettlementService(db)

    return service.confirm_deposit(
        settlement_id, current_user.company_id, data,
    )


@router.post(
    "/{settlement_id}/cancel",
    response_model=SettlementResponse,
)
def cancel_settlement(
    settlement_id: int,
    data: SettlementMemoUpdate | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """PENDING → CANCELLED."""

    service = SettlementService(db)

    return service.cancel(settlement_id, current_user.company_id, data)


@router.post(
    "/{settlement_id}/reverse",
    response_model=SettlementResponse,
)
def reverse_settlement(
    settlement_id: int,
    data: SettlementMemoUpdate | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    DEPOSITED → REVERSED + Funding Remove.

    REVERSED 재호출 시 재차감 금지.
    """

    service = SettlementService(db)

    return service.reverse(settlement_id, current_user.company_id, data)


# --------------------------------------------------
# HELD / MISMATCH (2026-08-15 V7 Gate 5, 요구사항 4)
# --------------------------------------------------

@router.post(
    "/{settlement_id}/hold",
    response_model=SettlementResponse,
)
def hold_settlement(
    settlement_id: int,
    data: SettlementMemoUpdate | None = None,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """PENDING → HELD. 조사를 위해 입금 확인을 잠시 막는다."""

    service = SettlementService(db)

    return service.hold(settlement_id, current_user.company_id, data)


@router.post(
    "/{settlement_id}/release-hold",
    response_model=SettlementResponse,
)
def release_settlement_hold(
    settlement_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """HELD → PENDING."""

    service = SettlementService(db)

    return service.release_hold(settlement_id, current_user.company_id)


@router.post(
    "/{settlement_id}/flag-mismatch",
    response_model=SettlementResponse,
)
def flag_settlement_mismatch(
    settlement_id: int,
    data: SettlementMemoUpdate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    PENDING/HELD → MISMATCH. 금액을 자동으로 고치지 않는다 — 플래그만
    한다.
    """

    service = SettlementService(db)

    return service.flag_mismatch(settlement_id, current_user.company_id, data)


@router.post(
    "/{settlement_id}/resolve-mismatch",
    response_model=SettlementResponse,
)
def resolve_settlement_mismatch(
    settlement_id: int,
    data: SettlementMemoUpdate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    MISMATCH → PENDING. 금액을 자동으로 고치지 않는다 — 관리자의
    수동 조사 완료 확인일 뿐이다(근거 memo 필수).
    """

    service = SettlementService(db)

    return service.resolve_mismatch(
        settlement_id, current_user.company_id, data,
    )


__all__ = [
    "router",
]
