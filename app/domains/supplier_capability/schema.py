"""
=========================================================
Homez OS

File : app/domains/supplier_capability/schema.py

2026-09-10 Phase 9 — 공급처 프로필·능력 플래그 API 요청/응답 스키마.
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict


class SupplierProfileSetRequest(BaseModel):

    is_international: bool | None = None
    country_code: str | None = None
    default_currency: str | None = None
    consignment_direct_to_customer: bool | None = None


class SupplierProfileResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int
    is_international: bool
    country_code: str | None
    default_currency: str
    consignment_direct_to_customer: bool


class CapabilitySetRequest(BaseModel):

    capability: str
    support: str
    note: str | None = None


class CapabilityMatrixResponse(BaseModel):

    supplier_id: int
    capabilities: dict[str, dict]
