"""
=========================================================
Homez OS

File : app/domains/return_order/service.py

ReturnOrder Service — V7 Gate 4(2026-08-15) 신규(요구사항 5).

흐름: REQUESTED(반품/교환접수) → APPROVED(승인) → RECEIVED(회수 확인)
→ COMPLETED(재고/배송 상태 최종 반영), 또는 REQUESTED/APPROVED에서
REJECTED로 종료.

COMPLETED 시점에 실제 재고/배송 상태를 되돌린다(요구사항 3/5 연결):
  - RETURN: InventoryService.restock()으로 재고를 되돌리고,
    OrderItem/Shipment/Order 상태를 RETURNED로 진행.
  - EXCHANGE: 기존 수량을 restock()으로 회수한 뒤 동일 SKU/수량으로
    InventoryService.reserve()를 다시 걸어 품목을 RESERVED로 되돌린다
    (새 Shipment를 통한 재발송 준비 — 새 송장 생성 자체는 이 Gate의
    일반 Shipment 생성 API를 그대로 재사용한다).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus
from app.domains.order.constants import OrderStatusEventSource
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.constants import ReturnOrderType
from app.domains.return_order.model import ReturnOrder
from app.domains.return_order.model import ReturnOrderStatusEvent
from app.domains.return_order.repository import ReturnOrderRepository
from app.domains.return_order.schema import ReturnOrderCreate
from app.domains.shipment.constants import ShipmentStatus
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)


class ReturnOrderService:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = ReturnOrderRepository(db)

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _get_required(
        self,
        return_order_id: int,
        company_id: int,
    ) -> ReturnOrder:

        return_order = self.repository.get_return_order_for_company(
            return_order_id, company_id,
        )

        if return_order is None:
            raise NotFoundException("반품/교환 요청을 찾을 수 없습니다.")

        return return_order

    def _append_status_event_no_commit(
        self,
        return_order: ReturnOrder,
        *,
        previous_status: str | None,
        new_status: str,
        reason: str | None = None,
        triggered_by: int | None = None,
    ) -> ReturnOrderStatusEvent:

        return self.repository.add_status_event_no_commit(
            ReturnOrderStatusEvent(
                company_id=return_order.company_id,
                return_order_id=return_order.id,
                previous_status=previous_status,
                new_status=new_status,
                reason=reason,
                triggered_by=triggered_by,
            ),
        )

    # --------------------------------------------------
    # 생성(요구사항 5)
    # --------------------------------------------------

    def create_return_order(
        self,
        company_id: int,
        data: ReturnOrderCreate,
        triggered_by: int | None = None,
    ) -> ReturnOrder:
        # Audit(2026-08-21, AG-0) — 되돌림: 반품 생성은 운영자가 직접
        # 수행하는 핵심 업무 CRUD다 — AI Capability Registry로
        # 게이트하지 않는다.

        if data.return_type not in ReturnOrderType.ALL:
            raise BadRequestException(
                f"알 수 없는 반품/교환 유형입니다: {data.return_type}",
            )

        existing = self.repository.get_by_idempotency(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing

        from app.domains.order.repository import OrderRepository
        from app.domains.shipment.repository import ShipmentRepository

        order_repository = OrderRepository(self.db)
        shipment_repository = ShipmentRepository(self.db)

        order_item = order_repository.get_item_for_company(
            data.order_item_id, company_id,
        )
        if order_item is None:
            raise NotFoundException("주문 품목을 찾을 수 없습니다.")

        shipment = shipment_repository.get_shipment_for_company(
            data.shipment_id, company_id,
        )
        if shipment is None or shipment.order_id != order_item.order_id:
            raise NotFoundException("송장을 찾을 수 없습니다.")

        if order_item.status != OrderItemStatus.SHIPPED:
            raise BadRequestException(
                "출고 완료(SHIPPED) 상태의 주문 품목만 반품/교환을 "
                f"접수할 수 있습니다 — 현재 상태: {order_item.status}",
            )

        remaining = order_item.shipped_quantity - order_item.returned_quantity
        if data.quantity != remaining:
            raise BadRequestException(
                "반품/교환 수량은 아직 반품되지 않은 출고 수량과 "
                f"정확히 같아야 합니다(Gate 4 범위 — 부분 반품 미지원). "
                f"요청={data.quantity}, 반품가능={remaining}",
            )

        return_order = ReturnOrder(
            company_id=company_id,
            order_id=order_item.order_id,
            order_item_id=order_item.id,
            shipment_id=shipment.id,
            return_type=data.return_type,
            status=ReturnOrderStatus.REQUESTED,
            quantity=data.quantity,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
        )

        try:
            return_order = self.repository.add_no_commit(return_order)
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_by_idempotency(
                company_id, data.idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "반품/교환 접수 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        self._append_status_event_no_commit(
            return_order, previous_status=None,
            new_status=ReturnOrderStatus.REQUESTED,
            reason=data.reason, triggered_by=triggered_by,
        )

        shipment_new_status = (
            ShipmentStatus.RETURN_REQUESTED
            if data.return_type == ReturnOrderType.RETURN
            else ShipmentStatus.EXCHANGE_REQUESTED
        )
        if shipment_new_status in ShipmentStatus.ALLOWED_TRANSITIONS.get(
            shipment.status, set(),
        ):
            shipment.status = shipment_new_status
            shipment_repository.save_shipment_no_commit(shipment)

        order_new_status = (
            OrderStatus.RETURN_REQUESTED
            if data.return_type == ReturnOrderType.RETURN
            else OrderStatus.EXCHANGE_REQUESTED
        )
        order = order_repository.get_order_for_company(
            order_item.order_id, company_id,
        )
        if order is not None:
            previous_status = order.status
            order.status = order_new_status
            order_repository.save_order_no_commit(order)
            from app.domains.order.model import OrderStatusEvent

            order_repository.add_status_event_no_commit(
                OrderStatusEvent(
                    company_id=company_id,
                    order_id=order.id,
                    previous_status=previous_status,
                    new_status=order_new_status,
                    source=(
                        OrderStatusEventSource.RETURN
                        if data.return_type == ReturnOrderType.RETURN
                        else OrderStatusEventSource.EXCHANGE
                    ),
                    reason=data.reason,
                    triggered_by=triggered_by,
                ),
            )

        self.db.commit()
        self.db.refresh(return_order)

        dispatch_operational_event(
            self.db, "RETURN_EXCHANGE_APPROVAL_NEEDED",
            company_id=company_id, user_id=triggered_by,
            idempotency_key=f"return-approval:{return_order.id}:REQUESTED",
            title="반품·교환 승인이 필요합니다",
            message=f"반품·교환 요청 #{return_order.id}이 접수됐습니다.",
            link_path="returns", entity_ref=f"return_order:{return_order.id}",
            reason=data.reason,
            entity_summary=f"주문 #{return_order.order_id}",
        )

        return return_order

    # --------------------------------------------------
    # 상태 전이
    # --------------------------------------------------

    def approve(
        self,
        return_order_id: int,
        company_id: int,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> ReturnOrder:

        now = now or datetime.utcnow()
        return_order = self._get_required(return_order_id, company_id)

        rowcount = self.repository.transition_status_conditional(
            return_order_id, company_id,
            ReturnOrderStatus.REQUESTED, ReturnOrderStatus.APPROVED,
            extra_values={"approved_at": now},
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "REQUESTED 상태가 아닌 반품/교환은 승인할 수 없습니다 "
                f"— 현재 상태: {return_order.status}",
            )

        self._append_status_event_no_commit(
            return_order, previous_status=ReturnOrderStatus.REQUESTED,
            new_status=ReturnOrderStatus.APPROVED, triggered_by=triggered_by,
        )

        self.db.commit()
        self.db.refresh(return_order)

        return return_order

    def mark_received(
        self,
        return_order_id: int,
        company_id: int,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> ReturnOrder:

        now = now or datetime.utcnow()
        return_order = self._get_required(return_order_id, company_id)

        rowcount = self.repository.transition_status_conditional(
            return_order_id, company_id,
            ReturnOrderStatus.APPROVED, ReturnOrderStatus.RECEIVED,
            extra_values={"received_at": now},
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "APPROVED 상태가 아닌 반품/교환은 회수 확인할 수 "
                f"없습니다 — 현재 상태: {return_order.status}",
            )

        self._append_status_event_no_commit(
            return_order, previous_status=ReturnOrderStatus.APPROVED,
            new_status=ReturnOrderStatus.RECEIVED, triggered_by=triggered_by,
        )

        self.db.commit()
        self.db.refresh(return_order)

        return return_order

    def reject(
        self,
        return_order_id: int,
        company_id: int,
        reason: str,
        triggered_by: int | None = None,
    ) -> ReturnOrder:

        return_order = self._get_required(return_order_id, company_id)

        if return_order.status not in (
            ReturnOrderStatus.REQUESTED, ReturnOrderStatus.APPROVED,
        ):
            raise BadRequestException(
                "REQUESTED/APPROVED 상태가 아닌 반품/교환은 거절할 수 "
                f"없습니다 — 현재 상태: {return_order.status}",
            )

        rowcount = self.repository.transition_status_conditional(
            return_order_id, company_id,
            return_order.status, ReturnOrderStatus.REJECTED,
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "반품/교환 상태가 이미 다른 요청에 의해 변경되었습니다.",
            )

        self._append_status_event_no_commit(
            return_order, previous_status=return_order.status,
            new_status=ReturnOrderStatus.REJECTED, reason=reason,
            triggered_by=triggered_by,
        )

        self.db.commit()
        self.db.refresh(return_order)

        return return_order

    def complete(
        self,
        return_order_id: int,
        company_id: int,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> ReturnOrder:
        """
        RECEIVED → COMPLETED — 실제 재고/배송/주문 상태를 최종 반영한다
        (요구사항 3/5 연결 고리를 닫는다).
        """

        now = now or datetime.utcnow()
        return_order = self._get_required(return_order_id, company_id)

        rowcount = self.repository.transition_status_conditional(
            return_order_id, company_id,
            ReturnOrderStatus.RECEIVED, ReturnOrderStatus.COMPLETED,
            extra_values={"completed_at": now},
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "RECEIVED 상태가 아닌 반품/교환은 최종 완료 처리할 수 "
                f"없습니다 — 현재 상태: {return_order.status}",
            )

        from app.domains.inventory.schema import InventoryRestockRequest
        from app.domains.inventory.schema import InventoryReserveRequest
        from app.domains.inventory.service import InventoryService
        from app.domains.order.repository import OrderRepository
        from app.domains.shipment.repository import ShipmentRepository

        inventory_service = InventoryService(self.db)
        order_repository = OrderRepository(self.db)
        shipment_repository = ShipmentRepository(self.db)

        order_item = order_repository.get_item_for_company(
            return_order.order_item_id, company_id,
        )
        if order_item is None:
            raise NotFoundException("주문 품목을 찾을 수 없습니다.")

        inventory_service.restock(
            order_item.inventory_sku_id,
            company_id,
            InventoryRestockRequest(
                quantity=return_order.quantity,
                idempotency_key=(
                    f"return_order:{return_order.id}:restock"
                ),
                reason=(
                    "반품 입고"
                    if return_order.return_type == ReturnOrderType.RETURN
                    else "교환 회수 입고"
                ),
            ),
            triggered_by,
        )

        shipment = shipment_repository.get_shipment_for_company(
            return_order.shipment_id, company_id,
        )

        if return_order.return_type == ReturnOrderType.RETURN:
            order_item.status = OrderItemStatus.RETURNED
            order_item.returned_quantity += return_order.quantity
            order_repository.save_item_no_commit(order_item)

            if shipment is not None:
                shipment_repository.transition_shipment_status_conditional(
                    shipment.id, company_id,
                    ShipmentStatus.RETURN_REQUESTED, ShipmentStatus.RETURNED,
                )

            final_order_status = OrderStatus.RETURNED
            final_source = OrderStatusEventSource.RETURN
        else:
            reservation = inventory_service.reserve(
                order_item.inventory_sku_id,
                company_id,
                InventoryReserveRequest(
                    quantity=return_order.quantity,
                    idempotency_key=(
                        f"return_order:{return_order.id}:re_reserve"
                    ),
                    reference_type="order_item",
                    reference_id=order_item.id,
                ),
                triggered_by,
            )
            order_item.status = OrderItemStatus.RESERVED
            order_item.reservation_id = reservation.id
            order_item.shipped_quantity = 0
            order_repository.save_item_no_commit(order_item)

            if shipment is not None:
                shipment_repository.transition_shipment_status_conditional(
                    shipment.id, company_id,
                    ShipmentStatus.EXCHANGE_REQUESTED,
                    ShipmentStatus.EXCHANGED,
                )

            final_order_status = OrderStatus.EXCHANGED
            final_source = OrderStatusEventSource.EXCHANGE

        order = order_repository.get_order_for_company(
            return_order.order_id, company_id,
        )
        if order is not None:
            previous_status = order.status
            order.status = final_order_status
            order_repository.save_order_no_commit(order)
            from app.domains.order.model import OrderStatusEvent

            order_repository.add_status_event_no_commit(
                OrderStatusEvent(
                    company_id=company_id,
                    order_id=order.id,
                    previous_status=previous_status,
                    new_status=final_order_status,
                    source=final_source,
                    triggered_by=triggered_by,
                ),
            )

        self._append_status_event_no_commit(
            return_order, previous_status=ReturnOrderStatus.RECEIVED,
            new_status=ReturnOrderStatus.COMPLETED, triggered_by=triggered_by,
        )

        self.db.commit()
        self.db.refresh(return_order)

        return return_order

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get(
        self,
        return_order_id: int,
        company_id: int,
    ) -> ReturnOrder:

        return self._get_required(return_order_id, company_id)

    def list_for_company(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ReturnOrder]:

        return self.repository.list_for_company(
            company_id, status, skip, limit,
        )

    def list_status_events(
        self,
        return_order_id: int,
        company_id: int,
    ) -> list[ReturnOrderStatusEvent]:

        self._get_required(return_order_id, company_id)

        return self.repository.list_status_events(
            return_order_id, company_id,
        )


__all__ = [
    "ReturnOrderService",
]
