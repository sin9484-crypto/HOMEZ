"""
=========================================================
Homez OS

File : app/domains/refund/schema.py

2026-09-10 Phase 8 — 환불 API 요청/응답 스키마.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class RefundCreateRequest(BaseModel):

    order_id: int
    return_order_id: int | None = None
    refund_type: str
    amount: float
    currency: str = "KRW"
    reason: str
    idempotency_key: str


class RefundResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    return_order_id: int | None
    refund_type: str
    status: str
    amount: float
    currency: str
    reason: str
    requested_by: int
    approved_by: int | None
    requested_at: datetime
    approved_at: datetime | None
    executed_at: datetime | None


class RefundRejectRequest(BaseModel):

    reason: str
