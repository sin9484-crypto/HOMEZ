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


class OrderAutoCollectionState(Base):
    """2026-09-16 개인 베타 잔여 작업(Phase 6, HOMEZ_USER_OPERATION_
    SETTINGS.md 2-8) — 회사 단위 자동 주문 감지 스케줄러 설정 및
    실행 이력. "사용 여부" 자체는 별도 플래그를 두지 않는다 —
    `app.domains.automation_safety`의 `FunctionCode.ORDER_COLLECTION`
    함수모드가 AUTOMATIC일 때만 스케줄러가 이 회사를 시도한다(기존
    기능별 자동화 개념을 재사용, 중복 플래그를 만들지 않는다). 이
    행은 "언제·얼마나 자주 시도했는지/마지막으로 언제 성공했는지/
    연속 몇 번 실패했는지"만 기록한다.

    `OrderMultiChannelCollectionService.run_all()`이 회사 단위로 그
    회사의 모든 판매채널 연결을 한 번에 순회하므로, 이 스케줄러의
    실행 단위도 회사 단위다(연결별 독립 주기는 기존 서비스를 다시
    구현해야 해서 만들지 않았다 — "기존 서비스 재사용" 원칙)."""

    __tablename__ = "order_auto_collection_states"
    __table_args__ = (
        UniqueConstraint(
            "company_id", name="uq_order_auto_collection_state_company",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_skip_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    consecutive_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 2026-09-16 개인 베타 잔여 작업(Phase 7, 2-8 운영 화면) — 마지막
    # 실행에서 나온 신규/중복/미연결/실패 주문 수(운영 화면의
    # "신규·중복·미연결·실패 주문수" 표시용). 실행하지 않았으면
    # 전부 NULL — 0과 "아직 실행 안 됨"을 구분한다.
    last_new_fulfillment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_duplicate_fulfillment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_unresolved_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_failed_order_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )


class OrderCollectionTestBudgetUsage(Base):
    """2026-09-18 Phase 7B 실제 테스트 주문 검증 — 시험 전용 호출
    예산(연결 1개·ACCEPT·페이지 1장 상한, `auto_collection_scheduler.
    run_test_budget_collection()`)의 누적 사용량. `max_pages=1`은
    "한 번의 실행이 몇 페이지까지 받아오는지"만 제한하고, 이 테이블은
    "총 몇 번 실제로 조회를 시도했는지"를 별도로 센다 — 두 제한은
    독립적이며 서로를 대신하지 않는다. 반복 클릭·동시 요청·프로세스
    재시작에도 살아남아야 하므로 프로세스 메모리가 아니라 DB에
    영구 기록한다. 실제 `provider.collect()` 호출 직전에만 1 증가한다
    (EmergencyStop·Migration 제한 모드·자격증명 오류 등으로 실제 HTTP
    요청이 나가기 전에 막힌 시도는 세지 않는다 — 실제로 전송된
    시도만 예산을 소모한다)."""

    __tablename__ = "order_collection_test_budget_usages"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "store_connection_id", "channel_status",
            name="uq_order_collection_test_budget_usage_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    store_connection_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel_status: Mapped[str] = mapped_column(String(30), nullable=False)
    get_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )


__all__ = [
    "OrderChannelFulfillment", "OrderCollectionCursor", "OrderSkuResolution",
    "UnresolvedOrderItem", "OrderAutoCollectionState",
    "OrderCollectionTestBudgetUsage",
]
