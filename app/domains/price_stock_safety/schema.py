"""
=========================================================
Homez OS

File : app/domains/price_stock_safety/schema.py

2026-09-10 Phase 10 — 가상재고 임계값·가격 검토주기 API 스키마.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class VirtualStockThresholdSetRequest(BaseModel):

    threshold_quantity: int


class VirtualStockThresholdResponse(BaseModel):

    threshold_quantity: int | None


class VirtualStockCheckRequest(BaseModel):

    displayed_stock: int


class VirtualStockCheckResponse(BaseModel):

    allowed: bool
    reason: str | None = None


class ReviewCycleSetRequest(BaseModel):

    review_cycle_days: int


class ReviewCycleResponse(BaseModel):

    review_cycle_days: int


class PriceCacheTtlSetRequest(BaseModel):

    ttl_minutes: int


class PriceCacheTtlResponse(BaseModel):

    ttl_minutes: int


class StockCacheTtlSetRequest(BaseModel):

    ttl_minutes: int


class StockCacheTtlResponse(BaseModel):

    ttl_minutes: int


class VirtualStockZeroProposalResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    product_code: str
    status: str
    reason: str
    created_at: datetime
    resolved_by: Optional[int] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None


class ResolveVirtualStockZeroProposalRequest(BaseModel):

    approve: bool
    resolution_note: str = Field(min_length=1)
