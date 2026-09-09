"""
=========================================================
Homez OS

File : app/domains/settlement/model.py

Marketplace Settlement — 마켓 정산금 유입
(Payment/PG 와 분리)
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


class MarketplaceSettlement(Base):
    """
    마켓플레이스 정산 기록.

    마켓 → HOMEZ 입금 예정/확정.
    고객 Payment Domain과 분리한다.

    2026-08-14 테넌트 격리 감사(Gate R13) — company_id 컬럼 추가:
    이전에는 FundingAccount.company_id(account_id 조인)만이 회사
    경계의 Source of Truth였고, 이 테이블 자체에는 company_id가 없어
    idempotency_key UNIQUE가 전역이었다(회사 A가 쓴 idempotency_key를
    회사 B는 절대 쓸 수 없는 잔존 한계로 문서화돼 있었음). company_id를
    account_id로부터 비정규화해 저장하고(생성 시점에 FundingAccount.
    company_id를 그대로 복사, 이후 절대 변경하지 않음) UNIQUE를
    (company_id, idempotency_key) 복합으로 바꿔 이 한계를 해소한다.
    조회 경로(get_for_account/list_for_account 등 account_id 기준
    스코프)는 이미 안전했으므로 그대로 유지한다 — 이번 변경은 순수
    idempotency 범위 확장이다.
    """

    __tablename__ = "marketplace_settlements"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_marketplace_settlements_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # 논리 참조 (companies.id) — FundingAccount.company_id에서 생성
    # 시점에 비정규화. account_id가 Source of Truth이며, 이 컬럼은
    # idempotency UNIQUE 범위 확장 전용이다(회사 판단의 근거로 이
    # 컬럼을 새로 쓰지 않는다 — 기존 account_id 기준 스코프를 그대로
    # 유지한다).
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    market: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    market_order_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    # 논리 참조 (orders.id)
    order_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 논리 참조 (funding_accounts.id)
    account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    gross_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    fee_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0,
    )

    net_amount: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # PENDING(정산 예정) / DEPOSITED(정산 완료) / CANCELLED / REVERSED /
    # HELD(보류, 2026-08-15 V7 Gate 5 추가) / MISMATCH(불일치, 2026-08-15
    # V7 Gate 5 추가) — HELD/MISMATCH는 PENDING에서만 진입하고
    # PENDING으로만 복귀한다(app/domains/settlement/service.py::hold/
    # release_hold/flag_mismatch/resolve_mismatch). DEPOSITED/REVERSED
    # 전이 규칙(Settlement Fixed Rules, confirm_deposit/reverse)은
    # 이번 추가로 전혀 바뀌지 않았다 — HELD/MISMATCH는 입금 확인 "전"
    # 단계에서만 오가는 별도 곁가지 상태다.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        index=True,
    )

    deposited_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # 논리 참조 (funding_ledgers.id)
    funding_ledger_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(120),
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

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


__all__ = [
    "MarketplaceSettlement",
]
