"""
=========================================================
Homez OS

File : app/domains/pricing/schema.py

Pricing & Margin Reconciliation Schema — V7 Gate 5(2026-08-15).

입력 금액/비율은 전부 Decimal이다(margin_calculator.py와 동일
컨벤션) — float 왕복으로 인한 정밀도 손실을 피한다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


# --------------------------------------------------
# 원가/배송비/수수료/광고비/세금 입력(요구사항 1)
# --------------------------------------------------

class EconomicsInputsUpdate(BaseModel):
    """
    비율(fee/reserve/tax)은 0~1 사이 소수(예: 10% -> 0.10)로 받는다
    (margin_calculator.EconomicsInputItem과 동일 계약).
    """

    cost_of_goods: Decimal = Field(ge=0)
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    packaging_cost: Decimal = Field(default=Decimal("0"), ge=0)
    ad_cost: Decimal = Field(default=Decimal("0"), ge=0)
    channel_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    payment_fee_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    return_reserve_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    tax_basis_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    model_config = ConfigDict(extra="forbid")


class ProductPricingInitCreate(EconomicsInputsUpdate):
    """Listing 최초 가격/원가 구성 초기화."""

    listing_id: int = Field(gt=0)
    initial_sale_price: Decimal = Field(gt=0)


class ProductPricingResponse(BaseModel):

    id: int
    company_id: int
    listing_id: int
    current_sale_price: Decimal

    cost_of_goods: Decimal
    shipping_cost: Decimal
    packaging_cost: Decimal
    ad_cost: Decimal
    channel_fee_rate: Decimal
    payment_fee_rate: Decimal
    return_reserve_rate: Decimal
    tax_basis_rate: Decimal

    expected_revenue: Decimal
    expected_total_cost: Decimal
    expected_margin_amount: Decimal
    expected_margin_rate: Decimal
    expected_break_even_price: Optional[Decimal] = None

    pending_price_change_id: Optional[int] = None
    version: int

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# economics_view 권한이 없는 사용자에게 노출되는 축소 버전 — 컬럼
# 자체가 빠진다(listing_wizard_csv_export.py의 include_economics=False
# 철학과 동일, 값을 가리지 않고 필드째 제외).
class ProductPricingPublicResponse(BaseModel):

    id: int
    company_id: int
    listing_id: int
    pending_price_change_id: Optional[int] = None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------
# 가격 변경 승인 흐름(요구사항 3)
# --------------------------------------------------

class PriceChangeCreate(BaseModel):

    requested_sale_price: Decimal = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=160)


class PriceChangeDecision(BaseModel):

    decision_reason: Optional[str] = Field(default=None, max_length=500)


class PriceChangeRequestResponse(BaseModel):

    id: int
    company_id: int
    listing_id: int
    product_pricing_id: int

    previous_sale_price: Decimal
    requested_sale_price: Decimal

    reason: Optional[str] = None
    status: str

    requested_by: int
    requested_at: datetime

    decided_by: Optional[int] = None
    decided_at: Optional[datetime] = None
    decision_reason: Optional[str] = None

    idempotency_key: str

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PriceChangeStatusEventResponse(BaseModel):

    id: int
    company_id: int
    price_change_request_id: int
    listing_id: int

    previous_status: Optional[str] = None
    new_status: str

    sale_price_snapshot: Decimal

    reason: Optional[str] = None
    actor_user_id: Optional[int] = None

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------
# 마진 스냅샷 / 괴리 조회(요구사항 2)
# --------------------------------------------------

class MarginSnapshotResponse(BaseModel):

    id: int
    company_id: int
    listing_id: int
    margin_type: str

    order_id: Optional[int] = None
    settlement_id: Optional[int] = None

    reason: str
    quantity_basis: int

    revenue: Decimal
    cost_of_goods: Decimal
    channel_fee: Decimal
    payment_fee: Decimal
    shipping_cost: Decimal
    packaging_cost: Decimal
    ad_cost: Decimal
    return_reserve: Decimal
    tax: Decimal
    refund_adjustment: Decimal

    total_cost: Decimal
    margin_amount: Decimal
    margin_rate: Decimal

    estimated_components_json: str

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MarginVarianceResponse(BaseModel):
    """
    Listing 단위 "가장 최근 예상" vs "가장 최근 실제" 마진 괴리
    (요구사항 2 — "둘의 괴리를 조회할 수 있어야 한다").
    """

    listing_id: int
    expected: Optional[MarginSnapshotResponse] = None
    latest_actual: Optional[MarginSnapshotResponse] = None
    margin_amount_variance: Optional[Decimal] = None
    margin_rate_variance: Optional[Decimal] = None


# --------------------------------------------------
# 정산 대사(요구사항 4/5)
# --------------------------------------------------

class ReconciliationResponse(BaseModel):

    id: int
    company_id: int
    order_id: int
    settlement_id: Optional[int] = None

    expected_net_amount: Decimal
    actual_net_amount: Optional[Decimal] = None
    variance_amount: Optional[Decimal] = None
    refund_amount: Decimal

    status: str
    notes: Optional[str] = None
    reconciled_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReconciliationHoldRequest(BaseModel):

    notes: str = Field(min_length=1, max_length=1000)


class ActualMarginRecordResult(BaseModel):

    order_id: int
    snapshots: list[MarginSnapshotResponse] = Field(default_factory=list)
    reconciliation: Optional[ReconciliationResponse] = None
    skipped_items: list[str] = Field(default_factory=list)


# --------------------------------------------------
# Gate AI-F(2026-08-22) — 가격 추천(PriceAdvisoryService) API 경계.
# 실제 가격을 바꾸지 않는다 — 계산 결과·제안만 반환한다.
# --------------------------------------------------

class PriceSuggestionRequest(BaseModel):

    current_sale_price: Decimal
    cost_of_goods: Optional[Decimal] = None
    channel_fee_rate: Optional[Decimal] = None
    payment_fee_rate: Optional[Decimal] = None
    shipping_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    ad_cost: Optional[Decimal] = None
    return_reserve_rate: Optional[Decimal] = None
    tax_basis_rate: Optional[Decimal] = None
    target_margin_rate: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    max_change_rate: Optional[Decimal] = None
    product_candidate_id: Optional[int] = None
    channel: Optional[str] = None


class PriceSuggestionResponse(BaseModel):

    recommended_sale_price: Optional[Decimal]
    expected_contribution_margin: Optional[Decimal]
    expected_margin_rate: Optional[Decimal]
    change_vs_current: Optional[Decimal]
    change_rate_vs_current: Optional[Decimal]
    break_even_price: Optional[Decimal]
    missing_cost_fields: list[str]
    is_provisional: bool
    reason: list[str]
    risk_warnings: list[str]
    policy_blocked: bool
    ai_result: dict


class PriceChangeProposalCreate(PriceSuggestionRequest):

    listing_id: int
    idempotency_key: str = Field(..., min_length=1, max_length=120)


class ProposedActionSummaryResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    capability_code: str
    action_type: str
    target_entity: str
    status: str
    risk_level: str
    reason: str
    expires_at: Optional[datetime]
    created_at: datetime


class PriceChangeProposalResponse(BaseModel):

    suggestion: PriceSuggestionResponse
    proposed_action: Optional[ProposedActionSummaryResponse]


__all__ = [
    "EconomicsInputsUpdate",
    "ProductPricingInitCreate",
    "ProductPricingResponse",
    "ProductPricingPublicResponse",
    "PriceChangeCreate",
    "PriceChangeDecision",
    "PriceChangeRequestResponse",
    "PriceChangeStatusEventResponse",
    "MarginSnapshotResponse",
    "MarginVarianceResponse",
    "ReconciliationResponse",
    "ReconciliationHoldRequest",
    "ActualMarginRecordResult",
    "PriceSuggestionRequest",
    "PriceSuggestionResponse",
    "PriceChangeProposalCreate",
    "ProposedActionSummaryResponse",
    "PriceChangeProposalResponse",
]
