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

from sqlalchemy import DateTime
from sqlalchemy import Integer

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


__all__ = ["VirtualStockThreshold", "PriceReviewCycleSetting"]
