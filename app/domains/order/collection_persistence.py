"""Idempotent persistence for normalized Coupang fulfillment records."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.order.collection_model import (
    OrderChannelFulfillment, UnresolvedOrderItem,
)
from app.domains.order.coupang_normalizer import NormalizedCoupangOrder


class CoupangOrderCollectionPersistence:
    def __init__(self, db: Session):
        self.db = db

    def get_fulfillment(
        self, company_id: int, store_connection_id: int,
        channel_order_id: str, shipment_box_id: str,
    ) -> OrderChannelFulfillment | None:
        return (
            self.db.query(OrderChannelFulfillment)
            .filter(OrderChannelFulfillment.company_id == company_id)
            .filter(OrderChannelFulfillment.store_connection_id == store_connection_id)
            .filter(OrderChannelFulfillment.channel_order_id == channel_order_id)
            .filter(OrderChannelFulfillment.shipment_box_id == shipment_box_id)
            .first()
        )

    def upsert_fulfillment(
        self, company_id: int, store_connection_id: int,
        order: NormalizedCoupangOrder,
    ) -> tuple[OrderChannelFulfillment, bool]:
        existing = self.get_fulfillment(
            company_id, store_connection_id,
            order.channel_order_id, order.channel_fulfillment_id,
        )
        if existing is not None:
            existing.raw_status = order.raw_status
            existing.ordered_at = order.ordered_at
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing, False

        record = OrderChannelFulfillment(
            company_id=company_id,
            store_connection_id=store_connection_id,
            channel_order_id=order.channel_order_id,
            shipment_box_id=order.channel_fulfillment_id,
            raw_status=order.raw_status,
            ordered_at=order.ordered_at,
        )
        self.db.add(record)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.get_fulfillment(
                company_id, store_connection_id,
                order.channel_order_id, order.channel_fulfillment_id,
            )
            if existing is None:
                raise
            return existing, False
        self.db.refresh(record)
        return record, True

    def preserve_unresolved_items(
        self, company_id: int, fulfillment_id: int,
        order: NormalizedCoupangOrder,
    ) -> tuple[list[UnresolvedOrderItem], int]:
        saved: list[UnresolvedOrderItem] = []
        created = 0
        for item in order.items:
            existing = (
                self.db.query(UnresolvedOrderItem)
                .filter(UnresolvedOrderItem.company_id == company_id)
                .filter(UnresolvedOrderItem.fulfillment_id == fulfillment_id)
                .filter(UnresolvedOrderItem.channel_item_id == item.channel_item_id)
                .first()
            )
            if existing is not None:
                saved.append(existing)
                continue
            record = UnresolvedOrderItem(
                company_id=company_id,
                fulfillment_id=fulfillment_id,
                channel_item_id=item.channel_item_id,
                vendor_item_id=item.vendor_item_id,
                channel_sku=item.channel_sku,
                product_name_snapshot=item.product_name,
                quantity=item.fulfillable_quantity,
                unit_price=item.unit_price.amount,
                order_price=item.order_price.amount,
                currency_code=item.unit_price.currency,
                status=(
                    "MAPPING_REQUIRED" if item.fulfillable
                    else "NO_FULFILLABLE_QUANTITY"
                ),
            )
            self.db.add(record)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                record = (
                    self.db.query(UnresolvedOrderItem)
                    .filter(UnresolvedOrderItem.company_id == company_id)
                    .filter(UnresolvedOrderItem.fulfillment_id == fulfillment_id)
                    .filter(UnresolvedOrderItem.channel_item_id == item.channel_item_id)
                    .one()
                )
            else:
                created += 1
                self.db.refresh(record)
            saved.append(record)
        return saved, created

    def list_unresolved(
        self, company_id: int, *, status: str | None = "MAPPING_REQUIRED",
    ) -> list[UnresolvedOrderItem]:
        query = self.db.query(UnresolvedOrderItem).filter(
            UnresolvedOrderItem.company_id == company_id,
        )
        if status is not None:
            query = query.filter(UnresolvedOrderItem.status == status)
        return query.order_by(UnresolvedOrderItem.id.desc()).all()


__all__ = ["CoupangOrderCollectionPersistence"]
