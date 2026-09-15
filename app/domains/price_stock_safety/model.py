"""
=========================================================
Homez OS

File : app/domains/price_stock_safety/model.py

2026-09-10 Phase 10(HOMEZ_USER_OPERATION_SETTINGS.md 2·7번 — "가격은
주 1회 정기 검토한다", "판매채널 가상재고 기준 이하 시 신규판매
중지") — 둘 다 회사별 append-only 설정(function_automation_states/
payment_auto_limits와 동일한 패턴, 최신 행이 현재 값)이다.

**"가상재고"(판매채널에 실제로 표시하는, 의도적으로 낮춘 재고
수치)와 `app/domains/inventory/model.py::InventorySku`(실물 창고
재고)는 완전히 다른 개념이다 — 이 Model은 InventorySku를 전혀
참조하지 않는다(High 결함 #15가 이미 지적한 개념 혼동을 반복하지
않는다).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class VirtualStockThreshold(Base):
    """
    회사별 가상재고 임계값(수량). 이 값 이하로 표시되면 신규
    자동판매/자동발주를 금지한다는 게이트 판정에 쓰인다
    (`app/domains/price_stock_safety/service.py::
    PriceStockSafetyService.check_virtual_stock_allows_auto_order()`).

    현재는 회사 전체에 적용되는 단일 임계값만 지원한다 — 상품별
    개별 임계값은 이번 Phase 범위 밖(모듈 docstring/서비스 주석에
    이유 기록).
    """

    __tablename__ = "virtual_stock_thresholds"

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

    threshold_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 논리 참조 (users.id)
    set_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    set_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class PriceReviewCycleSetting(Base):
    """
    회사별 가격 정기 검토 주기(일 단위). 문서 원문 기본값은 7일
    (주 1회) — 값이 없으면(행이 하나도 없으면)
    `app/domains/price_stock_safety/constants.py::
    DEFAULT_PRICE_REVIEW_CYCLE_DAYS`를 쓴다.
    """

    __tablename__ = "price_review_cycle_settings"

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

    review_cycle_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # 논리 참조 (users.id)
    set_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    set_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )


class PriceCacheTtlSetting(Base):
    """2026-09-15 Phase 9D(HOMEZ_USER_OPERATION_SETTINGS.md 8-5 —
    "가격정보 기본 유효시간은 30분으로 하며 사용자가 설정 화면에서
    변경할 수 있게 한다"). 재고 TTL(StockCacheTtlSetting)과 의도적
    으로 분리한다 — 두 값이 함께 바뀌어야 할 이유가 없고, 문서도
    두 항목을 서로 다른 번호(8-5/8-6)로 구분한다."""

    __tablename__ = "price_cache_ttl_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    ttl_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    set_by: Mapped[int] = mapped_column(Integer, nullable=False)
    set_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )


class StockCacheTtlSetting(Base):
    """2026-09-15 Phase 9E(8-6 — "재고정보 기본 유효시간은 10분으로
    하며 사용자가 설정 화면에서 변경할 수 있게 한다")."""

    __tablename__ = "stock_cache_ttl_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    ttl_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    set_by: Mapped[int] = mapped_column(Integer, nullable=False)
    set_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )


class PriceStockQuoteCache(Base):
    """2026-09-15 Phase 9A/9D/9E(7-8/8-5/8-6) — 매입처 실제 조회
    결과(가격·재고)를 회사·연결계정·상품·옵션 단위로 격리해
    캐시한다(append-only가 아니라 "현재 상태" 1행 — 같은 키로 다시
    조회하면 그 행을 갱신한다).

    가격과 재고는 TTL이 다르므로(30분/10분) 각자 독립적인
    confirmed_at/expires_at을 갖는다 — 한쪽만 만료되고 다른 쪽은
    아직 유효한 상태가 정상적으로 존재할 수 있다.

    **이 캐시에 절대 쓰지 않는 값**: 오류·인증실패·응답 형식 불명
    (RESULT_UNKNOWN류) 조회 결과. "확인됨"과 "확인 안 됨"을 섞으면
    캐시가 거짓 정보를 제공하게 된다 — 서비스 계층이 SUPPORTED
    (CapabilitySupport)인 응답만 이 테이블에 쓴다.

    **이 캐시를 절대 읽지 않는 호출부**: 발주 직전 최종 조회
    (order_submission_service.py::_verify_point_balance_and_
    shipping_or_block)는 이 캐시를 거치지 않고 항상
    adapter.lookup_product()를 직접 호출한다(가격 인상을 놓치지
    않기 위함, 기존 "캐시·이전 조회값 재사용 금지" 원칙 그대로
    유지)."""

    __tablename__ = "price_stock_quote_caches"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "connection_id", "product_code", "option_id",
            name="uq_price_stock_quote_cache_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    product_code: Mapped[str] = mapped_column(String(100), nullable=False)
    option_id: Mapped[str] = mapped_column(String(100), nullable=False)

    price_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    price_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True,
    )

    in_stock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stock_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    stock_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True,
    )

    # 항상 "real_api"다(이 캐시는 실제 조회 결과만 담는다) — 향후
    # 다른 출처가 생기면 그때 값을 늘린다, 지금은 추측하지 않는다.
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="real_api",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class VirtualStockZeroProposalStatus:
    """2026-09-15 Phase 9F(HOMEZ_USER_OPERATION_SETTINGS.md 8-19 —
    "매입처의 판매 가능 여부를 확인할 수 없으면 가상재고를 0으로
    바꾸고 신규 판매를 중지한다"). "0으로 바꾼다"를 외부 판매채널에
    자동으로 전송하지 않는다 — 변경안(제안)만 만들고, 사람이
    승인해야만(승인 자체도 이 시스템 범위 밖 — 실제 판매채널 반영은
    별도 작업) "처리됨"으로 넘어간다."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    ALL = (PENDING, APPROVED, REJECTED)


class VirtualStockZeroProposal(Base):
    """공급처 판매 가능 여부를 확인할 수 없을 때 생성되는 "가상재고
    0 제안" — 현재 상태 1행(PENDING→APPROVED/REJECTED). 승인/거부는
    항상 사람이 한다(자동 복구 없음 — 재고 확인이 나중에 성공해도
    이 행은 스스로 바뀌지 않는다)."""

    __tablename__ = "virtual_stock_zero_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    product_code: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False,
        default=VirtualStockZeroProposalStatus.PENDING, index=True,
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    resolved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )


__all__ = [
    "VirtualStockThreshold",
    "PriceReviewCycleSetting",
    "PriceCacheTtlSetting",
    "StockCacheTtlSetting",
    "PriceStockQuoteCache",
    "VirtualStockZeroProposalStatus",
    "VirtualStockZeroProposal",
]
