"""
=========================================================
Homez OS

File : app/domains/return_order/model.py

ReturnOrder Model — V7 Gate 4(2026-08-15) 신규.

기존 `app/domains/return_order/*`는 완전히 빈 스캐폴딩(0 loc)이었다
(Gate R Phase C 전수 재분류에서 확인된 69개 완전 빈 도메인 중 하나).
이번 Gate에서 처음으로 채운다 — 요구사항 5(반품접수/반품완료/교환접수/
교환완료)를 담당한다.

설계: FundingLedger/InventoryLedgerEvent와 동일한 append-only 상태
이력 패턴(ReturnOrderStatusEvent) + 현재상태(ReturnOrder). 반품/교환
1건은 정확히 하나의 Shipment × OrderItem 조합에 대해서만 만들 수
있고, 수량은 그 품목이 실제로 출고된 수량과 정확히 같아야 한다
(Gate 3 Inventory 예약이 all-or-nothing consume만 지원하는 구조적
한계 — Shipment/model.py 상단 주석과 동일한 근거로 부분 반품/교환은
이번 Gate 범위 밖).
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
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.constants import ReturnOrderType


class ReturnOrder(Base):

    __tablename__ = "return_orders"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_return_orders_company_idempotency",
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

    # 논리 참조 (order_items.id)
    order_item_id: Mapped[int] = mapped_column(
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

    # RETURN / EXCHANGE
    return_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReturnOrderStatus.REQUESTED,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
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


class ReturnOrderStatusEvent(Base):
    """append-only 반품/교환 상태 이력."""

    __tablename__ = "return_order_status_events"

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

    # 논리 참조 (return_orders.id)
    return_order_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    new_status: Mapped[str] = mapped_column(
        String(20),
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
    "ReturnOrder",
    "ReturnOrderStatusEvent",
]
