"""
=========================================================
Homez OS

File : app/domains/purchase/model.py

Purchase Model — V7 Gate 4(2026-08-15) 처음부터 재설계.

이전 파일(orders.id/suppliers.id를 실제 ForeignKey로 참조하는
pre-pivot 구조, main.py에 router import만 있고 마운트는 안 됨)를
완전히 대체한다 — order/model.py 상단 주석과 동일한 근거(레거시
relationship() back_populates 없음, 실제 homez.db에 purchases 테이블
자체가 없음, CTO 지시문이 명시적으로 재설계를 허용).

설계(요구사항 4 — "무재고 중개" 발주/입고):
  - `Purchase` = "이 주문에 대해 특정 공급처(supplier_id)에 넣은 발주
    1건"(현재상태). 하나의 주문은 여러 Purchase를 가질 수 있지만(품목별
    공급처가 다를 수 있음), **하나의 Purchase는 정확히 하나의
    FundingHold와 1:1로 대응**한다 — `app/domains/funding/service.py::
    ensure_supply_hold()`가 order_id 단위로 Hold 금액을 고정하는 기존
    설계(Gate 2, 변경 범위 밖)와 충돌하지 않도록, 한 Purchase에 속한
    모든 PurchaseItem의 비용 합계를 한 번에 Hold한다.
  - `PurchaseItem` = Purchase에 포함된 개별 OrderItem 라인(수량/단가).
    입고 확정 시 PurchaseItem 단위로 Inventory.restock()을 호출한다
    (SKU 단위 연산이므로).
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
from app.domains.purchase.constants import PurchaseStatus


class Purchase(Base):

    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_purchases_company_idempotency",
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

    # 논리 참조 (orders.id) — app/domains/funding/service.py::
    # confirm_supplier_payment()가 purchase.order_id를 그대로
    # 읽으므로 필드명을 유지한다(무변경 호환).
    order_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 논리 참조 (suppliers.id) — Supplier 도메인은 아직 회사 스코프가
    # 없다(Gate 4 범위 밖, 전역 공급처로 취급).
    supplier_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PurchaseStatus.REQUESTED,
        index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    total_cost: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    supplier_order_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    memo: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
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

    # --------------------------------------------------
    # 공급처 발주 전송(SupplierOrderProvider 연결, 2026-08-20 추가)
    # --------------------------------------------------
    # 기존 status(REQUESTED/CONFIRMED/RECEIVED/CANCELLED)와
    # ALLOWED_TRANSITIONS는 전혀 건드리지 않는다 — CONFIRMED는 여전히
    # "사용자 승인" 그 자체다. 아래 컬럼들은 그 승인 이후 실제
    # Provider 전송 결과만 별도로 additive하게 기록한다(전부
    # nullable, 기존 행에는 전부 NULL).

    # PENDING / SUBMITTED / PARTIALLY_ACCEPTED / REJECTED / FAILED
    submission_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True,
    )

    submission_provider_code: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )

    supplier_order_id: Mapped[str | None] = mapped_column(
        String(150), nullable=True, index=True,
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    confirmed_price: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )

    accepted_quantities_json: Mapped[str | None] = mapped_column(
        String(2000), nullable=True,
    )

    rejected_quantities_json: Mapped[str | None] = mapped_column(
        String(2000), nullable=True,
    )

    submission_error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )

    submission_retryable: Mapped[bool | None] = mapped_column(
        nullable=True,
    )

    submission_retry_after_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    correlation_id: Mapped[str | None] = mapped_column(
        String(150), nullable=True,
    )

    # 발주 확정(CONFIRMED) 시점의 상태 지문(company_id/supplier_id/
    # items/total_cost) — 전송 직전 재확인해, 승인 이후 뭔가 달라졌으면
    # (이 코드베이스 설계상 Purchase는 생성 후 불변이라 실제로는 항상
    # 일치하지만, 방어적으로 재계산해 비교한다) 전송을 차단한다.
    approval_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )


class PurchaseItem(Base):
    """Purchase에 포함된 개별 주문 품목 라인."""

    __tablename__ = "purchase_items"

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

    # 논리 참조 (purchases.id)
    purchase_id: Mapped[int] = mapped_column(
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

    # 논리 참조 (inventory_skus.id)
    inventory_sku_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    unit_cost: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    subtotal_cost: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "Purchase",
    "PurchaseItem",
]
