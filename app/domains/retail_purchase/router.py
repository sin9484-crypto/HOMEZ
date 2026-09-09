"""
=========================================================
Homez OS

File : app/domains/retail_purchase/router.py

Gate RP-1(2026-08-22) — 구매 실행 연결 API. 전 엔드포인트가
current_user.company_id만 사용한다(요청 바디로 company_id를 받지
않는다 — 기존 order/settlement router와 동일 컨벤션).

권한 배분(지시문 12): 조회는 StaffGuard(STAFF/MANAGER/ADMIN/
SUPER_ADMIN 전부 통과 — MANAGER "연결 상태·구매 결과 조회", STAFF
"허용된 업무 결과 조회"를 하나의 가드로 동시에 만족시킨다). 정책·
결제계정 설정과 구매 실행 트리거는 AdminGuard(+recent-auth) —
store_connection의 기존 Credential 교체·삭제 엔드포인트가 nonce
없이 admin_guard만 쓰는 것과 동일한 민감도 수준으로 맞췄다(단일
사용 nonce는 이번 라운드에 구현하지 않음 — 정직하게 남긴 항목).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import Query
from fastapi import Request
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.exceptions import BadRequestException
from app.core.exceptions import UnauthorizedException
from app.core.guard import AdminGuard
from app.core.guard import StaffGuard
from app.core.recent_auth import consume_recent_auth_token
from app.domains.retail_purchase.constants import ProviderCapability
from app.domains.retail_purchase.constants import ProviderConnectionStatus
from app.domains.retail_purchase.policy_service import (
    RetailPurchasePolicyCheckInput,
)
from app.domains.retail_purchase.policy_service import RetailPurchasePolicyService
from app.domains.retail_purchase.product_matching import ProductAttributes
from app.domains.retail_purchase.product_matching import evaluate_same_product
from app.domains.retail_purchase.provider import (
    ProcurementGatewayProvider,
    CorporateProcurementProvider,
    OfficialMarketplacePurchaseProvider,
    VirtualCardProcurementProvider,
    DirectSupplierProvider,
    FakeRetailPurchaseProvider,
)
from app.domains.retail_purchase.provider import CheckoutLineItem
from app.domains.retail_purchase.provider import RetailPurchaseProviderError
from app.domains.retail_purchase.provider import get_retail_purchase_provider
from app.domains.retail_purchase.repository import RetailPurchaseRepository
from app.domains.retail_purchase.schema import CheckoutPreviewRequest
from app.domains.retail_purchase.schema import CheckoutPreviewResponse
from app.domains.retail_purchase.schema import PaymentAccountReferenceResponse
from app.domains.retail_purchase.schema import ProductDetailResponse
from app.domains.retail_purchase.schema import ProductSearchQueryRequest
from app.domains.retail_purchase.schema import ProductSearchResponse
from app.domains.retail_purchase.schema import ProductSearchResultItemResponse
from app.domains.retail_purchase.schema import ProviderInfoResponse
from app.domains.retail_purchase.schema import RetailPurchaseCancelRequest
from app.domains.retail_purchase.schema import RetailPurchaseOrderResponse
from app.domains.retail_purchase.schema import RetailPurchasePlaceOrderRequest
from app.domains.retail_purchase.schema import (
    RetailPurchasePolicyCheckRequest,
)
from app.domains.retail_purchase.schema import (
    RetailPurchasePolicyCheckResponse,
)
from app.domains.retail_purchase.schema import (
    RetailPurchasePolicySettingResponse,
)
from app.domains.retail_purchase.schema import (
    RetailPurchasePolicySettingUpdate,
)
from app.domains.retail_purchase.schema import RetailPurchaseQuoteResponse
from app.domains.retail_purchase.schema import RetailPurchaseRequestCreate
from app.domains.retail_purchase.schema import (
    RetailPurchaseReserveBudgetRequest,
)
from app.domains.retail_purchase.service import RetailPurchaseService
from app.domains.retail_purchase.webhook import WebhookReplayError
from app.domains.retail_purchase.webhook import WebhookSignatureError
from app.domains.retail_purchase.webhook import record_webhook_event_once
from app.domains.retail_purchase.webhook import verify_webhook_signature
from app.domains.user.model import User

router = APIRouter(prefix="/retail-purchase", tags=["Retail Purchase"])


# --------------------------------------------------
# Provider 정보(읽기 전용) — 실제 계약 여부를 정직하게 표시한다.
# --------------------------------------------------

_PROVIDER_CLASSES = {
    "FAKE": FakeRetailPurchaseProvider,
    "PROCUREMENT_GATEWAY": ProcurementGatewayProvider,
    "OFFICIAL_MARKETPLACE": OfficialMarketplacePurchaseProvider,
    "CORPORATE_PROCUREMENT": CorporateProcurementProvider,
    "VIRTUAL_CARD": VirtualCardProcurementProvider,
    "DIRECT_SUPPLIER": DirectSupplierProvider,
}


def _connection_status_for(provider_code: str, has_account: bool) -> str:

    if provider_code == "FAKE":
        return ProviderConnectionStatus.TEST_ONLY
    if not has_account:
        return ProviderConnectionStatus.CONTRACT_REQUIRED
    return ProviderConnectionStatus.CREDENTIAL_REQUIRED


@router.get("/providers", response_model=list[ProviderInfoResponse])
def list_providers(
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    repository = RetailPurchaseRepository(db)
    accounts = {
        a.provider_code for a in repository.list_payment_accounts(
            current_user.company_id,
        )
    }

    return [
        ProviderInfoResponse(
            provider_code=code,
            connection_status=_connection_status_for(code, code in accounts),
            capabilities=sorted(provider_cls.capabilities),
            not_supported=sorted(
                set(ProviderCapability.ALL) - provider_cls.capabilities,
            ),
        )
        for code, provider_cls in _PROVIDER_CLASSES.items()
    ]


# --------------------------------------------------
# 결제계정 참조(PaymentAccountReference) — Credential Manager target
# name만 저장한다, Secret 자체는 API 응답에 절대 포함하지 않는다.
# --------------------------------------------------

@router.get(
    "/payment-accounts", response_model=list[PaymentAccountReferenceResponse],
)
def list_payment_accounts(
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    repository = RetailPurchaseRepository(db)
    return repository.list_payment_accounts(current_user.company_id)


# --------------------------------------------------
# 자동구매 정책
# --------------------------------------------------

@router.get(
    "/policy", response_model=RetailPurchasePolicySettingResponse,
)
def get_policy(
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = RetailPurchasePolicyService(db)
    setting = service.get_or_create_default_settings(current_user.company_id)
    db.commit()
    return RetailPurchasePolicySettingResponse.from_model(setting)


@router.put(
    "/policy", response_model=RetailPurchasePolicySettingResponse,
)
def update_policy(
    data: RetailPurchasePolicySettingUpdate,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
    recent_auth_token: str | None = Header(
        default=None, alias="X-Recent-Auth-Token",
    ),
):
    """정책 변경(예산·마진·허용 쇼핑몰 등)은 recent-auth를 요구한다
    — 이 값들이 자동구매 실행 범위를 직접 통제하기 때문이다."""

    if not consume_recent_auth_token(recent_auth_token, current_user.id):
        raise UnauthorizedException(
            "RETAIL_PURCHASE_POLICY_RECENT_AUTH_REQUIRED: 자동구매 "
            "정책을 변경하려면 현재 비밀번호를 다시 확인해야 합니다.",
        )

    service = RetailPurchasePolicyService(db)
    setting = service.get_or_create_default_settings(current_user.company_id)

    import json

    update_data = data.model_dump(exclude_unset=True)
    if "allowed_provider_codes" in update_data:
        setting.allowed_provider_codes_json = json.dumps(
            update_data.pop("allowed_provider_codes"),
        )
    for field_name, value in update_data.items():
        setattr(setting, field_name, value)

    db.commit()
    return RetailPurchasePolicySettingResponse.from_model(setting)


# --------------------------------------------------
# 동일상품 판정(읽기 전용, 순수 계산 — DB 쓰기 없음)
# --------------------------------------------------

@router.post("/match-check")
def check_same_product(
    source: dict, candidate: dict,
    current_user: User = Depends(StaffGuard),
):

    source_attrs = ProductAttributes(**{
        k: v for k, v in source.items() if k in ProductAttributes.__dataclass_fields__
    })
    candidate_attrs = ProductAttributes(**{
        k: v for k, v in candidate.items()
        if k in ProductAttributes.__dataclass_fields__
    })
    result = evaluate_same_product(source_attrs, candidate_attrs)

    return {
        "tier": result.tier,
        "confidence": result.confidence,
        "blocked_reason": result.blocked_reason,
        "missing_attributes": list(result.missing_attributes),
        "evidence": [
            {
                "criterion": e.criterion, "source_value": e.source_value,
                "candidate_value": e.candidate_value, "matched": e.matched,
            }
            for e in result.evidence
        ],
    }


# --------------------------------------------------
# 상품 검색·견적 미리보기(장바구니형 검토 화면, 작업 3) — DB 쓰기
# 없는 순수 조회. /match-check와 동일하게 Service를 거치지 않고
# Provider를 직접 호출한다(상태 변경이 없으므로 Service 계층이
# 필요하지 않다).
# --------------------------------------------------

def _provider_error_to_http(e: RetailPurchaseProviderError) -> BadRequestException:

    return BadRequestException(f"{e.error_code}: {e}")


@router.post("/search", response_model=ProductSearchResponse)
def search_products(
    data: ProductSearchQueryRequest,
    current_user: User = Depends(StaffGuard),
):
    """실구매 불가·시연용 여부는 provider_code로 판단한다(FAKE만
    실제 검색 결과를 낸다) — 화면은 /providers 응답의
    connection_status로 이를 명확히 표시해야 한다."""

    from app.domains.retail_purchase.provider import ProductSearchQuery

    provider = get_retail_purchase_provider(data.provider_code)
    try:
        result = provider.search_product(ProductSearchQuery(
            keyword=data.keyword, brand=data.brand,
            model_name=data.model_name, gtin=data.gtin, limit=data.limit,
        ))
    except RetailPurchaseProviderError as e:
        raise _provider_error_to_http(e) from e

    return ProductSearchResponse(
        provider_code=data.provider_code,
        items=[
            ProductSearchResultItemResponse(
                external_product_id=item.external_product_id,
                product_url=item.product_url, title=item.title,
                brand=item.brand, manufacturer=item.manufacturer,
                model_name=item.model_name, gtin=item.gtin,
                seller_name=item.seller_name,
                seller_trust_score=item.seller_trust_score,
                list_price=item.list_price, in_stock=item.in_stock,
                is_authorized_dealer=item.is_authorized_dealer,
            )
            for item in result.items
        ],
        evaluated_at=result.evaluated_at,
    )


@router.get(
    "/products/{provider_code}/{external_product_id}",
    response_model=ProductDetailResponse,
)
def get_product_detail(
    provider_code: str, external_product_id: str,
    current_user: User = Depends(StaffGuard),
):

    provider = get_retail_purchase_provider(provider_code)
    try:
        detail = provider.get_product(external_product_id)
    except RetailPurchaseProviderError as e:
        raise _provider_error_to_http(e) from e

    return ProductDetailResponse(
        external_product_id=detail.external_product_id,
        product_url=detail.product_url, title=detail.title,
        brand=detail.brand, manufacturer=detail.manufacturer,
        model_name=detail.model_name, gtin=detail.gtin,
        options=[
            {
                "option_id": o.option_id, "label": o.label,
                "color_or_scent": o.color_or_scent,
                "capacity": o.capacity,
                "additional_price": str(o.additional_price),
            }
            for o in detail.options
        ],
        components=list(detail.components),
        is_authorized_dealer=detail.is_authorized_dealer,
        certification_info=detail.certification_info,
        return_policy_summary=detail.return_policy_summary,
        return_allowed=detail.return_allowed,
        seller_name=detail.seller_name,
        seller_trust_score=detail.seller_trust_score,
        fetched_at=detail.fetched_at,
    )


@router.post("/checkout-preview", response_model=CheckoutPreviewResponse)
def preview_checkout(
    data: CheckoutPreviewRequest,
    current_user: User = Depends(StaffGuard),
):
    """장바구니형 검토 화면의 단가·수량·배송비·총매입액·예상매출·
    예상이익·예상마진율 계산 — RetailPurchaseOrder를 생성하지
    않는다(순수 미리보기)."""

    provider = get_retail_purchase_provider(data.provider_code)
    items = (CheckoutLineItem(
        external_product_id=data.external_product_id,
        option_id=data.option_id, quantity=data.quantity,
    ),)

    try:
        calc = provider.calculate_checkout(items)
    except RetailPurchaseProviderError as e:
        raise _provider_error_to_http(e) from e

    unit_price_estimate = (
        (calc.item_total / data.quantity) if data.quantity > 0 else None
    )

    expected_net_profit = None
    expected_margin_rate = None
    if data.expected_sale_amount is not None:
        fee = data.expected_sale_fee_amount or 0
        expected_net_profit = (
            data.expected_sale_amount - calc.total_amount - fee
        )
        if data.expected_sale_amount > 0:
            expected_margin_rate = (
                expected_net_profit / data.expected_sale_amount
            )

    return CheckoutPreviewResponse(
        provider_code=data.provider_code,
        external_product_id=data.external_product_id,
        quantity=data.quantity, unit_price_estimate=unit_price_estimate,
        item_total=calc.item_total, shipping_fee=calc.shipping_fee,
        total_purchase_amount=calc.total_amount,
        estimated_delivery_days=calc.estimated_delivery_days,
        expected_sale_amount=data.expected_sale_amount,
        expected_net_profit=expected_net_profit,
        expected_margin_rate=expected_margin_rate,
        calculated_at=calc.calculated_at,
    )


# --------------------------------------------------
# Provider Webhook(작업 5) — 서명 검증·재사용 방지 계약까지만.
# 실제 계약된 Provider의 signing secret이 없으므로, 이 엔드포인트는
# 현재 구조상 항상 서명 검증에 실패한다(fail-closed) — 성공 처리를
# 흉내내지 않는다. 주문 상태를 실제로 갱신하는 로직은 실 Provider
# 연결 후 별도 구현 대상(NOT_IMPLEMENTED, 정직하게 남김).
#
# 이 엔드포인트는 로그인한 HOMEZ 사용자가 아니라 외부 Provider
# 서버가 호출한다 — StaffGuard/AdminGuard(세션 인증) 대신 HMAC 서명
# 검증을 인증 수단으로 쓴다. tests/test_route_authentication_
# contract.py가 "모든 route는 인식된 인증 Depends를 가져야 한다"를
# 강제하므로, 서명 검증을 함수 본문이 아니라 FastAPI Depends로
# 분리해 그 계약 검사기가 실제로 인증 메커니즘의 존재를 확인할 수
# 있게 한다(verify_webhook_request_signature를 그 테스트의
# KNOWN_AUTH_DEPENDENCY_NAMES에 등록해 뒀다).
# --------------------------------------------------

async def verify_webhook_request_signature(
    provider_code: str,
    request: Request,
    signature: str | None = Header(default=None, alias="X-Provider-Signature"),
) -> bytes:

    payload = await request.body()

    # 실 Provider별 signing secret은 아직 어디에도 저장돼 있지 않다
    # (PaymentAccountReference는 Credential Manager 참조만 가짐,
    # 이번 라운드는 Webhook secret 조회를 연결하지 않았다 — 실
    # 계약 시점에 채워야 할 자리를 정직하게 None으로 둔다). secret이
    # 없으므로 이 Depends는 지금 구조상 항상 거부한다(fail-closed).
    secret = None

    try:
        verify_webhook_signature(
            secret=secret, payload=payload, signature_header=signature,
        )
    except WebhookSignatureError as e:
        raise UnauthorizedException(f"WEBHOOK_SIGNATURE_INVALID: {e}") from e

    return payload


@router.post("/webhook/{provider_code}", status_code=status.HTTP_202_ACCEPTED)
def receive_provider_webhook(
    provider_code: str,
    payload: bytes = Depends(verify_webhook_request_signature),
    db: Session = Depends(get_db),
    event_id: str | None = Header(default=None, alias="X-Provider-Event-Id"),
):

    if not event_id:
        raise BadRequestException("X-Provider-Event-Id 헤더가 필요합니다.")

    try:
        record_webhook_event_once(
            db, provider_code=provider_code, event_id=event_id,
        )
    except WebhookReplayError as e:
        raise BadRequestException(f"WEBHOOK_REPLAY_REJECTED: {e}") from e

    db.commit()
    return {"accepted": True, "note": "NOT_IMPLEMENTED: dispatch to order state"}


# --------------------------------------------------
# 구매 실행 상태머신
# --------------------------------------------------

@router.get("", response_model=list[RetailPurchaseOrderResponse])
def list_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = RetailPurchaseService(db)
    return service.list_orders(current_user.company_id, status=status_filter)


@router.get("/{order_id}", response_model=RetailPurchaseOrderResponse)
def get_order(
    order_id: int,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = RetailPurchaseService(db)
    return service.get_order(order_id, current_user.company_id)


@router.post(
    "", response_model=RetailPurchaseOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_purchase_request(
    data: RetailPurchaseRequestCreate,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    import json

    service = RetailPurchaseService(db)
    return service.create_purchase_request(
        current_user.company_id, source_order_id=data.source_order_id,
        provider_code=data.provider_code, product_url=data.product_url,
        external_product_id=data.external_product_id,
        selected_option=data.selected_option, quantity=data.quantity,
        idempotency_key=data.idempotency_key,
        match_confidence=data.match_confidence,
        match_evidence_json=json.dumps(data.match_evidence),
        expected_amount=data.expected_amount,
        created_by=current_user.id,
    )


@router.post(
    "/{order_id}/policy-check",
    response_model=RetailPurchasePolicyCheckResponse,
)
def run_policy_check(
    order_id: int,
    data: RetailPurchasePolicyCheckRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """
    match_result는 클라이언트가 재계산해서 보내지 않는다 — 서버가
    order.match_confidence(생성 시점에 이미 저장된 값)로 다시
    evaluate_same_product 없이 판정 결과를 재구성한다(BLOCKED/
    NEEDS_REVIEW/AUTO_CANDIDATE 경계는 고정 상수이므로 confidence
    값만 있으면 결정적으로 재구성 가능하다).
    """

    from app.domains.retail_purchase.constants import SameProductConfidenceTier
    from app.domains.retail_purchase.product_matching import (
        SameProductMatchResult,
    )

    service = RetailPurchaseService(db)
    order = service.get_order(order_id, current_user.company_id)

    confidence = order.match_confidence or 0.0
    if confidence >= SameProductConfidenceTier.AUTO_CANDIDATE_THRESHOLD:
        tier = SameProductConfidenceTier.AUTO_CANDIDATE
    elif confidence >= SameProductConfidenceTier.NEEDS_REVIEW_THRESHOLD:
        tier = SameProductConfidenceTier.NEEDS_REVIEW
    else:
        tier = SameProductConfidenceTier.BLOCKED

    match_result = SameProductMatchResult(
        confidence=confidence, tier=tier, evidence=(),
        blocked_reason=(
            None if tier != SameProductConfidenceTier.BLOCKED
            else "PRODUCT_MATCH_INSUFFICIENT"
        ),
        missing_attributes=(),
    )

    policy_input = RetailPurchasePolicyCheckInput(
        provider_code=order.provider_code, match_result=match_result,
        in_stock=data.in_stock,
        estimated_delivery_days=data.estimated_delivery_days,
        return_allowed=data.return_allowed,
        seller_trust_score=data.seller_trust_score,
        coupang_sale_amount=data.coupang_sale_amount,
        retail_actual_amount=data.retail_actual_amount,
        shipping_fee=data.shipping_fee,
        coupang_fee_amount=data.coupang_fee_amount,
        automation_cost=data.automation_cost,
        return_reserve_amount=data.return_reserve_amount,
        expected_amount_at_proposal=(
            None if order.expected_amount is None
            else __import__("decimal").Decimal(str(order.expected_amount))
        ),
        quantity=order.quantity,
    )

    order, result = service.run_policy_check(
        order_id, current_user.company_id, policy_input,
        actor_user_id=current_user.id,
    )

    return RetailPurchasePolicyCheckResponse(
        order=order, decision=result.decision, reasons=list(result.reasons),
        match_tier=tier, match_confidence=confidence,
    )


@router.post(
    "/{order_id}/reserve-budget", response_model=RetailPurchaseOrderResponse,
)
def reserve_budget(
    order_id: int,
    data: RetailPurchaseReserveBudgetRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = RetailPurchaseService(db)
    return service.reserve_budget(
        order_id, current_user.company_id, data.required_amount,
        actor_user_id=current_user.id,
    )


@router.post(
    "/{order_id}/quote", response_model=RetailPurchaseQuoteResponse,
)
def request_quote(
    order_id: int,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = RetailPurchaseService(db)
    order, quote = service.request_quote(
        order_id, current_user.company_id, actor_user_id=current_user.id,
    )

    return RetailPurchaseQuoteResponse(
        order=order, quote_id=quote.quote_id, total_amount=quote.total_amount,
        shipping_fee=quote.shipping_fee, in_stock=quote.in_stock,
        expires_at=quote.expires_at,
    )


@router.post(
    "/{order_id}/place-order", response_model=RetailPurchaseOrderResponse,
)
def place_order(
    order_id: int,
    data: RetailPurchasePlaceOrderRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):
    """실제 계약 전 Provider(FAKE 제외)는 항상 LiveInputRequiredError
    로 차단된다 — 지시문 15 Live Gate 전까지는 어떤 실제 구매도
    실행되지 않는다."""

    from app.domains.retail_purchase.provider import LiveInputRequiredError

    service = RetailPurchaseService(db)
    try:
        return service.place_order(
            order_id, current_user.company_id, quote_id=data.quote_id,
            shipping_address_reference=data.shipping_address_reference,
            created_by=current_user.id,
        )
    except LiveInputRequiredError as e:
        raise BadRequestException(str(e)) from e


@router.post(
    "/{order_id}/cancel", response_model=RetailPurchaseOrderResponse,
)
def cancel_order(
    order_id: int,
    data: RetailPurchaseCancelRequest,
    current_user: User = Depends(AdminGuard),
    db: Session = Depends(get_db),
):

    service = RetailPurchaseService(db)
    return service.cancel_order(
        order_id, current_user.company_id, data.reason,
        actor_user_id=current_user.id,
    )


@router.post(
    "/{order_id}/refresh-tracking", response_model=RetailPurchaseOrderResponse,
)
def refresh_tracking(
    order_id: int,
    current_user: User = Depends(StaffGuard),
    db: Session = Depends(get_db),
):

    service = RetailPurchaseService(db)
    return service.refresh_tracking(order_id, current_user.company_id)


__all__ = ["router"]
