"""
=========================================================
Homez OS

File : app/domains/supplier/schema.py

Supplier Schema
=========================================================
"""

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class SupplierBase(
    BaseModel
):

    name: str

    code: str | None = None

    supplier_type: str = "GENERAL"

    description: str | None = None

    website: str | None = None

    api_url: str | None = None


class SupplierCreate(
    SupplierBase
):
    pass


class SupplierUpdate(
    BaseModel
):

    name: str | None = None

    description: str | None = None

    website: str | None = None

    supplier_type: str | None = None

    api_url: str | None = None
class SupplierResponse(
    SupplierBase
):

    id: int

    is_active: bool

    is_verified: bool

    trust_score: float

    delivery_score: float

    return_rate: float

    order_count: int

    success_count: int

    ai_score: float

    created_at: datetime

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class SupplierListResponse(
    BaseModel
):

    items: list[SupplierResponse]

    total: int


class SupplierSearchRequest(
    BaseModel
):

    keyword: str | None = None

    supplier_type: str | None = None

    is_verified: bool | None = None

    is_active: bool | None = None

    page: int = 1

    size: int = 20
class SupplierStatusResponse(
    BaseModel
):

    id: int

    is_active: bool

    is_verified: bool

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class SupplierRecommendationResponse(
    BaseModel
):

    supplier_id: int

    score: float

    reason: str | None = None


class SupplierEvaluationResponse(
    BaseModel
):

    supplier_id: int

    trust_score: float

    delivery_score: float

    return_rate: float

    success_rate: float
class SupplierFilter(
    BaseModel
):

    supplier_type: str | None = None

    min_trust_score: float | None = None

    min_delivery_score: float | None = None

    max_return_rate: float | None = None

    is_verified: bool | None = None

    is_active: bool | None = None


class SupplierAIAnalysisResponse(
    BaseModel
):

    supplier_id: int

    ai_score: float

    keywords: list[str] = []

    summary: str | None = None


class SupplierOrderRequest(
    BaseModel
):

    supplier_id: int

    product_id: int

    quantity: int = 1


__all__ = [
    "SupplierBase",
    "SupplierCreate",
    "SupplierUpdate",

    "SupplierResponse",
    "SupplierListResponse",

    "SupplierSearchRequest",
    "SupplierFilter",

    "SupplierStatusResponse",
    "SupplierRecommendationResponse",
    "SupplierEvaluationResponse",
    "SupplierAIAnalysisResponse",
    "SupplierOrderRequest",
]    