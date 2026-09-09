"""
=========================================================
Homez OS

File : app/domains/shipment/schema.py

Shipment Schema — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ShipmentCreate(BaseModel):
    """
    포함할 주문 품목(order_item_id) 목록 — 각 품목은 반드시 RESERVED
    상태여야 하며, 이 호출로 그 예약을 전량 소모(consume)한다(요구사항
    3/5 — Gate3 예약 모델이 all-or-nothing consume만 지원하는 구조적
    한계로, 품목의 일부 수량만 출고하는 것은 지원하지 않는다).
    """

    order_item_ids: list[int] = Field(min_length=1)

    courier: str = Field(min_length=1, max_length=100)

    invoice_number: str = Field(min_length=1, max_length=100)

    idempotency_key: str = Field(min_length=1, max_length=150)

    tracking_url: Optional[str] = Field(default=None, max_length=500)


class ShipmentItemResponse(BaseModel):

    id: int

    company_id: int

    shipment_id: int

    order_item_id: int

    reservation_id: int

    quantity: int

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class ShipmentResponse(BaseModel):

    id: int

    company_id: int

    order_id: int

    shipment_number: str

    status: str

    courier: Optional[str] = None

    invoice_number: Optional[str] = None

    tracking_url: Optional[str] = None

    shipped_at: Optional[datetime] = None

    delivered_at: Optional[datetime] = None

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class ShipmentStatusUpdateRequest(BaseModel):

    new_status: str = Field(min_length=1, max_length=30)

    reason: Optional[str] = Field(default=None, max_length=500)


class ShipmentStatusEventResponse(BaseModel):

    id: int

    company_id: int

    shipment_id: int

    previous_status: Optional[str] = None

    new_status: str

    reason: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class ShipmentMessage(BaseModel):

    message: str


__all__ = [
    "ShipmentCreate",
    "ShipmentItemResponse",
    "ShipmentResponse",
    "ShipmentStatusUpdateRequest",
    "ShipmentStatusEventResponse",
    "ShipmentMessage",
]
