"""
=========================================================
Homez OS

File : app/domains/inventory/schema.py

Inventory Schema — V7 Gate 3(2026-08-15) 처음부터 재설계.
=========================================================
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


# --------------------------------------------------
# SKU
# --------------------------------------------------

class InventorySkuCreate(BaseModel):

    product_candidate_id: int

    sku_code: str = Field(min_length=1, max_length=100)

    option_label: Optional[str] = Field(default="기본", max_length=200)

    initial_qty: int = Field(default=0, ge=0)

    safety_stock: int = Field(default=0, ge=0)


class InventorySkuResponse(BaseModel):

    id: int

    company_id: int

    product_candidate_id: int

    sku_code: str

    option_label: str

    available_qty: int

    reserved_qty: int

    safety_stock: int

    is_active: bool

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# --------------------------------------------------
# 예약 / 해제 / 확정소모
# --------------------------------------------------

class InventoryReserveRequest(BaseModel):

    quantity: int = Field(gt=0)

    idempotency_key: str = Field(min_length=1, max_length=150)

    reference_type: Optional[str] = Field(default=None, max_length=50)

    reference_id: Optional[int] = None


class InventoryReservationResponse(BaseModel):

    id: int

    company_id: int

    inventory_sku_id: int

    quantity: int

    status: str

    reference_type: Optional[str] = None

    reference_id: Optional[int] = None

    idempotency_key: str

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# --------------------------------------------------
# 입고 / 수동 조정
# --------------------------------------------------

class InventoryRestockRequest(BaseModel):

    quantity: int = Field(gt=0)

    idempotency_key: str = Field(min_length=1, max_length=150)

    reason: Optional[str] = Field(default=None, max_length=500)


class InventoryAdjustRequest(BaseModel):
    """
    수동 재고 조정 — reason 필수(요구사항 7). Field(min_length=1)만으로는
    공백 문자열(" ")을 통과시킬 수 있어 Service 레벨에서 strip() 후
    다시 검증한다(이중 방어).
    """

    quantity_delta: int = Field(
        description="양수=증가, 음수=감소. 0은 허용하지 않는다.",
    )

    reason: str = Field(min_length=1, max_length=500)

    idempotency_key: str = Field(min_length=1, max_length=150)


class InventoryLedgerEventResponse(BaseModel):

    id: int

    company_id: int

    inventory_sku_id: int

    event_type: str

    quantity_delta: int

    available_after: int

    reserved_after: int

    safety_stock_threshold: int

    channel_code: Optional[str] = None

    reservation_id: Optional[int] = None

    idempotency_key: Optional[str] = None

    reason: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# --------------------------------------------------
# 채널 매핑
# --------------------------------------------------

class InventoryChannelMappingCreate(BaseModel):

    marketplace_listing_id: int

    channel_code: str = Field(min_length=1, max_length=30)

    channel_sku: str = Field(min_length=1, max_length=150)


class InventoryChannelMappingResponse(BaseModel):

    id: int

    company_id: int

    inventory_sku_id: int

    marketplace_listing_id: int

    channel_code: str

    channel_sku: str

    is_active: bool

    last_sync_status: str

    last_synced_at: Optional[datetime] = None

    last_sync_error: Optional[str] = None

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# --------------------------------------------------
# Message
# --------------------------------------------------

class InventoryMessage(BaseModel):

    message: str


# --------------------------------------------------
# Gate AI-F(2026-08-22) — 재고 보충 추천(ReplenishmentAdvisoryService)
# API 경계. 실제 재고를 바꾸지 않는다 — 계산 결과·제안만 반환한다.
# --------------------------------------------------

class ReplenishmentSuggestionRequest(BaseModel):

    lookback_days: int = Field(default=30, ge=1, le=365)


class ReplenishmentSuggestionResponse(BaseModel):

    sku_id: int
    available_qty: int
    reserved_qty: int
    safety_stock: int
    lookback_days: int
    total_consumed_in_lookback: int
    avg_daily_sales: Optional[Decimal]
    projected_stockout_date: Optional[str]
    recommended_order_by_date: Optional[str]
    recommended_order_qty: Optional[int]
    moq_applied: bool
    supplier_lead_time_days: Optional[int]
    projected_arrival_date: Optional[str]
    reasoning: list[str]
    confidence: float
    missing_evidence: list[str]
    ai_result: dict


class ReplenishmentProposalCreate(ReplenishmentSuggestionRequest):

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


class ReplenishmentProposalResponse(BaseModel):

    suggestion: ReplenishmentSuggestionResponse
    proposed_action: Optional[ProposedActionSummaryResponse]


__all__ = [
    "InventorySkuCreate",
    "InventorySkuResponse",
    "InventoryReserveRequest",
    "InventoryReservationResponse",
    "InventoryRestockRequest",
    "InventoryAdjustRequest",
    "InventoryLedgerEventResponse",
    "InventoryChannelMappingCreate",
    "InventoryChannelMappingResponse",
    "InventoryMessage",
    "ReplenishmentSuggestionRequest",
    "ReplenishmentSuggestionResponse",
    "ReplenishmentProposalCreate",
    "ProposedActionSummaryResponse",
    "ReplenishmentProposalResponse",
]
