"""
=========================================================
Homez OS

File : app/core/pagination.py
Version : 2.1.0

Pagination Utilities
=========================================================
"""

from math import ceil

from pydantic import BaseModel
from pydantic import Field


class PaginationParams(
    BaseModel
):

    page: int = Field(
        default=1,
        ge=1,
        description="Page Number",
    )

    size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Page Size",
    )


class PaginationMeta(
    BaseModel
):

    page: int
    size: int
    total: int
    total_pages: int
    has_previous: bool
    has_next: bool


class PaginationResponse(
    BaseModel
):

    meta: PaginationMeta
    items: list


def paginate(
    items: list,
    page: int = 1,
    size: int = 20,
) -> PaginationResponse:

    total = len(items)

    total_pages = (
        ceil(total / size)
        if total > 0
        else 1
    )

    start = (page - 1) * size
    end = start + size

    return PaginationResponse(
        meta=PaginationMeta(
            page=page,
            size=size,
            total=total,
            total_pages=total_pages,
            has_previous=page > 1,
            has_next=page < total_pages,
        ),
        items=items[start:end],
    )


__all__ = [
    "PaginationParams",
    "PaginationMeta",
    "PaginationResponse",
    "paginate",
]