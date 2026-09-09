"""
=========================================================
Homez OS

File : app/domains/order/repository.py

Order Repository — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.model import OrderStatusEvent


class OrderRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # Order
    # --------------------------------------------------

    def get_order(
        self,
        order_id: int,
    ) -> Order | None:

        return (
            self.db.query(Order)
            .filter(Order.id == order_id)
            .first()
        )

    def get_order_for_company(
        self,
        order_id: int,
        company_id: int,
    ) -> Order | None:

        return (
            self.db.query(Order)
            .filter(Order.id == order_id)
            .filter(Order.company_id == company_id)
            .first()
        )

    def get_order_by_channel(
        self,
        company_id: int,
        channel_code: str,
        channel_order_id: str,
    ) -> Order | None:

        return (
            self.db.query(Order)
            .filter(Order.company_id == company_id)
            .filter(Order.channel_code == channel_code)
            .filter(Order.channel_order_id == channel_order_id)
            .first()
        )

    def list_orders_for_company(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:

        query = self.db.query(Order).filter(
            Order.company_id == company_id,
        )

        if status is not None:
            query = query.filter(Order.status == status)

        return (
            query
            .order_by(Order.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_order_no_commit(
        self,
        order: Order,
    ) -> Order:

        self.db.add(order)
        self.db.flush()

        return order

    def save_order_no_commit(
        self,
        order: Order,
    ) -> Order:

        self.db.add(order)
        self.db.flush()

        return order

    # --------------------------------------------------
    # OrderItem
    # --------------------------------------------------

    def get_item(
        self,
        item_id: int,
    ) -> OrderItem | None:

        return (
            self.db.query(OrderItem)
            .filter(OrderItem.id == item_id)
            .first()
        )

    def get_item_for_company(
        self,
        item_id: int,
        company_id: int,
    ) -> OrderItem | None:

        return (
            self.db.query(OrderItem)
            .filter(OrderItem.id == item_id)
            .filter(OrderItem.company_id == company_id)
            .first()
        )

    def list_items_for_order(
        self,
        order_id: int,
        company_id: int,
    ) -> list[OrderItem]:

        return (
            self.db.query(OrderItem)
            .filter(OrderItem.order_id == order_id)
            .filter(OrderItem.company_id == company_id)
            .order_by(OrderItem.id.asc())
            .all()
        )

    def add_item_no_commit(
        self,
        item: OrderItem,
    ) -> OrderItem:

        self.db.add(item)
        self.db.flush()

        return item

    def save_item_no_commit(
        self,
        item: OrderItem,
    ) -> OrderItem:

        self.db.add(item)
        self.db.flush()

        return item

    def transition_item_status_conditional(
        self,
        item_id: int,
        company_id: int,
        from_status: str,
        to_status: str,
    ) -> int:
        """
        OrderItem 상태 전이를 원자적 조건부 UPDATE로 강제한다 —
        Inventory Gate 3의 transition_reservation_conditional()과
        동일 철학(동시 이중 처리 방지).
        """

        stmt = (
            update(OrderItem)
            .where(OrderItem.id == item_id)
            .where(OrderItem.company_id == company_id)
            .where(OrderItem.status == from_status)
            .values(status=to_status)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # OrderIngestionEvent (append-only)
    # --------------------------------------------------

    def add_ingestion_event_no_commit(
        self,
        event: OrderIngestionEvent,
    ) -> OrderIngestionEvent:

        self.db.add(event)
        self.db.flush()

        return event

    def list_ingestion_events_for_order(
        self,
        order_id: int,
        company_id: int,
    ) -> list[OrderIngestionEvent]:

        return (
            self.db.query(OrderIngestionEvent)
            .filter(OrderIngestionEvent.order_id == order_id)
            .filter(OrderIngestionEvent.company_id == company_id)
            .order_by(OrderIngestionEvent.id.desc())
            .all()
        )

    # --------------------------------------------------
    # OrderStatusEvent (append-only)
    # --------------------------------------------------

    def add_status_event_no_commit(
        self,
        event: OrderStatusEvent,
    ) -> OrderStatusEvent:

        self.db.add(event)
        self.db.flush()

        return event

    def list_status_events_for_order(
        self,
        order_id: int,
        company_id: int,
    ) -> list[OrderStatusEvent]:

        return (
            self.db.query(OrderStatusEvent)
            .filter(OrderStatusEvent.order_id == order_id)
            .filter(OrderStatusEvent.company_id == company_id)
            .order_by(OrderStatusEvent.id.asc())
            .all()
        )


__all__ = [
    "OrderRepository",
]
