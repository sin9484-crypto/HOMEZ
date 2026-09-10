"""
=========================================================
Homez OS

File : app/domains/payment/schema.py

2026-09-10 Phase 7 — 결제수단·자동결제 한도 API 요청/응답 스키마.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class PaymentMethodRegisterRequest(BaseModel):
    """
    `raw_details`는 이 요청 처리 도중에만 메모리에 존재하고 절대
    DB에 저장되지 않는다 — `PaymentService.register_method()`가
    `raw_details`를 Fake Provider의 `tokenize()`에 넘겨 토큰만
    받은 뒤 버린다.
    """

    method_type: str
    display_name: str
    raw_details: dict = Field(default_factory=dict)
    make_default: bool = False


class PaymentMethodResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    method_type: str
    display_name: str
    is_default: bool
    active: bool
    created_at: datetime
    deactivated_at: datetime | None = None


class PaymentAutoLimitSetRequest(BaseModel):

    per_transaction_limit_amount: float
    daily_limit_amount: float
    currency: str = "KRW"


class PaymentAutoLimitResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    per_transaction_limit_amount: float
    daily_limit_amount: float
    currency: str
    set_at: datetime


class AutoPaymentAllowedResponse(BaseModel):

    allowed: bool
    reason: str | None = None
