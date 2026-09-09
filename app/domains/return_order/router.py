"""
=========================================================
Homez OS

File : app/domains/return_order/router.py

ReturnOrder Router — V7 Gate 4(2026-08-15) 신규.
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.domains.return_order.schema import ReturnOrderCreate
from app.domains.return_order.schema import ReturnOrderRejectRequest
from app.domains.return_order.schema import ReturnOrderResponse
from app.domains.return_order.schema import ReturnOrderStatusEventResponse
from app.domains.return_order.service import ReturnOrderService
from app.domains.user.model import User

router = APIRouter(
    prefix="/return-orders",
    tags=["ReturnOrder"],
)


@router.post(
    "",
    response_model=ReturnOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_return_order(
    data: ReturnOrderCreate,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReturnOrderService(db)

    return service.create_return_order(
        current_user.company_id, data, current_user.id,
    )


@router.get(
    "",
    response_model=list[ReturnOrderResponse],
)
def list_return_orders(
    return_status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReturnOrderService(db)

    return service.list_for_company(
        current_user.company_id, return_status, skip, limit,
    )


@router.get(
    "/{return_order_id}",
    response_model=ReturnOrderResponse,
)
def get_return_order(
    return_order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReturnOrderService(db)

    return service.get(return_order_id, current_user.company_id)


@router.get(
    "/{return_order_id}/status-events",
    response_model=list[ReturnOrderStatusEventResponse],
)
def list_return_order_status_events(
    return_order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReturnOrderService(db)

    return service.list_status_events(
        return_order_id, current_user.company_id,
    )


@router.post(
    "/{return_order_id}/approve",
    response_model=ReturnOrderResponse,
)
def approve_return_order(
    return_order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReturnOrderService(db)

    return service.approve(
        return_order_id, current_user.company_id, current_user.id,
    )


@router.post(
    "/{return_order_id}/receive",
    response_model=ReturnOrderResponse,
)
def mark_return_order_received(
    return_order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReturnOrderService(db)

    return service.mark_received(
        return_order_id, current_user.company_id, current_user.id,
    )


@router.post(
    "/{return_order_id}/complete",
    response_model=ReturnOrderResponse,
)
def complete_return_order(
    return_order_id: int,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):
    """
    실제 재고 반영(Inventory.restock()/reserve()) + 배송/주문 상태
    최종 반영까지 한 번에 수행한다.
    """

    service = ReturnOrderService(db)

    return service.complete(
        return_order_id, current_user.company_id, current_user.id,
    )


@router.post(
    "/{return_order_id}/reject",
    response_model=ReturnOrderResponse,
)
def reject_return_order(
    return_order_id: int,
    data: ReturnOrderRejectRequest,
    current_user: User = Depends(admin_guard),
    db: Session = Depends(get_db),
):

    service = ReturnOrderService(db)

    return service.reject(
        return_order_id, current_user.company_id, data.reason,
        current_user.id,
    )


__all__ = [
    "router",
]
