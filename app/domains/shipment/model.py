"""
=========================================================
Homez OS

File : app/domains/shipment/model.py

Shipment Model — V7 Gate 4(2026-08-15) 처음부터 재설계.

order/model.py, purchase/model.py 상단 주석과 동일한 근거로 완전히
새로 설계한다(레거시 relationship() 없음, 실제 homez.db에 shipments
테이블 없음, CTO 지시문이 명시적으로 재설계를 허용).

설계(요구사항 5 — 여러 송장으로 나뉘는 부분출고):
  - Inventory Gate 3의 InventoryReservation은 all-or-nothing consume만
    지원한다(품목 하나의 수량을 여러 번에 걸쳐 나눠 소모하는 기능은
    없음). 따라서 이번 Gate의 "부분출고"는 **주문(Order) 하나에 속한
    여러 OrderItem을 서로 다른 Shipment로 나눠 발송**하는 형태로
    구현한다 — 품목 하나의 수량 자체를 여러 송장으로 쪼개는 것은 범위
    밖(Gate3 예약 모델의 구조적 한계, 결과 보고서에 명시).
  - `Shipment`(송장 1건, 현재상태) + `ShipmentItem`(포함된 주문 품목
    라인) + `ShipmentStatusEvent`(append-only 상태 이력 — 요구사항 5).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base
from app.domains.shipment.constants import ShipmentStatus


class Shipment(Base):

    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_shipments_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (orders.id)
    order_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    shipment_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=ShipmentStatus.PENDING,
        index=True,
    )

    courier: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    invoice_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    tracking_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ShipmentItem(Base):
    """Shipment에 포함된 주문 품목 라인 — 전량(quantity 전부) 포함."""

    __tablename__ = "shipment_items"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (shipments.id)
    shipment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (order_items.id)
    order_item_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (inventory_reservations.id) — consume() 대상.
    reservation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class ShipmentStatusEvent(Base):
    """append-only 배송 상태 이력(요구사항 5)."""

    __tablename__ = "shipment_status_events"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (shipments.id)
    shipment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    new_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    triggered_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "Shipment",
    "ShipmentItem",
    "ShipmentStatusEvent",
]
