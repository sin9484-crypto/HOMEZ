"""
=========================================================
Homez OS

File : app/domains/supplier/router.py

Supplier Router
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from sqlalchemy.orm import Session

from app.database.session import get_db

from app.domains.supplier.model import (
    Supplier,
)

from app.domains.supplier.schema import (
    SupplierCreate,
    SupplierUpdate,
    SupplierResponse,
    SupplierListResponse,
)

from app.domains.supplier.service import (
    SupplierService,
)


router = APIRouter(
    prefix="/suppliers",
    tags=["Suppliers"],
)


def get_service(
    db: Session = Depends(get_db),
) -> SupplierService:

    return SupplierService(
        db,
    )


@router.post(
    "",
    response_model=SupplierResponse,
)
def create_supplier(
    data: SupplierCreate,
    service: SupplierService = Depends(
        get_service
    ),
):

    supplier = Supplier(
        **data.model_dump()
    )

    return service.create(
        supplier,
    )


@router.get(
    "/{supplier_id}",
    response_model=SupplierResponse,
)
def get_supplier(
    supplier_id: int,
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get(
        supplier_id,
    )
@router.get(
    "",
    response_model=SupplierListResponse,
)
def get_suppliers(
    service: SupplierService = Depends(
        get_service
    ),
):

    suppliers = service.list_active()

    return {
        "items": suppliers,
        "total": len(suppliers),
    }


@router.get(
    "/code/{code}",
    response_model=SupplierResponse,
)
def get_supplier_by_code(
    code: str,
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get_by_code(
        code,
    )


@router.get(
    "/name/{name}",
    response_model=SupplierResponse,
)
def get_supplier_by_name(
    name: str,
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get_by_name(
        name,
    )


@router.get(
    "/search/{keyword}",
    response_model=list[SupplierResponse],
)
def search_suppliers(
    keyword: str,
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.search(
        keyword,
    )
@router.get(
    "/verified/list",
    response_model=list[SupplierResponse],
)
def get_verified_suppliers(
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get_verified_suppliers()


@router.get(
    "/type/{supplier_type}",
    response_model=list[SupplierResponse],
)
def get_suppliers_by_type(
    supplier_type: str,
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get_by_type(
        supplier_type,
    )


@router.get(
    "/trust/list",
    response_model=list[SupplierResponse],
)
def get_high_trust_suppliers(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get_high_trust_suppliers(
        limit,
    )


@router.get(
    "/delivery/list",
    response_model=list[SupplierResponse],
)
def get_best_delivery_suppliers(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get_best_delivery_suppliers(
        limit,
    )
@router.get(
    "/recommend/list",
    response_model=list[SupplierResponse],
)
def get_ai_recommended_suppliers(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    service: SupplierService = Depends(
        get_service
    ),
):

    return service.get_ai_recommended_suppliers(
        limit,
    )


@router.put(
    "/{supplier_id}",
    response_model=SupplierResponse,
)
def update_supplier(
    supplier_id: int,
    data: SupplierUpdate,
    service: SupplierService = Depends(
        get_service
    ),
):

    supplier = service.get(
        supplier_id,
    )

    if supplier is None:
        return None

    for key, value in data.model_dump(
        exclude_unset=True
    ).items():

        setattr(
            supplier,
            key,
            value,
        )

    return service.update(
        supplier,
    )


@router.patch(
    "/{supplier_id}/deactivate",
    response_model=SupplierResponse,
)
def deactivate_supplier(
    supplier_id: int,
    service: SupplierService = Depends(
        get_service
    ),
):

    supplier = service.get(
        supplier_id,
    )

    if supplier is None:
        return None

    return service.deactivate(
        supplier,
    )


@router.delete(
    "/{supplier_id}",
)
def delete_supplier(
    supplier_id: int,
    service: SupplierService = Depends(
        get_service
    ),
):

    supplier = service.get(
        supplier_id,
    )

    if supplier is None:
        return {
            "success": False,
        }

    service.delete(
        supplier,
    )

    return {
        "success": True,
    }


__all__ = [
    "router",
]