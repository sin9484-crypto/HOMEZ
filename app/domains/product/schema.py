from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class ProductBase(
    BaseModel
):

    name: str

    description: str | None = None

    sku: str | None = None

    brand_id: int | None = None

    category_id: int | None = None

    supplier_id: int | None = None

    price: float = 0

    sale_price: float | None = None

    cost_price: float | None = None

    image_url: str | None = None


class ProductCreate(
    ProductBase
):
    pass


class ProductUpdate(
    BaseModel
):

    name: str | None = None

    description: str | None = None

    price: float | None = None

    sale_price: float | None = None

    image_url: str | None = None
class ProductResponse(
    ProductBase
):

    id: int

    stock_quantity: int

    is_active: bool

    status: str

    ai_score: float | None = None

    view_count: int

    sales_count: int

    return_rate: float | None = None

    created_at: datetime

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class ProductListResponse(
    BaseModel
):

    items: list[ProductResponse]

    total: int


class ProductFilter(
    BaseModel
):

    brand_id: int | None = None

    category_id: int | None = None

    supplier_id: int | None = None

    status: str | None = None

    is_active: bool | None = None   
class ProductSearchRequest(
    BaseModel
):

    keyword: str | None = None

    brand_id: int | None = None

    category_id: int | None = None

    supplier_id: int | None = None

    min_price: float | None = None

    max_price: float | None = None

    status: str | None = None

    page: int = 1

    size: int = 20


class ProductPriceUpdateRequest(
    BaseModel
):

    price: float

    sale_price: float | None = None


class ProductStatusResponse(
    BaseModel
):

    id: int

    status: str

    is_active: bool

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )
class ProductAIAnalysisRequest(
    BaseModel
):

    product_id: int

    keywords: list[str] = []

    analyze_image: bool = False

    analyze_price: bool = False


class ProductRecommendationResponse(
    BaseModel
):

    product_id: int

    score: float

    reason: str | None = None


class ProductPriceCompareResponse(
    BaseModel
):

    product_id: int

    current_price: float

    supplier_price: float | None = None

    margin_rate: float | None = None

    recommended_price: float | None = None


__all__ = [
    "ProductBase",
    "ProductCreate",
    "ProductUpdate",
    "ProductResponse",
    "ProductListResponse",
    "ProductFilter",
    "ProductSearchRequest",
    "ProductPriceUpdateRequest",
    "ProductStatusResponse",
    "ProductAIAnalysisRequest",
    "ProductRecommendationResponse",
    "ProductPriceCompareResponse",
]     