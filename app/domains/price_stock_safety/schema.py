"""
=========================================================
Homez OS

File : app/domains/price_stock_safety/schema.py

2026-09-10 Phase 10 — 가상재고 임계값·가격 검토주기 API 스키마.
"""

from __future__ import annotations


from pydantic import BaseModel


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
