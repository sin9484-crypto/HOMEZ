"""
=========================================================
Homez OS

File : app/domains/decision/router.py

HOMEZ V4 Decision AI — 운영자 API

로그인 전에는 접근할 수 없다 — 전부 admin_guard로 보호된다(UI 숨김
뿐 아니라 서버에서도 권한을 검사한다). 자동 실행 엔드포인트는
존재하지 않는다 — 평가 실행/조회, 검토(승인·보류·거절·override),
감사 로그 조회, 정책 조회/생성만 제공한다.

2026-08-14 테넌트 격리 감사(Gate R13) — 평가 관련 전 엔드포인트가
current_user.company_id를 Service로 실제 전달한다. 정책 엔드포인트는
전역이라 company_id를 넘기지 않는다(기존 그대로).
=========================================================
"""

import uuid

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.desktop_auth import require_desktop_token
from app.core.guard import admin_guard
from app.domains.decision.schema import DecisionCompanyPolicyCreateRequest
from app.domains.decision.schema import DecisionCompanyPolicyResponse
from app.domains.decision.schema import DecisionEvaluationRequest
from app.domains.decision.schema import DecisionEvaluationResponse
from app.domains.decision.schema import DecisionOverrideRequest
from app.domains.decision.schema import DecisionPolicyCreateRequest
from app.domains.decision.schema import DecisionPolicyResponse
from app.domains.decision.schema import DecisionReviewRequest
from app.domains.decision.schema import DecisionReviewResponse
from app.domains.decision.schema import DecisionScoreResponse
from app.domains.decision.service import DecisionService
from app.domains.user.model import User

router = APIRouter(
    prefix="/decisions",
    tags=["Decision AI"],
)

policy_router = APIRouter(
    prefix="/decision-policies",
    tags=["Decision Policy"],
)


@router.post(
    "/evaluate",
    response_model=DecisionEvaluationResponse,
)
def evaluate_candidate(
    data: DecisionEvaluationRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):
    """
    평가를 실행(또는 재요청 — 동일 idempotency_key면 기존 결과 반환)
    한다. Emergency Stop이 활성화되어 있으면 즉시 차단되고, 사용 가능한
    정책이 없으면 fail-closed로 차단된다. 이 호출만으로는 어떤 상태도
    자동으로 APPROVED가 되지 않는다.
    """

    service = DecisionService(db)
    evaluation, _scores, _duplicate = service.evaluate_candidate(
        data.candidate_id,
        current_user.company_id,
        idempotency_key=data.idempotency_key,
        supplementary_inputs=data.supplementary_inputs,
    )

    return evaluation


@router.get(
    "/pending-review",
    response_model=list[DecisionEvaluationResponse],
)
def list_pending_review(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.list_pending_review(
        current_user.company_id, skip=skip, limit=limit,
    )


@router.get(
    "",
    response_model=list[DecisionEvaluationResponse],
)
def list_evaluations(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.list_evaluations(
        current_user.company_id, skip=skip, limit=limit,
    )


@router.get(
    "/{evaluation_id}",
    response_model=DecisionEvaluationResponse,
)
def get_evaluation(
    evaluation_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.get_evaluation(evaluation_id, current_user.company_id)


@router.get(
    "/{evaluation_id}/scores",
    response_model=list[DecisionScoreResponse],
)
def get_scores(
    evaluation_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.list_scores(evaluation_id, current_user.company_id)


@router.get(
    "/{evaluation_id}/reviews",
    response_model=list[DecisionReviewResponse],
)
def get_reviews(
    evaluation_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.list_reviews(evaluation_id, current_user.company_id)


@router.post(
    "/{evaluation_id}/approve",
    response_model=DecisionEvaluationResponse,
)
def approve_evaluation(
    evaluation_id: int,
    data: DecisionReviewRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):

    from app.domains.decision.constants import ReviewAction

    service = DecisionService(db)
    evaluation, _duplicate = service.review_evaluation(
        evaluation_id, current_user.company_id, ReviewAction.APPROVE,
        operator_id=current_user.id, is_admin=True,
        idempotency_key=data.idempotency_key, memo=data.memo,
    )

    return evaluation


@router.post(
    "/{evaluation_id}/hold",
    response_model=DecisionEvaluationResponse,
)
def hold_evaluation(
    evaluation_id: int,
    data: DecisionReviewRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):

    from app.domains.decision.constants import ReviewAction

    service = DecisionService(db)
    evaluation, _duplicate = service.review_evaluation(
        evaluation_id, current_user.company_id, ReviewAction.HOLD,
        operator_id=current_user.id, is_admin=True,
        idempotency_key=data.idempotency_key, memo=data.memo,
    )

    return evaluation


@router.post(
    "/{evaluation_id}/reject",
    response_model=DecisionEvaluationResponse,
)
def reject_evaluation(
    evaluation_id: int,
    data: DecisionReviewRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):

    from app.domains.decision.constants import ReviewAction

    service = DecisionService(db)
    evaluation, _duplicate = service.review_evaluation(
        evaluation_id, current_user.company_id, ReviewAction.REJECT,
        operator_id=current_user.id, is_admin=True,
        idempotency_key=data.idempotency_key, memo=data.memo,
    )

    return evaluation


@router.post(
    "/{evaluation_id}/override",
    response_model=DecisionEvaluationResponse,
)
def override_evaluation(
    evaluation_id: int,
    data: DecisionOverrideRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
    _desktop: None = Depends(require_desktop_token),
):
    """
    AI 추천을 운영자가 재정의한다. 권한(admin_guard)·사유
    (override_reason)·이전 값·새 값·시각이 DecisionReview +
    DecisionAuditLog에 전부 남는다.
    """

    from app.domains.decision.constants import ReviewAction

    service = DecisionService(db)
    evaluation, _duplicate = service.review_evaluation(
        evaluation_id, current_user.company_id, ReviewAction.OVERRIDE,
        operator_id=current_user.id, is_admin=True,
        idempotency_key=data.idempotency_key, memo=data.memo,
        override_reason=data.override_reason, new_value=data.new_value,
    )

    return evaluation


@router.get(
    "/candidates/{candidate_id}/audit-log",
)
def get_audit_log_for_candidate(
    candidate_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)
    rows = service.list_audit_log_for_candidate(
        candidate_id, current_user.company_id,
    )

    return [
        {
            "id": row.id,
            "evaluation_id": row.evaluation_id,
            "candidate_id": row.candidate_id,
            "event_type": row.event_type,
            "actor": row.actor,
            "payload_summary": row.payload_summary,
            "occurred_at": row.occurred_at,
        }
        for row in rows
    ]


# --------------------------------------------------
# 정책 관리 (admin 전용, 별도 명시 작업, 전역)
# --------------------------------------------------

@policy_router.post(
    "",
    response_model=DecisionPolicyResponse,
)
def create_policy(
    data: DecisionPolicyCreateRequest,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.create_policy(data)


@policy_router.get(
    "",
    response_model=list[DecisionPolicyResponse],
)
def list_policies(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.list_policies()


# --------------------------------------------------
# 회사별 적용 정책 (2026-08-15 V7 Gate 2, 요구사항 1)
# --------------------------------------------------

company_policy_router = APIRouter(
    prefix="/decision-company-policies",
    tags=["Decision Policy"],
)


@company_policy_router.post(
    "",
    response_model=DecisionCompanyPolicyResponse,
)
def create_company_policy(
    data: DecisionCompanyPolicyCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    이 회사 전용 적용 정책을 만든다 — company_id는 항상
    current_user.company_id에서 가져온다(요청 바디로 받지 않는다,
    다른 회사 명의로 정책을 만드는 경로 원천 차단).
    """

    service = DecisionService(db)

    return service.create_company_policy(data, current_user.company_id)


@company_policy_router.get(
    "",
    response_model=list[DecisionCompanyPolicyResponse],
)
def list_company_policies(
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = DecisionService(db)

    return service.list_company_policies(current_user.company_id)


__all__ = [
    "router",
    "policy_router",
    "company_policy_router",
]
