"""
=========================================================
Homez OS

File : app/domains/retail_purchase/schema.py

Gate RP-1(2026-08-22) — API 요청/응답 스키마. company_id는 어떤 요청
바디에도 없다(항상 current_user.company_id만 사용, Gate 1/2/3에서
반복 발견된 크로스테넌트 결함 클래스를 처음부터 차단).
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from app.domains.retail_purchase.constants import (
    RetailPurchaseUncertainHandlingPolicy,
)


# --------------------------------------------------
# Provider
# --------------------------------------------------

class ProviderInfoResponse(BaseModel):

    provider_code: str
    connection_status: str
    capabilities: list[str]
    not_supported: list[str]


# --------------------------------------------------
# 정책
# --------------------------------------------------

class RetailPurchasePolicySettingResponse(BaseModel):
    """`model_validate(setting)`을 직접 쓰지 않는다 — ORM 컬럼
    `allowed_provider_codes_json`(JSON 문자열)과 이 응답 필드
    `allowed_provider_codes`(list[str])의 이름·타입이 달라
    `from_attributes` 자동 매핑이 적용되지 않는다."""

    id: int
    company_id: int
    min_net_profit: float
    min_margin_rate: float
    max_purchase_price: Optional[float]
    max_price_increase_rate: float
    max_delivery_days: Optional[int]
    require_return_allowed: bool
    min_seller_trust_score: float
    min_match_confidence: float
    allowed_provider_codes: list[str]
    per_order_max_amount: Optional[float]
    daily_purchase_limit_amount: Optional[float]
    monthly_purchase_budget_amount: Optional[float]
    max_concurrent_orders: Optional[int]
    max_quantity_per_product: Optional[int]
    auto_execute_enabled: bool
    approval_required_amount_threshold: Optional[float]
    uncertain_handling_policy: str
    uncertain_auto_release_after_hours: Optional[int]
    updated_at: datetime

    @classmethod
    def from_model(cls, setting) -> "RetailPurchasePolicySettingResponse":

        import json

        return cls(
            id=setting.id, company_id=setting.company_id,
            min_net_profit=setting.min_net_profit,
            min_margin_rate=setting.min_margin_rate,
            max_purchase_price=setting.max_purchase_price,
            max_price_increase_rate=setting.max_price_increase_rate,
            max_delivery_days=setting.max_delivery_days,
            require_return_allowed=setting.require_return_allowed,
            min_seller_trust_score=setting.min_seller_trust_score,
            min_match_confidence=setting.min_match_confidence,
            allowed_provider_codes=json.loads(setting.allowed_provider_codes_json),
            per_order_max_amount=setting.per_order_max_amount,
            daily_purchase_limit_amount=setting.daily_purchase_limit_amount,
            monthly_purchase_budget_amount=setting.monthly_purchase_budget_amount,
            max_concurrent_orders=setting.max_concurrent_orders,
            max_quantity_per_product=setting.max_quantity_per_product,
            auto_execute_enabled=setting.auto_execute_enabled,
            approval_required_amount_threshold=(
                setting.approval_required_amount_threshold
            ),
            uncertain_handling_policy=setting.uncertain_handling_policy,
            uncertain_auto_release_after_hours=(
                setting.uncertain_auto_release_after_hours
            ),
            updated_at=setting.updated_at,
        )


class RetailPurchasePolicySettingUpdate(BaseModel):

    min_net_profit: Optional[float] = Field(default=None, ge=0)
    min_margin_rate: Optional[float] = Field(default=None, ge=0, le=1)
    max_purchase_price: Optional[float] = Field(default=None, gt=0)
    max_price_increase_rate: Optional[float] = Field(default=None, ge=0, le=1)
    max_delivery_days: Optional[int] = Field(default=None, gt=0)
    require_return_allowed: Optional[bool] = None
    min_seller_trust_score: Optional[float] = Field(default=None, ge=0, le=1)
    min_match_confidence: Optional[float] = Field(default=None, ge=0.90, le=1)
    allowed_provider_codes: Optional[list[str]] = None
    per_order_max_amount: Optional[float] = Field(default=None, gt=0)
    daily_purchase_limit_amount: Optional[float] = Field(default=None, gt=0)
    monthly_purchase_budget_amount: Optional[float] = Field(default=None, gt=0)
    max_concurrent_orders: Optional[int] = Field(default=None, gt=0)
    max_quantity_per_product: Optional[int] = Field(default=None, gt=0)
    auto_execute_enabled: Optional[bool] = None
    approval_required_amount_threshold: Optional[float] = Field(
        default=None, gt=0,
    )
    uncertain_handling_policy: Optional[str] = None
    uncertain_auto_release_after_hours: Optional[int] = Field(
        default=None, gt=0,
    )

    @field_validator("uncertain_handling_policy")
    @classmethod
    def _validate_uncertain_handling_policy(cls, v):

        if v is not None and v not in RetailPurchaseUncertainHandlingPolicy.ALL:
            raise ValueError(
                "uncertain_handling_policy는 "
                f"{RetailPurchaseUncertainHandlingPolicy.ALL} 중 하나여야 "
                "합니다.",
            )
        return v


# --------------------------------------------------
# PaymentAccountReference
# --------------------------------------------------

class PaymentAccountReferenceResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    provider_code: str
    status: str
    balance_or_credit_snapshot: Optional[float]
    last_synced_at: Optional[datetime]
    # external_account_reference(Credential Manager target name)는
    # 응답에 절대 포함하지 않는다 — 내부 참조일 뿐이지만 노출 표면을
    # 최소화한다.


# --------------------------------------------------
# 상품 검색·견적 미리보기(장바구니형 검토 화면 — 작업 3, DB 쓰기 없음)
# --------------------------------------------------

class ProductSearchQueryRequest(BaseModel):

    provider_code: str = Field(min_length=1, max_length=30)
    keyword: str = Field(min_length=1, max_length=200)
    brand: Optional[str] = Field(default=None, max_length=100)
    model_name: Optional[str] = Field(default=None, max_length=100)
    gtin: Optional[str] = Field(default=None, max_length=50)
    limit: int = Field(default=20, gt=0, le=50)


class ProductSearchResultItemResponse(BaseModel):

    external_product_id: str
    product_url: str
    title: str
    brand: Optional[str]
    manufacturer: Optional[str]
    model_name: Optional[str]
    gtin: Optional[str]
    seller_name: Optional[str]
    seller_trust_score: Optional[float]
    list_price: Optional[Decimal]
    in_stock: Optional[bool]
    is_authorized_dealer: Optional[bool]


class ProductSearchResponse(BaseModel):

    provider_code: str
    items: list[ProductSearchResultItemResponse]
    evaluated_at: datetime


class ProductDetailResponse(BaseModel):

    external_product_id: str
    product_url: str
    title: str
    brand: Optional[str]
    manufacturer: Optional[str]
    model_name: Optional[str]
    gtin: Optional[str]
    options: list[dict]
    components: list[str]
    is_authorized_dealer: Optional[bool]
    certification_info: Optional[str]
    return_policy_summary: Optional[str]
    return_allowed: Optional[bool]
    seller_name: Optional[str]
    seller_trust_score: Optional[float]
    fetched_at: datetime


class CheckoutPreviewRequest(BaseModel):
    """장바구니형 검토 화면(작업 3) — DB에 아무것도 쓰지 않는 순수
    미리보기다. 실제 RetailPurchaseOrder는 이후 별도 생성 API로
    만든다."""

    provider_code: str = Field(min_length=1, max_length=30)
    external_product_id: str = Field(min_length=1, max_length=200)
    option_id: Optional[str] = Field(default=None, max_length=100)
    quantity: int = Field(gt=0)
    # 매출/마진 미리보기용 — 없으면 매출·마진 항목은 계산하지 않는다
    # (0으로 채우지 않는다, policy_service.evaluate()와 동일 원칙).
    expected_sale_amount: Optional[Decimal] = None
    expected_sale_fee_amount: Optional[Decimal] = None


class CheckoutPreviewResponse(BaseModel):

    provider_code: str
    external_product_id: str
    quantity: int
    unit_price_estimate: Optional[Decimal]
    item_total: Decimal
    shipping_fee: Decimal
    total_purchase_amount: Decimal
    estimated_delivery_days: Optional[int]
    expected_sale_amount: Optional[Decimal]
    expected_net_profit: Optional[Decimal]
    expected_margin_rate: Optional[Decimal]
    calculated_at: datetime


# --------------------------------------------------
# 구매 실행
# --------------------------------------------------

class RetailPurchaseOrderResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    source_order_id: int
    provider_code: str
    product_url: str
    external_product_id: str
    selected_option: Optional[str]
    quantity: int
    match_confidence: Optional[float]
    expected_amount: Optional[float]
    actual_amount: Optional[float]
    expected_net_profit: Optional[float]
    external_order_id: Optional[str]
    external_order_number: Optional[str]
    status: str
    tracking_company: Optional[str]
    tracking_number: Optional[str]
    failure_code: Optional[str]
    retryable: bool
    uncertain_budget_released: bool
    created_at: datetime
    updated_at: datetime


class RetailPurchaseRequestCreate(BaseModel):

    source_order_id: int = Field(gt=0)
    provider_code: str = Field(min_length=1, max_length=30)
    product_url: str = Field(min_length=1, max_length=1000)
    external_product_id: str = Field(min_length=1, max_length=200)
    selected_option: Optional[str] = Field(default=None, max_length=500)
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=150)
    match_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    match_evidence: list[dict] = Field(default_factory=list)
    expected_amount: Optional[float] = Field(default=None, gt=0)


class RetailPurchasePolicyCheckRequest(BaseModel):
    """지시문 F 흐름 그대로 — 순이익 계산에 쓰는 실제 금액은 전부
    호출자가 채워야 한다(서버가 임의로 추정하지 않는다)."""

    in_stock: Optional[bool] = None
    estimated_delivery_days: Optional[int] = Field(default=None, gt=0)
    return_allowed: Optional[bool] = None
    seller_trust_score: Optional[float] = Field(default=None, ge=0, le=1)
    coupang_sale_amount: Optional[Decimal] = None
    retail_actual_amount: Optional[Decimal] = None
    shipping_fee: Optional[Decimal] = None
    coupang_fee_amount: Optional[Decimal] = None
    automation_cost: Decimal = Decimal("0")
    return_reserve_amount: Decimal = Decimal("0")


class RetailPurchasePolicyCheckResponse(BaseModel):

    order: RetailPurchaseOrderResponse
    decision: str
    reasons: list[str]
    match_tier: str
    match_confidence: float


class RetailPurchaseReserveBudgetRequest(BaseModel):

    required_amount: Decimal = Field(gt=0)


class RetailPurchaseQuoteResponse(BaseModel):

    order: RetailPurchaseOrderResponse
    quote_id: str
    total_amount: Decimal
    shipping_fee: Decimal
    in_stock: bool
    expires_at: datetime


class RetailPurchasePlaceOrderRequest(BaseModel):

    quote_id: str = Field(min_length=1, max_length=200)
    shipping_address_reference: str = Field(min_length=1, max_length=200)


class RetailPurchaseCancelRequest(BaseModel):

    reason: str = Field(min_length=1, max_length=500)


__all__ = [
    "ProviderInfoResponse",
    "RetailPurchasePolicySettingResponse",
    "RetailPurchasePolicySettingUpdate",
    "PaymentAccountReferenceResponse",
    "ProductSearchQueryRequest",
    "ProductSearchResultItemResponse",
    "ProductSearchResponse",
    "ProductDetailResponse",
    "CheckoutPreviewRequest",
    "CheckoutPreviewResponse",
    "RetailPurchaseOrderResponse",
    "RetailPurchaseRequestCreate",
    "RetailPurchasePolicyCheckRequest",
    "RetailPurchasePolicyCheckResponse",
    "RetailPurchaseReserveBudgetRequest",
    "RetailPurchaseQuoteResponse",
    "RetailPurchasePlaceOrderRequest",
    "RetailPurchaseCancelRequest",
]
