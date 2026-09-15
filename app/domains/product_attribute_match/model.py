"""
=========================================================
Homez OS

File : app/domains/product_attribute_match/model.py

2026-09-15 전면 감사 후속(Phase 9G, HOMEZ_USER_OPERATION_SETTINGS.md
10-4 — "상품 속성(이름/옵션/수량/사이즈/제조사/원산지)이 매입처·
후보(candidate)·판매채널 세 데이터 소스 사이에서 일치하는지 정규화해
비교하고, 필수 항목이 불일치하거나 확인 불가면 자동 등록/자동 발주를
막는다").

**"확인 불가(UNCONFIRMED)"와 "실제 불일치(MISMATCHED)"는 서로 다른
상태다** — 두 개 이상의 소스에서 값을 구할 수 없으면 UNCONFIRMED,
두 개 이상의 값이 있는데 서로 다르면 MISMATCHED다(둘 다 자동 진행을
막지만, 원인이 다르므로 섞어서 기록하지 않는다). 문자열 유사도만으로
"같은 상품"이라고 스스로 판단하지 않는다 — 정규화 후 완전히 같을
때만 MATCHED다(app/domains/product_attribute_match/service.py의
_normalize()/_compare_field() 참고).

ProductAttributeComparisonRun: 비교 1회 실행의 "헤더" 행(현재 상태 —
같은 product_identifier에 대해 재비교하면 새 행이 추가된다, 과거
비교 이력은 그대로 남는다). 해소 사유는 resolution_note에 남긴다.
ProductAttributeComparisonItem: 그 실행에 포함된 항목(필드)별 상세 —
매입처/판매채널/HOMEZ 현재 값과 각각의 출처·확인시각, 판정 결과,
그리고(불일치/미확인 항목에 한해) 사람이 직접 고른 선택값(Phase 9H
UI가 채운다 — 임의 기본값 선택 없음).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from app.database.base import Base


class ProductAttributeComparisonRun(Base):
    """상품 속성 비교 1회 실행 — 헤더 행."""

    __tablename__ = "product_attribute_comparison_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # candidate_key 또는 매입처 product_code 등, 이 비교가 어떤 상품에
    # 대한 것인지 식별하는 문자열(도메인 경계를 넘나드는 값이라 FK로
    # 강제하지 않는다 — candidate_key 뿐 아니라 판매채널측 식별자가
    # 먼저 확정된 경우도 있을 수 있어 문자열 식별자로 둔다).
    product_identifier: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True,
    )

    connection_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    # PASSED: 필수 항목이 전부 MATCHED. BLOCKED: 하나 이상 MISMATCHED
    # 또는 UNCONFIRMED — 자동 등록/자동 발주를 막는다.
    overall_status: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
    )

    triggered_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    # BLOCKED 상태를 해소하려면 사람이 각 불일치/미확인 항목의
    # selected_value를 채운 뒤 이 run을 resolve해야 한다(자동 해소
    # 없음 — 재비교로 우연히 일치해도 스스로 바뀌지 않는다).
    resolved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )

    items: Mapped[list["ProductAttributeComparisonItem"]] = relationship(
        "ProductAttributeComparisonItem", back_populates="run",
        cascade="all, delete-orphan",
    )


class ProductAttributeComparisonItem(Base):
    """비교 실행 1건 안의 필드 하나에 대한 상세 — 세 소스의 값·출처·
    확인시각과 판정 결과를 담는다."""

    __tablename__ = "product_attribute_comparison_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("product_attribute_comparison_runs.id"),
        nullable=False, index=True,
    )

    field_name: Mapped[str] = mapped_column(String(30), nullable=False)

    supplier_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    supplier_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supplier_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    sales_channel_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sales_channel_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sales_channel_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    homez_current_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    homez_current_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    homez_current_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    match_status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Phase 9H UI가 채운다 — 세 값 중 무엇이 맞는지(혹은 넷째 값)
    # 사람이 직접 고른다. 임의 기본값을 서버가 미리 채워 넣지
    # 않는다. 선택 사유는 항목별이 아니라 run 전체 기준으로
    # ProductAttributeComparisonRun.resolution_note에 남긴다.
    selected_value: Mapped[str | None] = mapped_column(String(300), nullable=True)

    run: Mapped["ProductAttributeComparisonRun"] = relationship(
        "ProductAttributeComparisonRun", back_populates="items",
    )


__all__ = [
    "ProductAttributeComparisonRun",
    "ProductAttributeComparisonItem",
]
