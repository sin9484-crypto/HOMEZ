"""
=========================================================
Homez OS

File : app/domains/shipment/service.py

Shipment Service — V7 Gate 4(2026-08-15) 처음부터 재설계.

create_shipment()은 생성과 동시에 발송 처리까지 한 번에 수행한다
(PENDING→READY→SHIPPED, 각 단계마다 append-only ShipmentStatusEvent를
남긴다 — 요구사항 5). 이 시점에 포함된 각 OrderItem의 Inventory
예약을 InventoryService.consume()으로 전량 소모한다(요구사항 3).
이후 배송중/배송완료/반품/교환 등 후속 상태는 update_status()로
관리한다.
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
from app.domains.shipment.constants import ShipmentStatus
from app.domains.shipment.model import Shipment
from app.domains.shipment.model import ShipmentItem
from app.domains.shipment.model import ShipmentStatusEvent
from app.domains.shipment.repository import ShipmentRepository
from app.domains.shipment.schema import ShipmentCreate


class ShipmentService:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = ShipmentRepository(db)

    # --------------------------------------------------
    # 내부 헬퍼
    # --------------------------------------------------

    def _get_shipment_required(
        self,
        shipment_id: int,
        company_id: int,
    ) -> Shipment:

        shipment = self.repository.get_shipment_for_company(
            shipment_id, company_id,
        )

        if shipment is None:
            raise NotFoundException("송장(Shipment)을 찾을 수 없습니다.")

        return shipment

    def _append_status_event_no_commit(
        self,
        shipment: Shipment,
        *,
        previous_status: str | None,
        new_status: str,
        reason: str | None = None,
        triggered_by: int | None = None,
    ) -> ShipmentStatusEvent:

        return self.repository.add_status_event_no_commit(
            ShipmentStatusEvent(
                company_id=shipment.company_id,
                shipment_id=shipment.id,
                previous_status=previous_status,
                new_status=new_status,
                reason=reason,
                triggered_by=triggered_by,
            ),
        )

    def _recompute_order_status(
        self,
        order_id: int,
        company_id: int,
        triggered_by: int | None,
    ) -> None:

        from app.domains.order.model import OrderStatusEvent
        from app.domains.order.repository import OrderRepository

        order_repository = OrderRepository(self.db)
        order = order_repository.get_order_for_company(order_id, company_id)
        if order is None:
            return

        items = order_repository.list_items_for_order(order_id, company_id)
        active_items = [
            item for item in items
            if item.status != OrderItemStatus.CANCELLED
        ]
        if not active_items:
            return

        all_shipped = all(
            item.status == OrderItemStatus.SHIPPED for item in active_items
        )
        any_shipped = any(
            item.status == OrderItemStatus.SHIPPED for item in active_items
        )

        new_status = order.status
        if all_shipped:
            new_status = OrderStatus.SHIPPED
        elif any_shipped:
            new_status = OrderStatus.PARTIALLY_SHIPPED

        if new_status != order.status:
            previous_status = order.status
            order.status = new_status
            order_repository.save_order_no_commit(order)
            order_repository.add_status_event_no_commit(
                OrderStatusEvent(
                    company_id=company_id,
                    order_id=order.id,
                    previous_status=previous_status,
                    new_status=new_status,
                    source=OrderStatusEventSource.SHIPMENT,
                    triggered_by=triggered_by,
                ),
            )

    # --------------------------------------------------
    # 생성 + 발송(요구사항 3/5)
    # --------------------------------------------------

    def create_shipment(
        self,
        company_id: int,
        order_id: int,
        data: ShipmentCreate,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> Shipment:
        # Audit(2026-08-21, AG-0) — 되돌림: 출고 생성은 운영자가 직접
        # 수행하는 핵심 업무 CRUD다 — AI Capability Registry로
        # 게이트하지 않는다.

        now = now or datetime.utcnow()

        existing = self.repository.get_shipment_by_idempotency(
            company_id, data.idempotency_key,
        )
        if existing is not None:
            return existing

        from app.domains.order.repository import OrderRepository

        order_repository = OrderRepository(self.db)

        order = order_repository.get_order_for_company(order_id, company_id)
        if order is None:
            raise NotFoundException("주문을 찾을 수 없습니다.")

        resolved_items = []
        for order_item_id in data.order_item_ids:
            item = order_repository.get_item_for_company(
                order_item_id, company_id,
            )
            if item is None or item.order_id != order_id:
                raise NotFoundException(
                    f"주문 품목 {order_item_id}을(를) 찾을 수 없습니다.",
                )
            if item.status != OrderItemStatus.RESERVED:
                raise BadRequestException(
                    f"주문 품목 {order_item_id}은(는) RESERVED 상태가 "
                    f"아니어서 출고할 수 없습니다 — 현재 상태: "
                    f"{item.status}",
                )
            resolved_items.append(item)

        shipment = Shipment(
            company_id=company_id,
            order_id=order_id,
            idempotency_key=data.idempotency_key,
            status=ShipmentStatus.PENDING,
            courier=data.courier,
            invoice_number=data.invoice_number,
            tracking_url=data.tracking_url,
        )

        try:
            shipment = self.repository.add_shipment_no_commit(shipment)
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_shipment_by_idempotency(
                company_id, data.idempotency_key,
            )
            if existing is not None:
                return existing
            raise ConflictException(
                "송장 생성 중 충돌이 발생했습니다 — 다시 시도하세요.",
            )

        shipment.shipment_number = f"S-{shipment.id}"

        self._append_status_event_no_commit(
            shipment, previous_status=None, new_status=ShipmentStatus.PENDING,
            triggered_by=triggered_by,
        )

        shipment.status = ShipmentStatus.READY
        self._append_status_event_no_commit(
            shipment, previous_status=ShipmentStatus.PENDING,
            new_status=ShipmentStatus.READY, triggered_by=triggered_by,
        )

        from app.domains.inventory.service import InventoryService

        inventory_service = InventoryService(self.db)

        for item in resolved_items:
            inventory_service.consume(
                item.reservation_id, company_id, triggered_by,
            )
            self.repository.add_shipment_item_no_commit(
                ShipmentItem(
                    company_id=company_id,
                    shipment_id=shipment.id,
                    order_item_id=item.id,
                    reservation_id=item.reservation_id,
                    quantity=item.quantity,
                ),
            )
            item.status = OrderItemStatus.SHIPPED
            item.shipped_quantity = item.quantity
            order_repository.save_item_no_commit(item)

        shipment.status = ShipmentStatus.SHIPPED
        shipment.shipped_at = now
        self.repository.save_shipment_no_commit(shipment)
        self._append_status_event_no_commit(
            shipment, previous_status=ShipmentStatus.READY,
            new_status=ShipmentStatus.SHIPPED, triggered_by=triggered_by,
        )

        self._recompute_order_status(order_id, company_id, triggered_by)

        self.db.commit()
        self.db.refresh(shipment)

        return shipment

    # --------------------------------------------------
    # 후속 상태 전이(요구사항 5)
    # --------------------------------------------------

    def update_status(
        self,
        shipment_id: int,
        company_id: int,
        new_status: str,
        reason: str | None = None,
        triggered_by: int | None = None,
        now: datetime | None = None,
    ) -> Shipment:

        now = now or datetime.utcnow()

        shipment = self._get_shipment_required(shipment_id, company_id)

        allowed = ShipmentStatus.ALLOWED_TRANSITIONS.get(shipment.status)
        if allowed is None or new_status not in allowed:
            raise BadRequestException(
                f"허용되지 않는 배송 상태 전이입니다: "
                f"{shipment.status} → {new_status}",
            )

        extra_values: dict = {}
        if new_status == ShipmentStatus.DELIVERED:
            extra_values["delivered_at"] = now

        rowcount = self.repository.transition_shipment_status_conditional(
            shipment_id, company_id, shipment.status, new_status,
            extra_values=extra_values or None,
        )

        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "배송 상태가 이미 다른 요청에 의해 변경되었습니다 — "
                "다시 조회 후 시도하세요.",
            )

        self._append_status_event_no_commit(
            shipment, previous_status=shipment.status, new_status=new_status,
            reason=reason, triggered_by=triggered_by,
        )

        if new_status == ShipmentStatus.DELIVERED:
            from app.domains.order.repository import OrderRepository

            order_repository = OrderRepository(self.db)
            order = order_repository.get_order_for_company(
                shipment.order_id, company_id,
            )
            if order is not None:
                shipments = self.repository.list_shipments_for_order(
                    shipment.order_id, company_id,
                )
                if all(
                    s.status == ShipmentStatus.DELIVERED
                    or s.id == shipment.id
                    for s in shipments
                ):
                    previous_status = order.status
                    order.status = OrderStatus.DELIVERED
                    order_repository.save_order_no_commit(order)
                    from app.domains.order.model import OrderStatusEvent

                    order_repository.add_status_event_no_commit(
                        OrderStatusEvent(
                            company_id=company_id,
                            order_id=order.id,
                            previous_status=previous_status,
                            new_status=OrderStatus.DELIVERED,
                            source=OrderStatusEventSource.SHIPMENT,
                            triggered_by=triggered_by,
                        ),
                    )

        self.db.commit()
        self.db.refresh(shipment)

        return shipment

    # --------------------------------------------------
    # 조회
    # --------------------------------------------------

    def get_shipment(
        self,
        shipment_id: int,
        company_id: int,
    ) -> Shipment:

        return self._get_shipment_required(shipment_id, company_id)

    def list_shipments(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Shipment]:

        return self.repository.list_shipments_for_company(
            company_id, status, skip, limit,
        )

    def list_shipments_for_order(
        self,
        order_id: int,
        company_id: int,
    ) -> list[Shipment]:

        return self.repository.list_shipments_for_order(
            order_id, company_id,
        )

    def list_items(
        self,
        shipment_id: int,
        company_id: int,
    ) -> list[ShipmentItem]:

        self._get_shipment_required(shipment_id, company_id)

        return self.repository.list_items_for_shipment(
            shipment_id, company_id,
        )

    def list_status_events(
        self,
        shipment_id: int,
        company_id: int,
    ) -> list[ShipmentStatusEvent]:

        self._get_shipment_required(shipment_id, company_id)

        return self.repository.list_status_events_for_shipment(
            shipment_id, company_id,
        )


__all__ = [
    "ShipmentService",
]
