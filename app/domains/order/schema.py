"""
=========================================================
Homez OS

File : app/domains/order/schema.py

Order Schema — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from app.core.sensitive_data import mask_address
from app.core.sensitive_data import mask_name
from app.core.sensitive_data import mask_phone
from app.core.sensitive_data import mask_zipcode


# --------------------------------------------------
# 채널 주문 수집(웹훅형 — 요구사항 1/2/7)
# --------------------------------------------------

class OrderChannelItemPayload(BaseModel):
    """채널이 보낸 원본 주문 품목 — HOMEZ가 만들어내지 않는다."""

    channel_sku: str = Field(min_length=1, max_length=150)

    quantity: int = Field(gt=0)

    unit_price: float = Field(ge=0)

    product_name: str = Field(min_length=1, max_length=300)


class OrderChannelCollectRequest(BaseModel):
    """
    채널에서 수집한 원본 주문 — 실제 쿠팡/네이버 API가 붙기 전까지는
    Fake Provider/픽스처가 이 형태로 "채널에서 주문이 들어왔다"를
    시뮬레이션한다.
    """

    channel_code: str = Field(min_length=1, max_length=30)

    channel_order_id: str = Field(min_length=1, max_length=150)

    buyer_name: str = Field(min_length=1, max_length=100)

    receiver_name: str = Field(min_length=1, max_length=100)

    receiver_phone: str = Field(min_length=1, max_length=50)

    receiver_address: str = Field(min_length=1, max_length=500)

    receiver_zipcode: str = Field(min_length=1, max_length=20)

    ordered_at: datetime

    items: list[OrderChannelItemPayload] = Field(min_length=1)


class CoupangOrderCollectionRequest(BaseModel):
    store_connection_id: int = Field(gt=0)
    channel_status: str = Field(min_length=1, max_length=30)


class CoupangOrderCollectionRunResponse(BaseModel):
    status: str
    channel_status: str
    page_count: int
    received_order_count: int
    saved_fulfillment_count: int
    new_fulfillment_count: int
    updated_fulfillment_count: int
    duplicate_fulfillment_count: int
    new_unresolved_item_count: int
    failed_order_count: int
    error_codes: list[str]


class CoupangOrderCollectionPreviewResponse(BaseModel):
    store_connection_id: int
    channel_status: str
    created_at_from: datetime
    created_at_to: datetime
    read_only: bool = True


class MultiChannelCollectionEntryResponse(BaseModel):
    store_connection_id: int
    marketplace_code: str
    channel_status: str
    status: str
    received_order_count: int
    new_fulfillment_count: int
    updated_fulfillment_count: int
    duplicate_fulfillment_count: int
    failed_order_count: int
    error_codes: list[str]


class MultiChannelCollectionRunResponse(BaseModel):
    total_connections: int
    succeeded_runs: int
    partial_runs: int
    failed_runs: int
    skipped_marketplace_codes: list[str]
    entries: list[MultiChannelCollectionEntryResponse]


class OrderCollectionPositionResponse(BaseModel):
    id: int
    store_connection_id: int
    channel_status: str
    last_successful_to: Optional[datetime]
    run_status: str
    last_error_code: Optional[str]
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# 2026-09-16 개인 베타 잔여 작업(Phase 7, HOMEZ_USER_OPERATION_SETTINGS.md
# 2-8 운영 화면) — 개인정보(구매자/수취인 정보)를 전혀 포함하지
# 않는다(회사 단위 집계값만). `app/domains/order/auto_collection_
# scheduler.py`가 채우는 `OrderAutoCollectionState` 그대로 반영한다.

class OrderCollectionOpsStatusResponse(BaseModel):
    company_id: int
    function_mode: str
    interval_minutes: int
    last_attempted_at: Optional[datetime]
    last_succeeded_at: Optional[datetime]
    next_due_at: Optional[datetime]
    last_status: Optional[str]
    last_skip_reason: Optional[str]
    last_error_summary: Optional[str]
    consecutive_failure_count: int
    last_new_fulfillment_count: Optional[int]
    last_duplicate_fulfillment_count: Optional[int]
    last_unresolved_item_count: Optional[int]
    last_failed_order_count: Optional[int]


class OrderCollectionOpsTriggerResponse(BaseModel):
    company_id: int
    outcome: str
    detail: str = ""


class OrderCollectionOpsIntervalUpdateRequest(BaseModel):
    interval_minutes: int = Field(ge=1, le=1440)


class OrderCollectionOpsResumeResponse(BaseModel):
    company_id: int
    function_mode: str


class UnresolvedOrderItemResponse(BaseModel):
    id: int
    fulfillment_id: int
    channel_item_id: str
    vendor_item_id: str
    channel_sku: str
    product_name_snapshot: str
    quantity: int
    unit_price: float
    order_price: float
    currency_code: str
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UnresolvedOrderItemResolveRequest(BaseModel):
    inventory_sku_id: int = Field(gt=0)


# --------------------------------------------------
# 조회 응답
# --------------------------------------------------

class OrderItemResponse(BaseModel):

    id: int

    company_id: int

    order_id: int

    inventory_sku_id: int

    channel_sku: str

    sku_code_snapshot: str

    product_name_snapshot: str

    quantity: int

    unit_price: float

    status: str

    reservation_id: Optional[int] = None

    purchase_id: Optional[int] = None

    shipped_quantity: int

    returned_quantity: int

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class OrderResponse(BaseModel):

    id: int

    company_id: int

    channel_code: str

    channel_order_id: str

    order_number: str

    status: str

    buyer_name: str

    receiver_name: str

    receiver_phone: str

    receiver_address: str

    receiver_zipcode: str

    total_amount: float

    ordered_at: datetime

    channel_sync_status: str

    channel_last_synced_at: Optional[datetime] = None

    channel_last_sync_error: Optional[str] = None

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )

    @field_validator("buyer_name", "receiver_name", mode="before")
    @classmethod
    def _mask_name(cls, value):
        return mask_name(value)

    @field_validator("receiver_phone", mode="before")
    @classmethod
    def _mask_phone(cls, value):
        return mask_phone(value)

    @field_validator("receiver_address", mode="before")
    @classmethod
    def _mask_address(cls, value):
        return mask_address(value)

    @field_validator("receiver_zipcode", mode="before")
    @classmethod
    def _mask_zipcode(cls, value):
        return mask_zipcode(value)


class OrderSensitiveDetailResponse(BaseModel):
    """최근 비밀번호 확인을 마친 관리자에게만 반환하는 배송 원문."""

    id: int
    company_id: int
    buyer_name: str
    receiver_name: str
    receiver_phone: str
    receiver_address: str
    receiver_zipcode: str
    model_config = ConfigDict(from_attributes=True)


class OrderCollectResult(BaseModel):
    """수집 1회 호출의 결과 요약 — 이벤트 상태 + 생성된 주문(있으면)."""

    ingestion_status: str

    order: Optional[OrderResponse] = None

    items: list[OrderItemResponse] = Field(default_factory=list)

    unresolved_channel_skus: list[str] = Field(default_factory=list)


class OrderIngestionEventResponse(BaseModel):

    id: int

    company_id: int

    channel_code: str

    channel_order_id: str

    status: str

    order_id: Optional[int] = None

    has_raw_payload: bool

    has_normalized_snapshot: bool

    error_code: Optional[str] = None

    error_summary: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class OrderStatusEventResponse(BaseModel):

    id: int

    company_id: int

    order_id: int

    previous_status: Optional[str] = None

    new_status: str

    source: str

    reason: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# --------------------------------------------------
# 취소 / 채널 동기화
# --------------------------------------------------

class OrderCancelRequest(BaseModel):

    reason: str = Field(min_length=1, max_length=500)


class OrderChannelSyncResult(BaseModel):

    order: OrderResponse

    success: bool

    raw_status: Optional[str] = None

    error_code: Optional[str] = None

    cancelled: bool = False


class OrderMessage(BaseModel):

    message: str


# --------------------------------------------------
# Gate AI-F2(2026-08-22) — 주문·배송·반품 예외 분석(읽기 전용).
# --------------------------------------------------

class OrderExceptionResponse(BaseModel):

    exception_type: str
    urgency: str
    target_entity: str
    evidence: str
    elapsed_hours: float
    recommended_action: str
    missing_evidence: list[str]
    approval_required: bool
    execution_allowed: bool


class OrderExceptionAnalysisResult(BaseModel):

    exceptions: list[OrderExceptionResponse]
    ai_result: dict


class OrderExceptionReviewActionCreate(BaseModel):
    """
    urgency/evidence/execution_allowed는 클라이언트에서 받지 않는다 —
    라우터가 analyze()를 다시 실행해 지금 이 순간의 실제 상태에서
    (exception_type, target_entity)가 일치하는 예외를 새로 찾아
    그 값만 사용한다(클라이언트가 EStop 상태나 긴급도를 자칭할 수
    없다).
    """

    exception_type: str = Field(min_length=1, max_length=100)
    target_entity: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=120)


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


class OrderExceptionReviewActionResponse(BaseModel):

    proposed_action: Optional[ProposedActionSummaryResponse]


__all__ = [
    "OrderChannelItemPayload",
    "OrderChannelCollectRequest",
    "CoupangOrderCollectionRequest",
    "CoupangOrderCollectionRunResponse",
    "CoupangOrderCollectionPreviewResponse",
    "OrderCollectionPositionResponse",
    "UnresolvedOrderItemResponse",
    "UnresolvedOrderItemResolveRequest",
    "OrderItemResponse",
    "OrderResponse",
    "OrderSensitiveDetailResponse",
    "OrderCollectResult",
    "OrderIngestionEventResponse",
    "OrderStatusEventResponse",
    "OrderCancelRequest",
    "OrderChannelSyncResult",
    "OrderMessage",
    "OrderExceptionResponse",
    "OrderExceptionAnalysisResult",
    "OrderExceptionReviewActionCreate",
    "ProposedActionSummaryResponse",
    "OrderExceptionReviewActionResponse",
]
