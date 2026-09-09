"""
=========================================================
Homez OS

File : app/domains/pricing/model.py

Pricing & Margin Reconciliation — V7 Gate 5(2026-08-15).

기존 `app/domains/settlement`(V2.3 Marketplace Settlement Hardening,
homez.db에 이미 적용됨)과 `app/domains/marketplace_listing`(Gate I
Decimal 마진 계산기, Gate U-1 economics_view 권한 패턴)을 상품·마진·
정산 일치성 관점에서 연결·확장한다. 정산 자체를 다시 만들지 않는다.

설계(요구사항 1~7, CTO 지시 원문):
  - `ProductPricing` — Listing(marketplace_listings.id) 단위 현재
    판매가 + 원가 구성(원가/배송비/채널수수료/결제수수료/광고비/
    반품충당/세금, 요구사항 1) 현재상태. 판매가는 오직 승인된
    `PriceChangeRequest`를 통해서만 바뀐다(요구사항 3).
  - `PriceChangeRequest`(+ append-only `PriceChangeStatusEvent`) —
    가격 변경 승인 흐름 + 이력(요구사항 3). listing_wizard의 승인
    fingerprint, funding Hold 상태 머신과 동일한 원칙 — 조건부
    UPDATE(rowcount 검증)로만 상태가 전이하고, 결정된 행은 불변이다.
  - `MarginSnapshot`(append-only) — 채널별 "예상"(등록/가격변경
    시점) vs "실제"(정산 반영 후) 마진을 margin_type으로 구분해 추적
    한다(요구사항 2). 실측이 아닌 추정 성분은
    estimated_components_json으로 투명하게 표시한다.
  - `SettlementReconciliation` — Order × Settlement 대사 현재상태
    (요구사항 5). 정산 예정/완료/불일치/보류는 이 테이블과 기존
    `MarketplaceSettlement.status`(HELD/MISMATCH 추가, app/domains/
    settlement/model.py 참고) 두 계층으로 함께 표현한다(요구사항 4).
    불일치는 항상 플래그만 하고 자동 보정하지 않는다(CLAUDE.md 최상위
    규칙).

금액은 marketplace_listing.model.MONEY(Numeric(14,2,asdecimal=True))와
동일한 컨벤션을 그대로 재사용해 전부 Decimal로 다룬다. 모든
cross-domain 참조는 이 저장소 전역 관례대로 실제 ForeignKey가 아닌
논리 참조(정수 컬럼 + 주석)다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base

# app/domains/marketplace_listing/model.py::MONEY와 동일 컨벤션.
MONEY = Numeric(14, 2, asdecimal=True)
RATE = Numeric(6, 4, asdecimal=True)


class ProductPricing(Base):
    """
    Listing(marketplace_listings.id) 단위 현재 가격/원가 구성 — 한
    Listing에는 이 행이 정확히 1개만 존재한다. current_sale_price는
    approve_price_change()를 통해서만 바뀐다(공개 서비스 경로에
    직접 UPDATE하는 메서드가 없다) — 원가 계열 필드(비고객대면 내부
    정보)는 update_economics_inputs()로 승인 없이 즉시 갱신된다.
    """

    __tablename__ = "product_pricings"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "listing_id",
            name="uq_product_pricings_company_listing",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조 (marketplace_listings.id)
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    current_sale_price: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )

    # 요구사항 1 — 상품별 원가/배송비/수수료/광고비/세금.
    cost_of_goods: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    shipping_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    packaging_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    ad_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    channel_fee_rate: Mapped[object] = mapped_column(
        RATE, nullable=False, default=0,
    )
    payment_fee_rate: Mapped[object] = mapped_column(
        RATE, nullable=False, default=0,
    )
    return_reserve_rate: Mapped[object] = mapped_column(
        RATE, nullable=False, default=0,
    )
    tax_basis_rate: Mapped[object] = mapped_column(
        RATE, nullable=False, default=0,
    )

    # 가장 최근 계산된 예상 손익(margin_calculator.calculate_economics
    # 결과) 조회 편의 캐시 — Source of Truth는 append-only
    # MarginSnapshot(EXPECTED)이다.
    expected_revenue: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    expected_total_cost: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    expected_margin_amount: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )
    expected_margin_rate: Mapped[object] = mapped_column(
        RATE, nullable=False, default=0,
    )
    expected_break_even_price: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )

    # 진행 중(PENDING) 요청이 있으면 참조 — 중복 요청 방지(요구사항 3).
    # 논리 참조 (price_change_requests.id)
    pending_price_change_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # 낙관적 동시성 — 읽은 시점의 값을 그대로 WHERE 조건에 걸어
    # 조건부 UPDATE한다(클라이언트가 값을 보낼 필요 없음, 서비스가
    # 읽기→조건부쓰기 사이 경합만 차단).
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PriceChangeRequest(Base):
    """
    판매가 변경 요청(현재상태 행, 요구사항 3). PENDING에서만 다른
    상태로 전이하며(조건부 UPDATE + rowcount 검증), 결정된 행은 이후
    다시 바뀌지 않는다 — 재요청은 항상 새 행이다. 전체 변경 이력은
    이 테이블 전체(시간순 전체 요청)와 append-only
    PriceChangeStatusEvent 둘 다로 남는다.
    """

    __tablename__ = "price_change_requests"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_price_change_requests_company_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (product_pricings.id)
    product_pricing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    previous_sale_price: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    requested_sale_price: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )

    # 요청 시점 ProductPricing 상태 지문 — 승인 시점 재계산값과
    # 정확히 일치할 때만 승인이 유효하다(app/domains/pricing/
    # fingerprint.py).
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    # PriceChangeStatus: PENDING / APPROVED / REJECTED / CANCELLED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True,
    )

    requested_by: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    decided_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    decision_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class PriceChangeStatusEvent(Base):
    """
    append-only 가격 변경 이력(요구사항 3) —
    MarketplaceListingStatusEvent/OrderStatusEvent와 동일 철학. 요청
    생성·승인·거절·취소마다 새 행이 추가되며 과거 행은 절대 수정·
    삭제하지 않는다.
    """

    __tablename__ = "price_change_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (price_change_requests.id)
    price_change_request_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
    )
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)

    sale_price_snapshot: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )


class MarginSnapshot(Base):
    """
    append-only 손익 스냅샷(요구사항 1/2) — EXPECTED(등록/가격변경
    시점 예상)와 ACTUAL(정산 반영 후 실제)을 margin_type으로 구분한다.
    이 행 자체가 감사 기록이며 절대 수정·삭제하지 않는다. 괴리 조회는
    같은 (company_id, listing_id) 범위 안에서 최신 EXPECTED와
    order_id별 최신 ACTUAL을 비교해 서비스 계층이 수행한다.
    """

    __tablename__ = "margin_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # MarginType: EXPECTED / ACTUAL
    margin_type: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True,
    )

    # ACTUAL일 때만 채워짐 — 논리 참조 (orders.id / marketplace_settlements.id)
    order_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )
    settlement_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    # MarginSnapshotReason
    reason: Mapped[str] = mapped_column(String(30), nullable=False)

    quantity_basis: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    revenue: Mapped[object] = mapped_column(MONEY, nullable=False)
    cost_of_goods: Mapped[object] = mapped_column(MONEY, nullable=False)
    channel_fee: Mapped[object] = mapped_column(MONEY, nullable=False)
    payment_fee: Mapped[object] = mapped_column(MONEY, nullable=False)
    shipping_cost: Mapped[object] = mapped_column(MONEY, nullable=False)
    packaging_cost: Mapped[object] = mapped_column(MONEY, nullable=False)
    ad_cost: Mapped[object] = mapped_column(MONEY, nullable=False)
    return_reserve: Mapped[object] = mapped_column(MONEY, nullable=False)
    tax: Mapped[object] = mapped_column(MONEY, nullable=False)
    refund_adjustment: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )

    total_cost: Mapped[object] = mapped_column(MONEY, nullable=False)
    margin_amount: Mapped[object] = mapped_column(MONEY, nullable=False)
    margin_rate: Mapped[object] = mapped_column(RATE, nullable=False)

    # 실측이 아니라 마지막 예상값을 대신 사용한 성분들(요구사항 2 —
    # 정확도를 숨기지 않는다). JSON 문자열 배열
    # (app/domains/pricing/constants.py::EstimatedComponent).
    estimated_components_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )


class SettlementReconciliation(Base):
    """
    Order × Settlement 대사 현재상태(요구사항 4/5) — 한 Order당 정확히
    1개 행(재계산은 이 행을 조건부 UPDATE). 실제 반올림/불일치는 자동
    보정하지 않고 MISMATCH로 플래그만 한다(CLAUDE.md 최상위 규칙).
    """

    __tablename__ = "settlement_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "order_id",
            name="uq_settlement_reconciliations_company_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (orders.id)
    order_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    # 논리 참조 (marketplace_settlements.id) — 아직 정산이 들어오지
    # 않았으면 NULL.
    settlement_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    expected_net_amount: Mapped[object] = mapped_column(
        MONEY, nullable=False,
    )
    actual_net_amount: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )
    variance_amount: Mapped[object | None] = mapped_column(
        MONEY, nullable=True,
    )

    refund_amount: Mapped[object] = mapped_column(
        MONEY, nullable=False, default=0,
    )

    # ReconciliationStatus: PENDING_SETTLEMENT / MATCHED / MISMATCH / HELD
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING_SETTLEMENT",
        index=True,
    )

    notes: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )

    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "MONEY",
    "RATE",
    "ProductPricing",
    "PriceChangeRequest",
    "PriceChangeStatusEvent",
    "MarginSnapshot",
    "SettlementReconciliation",
]
