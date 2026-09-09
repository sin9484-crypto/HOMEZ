"""
=========================================================
Homez OS

File : app/domains/shipment/repository.py

Shipment Repository — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.shipment.model import Shipment
from app.domains.shipment.model import ShipmentItem
from app.domains.shipment.model import ShipmentStatusEvent


class ShipmentRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # Shipment
    # --------------------------------------------------

    def get_shipment(
        self,
        shipment_id: int,
    ) -> Shipment | None:

        return (
            self.db.query(Shipment)
            .filter(Shipment.id == shipment_id)
            .first()
        )

    def get_shipment_for_company(
        self,
        shipment_id: int,
        company_id: int,
    ) -> Shipment | None:

        return (
            self.db.query(Shipment)
            .filter(Shipment.id == shipment_id)
            .filter(Shipment.company_id == company_id)
            .first()
        )

    def get_shipment_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> Shipment | None:

        return (
            self.db.query(Shipment)
            .filter(Shipment.company_id == company_id)
            .filter(Shipment.idempotency_key == idempotency_key)
            .first()
        )

    def list_shipments_for_order(
        self,
        order_id: int,
        company_id: int,
    ) -> list[Shipment]:

        return (
            self.db.query(Shipment)
            .filter(Shipment.order_id == order_id)
            .filter(Shipment.company_id == company_id)
            .order_by(Shipment.id.asc())
            .all()
        )

    def list_shipments_for_company(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Shipment]:

        query = self.db.query(Shipment).filter(
            Shipment.company_id == company_id,
        )

        if status is not None:
            query = query.filter(Shipment.status == status)

        return (
            query
            .order_by(Shipment.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_shipment_no_commit(
        self,
        shipment: Shipment,
    ) -> Shipment:

        self.db.add(shipment)
        self.db.flush()

        return shipment

    def save_shipment_no_commit(
        self,
        shipment: Shipment,
    ) -> Shipment:

        self.db.add(shipment)
        self.db.flush()

        return shipment

    def transition_shipment_status_conditional(
        self,
        shipment_id: int,
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
            update(Shipment)
            .where(Shipment.id == shipment_id)
            .where(Shipment.company_id == company_id)
            .where(Shipment.status == from_status)
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # ShipmentItem
    # --------------------------------------------------

    def add_shipment_item_no_commit(
        self,
        item: ShipmentItem,
    ) -> ShipmentItem:

        self.db.add(item)
        self.db.flush()

        return item

    def list_items_for_shipment(
        self,
        shipment_id: int,
        company_id: int,
    ) -> list[ShipmentItem]:

        return (
            self.db.query(ShipmentItem)
            .filter(ShipmentItem.shipment_id == shipment_id)
            .filter(ShipmentItem.company_id == company_id)
            .order_by(ShipmentItem.id.asc())
            .all()
        )

    # --------------------------------------------------
    # ShipmentStatusEvent (append-only)
    # --------------------------------------------------

    def add_status_event_no_commit(
        self,
        event: ShipmentStatusEvent,
    ) -> ShipmentStatusEvent:

        self.db.add(event)
        self.db.flush()

        return event

    def list_status_events_for_shipment(
        self,
        shipment_id: int,
        company_id: int,
    ) -> list[ShipmentStatusEvent]:

        return (
            self.db.query(ShipmentStatusEvent)
            .filter(ShipmentStatusEvent.shipment_id == shipment_id)
            .filter(ShipmentStatusEvent.company_id == company_id)
            .order_by(ShipmentStatusEvent.id.asc())
            .all()
        )


__all__ = [
    "ShipmentRepository",
]
