"""
=========================================================
Homez OS

File : app/domains/refund/model.py

2026-09-10 Phase 8 — Refund(현재상태) + RefundStatusEvent(append-only
이력), `app/domains/return_order/model.py`와 동일한 설계 패턴
(ReturnOrder + ReturnOrderStatusEvent)을 그대로 따른다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base
from app.domains.refund.constants import RefundStatus


class Refund(Base):

    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "idempotency_key",
            name="uq_refunds_company_idempotency",
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

    # 논리 참조 (return_orders.id) — 반품 없이 발생하는 환불(예: 배송
    # 전 취소)도 있을 수 있어 nullable.
    return_order_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # RefundType.CUSTOMER_REFUND / SUPPLIER_RECLAIM
    refund_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RefundStatus.AWAITING_APPROVAL,
        index=True,
    )

    amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="KRW",
    )

    reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    # 논리 참조 (users.id)
    requested_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 논리 참조 (users.id)
    approved_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
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

    executed_at: Mapped[datetime | None] = mapped_column(
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


class RefundStatusEvent(Base):
    """append-only 환불 상태 이력."""

    __tablename__ = "refund_status_events"

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

    # 논리 참조 (refunds.id)
    refund_id: Mapped[int] = mapped_column(
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

    # 논리 참조 (users.id); 시스템 자동 처리는 None.
    triggered_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


__all__ = ["Refund", "RefundStatusEvent"]
