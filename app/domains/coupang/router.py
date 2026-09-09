"""
=========================================================
Homez OS

File : app/domains/coupang/router.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation — 운영자 API

이번 단계 범위(초안 생성 → 정책 검증 → 수익성 계산 → Dry Run → 최종
승인)만 제공한다. 실제 쿠팡 상품 등록·주문 수집·발주를 실행하는
엔드포인트는 존재하지 않는다.

2026-08-14 테넌트 격리 감사(Gate R13) — 상품 관련 전 엔드포인트가
current_user.company_id를 Service로 실제 전달한다. 정책 세트/규칙
엔드포인트는 전역이라 company_id를 넘기지 않는다(기존 그대로).
=========================================================
"""

import uuid

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.coupang.schema import CoupangApprovalRequest
from app.domains.coupang.schema import CoupangDraftCreateRequest
from app.domains.coupang.schema import CoupangDryRunRequest
from app.domains.coupang.schema import CoupangDryRunResponse
from app.domains.coupang.schema import CoupangPolicyRuleCreateRequest
from app.domains.coupang.schema import CoupangPolicyRuleResponse
from app.domains.coupang.schema import CoupangPolicySetCreateRequest
from app.domains.coupang.schema import CoupangPolicySetResponse
from app.domains.coupang.schema import CoupangProductNoticeResponse
from app.domains.coupang.schema import CoupangProductOptionResponse
from app.domains.coupang.schema import CoupangProductResponse
from app.domains.coupang.schema import CoupangProfitEstimateRequest
from app.domains.coupang.schema import CoupangProfitEstimateResponse
from app.domains.coupang.service import CoupangIntegrationService
from app.domains.user.model import User

router = APIRouter(
    prefix="/coupang-products",
    tags=["Coupang Integration"],
)

policy_router = APIRouter(
    prefix="/coupang-policy-sets",
    tags=["Coupang Policy"],
)


@router.post(
    "",
    response_model=CoupangProductResponse,
)
def create_draft(
    data: CoupangDraftCreateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)
    product, _duplicate, _events = service.create_draft(
        data, current_user.company_id, correlation_id=str(uuid.uuid4()),
    )

    return product


@router.get(
    "",
    response_model=list[CoupangProductResponse],
)
def list_products(
    status: str | None = Query(default=None),
    sales_method: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.list_products(
        current_user.company_id,
        status=status, sales_method=sales_method, skip=skip, limit=limit,
    )


@router.get(
    "/{product_id}",
    response_model=CoupangProductResponse,
)
def get_product(
    product_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.get(product_id, current_user.company_id)


@router.get(
    "/{product_id}/options",
    response_model=list[CoupangProductOptionResponse],
)
def get_product_options(
    product_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.list_options(product_id, current_user.company_id)


@router.get(
    "/{product_id}/notices",
    response_model=list[CoupangProductNoticeResponse],
)
def get_product_notices(
    product_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.list_notices(product_id, current_user.company_id)


@router.post(
    "/{product_id}/validate-policy",
    response_model=CoupangProductResponse,
)
def validate_policy(
    product_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    쿠팡 정책·금지상품 검증을 실행한다. AI/운영자 누구도 이 결과를
    임의로 완화(PROHIBITED 해제)할 수 없다 — 이 엔드포인트는 검증 결과를
    있는 그대로 반영할 뿐이다.
    """

    service = CoupangIntegrationService(db)
    product, _events = service.validate_policy(
        product_id, current_user.company_id, correlation_id=str(uuid.uuid4()),
    )

    return product


@router.post(
    "/{product_id}/profit-estimate",
    response_model=CoupangProfitEstimateResponse,
)
def estimate_profit(
    product_id: int,
    data: CoupangProfitEstimateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)
    estimate, _events = service.estimate_profit(
        product_id, current_user.company_id, data,
        correlation_id=str(uuid.uuid4()),
    )

    return estimate


@router.get(
    "/{product_id}/profit-estimates",
    response_model=list[CoupangProfitEstimateResponse],
)
def get_profit_estimates(
    product_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.list_profit_estimates(product_id, current_user.company_id)


@router.post(
    "/{product_id}/dry-run",
    response_model=CoupangDryRunResponse,
)
def run_dry_run(
    product_id: int,
    data: CoupangDryRunRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    Dry Run만 수행한다 — 실제 쿠팡 네트워크 호출이나 실제 상품 등록은
    하지 않는다(app/domains/coupang/gateway.py의 CoupangDryRunGateway
    참고).
    """

    service = CoupangIntegrationService(db)
    _product, result, duplicate, _events = service.run_dry_run(
        product_id,
        current_user.company_id,
        idempotency_key=data.idempotency_key,
        correlation_id=str(uuid.uuid4()),
    )

    return CoupangDryRunResponse(
        outcome=result.outcome,
        errors=result.errors,
        payload_field_count=result.payload_field_count,
        attempted_at=result.attempted_at,
        duplicate=duplicate,
    )


@router.post(
    "/{product_id}/approve-for-submission",
    response_model=CoupangProductResponse,
)
def approve_for_submission(
    product_id: int,
    data: CoupangApprovalRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    운영자 최종 승인 — 상태만 READY_FOR_SUBMISSION으로 변경한다. 실제
    쿠팡 상품 등록을 실행하지 않는다.
    """

    service = CoupangIntegrationService(db)
    product, _duplicate, _events = service.approve_for_submission(
        product_id,
        current_user.company_id,
        operator_id=current_user.id,
        is_admin=True,
        idempotency_key=data.idempotency_key,
        memo=data.memo,
        correlation_id=str(uuid.uuid4()),
    )

    return product


@router.get(
    "/{product_id}/decisions",
)
def get_decisions(
    product_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)
    rows = service.list_decisions(product_id, current_user.company_id)

    return [
        {
            "id": row.id,
            "action": row.action,
            "operator_id": row.operator_id,
            "memo": row.memo,
            "decided_at": row.decided_at,
        }
        for row in rows
    ]


@policy_router.post(
    "",
    response_model=CoupangPolicySetResponse,
)
def create_policy_set(
    data: CoupangPolicySetCreateRequest,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    정책 세트를 생성한다(전역 — 회사 스코프 아님). status=VERIFIED로
    생성하는 것은 실제 정책 문서를 대조 확인한 관리자의 명시적
    판단이어야 한다 — 이 엔드포인트가 admin_guard로 보호되는 것이 그
    통제 지점이다(AI가 임의로 호출해 자동으로 정책을 활성화할 수
    없다).
    """

    service = CoupangIntegrationService(db)

    return service.create_policy_set(data)


@policy_router.get(
    "",
    response_model=list[CoupangPolicySetResponse],
)
def list_policy_sets(
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.list_policy_sets()


@policy_router.post(
    "/{policy_set_id}/rules",
    response_model=CoupangPolicyRuleResponse,
)
def add_policy_rule(
    policy_set_id: int,
    data: CoupangPolicyRuleCreateRequest,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.add_policy_rule(policy_set_id, data)


@policy_router.get(
    "/{policy_set_id}/rules",
    response_model=list[CoupangPolicyRuleResponse],
)
def list_policy_rules(
    policy_set_id: int,
    _: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = CoupangIntegrationService(db)

    return service.list_policy_rules(policy_set_id)


__all__ = [
    "router",
    "policy_router",
]
