"""
=========================================================
Homez OS

File : app/domains/currency/schema.py

2026-09-10 Phase 9 — 환율 API 요청/응답 스키마.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class ExchangeRateRecordRequest(BaseModel):

    base_currency: str
    quote_currency: str
    rate: float


class ExchangeRateResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    base_currency: str
    quote_currency: str
    rate: float
    source: str
    recorded_at: datetime


class ToleranceSetRequest(BaseModel):

    tolerance_percent: float


class ToleranceResponse(BaseModel):

    tolerance_percent: float
