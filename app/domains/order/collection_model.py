"""Persistence contracts for channel order collection and unresolved items."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OrderChannelFulfillment(Base):
    __tablename__ = "order_channel_fulfillments"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "store_connection_id", "channel_order_id",
            "shipment_box_id", name="uq_order_fulfillment_external_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    store_connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel_order_id: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    shipment_box_id: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    raw_status: Mapped[str] = mapped_column(String(50), nullable=False)
    ordered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )


class UnresolvedOrderItem(Base):
    __tablename__ = "unresolved_order_items"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "fulfillment_id", "channel_item_id",
            name="uq_unresolved_order_item_external_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fulfillment_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel_item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_item_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel_sku: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    order_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="MAPPING_REQUIRED", index=True,
    )
    resolved_order_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OrderCollectionCursor(Base):
    __tablename__ = "order_collection_cursors"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "store_connection_id", "channel_status",
            name="uq_order_collection_cursor_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    store_connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel_status: Mapped[str] = mapped_column(String(30), nullable=False)
    last_successful_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="IDLE", index=True,
    )
    lock_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )


class OrderSkuResolution(Base):
    """쿠팡 판매계정의 채널 SKU를 HOMEZ 재고 SKU에 연결한다."""

    __tablename__ = "order_sku_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "store_connection_id", "channel_sku",
            name="uq_order_sku_resolution_scope",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    store_connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel_sku: Mapped[str] = mapped_column(String(150), nullable=False)
    inventory_sku_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )


__all__ = [
    "OrderChannelFulfillment", "OrderCollectionCursor", "OrderSkuResolution",
    "UnresolvedOrderItem",
]
