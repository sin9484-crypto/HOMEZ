"""
=========================================================
Homez OS

File : app/domains/shipment/router.py

Shipment Router — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.shipment.schema import ShipmentCreate
from app.domains.shipment.schema import ShipmentItemResponse
from app.domains.shipment.schema import ShipmentResponse
from app.domains.shipment.schema import ShipmentStatusEventResponse
from app.domains.shipment.schema import ShipmentStatusUpdateRequest
from app.domains.shipment.service import ShipmentService
from app.domains.user.model import User

router = APIRouter(
    prefix="/shipments",
    tags=["Shipment"],
)


@router.post(
    "/orders/{order_id}",
    response_model=ShipmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_shipment(
    order_id: int,
    data: ShipmentCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    생성과 동시에 발송 처리(Inventory.consume() 호출)까지 수행한다.
    """

    service = ShipmentService(db)

    return service.create_shipment(
        current_user.company_id, order_id, data, current_user.id,
    )


@router.get(
    "",
    response_model=list[ShipmentResponse],
)
def list_shipments(
    shipment_status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ShipmentService(db)

    return service.list_shipments(
        current_user.company_id, shipment_status, skip, limit,
    )


@router.get(
    "/orders/{order_id}",
    response_model=list[ShipmentResponse],
)
def list_shipments_for_order(
    order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ShipmentService(db)

    return service.list_shipments_for_order(
        order_id, current_user.company_id,
    )


@router.get(
    "/{shipment_id}",
    response_model=ShipmentResponse,
)
def get_shipment(
    shipment_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ShipmentService(db)

    return service.get_shipment(shipment_id, current_user.company_id)


@router.get(
    "/{shipment_id}/items",
    response_model=list[ShipmentItemResponse],
)
def list_shipment_items(
    shipment_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ShipmentService(db)

    return service.list_items(shipment_id, current_user.company_id)


@router.get(
    "/{shipment_id}/status-events",
    response_model=list[ShipmentStatusEventResponse],
)
def list_shipment_status_events(
    shipment_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ShipmentService(db)

    return service.list_status_events(shipment_id, current_user.company_id)


@router.post(
    "/{shipment_id}/status",
    response_model=ShipmentResponse,
)
def update_shipment_status(
    shipment_id: int,
    data: ShipmentStatusUpdateRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ShipmentService(db)

    return service.update_status(
        shipment_id, current_user.company_id, data.new_status,
        data.reason, current_user.id,
    )


__all__ = [
    "router",
]
