"""
=========================================================
Homez OS

File : app/domains/source/model.py

Sourcing(공급처 검색·상품 연결) Domain — V7 Section F(2026-08-20).

`SupplierProductLink` = 회사(company_id)가 특정 상품 후보
(product_candidate_id)를 전역 공급처 카탈로그(app.domains.supplier.
model.Supplier, 논리 참조 — FK 없음, 이 코드베이스 전체 컨벤션)의
특정 SKU와 연결한 회사별 소싱 정보 1건. Supplier 자체는 이번 CTO
결정(2026-08-20)에 따라 전역 공유 카탈로그로 유지하고 새 테이블을
만들지 않는다 — 공급가·MOQ·리드타임처럼 회사마다 다를 수 있는
정보만 이 테이블에 새로 둔다.

app/domains/purchase/model.py::Purchase.supplier_id는 이미 이
Supplier.id를 논리 참조한다 — 이 도메인은 그 값을 "찾아 연결"하는
상류(upstream) 단계만 채운다(PurchaseService 자체는 수정하지 않는다).
=========================================================
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class SupplierProductLink(Base):

    __tablename__ = "supplier_product_links"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "product_candidate_id", "supplier_id", "supplier_sku",
            name="uq_supplier_product_links_company_candidate_supplier_sku",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 논리 참조(product_candidates.id)
    product_candidate_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )

    # 논리 참조(suppliers.id — 전역 카탈로그)
    supplier_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    supplier_sku: Mapped[str] = mapped_column(String(150), nullable=False)

    # 부가세 포함 공급가(개당) — order_items.unit_price와 동일하게
    # FLOAT를 쓴다(이 코드베이스의 기존 금액 컬럼 관례를 그대로 따름).
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False)

    moq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    shipping_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    price_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    stock_available: Mapped[int | None] = mapped_column(Integer, nullable=True)

    return_policy: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ACTIVE / INACTIVE — INACTIVE는 소프트 비활성(삭제 없음).
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ACTIVE", index=True,
    )

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CompanySupplierRelation(Base):
    """
    회사별 공급처 거래정보 — 2026-08-20 CTO 정정: Supplier(전역
    테이블)는 상호·공개 사업자정보·활성/검증여부 같은 "공개 식별
    정보"만 전역 공유로 유지하고, 계약상태·담당자·비공개 연락처·
    결제조건·Credential 참조·메모·승인상태처럼 회사마다 달라야 하는
    정보는 이 테이블에 company_id로 격리해 보관한다. Credential
    원문은 저장하지 않는다(credential_reference는 다른 저장소를
    가리키는 불투명 문자열 포인터일 뿐).
    """

    __tablename__ = "company_supplier_relations"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "supplier_id",
            name="uq_company_supplier_relations_company_supplier",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # 논리 참조(suppliers.id — 전역 카탈로그)
    supplier_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # PENDING / APPROVED / REJECTED
    approval_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True,
    )

    contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    payment_terms: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 실제 자격증명 원문이 아니라, 다른 저장소(예: Credential Manager)를
    # 가리키는 불투명 참조 문자열만 저장한다 — 이 코드베이스 전체에서
    # Credential 원문을 DB에 저장하지 않는다는 원칙을 그대로 따른다.
    credential_reference: Mapped[str | None] = mapped_column(
        String(300), nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "SupplierProductLink",
    "CompanySupplierRelation",
]
