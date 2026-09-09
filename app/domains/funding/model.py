"""
=========================================================
Homez OS

File : app/domains/funding/model.py

Funding Account — 사업 운영자금 관리
(금융/은행 연동 아님)

2026-08-15 V7 Gate 2 — Funding 회사 스코프 강화(CTO 지시 원문
요구사항 3):

  - FundingAccount.company_id: nullable+unique였던 것을 NOT NULL로
    바꾼다. "기본 계정 선택은 반드시 company_id를 요구해야 한다"는
    요청 원문을 스키마 레벨에서도 강제한다 — 서비스 레벨에서만 막고
    Model이 계속 nullable을 허용하면, 향후 어떤 경로가 실수로 None을
    넣어도 DB가 막아주지 못한다. 실제 homez.db는 이 테이블이 0행이라
    backfill 없이 안전하게 적용 가능하다(Gate 2 리허설로 확인).
  - FundingHold/SupplierPayment: company_id를 신규 추가한다(계정
    생성 시점에 FundingAccount.company_id에서 비정규화 — 이 두
    테이블 모두 반드시 account_id를 갖고 있고 그 계정은 이제 항상
    회사가 있으므로 항상 채울 수 있다). idempotency_key UNIQUE를
    (company_id, idempotency_key) 복합으로 바꾼다 — settlement/
    coupang/decision과 동일한 패턴(Gate R13). 실제 코드 감사 결과:
    이 두 테이블의 idempotency_key는 order_id/purchase_id(각각 단일
    전역 자동증가 PK)로부터 결정론적으로 파생되어("order:{id}:...",
    "purchase:{id}:...") 현재 스키마에서는 회사 간 충돌이 실제로
    발생하지 않는다(각 값이 이미 전역적으로 유일) — 그러나 이는
    "우연히 안전한" 상태이지 스키마가 보장하는 것이 아니다. 이번
    변경은 이 우연에 의존하지 않고 다른 회사 스코프 테이블들과 동일한
    방어 수준으로 맞춘다.
  - FundingLedger: company_id를 신규 추가한다(account_id에서
    비정규화, append-only 감사 로그의 회사별 조회를 위함). UNIQUE
    제약은 이 파일 상단의 부분 유일 인덱스(uq_funding_ledger_
    settlement_type)뿐이며, 그 reference_id(settlement_id)는 이미
    MarketplaceSettlement.company_id로 유일하게 귀속되므로 이번
    범위에서는 인덱스 자체를 바꾸지 않는다(불필요한 변경 회피).
  - Order/Purchase 테이블은 실제 DB에 존재하지 않고(V7 Gate 3/4
    범위) company_id 설계가 아직 없다 — 이 두 테이블이 생기면 각각
    company_id 컬럼을 직접 가져야 하고(현재 FundingHold/
    SupplierPayment처럼 계정에서 비정규화하는 방식이 아니라, 주문/
    발주 자체가 회사 소속이므로 직접 컬럼이 맞다), FundingService.
    ensure_supply_hold()/confirm_supplier_payment()의 company_id
    선택적 매개변수를 필수로 승격해야 한다 — 이 설계 원칙은 문서로만
    남기고 이번 Gate 2에서 Order/Purchase 자체를 구현하지 않는다.
=========================================================
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import text

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class FundingAccount(Base):
    """사업 운영자금 계정."""

    __tablename__ = "funding_accounts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (companies.id) — 금융 API 없음. 2026-08-15 V7 Gate 2:
    # nullable이었던 것을 NOT NULL로 전환(요구사항 3, 스키마 레벨
    # 강제) — 회사 없는 Funding Account를 만들 수 있는 경로 자체를
    # 없앤다.
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        unique=True,
        index=True,
    )

    # 사업자가 설정한 사업 운영자금 총액
    total_funding: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    # active Hold 합계 캐시
    held_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="KRW",
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


class FundingLedger(Base):
    """사업 운영자금 변경 이력 (append-only)."""

    __tablename__ = "funding_ledgers"
    __table_args__ = (
        # Marketplace Settlement 전용 부분 유일 인덱스.
        # reference_type='settlement'인 행에만 적용되어
        # HOLD_CREATE/HOLD_RELEASE 등 다른 Ledger 타입은 영향받지 않는다.
        Index(
            "uq_funding_ledger_settlement_type",
            "reference_id",
            "type",
            unique=True,
            sqlite_where=text("reference_type = 'settlement'"),
            postgresql_where=text("reference_type = 'settlement'"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (companies.id) — account_id에서 비정규화(2026-08-15
    # V7 Gate 2, 회사별 조회용).
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # FUNDING_CREATE / FUNDING_ADD / FUNDING_REMOVE
    # HOLD_CREATE / HOLD_RELEASE / HOLD_COMMIT / FUNDING_PAYOUT
    type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    reference_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    reference_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    memo: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class FundingHold(Base):
    """주문별 공급 비용 예약."""

    __tablename__ = "funding_holds"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_funding_holds_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (companies.id) — account_id에서 비정규화(2026-08-15
    # V7 Gate 2, 요구사항 3 — idempotency_key 복합 UNIQUE 범위 확장).
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    account_id: Mapped[int] = mapped_column(
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

    # 논리 참조 (purchases.id)
    purchase_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # HELD / COMMITTED / RELEASED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="HELD",
        index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
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


class SupplierPayment(Base):
    """
    공급처 지급 확정 기록.

    은행 송금 API가 아닌, 관리자 수동 확정 MVP.
    고객 Payment Domain과 분리한다.
    """

    __tablename__ = "supplier_payments"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_supplier_payments_idempotency",
        ),
        UniqueConstraint(
            "purchase_id",
            name="uq_supplier_payments_purchase",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (companies.id) — account_id에서 비정규화(2026-08-15
    # V7 Gate 2, 요구사항 3). purchase_id UNIQUE는 그대로 전역으로
    # 둔다 — 하나의 Purchase는 회사와 무관하게 지급이 정확히 1건이어야
    # 하는 업무 불변식이라 company_id로 나눌 이유가 없다(idempotency_
    # key 복합 UNIQUE와는 별개 목적의 제약).
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    purchase_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    order_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    supplier_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    hold_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # FundingHold.amount와 동일해야 함
    amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # PAID (MVP)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PAID",
        index=True,
    )

    paid_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    memo: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "FundingAccount",
    "FundingLedger",
    "FundingHold",
    "SupplierPayment",
]
