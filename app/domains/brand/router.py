"""
=========================================================
Homez OS

Brand Domain
router.py
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.brand.model import (
    Brand,
)

from app.domains.brand.schema import (
    BrandCreate,
    BrandUpdate,
    BrandResponse,
    BrandListResponse,
)

from app.domains.brand.service import (
    BrandService,
)


router = APIRouter(
    prefix="/brands",
    tags=["Brands"],
)


def get_service(
    db: Session = Depends(get_db),
) -> BrandService:

    return BrandService(
        db,
    )


@router.post(
    "",
    response_model=BrandResponse,
)
def create_brand(
    data: BrandCreate,
    service: BrandService = Depends(
        get_service
    ),
):

    brand = Brand(
        **data.model_dump()
    )

    return service.create(
        brand,
    )


@router.get(
    "/{brand_id}",
    response_model=BrandResponse,
)
def get_brand(
    brand_id: int,
    service: BrandService = Depends(
        get_service
    ),
):

    return service.get(
        brand_id,
    )
@router.get(
    "",
    response_model=BrandListResponse,
)
def get_brands(
    service: BrandService = Depends(
        get_service
    ),
):

    brands = service.list_active()

    return {
        "items": brands,
        "total": len(brands),
    }


@router.get(
    "/slug/{slug}",
    response_model=BrandResponse,
)
def get_brand_by_slug(
    slug: str,
    service: BrandService = Depends(
        get_service
    ),
):

    return service.get_by_slug(
        slug,
    )


@router.get(
    "/name/{name}",
    response_model=BrandResponse,
)
def get_brand_by_name(
    name: str,
    service: BrandService = Depends(
        get_service
    ),
):

    return service.get_by_name(
        name,
    )


@router.get(
    "/search/{keyword}",
    response_model=list[BrandResponse],
)
def search_brands(
    keyword: str,
    service: BrandService = Depends(
        get_service
    ),
):

    return service.search(
        keyword,
    )
@router.get(
    "/verified/list",
    response_model=list[BrandResponse],
)
def get_verified_brands(
    service: BrandService = Depends(
        get_service
    ),
):

    return service.get_verified_brands()


@router.get(
    "/official/list",
    response_model=list[BrandResponse],
)
def get_official_brands(
    service: BrandService = Depends(
        get_service
    ),
):

    return service.get_official_brands()


@router.get(
    "/popular/list",
    response_model=list[BrandResponse],
)
def get_popular_brands(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: BrandService = Depends(
        get_service
    ),
):

    return service.get_popular_brands(
        limit,
    )


@router.get(
    "/recommend/list",
    response_model=list[BrandResponse],
)
def get_ai_recommended_brands(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: BrandService = Depends(
        get_service
    ),
):

    return service.get_ai_recommended_brands(
        limit,
    )
@router.put(
    "/{brand_id}",
    response_model=BrandResponse,
)
def update_brand(
    brand_id: int,
    data: BrandUpdate,
    service: BrandService = Depends(
        get_service
    ),
):

    brand = service.get(
        brand_id,
    )

    if brand is None:
        return None

    for key, value in data.model_dump(
        exclude_unset=True
    ).items():

        setattr(
            brand,
            key,
            value,
        )

    return service.update(
        brand,
    )


@router.patch(
    "/{brand_id}/deactivate",
    response_model=BrandResponse,
)
def deactivate_brand(
    brand_id: int,
    service: BrandService = Depends(
        get_service
    ),
):

    brand = service.get(
        brand_id,
    )

    if brand is None:
        return None

    return service.deactivate(
        brand,
    )


@router.delete(
    "/{brand_id}",
)
def delete_brand(
    brand_id: int,
    service: BrandService = Depends(
        get_service
    ),
):

    brand = service.get(
        brand_id,
    )

    if brand is None:
        return {
            "success": False,
        }

    service.delete(
        brand,
    )

    return {
        "success": True,
    }


__all__ = [
    "router",
]