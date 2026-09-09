"""
=========================================================
Homez OS

File : app/domains/order/model.py

Order Model — V7 Gate 4(2026-08-15) 처음부터 재설계.

이전 파일(products.id FK를 참조하는 pre-pivot 구조, `app/main.py`에
router import는 있으나 `include_router()`는 주석 처리되어 실제로
마운트된 적이 없음 — 실측 확인 완료)를 완전히 대체한다.

Gate 3(Inventory)와 달리 레거시 클래스를 보존할 필요가 없다: 실측
결과 `Product`/`Supplier` 모델에 order/purchase/shipment를 향한
`relationship()` back_populates가 전혀 없고(Gate 3에서 겪은 "레거시
클래스를 지우면 mapper 연쇄 실패" 함정의 전제 자체가 없음), 실제
`homez.db`에도 orders/purchases/shipments 테이블이 존재하지 않는다
(읽기 전용 확인). CTO 지시문도 "Order/Purchase도 완전히 새로 설계해도
된다"고 명시했다.

설계(Inventory Gate 3의 검증된 패턴을 그대로 재사용):
  - `Order`(현재상태) — UNIQUE(company_id, channel_code,
    channel_order_id)로 채널 주문 중복 수집을 DB 레벨에서 차단한다
    (요구사항 2).
  - `OrderItem` — Inventory 예약(reservation_id)과 1:1 대응, 부분
    출고/반품/교환 진행 상황을 shipped_quantity/returned_quantity로
    추적한다.
  - `OrderIngestionEvent`(append-only) — raw_payload(원본 그대로)와
    normalized_snapshot(정규화 결과)을 분리 저장한다(요구사항 7).
  - `OrderStatusEvent`(append-only) — 상태 변경 이력(요구사항 5,
    MarketplaceListingStatusEvent와 동일 철학).

모든 cross-domain 참조는 이 저장소 전역 관례대로 실제 ForeignKey가
아닌 논리 참조(정수 컬럼 + 주석)다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base
from app.domains.order.constants import OrderChannelSyncStatus
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus


class Order(Base):
    """
    회사 스코프 주문 현재상태 — 채널에서 수집한 원본 주문 하나가
    정확히 하나의 Order로 정규화된다. UNIQUE(company_id, channel_code,
    channel_order_id)가 중복 수집 방지의 최종 방어선이다(요구사항 2).
    """

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "channel_code",
            "channel_order_id",
            name="uq_orders_company_channel_order",
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

    channel_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    # 채널이 부여한 외부 주문번호 — 중복 수집 방지의 핵심 키.
    channel_order_id: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    # HOMEZ 내부 표시용 주문번호("O-{id}", 생성 후 채움).
    order_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=OrderStatus.PENDING,
        index=True,
    )

    buyer_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    receiver_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    receiver_phone: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    receiver_address: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    receiver_zipcode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    total_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    ordered_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    # --------------------------------------------------
    # 채널 상태 동기화(요구사항 6) — Fake Provider 전용, Inventory의
    # InventoryChannelMapping.last_sync_status와 동일 철학.
    # --------------------------------------------------

    channel_sync_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OrderChannelSyncStatus.PENDING,
    )

    channel_last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    channel_last_sync_error: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # Gate H(marketplace_listing) Retry-After 정규화 재사용.
    rate_limit_retry_after_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    rate_limit_retry_available_at: Mapped[datetime | None] = mapped_column(
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


class OrderItem(Base):
    """
    주문 품목 — 정확히 하나의 InventorySku(옵션)에 대응한다. 예약/
    발주/출고/반품/교환 진행 상황을 이 행 하나가 끝까지 추적한다.
    """

    __tablename__ = "order_items"

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

    # 논리 참조 (inventory_skus.id) — InventoryChannelMapping을 통해
    # channel_sku로부터 해석된다.
    inventory_sku_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # 채널이 보낸 원본 SKU 문자열(감사/디버깅용, 요구사항 7과 동일 정신).
    channel_sku: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    sku_code_snapshot: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    product_name_snapshot: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    unit_price: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OrderItemStatus.PENDING,
        index=True,
    )

    # 논리 참조 (inventory_reservations.id) — RESERVED일 때만 값이 있다.
    reservation_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 논리 참조 (purchases.id) — 발주가 생성되면 채워진다.
    purchase_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 논리 참조 (purchase_tasks.id) — 2026-09-07 V7 통합 매입 감사
    # 후속(HOMEZ_V7_PROCUREMENT_LEDGER_AUDIT_20260907.md §8, 사용자
    # 확정 "purchase_task 중심"). purchase_id(위)와 별개 컬럼이다 —
    # 두 값을 하나의 컬럼에 섞으면 어느 테이블(purchases vs
    # purchase_tasks)을 참조하는지 값만 보고 알 수 없어 위험하다.
    # purchase_task/service.py::record_tracking()이 실제 발송 시점에
    # 채운다.
    purchase_task_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    shipped_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    returned_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
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


class OrderIngestionEvent(Base):
    """
    append-only 채널 주문 수집 이력(요구사항 2/6/7) — raw_payload(채널
    원본 그대로)와 normalized_snapshot(HOMEZ 내부 정규화 결과)을 분리
    저장한다. 과거 행은 절대 UPDATE/DELETE하지 않는다(이 저장소 전역
    append-only 관례).
    """

    __tablename__ = "order_ingestion_events"

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

    channel_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    channel_order_id: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        index=True,
    )

    # COLLECTED / DUPLICATE_IGNORED / FAILED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    # 논리 참조 (orders.id) — COLLECTED/DUPLICATE_IGNORED일 때만 값이 있다.
    order_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 채널이 보낸 원본 JSON 그대로(요구사항 7 — 손실 없이 보존).
    raw_payload: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # HOMEZ 내부 정규화 결과 JSON(요구사항 7 — 원본과 분리 저장).
    normalized_snapshot: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    error_summary: Mapped[str | None] = mapped_column(
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


class OrderStatusEvent(Base):
    """
    append-only 주문 상태 이력(요구사항 5) —
    MarketplaceListingStatusEvent와 동일 철학.
    """

    __tablename__ = "order_status_events"

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

    previous_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    new_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # INGESTION / RESERVATION / CANCELLATION / PURCHASE / SHIPMENT /
    # RETURN / EXCHANGE / CHANNEL_SYNC
    source: Mapped[str] = mapped_column(
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
    "Order",
    "OrderItem",
    "OrderIngestionEvent",
    "OrderStatusEvent",
]
