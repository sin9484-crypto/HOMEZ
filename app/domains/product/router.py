from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.product.model import (
    Product,
)

from app.domains.product.schema import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
    ProductPriceUpdateRequest,
)

from app.domains.product.service import (
    ProductService,
)


router = APIRouter(
    prefix="/products",
    tags=["Products"],
)


def get_service(
    db: Session = Depends(get_db),
) -> ProductService:

    return ProductService(
        db,
    )


@router.post(
    "",
    response_model=ProductResponse,
)
def create_product(
    data: ProductCreate,
    service: ProductService = Depends(
        get_service
    ),
):

    product = Product(
        **data.model_dump()
    )

    return service.create(
        product,
    )


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
)
def get_product(
    product_id: int,
    service: ProductService = Depends(
        get_service
    ),
):

    return service.get(
        product_id,
    )
@router.get(
    "",
    response_model=ProductListResponse,
)
def get_products(
    service: ProductService = Depends(
        get_service
    ),
):

    products = service.list_active()

    return {
        "items": products,
        "total": len(products),
    }


@router.get(
    "/search/{keyword}",
    response_model=list[ProductResponse],
)
def search_products(
    keyword: str,
    service: ProductService = Depends(
        get_service
    ),
):

    return service.search(
        keyword,
    )


@router.get(
    "/category/{category_id}",
    response_model=list[ProductResponse],
)
def get_category_products(
    category_id: int,
    service: ProductService = Depends(
        get_service
    ),
):

    return service.get_by_category(
        category_id,
    )


@router.get(
    "/brand/{brand_id}",
    response_model=list[ProductResponse],
)
def get_brand_products(
    brand_id: int,
    service: ProductService = Depends(
        get_service
    ),
):

    return service.get_by_brand(
        brand_id,
    )
@router.get(
    "/supplier/{supplier_id}",
    response_model=list[ProductResponse],
)
def get_supplier_products(
    supplier_id: int,
    service: ProductService = Depends(
        get_service
    ),
):

    return service.get_by_supplier(
        supplier_id,
    )


@router.get(
    "/status/{status}",
    response_model=list[ProductResponse],
)
def get_status_products(
    status: str,
    service: ProductService = Depends(
        get_service
    ),
):

    return service.get_by_status(
        status,
    )


@router.get(
    "/best/list",
    response_model=list[ProductResponse],
)
def get_best_products(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: ProductService = Depends(
        get_service
    ),
):

    return service.get_best_products(
        limit,
    )


@router.get(
    "/recommend/list",
    response_model=list[ProductResponse],
)
def get_ai_products(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: ProductService = Depends(
        get_service
    ),
):

    return service.get_ai_recommended_products(
        limit,
    )
@router.put(
    "/{product_id}",
    response_model=ProductResponse,
)
def update_product(
    product_id: int,
    data: ProductUpdate,
    service: ProductService = Depends(
        get_service
    ),
):

    product = service.get(
        product_id,
    )

    if product is None:
        return None

    for key, value in data.model_dump(
        exclude_unset=True
    ).items():

        setattr(
            product,
            key,
            value,
        )

    return service.update(
        product,
    )


@router.patch(
    "/{product_id}/price",
    response_model=ProductResponse,
)
def update_product_price(
    product_id: int,
    data: ProductPriceUpdateRequest,
    service: ProductService = Depends(
        get_service
    ),
):

    product = service.get(
        product_id,
    )

    if product is None:
        return None

    return service.update_price(
        product,
        data.price,
    )


@router.patch(
    "/{product_id}/deactivate",
    response_model=ProductResponse,
)
def deactivate_product(
    product_id: int,
    service: ProductService = Depends(
        get_service
    ),
):

    product = service.get(
        product_id,
    )

    if product is None:
        return None

    return service.deactivate(
        product,
    )


@router.delete(
    "/{product_id}",
)
def delete_product(
    product_id: int,
    service: ProductService = Depends(
        get_service
    ),
):

    product = service.get(
        product_id,
    )

    if product is None:
        return {
            "success": False,
        }

    service.delete(
        product,
    )

    return {
        "success": True,
    }


__all__ = [
    "router",
]