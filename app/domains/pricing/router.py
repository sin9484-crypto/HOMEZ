"""
=========================================================
Homez OS

File : app/domains/pricing/router.py

Pricing & Margin Reconciliation Router — V7 Gate 5(2026-08-15).

경제성 정보 접근 제한(요구사항 7) — `app/domains/marketplace_listing`
가 이미 확립한 `LISTING_ECONOMICS_VIEW` permission(Gate U-1)을 그대로
재사용한다(새 permission 체계를 발명하지 않는다, CTO 지시 원문).
`ListingWizardPermissionGuard`는 ADMIN/SUPER_ADMIN을 무조건 통과시키고
그 외 역할은 실제로 이 권한이 부여됐을 때만 통과시킨다 — 이 permission
코드가 아직 실제 homez.db에 시딩되지 않은 상태에서도 기존 ADMIN 접근이
깨지지 않는다(listing_wizard_permission_guard.py와 동일 이유).

가격/원가/마진이 포함된 모든 엔드포인트는 이 Guard로 통제한다. 판매가
변경 승인 자체도 "금액 필드 열람"의 상위 개념이라 동일 Guard로
충분하다(ADMIN/SUPER_ADMIN이 아닌 역할에게 승인 권한만 별도로 주는
세분화는 이번 범위 밖 — listing_wizard가 LISTING_WIZARD_APPROVE를
별도로 둔 것과 달리, 이 Domain은 아직 승인 전용 세부 권한을 요청받지
않았다).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Response
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.guard import admin_guard
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
    user_can,
)
from app.domains.marketplace_listing.listing_wizard_permissions import (
    LISTING_ECONOMICS_VIEW,
)
from app.domains.automation_safety.service import SafetyService
from app.domains.pricing.price_advisory_service import PriceAdvisoryService
from app.domains.pricing.price_advisory_service import PriceSuggestionInput
from app.domains.pricing.schema import EconomicsInputsUpdate
from app.domains.pricing.schema import PriceChangeCreate
from app.domains.pricing.schema import PriceChangeDecision
from app.domains.pricing.schema import PriceChangeProposalCreate
from app.domains.pricing.schema import PriceChangeProposalResponse
from app.domains.pricing.schema import PriceChangeRequestResponse
from app.domains.pricing.schema import PriceChangeStatusEventResponse
from app.domains.pricing.schema import PriceSuggestionRequest
from app.domains.pricing.schema import PriceSuggestionResponse
from app.domains.pricing.schema import ProductPricingInitCreate
from app.domains.pricing.schema import ProductPricingPublicResponse
from app.domains.pricing.schema import ProductPricingResponse
from app.domains.pricing.schema import ReconciliationHoldRequest
from app.domains.pricing.service import PricingService
from app.domains.user.model import User

router = APIRouter(
    prefix="/pricing",
    tags=["Pricing"],
)

_EconomicsGuard = ListingWizardPermissionGuard(LISTING_ECONOMICS_VIEW)


def _serialize_pricing(db: Session, current_user: User, pricing) -> dict:

    if user_can(db, current_user, LISTING_ECONOMICS_VIEW):
        return ProductPricingResponse.model_validate(pricing).model_dump(
            mode="json",
        )

    return ProductPricingPublicResponse.model_validate(pricing).model_dump(
        mode="json",
    )


# --------------------------------------------------
# ProductPricing — 초기화 / 조회(요구사항 1, 7)
# --------------------------------------------------

@router.post(
    "/init",
    status_code=status.HTTP_201_CREATED,
)
def initialize_pricing(
    data: ProductPricingInitCreate,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)
    pricing = service.initialize_pricing(
        current_user.company_id, data, current_user.id,
    )

    return _serialize_pricing(db, current_user, pricing)


@router.get("")
def list_pricings(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)
    pricings = service.list_pricings(current_user.company_id, skip, limit)

    return [_serialize_pricing(db, current_user, p) for p in pricings]


@router.get("/by-listing/{listing_id}")
def get_pricing_by_listing(
    listing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)
    pricing = service.get_pricing_by_listing(
        listing_id, current_user.company_id,
    )

    return _serialize_pricing(db, current_user, pricing)


# V7 Gate 7(2026-08-15) 실제 Browser E2E 도중 발견한 실제 라우팅
# 결함 수정 — 이 GET("/reconciliations")가 원래 아래
# GET("/{pricing_id}")보다 뒤에 등록돼 있었다. FastAPI/Starlette는
# 라우트를 등록 순서대로 매칭하므로 "/pricing/reconciliations"
# 요청이 "/{pricing_id}"에 pricing_id="reconciliations"로 먼저
# 매칭되어 정수 파싱 실패(422)로 항상 깨져 있었다 — 이 GET("")
# 다음, 파라미터화된 경로보다 앞에 위치시켜 고정한다(다른 고정
# literal 경로들과 동일한 원칙 — by-listing/export/csv도 세그먼트
# 수가 달라 우연히 충돌을 피했을 뿐이다).
@router.get("/reconciliations")
def list_reconciliations(
    status_filter: str | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.list_reconciliations(
        current_user.company_id, status_filter, skip, limit,
    )


@router.get("/{pricing_id}")
def get_pricing(
    pricing_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)
    pricing = service.get_pricing(pricing_id, current_user.company_id)

    return _serialize_pricing(db, current_user, pricing)


@router.patch(
    "/{pricing_id}/economics",
)
def update_economics_inputs(
    pricing_id: int,
    data: EconomicsInputsUpdate,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):
    """원가/배송비/수수료/광고비/세금 갱신 — 승인 절차 없음(요구사항
    1, 비고객대면 내부 정보)."""

    service = PricingService(db)
    pricing = service.update_economics_inputs(
        pricing_id, current_user.company_id, data, current_user.id,
    )

    return _serialize_pricing(db, current_user, pricing)


# --------------------------------------------------
# 가격 변경 승인 흐름(요구사항 3)
# --------------------------------------------------

@router.post(
    "/{listing_id}/price-changes",
    response_model=PriceChangeRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_price_change(
    listing_id: int,
    data: PriceChangeCreate,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.request_price_change(
        current_user.company_id, listing_id, data, current_user.id,
    )


@router.get(
    "/{listing_id}/price-changes",
    response_model=list[PriceChangeRequestResponse],
)
def list_price_changes(
    listing_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.list_price_changes(
        listing_id, current_user.company_id, skip, limit,
    )


@router.post(
    "/price-changes/{request_id}/approve",
    response_model=PriceChangeRequestResponse,
)
def approve_price_change(
    request_id: int,
    data: PriceChangeDecision | None = None,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.approve_price_change(
        request_id, current_user.company_id, current_user.id, data,
    )


@router.post(
    "/price-changes/{request_id}/reject",
    response_model=PriceChangeRequestResponse,
)
def reject_price_change(
    request_id: int,
    data: PriceChangeDecision | None = None,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.reject_price_change(
        request_id, current_user.company_id, current_user.id, data,
    )


@router.post(
    "/price-changes/{request_id}/cancel",
    response_model=PriceChangeRequestResponse,
)
def cancel_price_change(
    request_id: int,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.cancel_price_change(
        request_id, current_user.company_id, current_user.id,
    )


@router.get(
    "/price-changes/{request_id}/status-events",
    response_model=list[PriceChangeStatusEventResponse],
)
def list_price_change_status_events(
    request_id: int,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.list_status_events(request_id, current_user.company_id)


# --------------------------------------------------
# 마진 스냅샷 / 괴리(요구사항 2)
# --------------------------------------------------

@router.get("/{listing_id}/margin-snapshots")
def list_margin_snapshots(
    listing_id: int,
    margin_type: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.list_margin_snapshots(
        listing_id, current_user.company_id, margin_type, skip, limit,
    )


@router.get("/{listing_id}/margin-variance")
def get_margin_variance(
    listing_id: int,
    order_id: int | None = Query(default=None),
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.get_margin_variance(
        listing_id, current_user.company_id, order_id,
    )


# --------------------------------------------------
# 실제 마진 반영 / 정산 대사(요구사항 4/5)
# --------------------------------------------------

@router.post("/orders/{order_id}/record-actual-margin")
def record_actual_margin(
    order_id: int,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.record_actual_margin(
        order_id, current_user.company_id, current_user.id,
    )


@router.get("/orders/{order_id}/reconciliation")
def get_reconciliation(
    order_id: int,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.get_reconciliation(order_id, current_user.company_id)


@router.post("/orders/{order_id}/reconciliation/hold")
def hold_reconciliation(
    order_id: int,
    data: ReconciliationHoldRequest,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.hold_reconciliation(
        order_id, current_user.company_id, data.notes, current_user.id,
    )


@router.post("/orders/{order_id}/reconciliation/release-hold")
def release_reconciliation_hold(
    order_id: int,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):

    service = PricingService(db)

    return service.release_reconciliation_hold(
        order_id, current_user.company_id, current_user.id,
    )


# --------------------------------------------------
# 회사별 회계 CSV 내보내기(요구사항 6)
# --------------------------------------------------

@router.get("/export/csv")
def export_accounting_csv(
    locale: str = Query(default="ko-KR"),
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    economics_view 권한이 없으면 금액/마진 컬럼 없이(listing_id/버전/
    상태 컬럼만) 내려간다 — CSV 자체는 admin_guard만으로 항상 받을 수
    있고, 열람 가능한 컬럼 폭이 permission에 따라 달라진다(Gate U-1
    철학과 동일).
    """

    include_economics = user_can(db, current_user, LISTING_ECONOMICS_VIEW)

    service = PricingService(db)
    csv_text = service.export_accounting_csv(
        current_user.company_id,
        include_economics=include_economics,
        locale=locale,
    )

    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; filename=homez_pricing_accounting.csv"
            ),
        },
    )


# --------------------------------------------------
# Gate AI-F(2026-08-22) — 가격 추천(AI Capability: PRICING_INVENTORY).
# 조회만으로는 실제 가격이 절대 바뀌지 않는다 — 이 라우터의 어떤
# GET/POST도 PricingService의 실제 가격 반영 경로(request_price_
# change/approve_price_change)를 호출하지 않는다.
# --------------------------------------------------

def _to_suggestion_input(data: PriceSuggestionRequest) -> PriceSuggestionInput:

    return PriceSuggestionInput(**data.model_dump())


@router.post("/advisory/price-suggestion", response_model=PriceSuggestionResponse)
def suggest_price(
    data: PriceSuggestionRequest,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):
    """가격 추천 조회 — 순수 계산이다. 이 호출만으로는 실제 판매가·
    재고·발주 상태가 절대 바뀌지 않는다."""

    service = PriceAdvisoryService(db)
    suggestion, envelope = service.suggest(
        current_user.company_id, _to_suggestion_input(data),
    )

    return PriceSuggestionResponse(
        **suggestion.__dict__, ai_result=envelope.model_dump(mode="json"),
    )


@router.post(
    "/advisory/price-change-proposals",
    response_model=PriceChangeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def propose_price_change(
    data: PriceChangeProposalCreate,
    current_user: User = Depends(_EconomicsGuard),
    db: Session = Depends(get_db),
):
    """
    가격 변경안 생성 — 사용자가 명시적으로 이 엔드포인트를 호출해야만
    ProposedAction이 만들어진다(조회 엔드포인트는 절대 만들지 않는다).
    EStop이 활성화되어 있으면 새 제안 자체를 만들지 않는다(신규 AI
    제안 생성은 EStop의 통제 대상 — 순수 조회와 다르다).
    """

    if SafetyService(db).is_emergency_stop_active():
        raise BadRequestException(
            "Emergency Stop이 활성화되어 있어 새 가격 변경 제안을 "
            "만들 수 없습니다.",
        )

    service = PriceAdvisoryService(db)
    suggestion_input = _to_suggestion_input(
        PriceSuggestionRequest(
            **data.model_dump(exclude={"listing_id", "idempotency_key"}),
        ),
    )

    suggestion, envelope, action = service.propose_price_change(
        current_user.company_id, data.listing_id, suggestion_input,
        idempotency_key=data.idempotency_key,
    )

    return PriceChangeProposalResponse(
        suggestion=PriceSuggestionResponse(
            **suggestion.__dict__, ai_result=envelope.model_dump(mode="json"),
        ),
        proposed_action=action,
    )


__all__ = [
    "router",
]
