"""
=========================================================
Homez OS

File : app/domains/return_order/repository.py

ReturnOrder Repository — V7 Gate 4(2026-08-15) 신규.
=========================================================
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.return_order.model import ReturnOrder
from app.domains.return_order.model import ReturnOrderStatusEvent


class ReturnOrderRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    def get_return_order(
        self,
        return_order_id: int,
    ) -> ReturnOrder | None:

        return (
            self.db.query(ReturnOrder)
            .filter(ReturnOrder.id == return_order_id)
            .first()
        )

    def get_return_order_for_company(
        self,
        return_order_id: int,
        company_id: int,
    ) -> ReturnOrder | None:

        return (
            self.db.query(ReturnOrder)
            .filter(ReturnOrder.id == return_order_id)
            .filter(ReturnOrder.company_id == company_id)
            .first()
        )

    def get_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> ReturnOrder | None:

        return (
            self.db.query(ReturnOrder)
            .filter(ReturnOrder.company_id == company_id)
            .filter(ReturnOrder.idempotency_key == idempotency_key)
            .first()
        )

    def list_for_order_item(
        self,
        order_item_id: int,
        company_id: int,
    ) -> list[ReturnOrder]:

        return (
            self.db.query(ReturnOrder)
            .filter(ReturnOrder.order_item_id == order_item_id)
            .filter(ReturnOrder.company_id == company_id)
            .order_by(ReturnOrder.id.asc())
            .all()
        )

    def list_for_company(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ReturnOrder]:

        query = self.db.query(ReturnOrder).filter(
            ReturnOrder.company_id == company_id,
        )

        if status is not None:
            query = query.filter(ReturnOrder.status == status)

        return (
            query
            .order_by(ReturnOrder.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_no_commit(
        self,
        return_order: ReturnOrder,
    ) -> ReturnOrder:

        self.db.add(return_order)
        self.db.flush()

        return return_order

    def save_no_commit(
        self,
        return_order: ReturnOrder,
    ) -> ReturnOrder:

        self.db.add(return_order)
        self.db.flush()

        return return_order

    def transition_status_conditional(
        self,
        return_order_id: int,
        company_id: int,
        from_status: str,
        to_status: str,
        *,
        extra_values: dict | None = None,
    ) -> int:

        values: dict = {"status": to_status}
        if extra_values:
            values.update(extra_values)

        stmt = (
            update(ReturnOrder)
            .where(ReturnOrder.id == return_order_id)
            .where(ReturnOrder.company_id == company_id)
            .where(ReturnOrder.status == from_status)
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def add_status_event_no_commit(
        self,
        event: ReturnOrderStatusEvent,
    ) -> ReturnOrderStatusEvent:

        self.db.add(event)
        self.db.flush()

        return event

    def list_status_events(
        self,
        return_order_id: int,
        company_id: int,
    ) -> list[ReturnOrderStatusEvent]:

        return (
            self.db.query(ReturnOrderStatusEvent)
            .filter(ReturnOrderStatusEvent.return_order_id == return_order_id)
            .filter(ReturnOrderStatusEvent.company_id == company_id)
            .order_by(ReturnOrderStatusEvent.id.asc())
            .all()
        )


__all__ = [
    "ReturnOrderRepository",
]
