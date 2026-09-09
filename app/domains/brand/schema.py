"""
=========================================================
Homez OS

Brand Domain
schema.py
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class BrandBase(
    BaseModel
):

    name: str

    slug: str | None = None

    description: str | None = None

    logo_url: str | None = None

    country: str | None = None


class BrandCreate(
    BrandBase
):
    pass


class BrandUpdate(
    BaseModel
):

    name: str | None = None

    description: str | None = None

    logo_url: str | None = None

    country: str | None = None
class BrandResponse(
    BrandBase
):

    id: int

    is_active: bool

    is_verified: bool

    is_official: bool

    product_count: int

    rating: float | None = None

    review_count: int

    ai_score: float | None = None

    created_at: datetime

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class BrandListResponse(
    BaseModel
):

    items: list[BrandResponse]

    total: int


class BrandSearchRequest(
    BaseModel
):

    keyword: str | None = None

    is_verified: bool | None = None

    is_official: bool | None = None

    is_active: bool | None = None

    page: int = 1

    size: int = 20
class BrandResponse(
    BrandBase
):

    id: int

    is_active: bool

    is_verified: bool

    is_official: bool

    product_count: int

    rating: float | None = None

    review_count: int

    ai_score: float | None = None

    created_at: datetime

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class BrandListResponse(
    BaseModel
):

    items: list[BrandResponse]

    total: int


class BrandSearchRequest(
    BaseModel
):

    keyword: str | None = None

    is_verified: bool | None = None

    is_official: bool | None = None

    is_active: bool | None = None

    page: int = 1

    size: int = 20
class BrandFilter(
    BaseModel
):

    is_active: bool | None = None

    is_verified: bool | None = None

    is_official: bool | None = None

    min_rating: float | None = None

    min_product_count: int | None = None


class BrandAIAnalysisResponse(
    BaseModel
):

    brand_id: int

    ai_score: float | None = None

    keywords: list[str] = []

    summary: str | None = None


__all__ = [
    "BrandBase",
    "BrandCreate",
    "BrandUpdate",

    "BrandResponse",
    "BrandListResponse",

    "BrandSearchRequest",
    "BrandFilter",

    "BrandAIAnalysisResponse",
]
