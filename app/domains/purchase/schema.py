"""
=========================================================
Homez OS

File : app/domains/purchase/schema.py

Purchase Schema — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class PurchaseItemCreate(BaseModel):

    order_item_id: int

    unit_cost: float = Field(ge=0)


class PurchaseCreate(BaseModel):

    order_id: int

    supplier_id: int

    items: list[PurchaseItemCreate] = Field(min_length=1)

    idempotency_key: str = Field(min_length=1, max_length=150)

    supplier_order_number: Optional[str] = Field(
        default=None, max_length=100,
    )

    memo: Optional[str] = Field(default=None, max_length=1000)


class PurchaseItemResponse(BaseModel):

    id: int

    company_id: int

    purchase_id: int

    order_item_id: int

    inventory_sku_id: int

    quantity: int

    unit_cost: float

    subtotal_cost: float

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class PurchaseResponse(BaseModel):

    id: int

    company_id: int

    order_id: int

    supplier_id: int

    status: str

    idempotency_key: str

    total_cost: float

    supplier_order_number: Optional[str] = None

    memo: Optional[str] = None

    requested_at: datetime

    confirmed_at: Optional[datetime] = None

    received_at: Optional[datetime] = None

    cancelled_at: Optional[datetime] = None

    created_at: datetime

    updated_at: datetime

    submission_status: Optional[str] = None
    submission_provider_code: Optional[str] = None
    supplier_order_id: Optional[str] = None
    submitted_at: Optional[datetime] = None
    confirmed_price: Optional[float] = None
    accepted_quantities_json: Optional[str] = None
    rejected_quantities_json: Optional[str] = None
    submission_error_code: Optional[str] = None
    submission_retryable: Optional[bool] = None
    submission_retry_after_seconds: Optional[int] = None
    correlation_id: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class PurchaseCancelRequest(BaseModel):

    reason: str = Field(min_length=1, max_length=500)


class PurchaseSubmitRequest(BaseModel):

    provider_code: str = Field(default="FAKE", description="FAKE / MANUAL / CSV")

    # 2026-08-21 5차 지시(작업 3) — FAKE Provider 전용 결정적 테스트
    # 시나리오(FULL_ACCEPT/PARTIAL/RETRYABLE_FAILURE/RETRY_AFTER/
    # NON_RETRYABLE_FAILURE). None(기본값)이면 기존 프로덕션 기본
    # 동작(전량 접수)이 그대로 유지된다 — MANUAL/CSV Provider는 이
    # 값을 아예 읽지 않는다.
    test_scenario: Optional[str] = Field(default=None)


class PurchaseRetrySubmitRequest(BaseModel):
    """2026-08-21 작업 1(항목 19) — 실패한 공급처 전송 재시도. 전송
    당시와 동일한 Provider로 재시도하는 것이 일반적이지만, 사용자가
    다른 Provider로 바꿔 재시도하는 것도 막지 않는다(예: FAKE 실패
    후 MANUAL로 전환)."""

    provider_code: str = Field(default="FAKE", description="FAKE / MANUAL / CSV")

    # PurchaseSubmitRequest.test_scenario와 동일한 계약.
    test_scenario: Optional[str] = Field(default=None)


class PurchaseMessage(BaseModel):

    message: str


class OnchannelCredentialSaveRequest(BaseModel):
    """
    2026-09-08 V7 통합 매입 순서 6번 — 온채널 API 인증키를 Windows
    Credential Manager에 등록한다. 홈즈 설치 전체가 공유하는 시스템
    레벨 자격증명이다(온채널 계정도 1개당 발급). `allowed_ip`는
    온채널 쪽 "호출 허용 IP" 설정과 실제로 일치해야 호출이 성공한다
    — HOMEZ가 강제하지 않고 참고용으로만 저장한다.
    """

    auth_key: str = Field(min_length=1, max_length=2000)
    allowed_ip: str = Field(default="", max_length=100)


class OnchannelCredentialStatusResponse(BaseModel):

    registered: bool
    allowed_ip: str | None = None


__all__ = [
    "PurchaseItemCreate",
    "PurchaseCreate",
    "PurchaseItemResponse",
    "PurchaseResponse",
    "PurchaseCancelRequest",
    "PurchaseSubmitRequest",
    "PurchaseRetrySubmitRequest",
    "PurchaseMessage",
    "OnchannelCredentialSaveRequest",
    "OnchannelCredentialStatusResponse",
]
