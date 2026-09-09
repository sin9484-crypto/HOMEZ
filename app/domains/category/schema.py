from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict


class CategoryBase(
    BaseModel
):

    name: str

    slug: str | None = None

    description: str | None = None

    parent_id: int | None = None

    sort_order: int = 0

    image_url: str | None = None


class CategoryCreate(
    CategoryBase
):
    pass


class CategoryUpdate(
    BaseModel
):

    name: str | None = None

    slug: str | None = None

    description: str | None = None

    parent_id: int | None = None

    sort_order: int | None = None

    image_url: str | None = None
class CategoryResponse(
    CategoryBase
):

    id: int

    is_active: bool

    is_visible: bool

    product_count: int

    level: int

    ai_keyword: str | None = None

    created_at: datetime

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class CategoryListResponse(
    BaseModel
):

    items: list[CategoryResponse]

    total: int


class CategoryTreeResponse(
    BaseModel
):

    id: int

    name: str

    children: list["CategoryTreeResponse"] = []


class CategorySearchRequest(
    BaseModel
):

    keyword: str | None = None

    parent_id: int | None = None

    level: int | None = None

    is_active: bool | None = None

    page: int = 1

    size: int = 20
class CategoryStatusResponse(
    BaseModel
):

    id: int

    is_active: bool

    is_visible: bool

    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )


class CategoryTreeRequest(
    BaseModel
):

    parent_id: int | None = None

    include_children: bool = True


class CategorySortRequest(
    BaseModel
):

    category_id: int

    sort_order: int


class CategoryProductCountResponse(
    BaseModel
):

    category_id: int

    product_count: int
CategoryTreeResponse.model_rebuild()


__all__ = [
    "CategoryBase",
    "CategoryCreate",
    "CategoryUpdate",

    "CategoryResponse",
    "CategoryListResponse",
    "CategoryTreeResponse",

    "CategorySearchRequest",
    "CategoryStatusResponse",
    "CategoryTreeRequest",
    "CategorySortRequest",
    "CategoryProductCountResponse",
]            