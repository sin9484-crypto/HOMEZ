"""
=========================================================
Homez OS

File : app/domains/ai_learning/router.py

2026-09-10 Phase 12 — AI 데이터·학습 기반 API. 전부 SuperAdminGuard.
승인(approve)만 X-Recent-Auth-Token을 추가로 요구한다 — 후보 모델
승인은 향후 실제 라이브 정책에 영향을 줄 수 있는 결정이라 결제·환불
승인과 동일한 민감도로 취급한다.
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import Query
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import UnauthorizedException
from app.core.guard import SuperAdminGuard
from app.core.recent_auth import consume_recent_auth_token
from app.domains.ai_learning.schema import DecisionOutcomeResponse
from app.domains.ai_learning.schema import ExportDatasetRequest
from app.domains.ai_learning.schema import LearningDatasetRecordResponse
from app.domains.ai_learning.schema import LearningReadinessResponse
from app.domains.ai_learning.schema import MarkOfflineEvaluatedRequest
from app.domains.ai_learning.schema import MarkRegressionComparedRequest
from app.domains.ai_learning.schema import ModelCandidateCreateRequest
from app.domains.ai_learning.schema import ModelCandidateResponse
from app.domains.ai_learning.schema import RecordOutcomeRequest
from app.domains.ai_learning.schema import RejectModelCandidateRequest
from app.domains.ai_learning.schema import SuggestionRecordCreateRequest
from app.domains.ai_learning.schema import SuggestionRecordUpdateRequest
from app.domains.ai_learning.service import AiLearningService
from app.domains.user.model import User

router = APIRouter(prefix="/ai-learning", tags=["ai-learning"])


def get_ai_learning_service(
    db: Session = Depends(get_db),
) -> AiLearningService:

    return AiLearningService(db)


def _require_recent_auth(token: str | None, user_id: int) -> None:

    if not consume_recent_auth_token(token, user_id):
        raise UnauthorizedException(
            "이 작업을 수행하려면 현재 비밀번호로 다시 확인해야 합니다.",
        )


@router.post("/outcomes", response_model=DecisionOutcomeResponse)
def record_outcome(
    data: RecordOutcomeRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    return service.record_outcome(
        company_id=current_user.company_id,
        evaluation_id=data.evaluation_id, order_id=data.order_id,
        actual_sales_amount=data.actual_sales_amount,
        actual_margin_amount=data.actual_margin_amount,
        stockout=data.stockout, cancelled=data.cancelled,
        returned=data.returned, shipping_delayed=data.shipping_delayed,
        recorded_by=current_user.id,
    )


@router.post("/dataset/export", response_model=LearningDatasetRecordResponse)
def export_dataset_record(
    data: ExportDatasetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    return service.export_to_learning_dataset(
        current_user.company_id, data.evaluation_id,
    )


@router.get("/dataset/readiness", response_model=LearningReadinessResponse)
def get_learning_readiness(
    total_completed_orders: int = Query(...),
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    ready, reason = service.check_learning_readiness(
        current_user.company_id, total_completed_orders,
    )

    return LearningReadinessResponse(
        ready=ready, reason=reason,
        available=service.get_dataset_size(current_user.company_id),
        required=service.get_required_sample_size(total_completed_orders),
    )


@router.post("/model-candidates", response_model=ModelCandidateResponse)
def create_model_candidate(
    data: ModelCandidateCreateRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    return service.create_model_candidate(
        company_id=current_user.company_id, user_id=current_user.id,
        is_admin=True, name=data.name, version=data.version,
    )


@router.get("/model-candidates", response_model=list[ModelCandidateResponse])
def list_model_candidates(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    return service.list_candidates(
        current_user.company_id, status=status_filter,
    )


@router.post(
    "/model-candidates/{candidate_id}/offline-evaluated",
    response_model=ModelCandidateResponse,
)
def mark_offline_evaluated(
    candidate_id: int,
    data: MarkOfflineEvaluatedRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    return service.mark_offline_evaluated(
        candidate_id=candidate_id, company_id=current_user.company_id,
        user_id=current_user.id, is_admin=True, summary=data.summary,
        sample_size_used=data.sample_size_used,
    )


@router.post(
    "/model-candidates/{candidate_id}/regression-compared",
    response_model=ModelCandidateResponse,
)
def mark_regression_compared(
    candidate_id: int,
    data: MarkRegressionComparedRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    return service.mark_regression_compared(
        candidate_id=candidate_id, company_id=current_user.company_id,
        user_id=current_user.id, is_admin=True, summary=data.summary,
    )


@router.post(
    "/model-candidates/{candidate_id}/approve",
    response_model=ModelCandidateResponse,
)
def approve_model_candidate(
    candidate_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """
    이 엔드포인트는 "사람이 승인했다"는 사실만 기록한다 — 실제로
    이 후보를 라이브 정책에 적용하는 코드는 없다(app/domains/
    ai_learning/service.py 상단 주석 참고).
    """

    _require_recent_auth(recent_auth_token, current_user.id)

    return service.approve_model_candidate(
        candidate_id=candidate_id, company_id=current_user.company_id,
        user_id=current_user.id, is_admin=True,
    )


@router.post(
    "/model-candidates/{candidate_id}/reject",
    response_model=ModelCandidateResponse,
)
def reject_model_candidate(
    candidate_id: int,
    data: RejectModelCandidateRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: AiLearningService = Depends(get_ai_learning_service),
):

    return service.reject_model_candidate(
        candidate_id=candidate_id, company_id=current_user.company_id,
        user_id=current_user.id, is_admin=True, reason=data.reason,
    )


# ---- 2026-10-23 마케팅 AI M4-full: 채택한 제안 기록과 전·후 성과 비교 ----
# 이 라우터의 모든 경로는 SuperAdminGuard라는 기존 계약(tests/test_ai_learning_router_guard.py)을 그대로 따른다.
# 테이블은 Migration 20261023_00이 만든다 — 앱의 Migration 승인 전에는 이 경로가 동작하지 않는다.


@router.get("/suggestions")
def list_suggestion_records(
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):
    from app.domains.ai_learning.suggestion_record_service import SuggestionRecordService

    return SuggestionRecordService(db).list(current_user.company_id, limit)


@router.post("/suggestions")
def create_suggestion_record(
    data: SuggestionRecordCreateRequest,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):
    """채택해 쿠팡에 반영한 제안 1건을 기록한다(HOMEZ DB에만, 쿠팡 쓰기 없음)."""

    from app.domains.ai_learning.suggestion_record_service import SuggestionRecordService

    return SuggestionRecordService(db).create(
        current_user.company_id, current_user.id, data.kind, data.suggested_value, data.suggested_on, data.product_ref, data.vendor_item_id, data.adopted,
    )


@router.post("/suggestions/evaluate")
def evaluate_suggestion_records(
    days: int = Query(default=30, ge=1, le=31),
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):
    """사용자가 누를 때만 쿠팡 매출내역을 읽어(허용이 있을 때만, 읽기 전용) 기록마다 제안일 전·후를 나란히 보여 준다. 판정하지 않는다."""

    from app.domains.ai_learning.suggestion_record_service import SuggestionRecordService

    return SuggestionRecordService(db).evaluate(current_user.company_id, days)


@router.patch("/suggestions/{record_id}")
def update_suggestion_record(
    record_id: int,
    data: SuggestionRecordUpdateRequest,
    current_user: User = Depends(SuperAdminGuard),
    db: Session = Depends(get_db),
):
    from app.domains.ai_learning.suggestion_record_service import SuggestionRecordService

    fields = data.model_dump(exclude_unset=True)
    return SuggestionRecordService(db).update(
        current_user.company_id, record_id, adopted=fields.get("adopted"), vendor_item_id=fields["vendor_item_id"] if "vendor_item_id" in fields else ...,
    )


__all__ = ["router"]
