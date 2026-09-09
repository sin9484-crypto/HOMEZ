"""
=========================================================
Homez OS

File : app/domains/source/schema.py
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class SupplierProductLinkCreate(BaseModel):

    product_candidate_id: int
    supplier_id: int
    supplier_sku: str = Field(..., min_length=1, max_length=150)
    unit_cost: float = Field(..., gt=0)
    moq: int = Field(default=1, ge=1)
    lead_time_days: int | None = Field(default=None, ge=0)
    shipping_cost: float | None = Field(default=None, ge=0)
    price_valid_until: datetime | None = None
    stock_available: int | None = Field(default=None, ge=0)
    return_policy: str | None = Field(default=None, max_length=500)


class SupplierProductLinkResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    product_candidate_id: int
    supplier_id: int
    supplier_sku: str
    unit_cost: float
    moq: int
    lead_time_days: int | None
    shipping_cost: float | None
    price_valid_until: datetime | None
    stock_available: int | None
    return_policy: str | None
    status: str
    created_by: int
    created_at: datetime
    updated_at: datetime


class CompanySupplierRelationCreate(BaseModel):

    supplier_id: int
    contact_name: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=50)
    contact_email: str | None = Field(default=None, max_length=200)
    payment_terms: str | None = Field(default=None, max_length=500)
    credential_reference: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)


class CompanySupplierRelationApprove(BaseModel):

    approve: bool


class CompanySupplierRelationResponse(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    supplier_id: int
    approval_status: str
    contact_name: str | None
    contact_phone: str | None
    contact_email: str | None
    payment_terms: str | None
    credential_reference: str | None
    notes: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime


class SupplierPublicDirectoryResponse(BaseModel):
    """전역 공개 식별정보만 — api_key/api_secret/metadata_json 등
    비공개 필드는 절대 포함하지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None
    supplier_type: str
    website: str | None
    is_active: bool
    is_verified: bool


# --------------------------------------------------
# 공급처 검색(SupplierDiscoveryProvider 경계, 2026-08-21 작업 1) —
# discovery_providers.py의 순수 dataclass를 API 경계에서만 Pydantic으로
# 감싼다(dataclass 자체는 바꾸지 않는다 — 순수 로직 계층 유지).
# --------------------------------------------------

class SupplierDiscoverySearchRequest(BaseModel):

    provider_code: str = Field(
        default="FAKE", description="FAKE / MANUAL / CSV",
    )
    product_name: str = Field(..., min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=100)
    barcode: str | None = Field(default=None, max_length=100)
    manufacturer_sku: str | None = Field(default=None, max_length=150)
    category: str | None = Field(default=None, max_length=100)
    min_order_qty: int | None = Field(default=None, ge=1)
    ship_regions: list[str] = Field(default_factory=list)
    # CSV Provider 전용 — 사용자가 직접 준비한 행 목록(외부 파일 업로드
    # 없이 이 요청 본문 안에서만 검색한다, 서버에 영구 저장하지 않음).
    csv_rows: list[dict] = Field(default_factory=list)


class SupplierDiscoveryResultResponse(BaseModel):

    supplier_id: int
    supplier_product_id: str
    product_name: str
    options: str | None
    unit_cost: float
    stock: int | None
    moq: int
    shipping_cost: float | None
    lead_time_days: int | None
    last_checked_at: datetime
    source: str
    verification_status: str


# --------------------------------------------------
# 공급처 상품 연결 화면 전용 최소 상품 후보 조회(2026-08-21 4차 지시,
# 작업 1) — 기존 admin 전용 GET /product-candidates의 원가/마진/
# 내부 점수/타 회사 정보를 그대로 노출하지 않는다. 연결에 실제로
# 필요한 값만 담는다.
# --------------------------------------------------

class SourcingProductCandidateResponse(BaseModel):

    id: int
    product_name: str
    brand: str | None
    category: str | None
    status: str
    is_approved: bool


__all__ = [
    "SupplierProductLinkCreate",
    "SupplierProductLinkResponse",
    "CompanySupplierRelationCreate",
    "CompanySupplierRelationApprove",
    "CompanySupplierRelationResponse",
    "SupplierPublicDirectoryResponse",
    "SupplierDiscoverySearchRequest",
    "SupplierDiscoveryResultResponse",
    "SourcingProductCandidateResponse",
]
