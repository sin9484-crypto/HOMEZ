"""
=========================================================
Homez OS

File : app/domains/return_order/schema.py

ReturnOrder Schema — V7 Gate 4(2026-08-15) 신규.
=========================================================
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ReturnOrderCreate(BaseModel):

    order_item_id: int

    shipment_id: int

    return_type: str = Field(min_length=1, max_length=20)

    quantity: int = Field(gt=0)

    reason: str = Field(min_length=1, max_length=500)

    idempotency_key: str = Field(min_length=1, max_length=150)


class ReturnOrderResponse(BaseModel):

    id: int

    company_id: int

    order_id: int

    order_item_id: int

    shipment_id: int

    return_type: str

    status: str

    quantity: int

    reason: str

    idempotency_key: str

    requested_at: datetime

    approved_at: Optional[datetime] = None

    received_at: Optional[datetime] = None

    completed_at: Optional[datetime] = None

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class ReturnOrderRejectRequest(BaseModel):

    reason: str = Field(min_length=1, max_length=500)


class ReturnOrderStatusEventResponse(BaseModel):

    id: int

    company_id: int

    return_order_id: int

    previous_status: Optional[str] = None

    new_status: str

    reason: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class ReturnOrderMessage(BaseModel):

    message: str


__all__ = [
    "ReturnOrderCreate",
    "ReturnOrderResponse",
    "ReturnOrderRejectRequest",
    "ReturnOrderStatusEventResponse",
    "ReturnOrderMessage",
]
