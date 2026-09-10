"""
=========================================================
Homez OS

File : app/domains/supplier_capability/router.py

2026-09-10 Phase 9 — 공급처 프로필·능력 플래그 API. 조회는 관리자만
(현재 이 저장소의 다른 민감 라우터와 동일한 기본 정책 — 향후 스태프
조회 권한이 별도로 필요하면 그때 완화).
=========================================================
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import SuperAdminGuard
from app.domains.supplier_capability.schema import CapabilityMatrixResponse
from app.domains.supplier_capability.schema import CapabilitySetRequest
from app.domains.supplier_capability.schema import SupplierProfileResponse
from app.domains.supplier_capability.schema import SupplierProfileSetRequest
from app.domains.supplier_capability.service import SupplierCapabilityService
from app.domains.user.model import User

router = APIRouter(
    prefix="/suppliers/{supplier_id}/capability", tags=["supplier-capability"],
)


def get_supplier_capability_service(
    db: Session = Depends(get_db),
) -> SupplierCapabilityService:

    return SupplierCapabilityService(db)


@router.get(
    "/profile",
    response_model=SupplierProfileResponse,
)
def get_supplier_profile(
    supplier_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: SupplierCapabilityService = Depends(
        get_supplier_capability_service,
    ),
):

    return service.get_or_create_profile(supplier_id)


@router.put(
    "/profile",
    response_model=SupplierProfileResponse,
)
def set_supplier_profile(
    supplier_id: int,
    data: SupplierProfileSetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: SupplierCapabilityService = Depends(
        get_supplier_capability_service,
    ),
):

    return service.set_profile(
        supplier_id=supplier_id, user_id=current_user.id, is_admin=True,
        is_international=data.is_international,
        country_code=data.country_code,
        default_currency=data.default_currency,
        consignment_direct_to_customer=data.consignment_direct_to_customer,
    )


@router.get(
    "/matrix",
    response_model=CapabilityMatrixResponse,
)
def get_capability_matrix(
    supplier_id: int,
    current_user: User = Depends(SuperAdminGuard),
    service: SupplierCapabilityService = Depends(
        get_supplier_capability_service,
    ),
):

    return CapabilityMatrixResponse(
        supplier_id=supplier_id,
        capabilities=service.get_capability_matrix(supplier_id),
    )


@router.put(
    "/matrix",
    response_model=CapabilityMatrixResponse,
)
def set_capability(
    supplier_id: int,
    data: CapabilitySetRequest,
    current_user: User = Depends(SuperAdminGuard),
    service: SupplierCapabilityService = Depends(
        get_supplier_capability_service,
    ),
):

    service.set_capability(
        supplier_id=supplier_id, user_id=current_user.id, is_admin=True,
        capability=data.capability, support=data.support, note=data.note,
    )

    return CapabilityMatrixResponse(
        supplier_id=supplier_id,
        capabilities=service.get_capability_matrix(supplier_id),
    )


__all__ = ["router"]
