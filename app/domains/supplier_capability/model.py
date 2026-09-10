"""
=========================================================
Homez OS

File : app/domains/supplier_capability/model.py

2026-09-10 Phase 9 — SupplierProfile(공급처 1건당 1행 — 국내/해외
구분, 기본 통화, 위탁배송 기본 흐름 여부) + SupplierCapabilityRecord
(공급처×능력 플래그별 현재 상태, upsert — 이력이 중요한 도메인이
아니라 "지금 아는 것"만 필요해 append-only로 만들지 않았다).
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base
from app.domains.supplier_capability.constants import CapabilitySupport


class SupplierProfile(Base):
    """
    논리 참조(suppliers.id) 1건당 정확히 1행. "매입처 직접배송을
    기본 흐름으로 유지한다"(문서 7번)를
    `consignment_direct_to_customer`의 기본값 True로 구조화한다 —
    HOMEZ가 직접 창고를 운영하는 예외적인 공급처만 명시적으로
    False로 표시해야 한다.
    """

    __tablename__ = "supplier_profiles"
    __table_args__ = (
        UniqueConstraint("supplier_id", name="uq_supplier_profiles_supplier_id"),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (suppliers.id)
    supplier_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    is_international: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # ISO 3166-1 alpha-2. 미확인이면 None(추측하지 않는다).
    country_code: Mapped[str | None] = mapped_column(
        String(2),
        nullable=True,
    )

    default_currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="KRW",
    )

    consignment_direct_to_customer: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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


class SupplierCapabilityRecord(Base):
    """(supplier_id, capability) 조합별 최신 판정 1행 — upsert
    (set_capability()가 있으면 UPDATE, 없으면 INSERT)."""

    __tablename__ = "supplier_capability_records"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "capability",
            name="uq_supplier_capability_records_supplier_capability",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (suppliers.id)
    supplier_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # app.domains.supplier_capability.constants.SupplierCapabilityFlag 중 하나.
    capability: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # app.domains.supplier_capability.constants.CapabilitySupport 중 하나.
    # 기본값 UNKNOWN — 기록된 적 없는 조합은 코드 레벨에서 UNKNOWN
    # 취급한다(이 컬럼의 default는 신규 행 삽입 시 참고용).
    support: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CapabilitySupport.UNKNOWN,
    )

    note: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # 논리 참조 (users.id); 시스템 판정 등은 None.
    checked_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    checked_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = ["SupplierProfile", "SupplierCapabilityRecord"]
