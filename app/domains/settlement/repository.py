"""
=========================================================
Homez OS

File : app/domains/settlement/repository.py

Marketplace Settlement Repository
=========================================================
"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.settlement.model import MarketplaceSettlement


class SettlementRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    def get(
        self,
        settlement_id: int,
    ) -> MarketplaceSettlement | None:
        """
        회사 범위 없는 내부/테스트 전용 조회 — Service의 공개 API
        경로(get/confirm_deposit/cancel/reverse)는 이 메서드를 직접
        쓰지 않고 반드시 get_for_account()를 거친다(2026-08-14
        테넌트 격리 감사에서 발견 — 이 메서드가 회사 스코프 없이
        Service 전 경로에서 그대로 쓰여 타사 Settlement를 id만으로
        조회할 수 있었다).
        """

        return (
            self.db.query(MarketplaceSettlement)
            .filter(MarketplaceSettlement.id == settlement_id)
            .first()
        )

    def get_for_account(
        self,
        settlement_id: int,
        account_id: int,
    ) -> MarketplaceSettlement | None:
        """id 일치 + 이 회사의 Funding Account에 속한 Settlement만 반환."""

        return (
            self.db.query(MarketplaceSettlement)
            .filter(MarketplaceSettlement.id == settlement_id)
            .filter(MarketplaceSettlement.account_id == account_id)
            .first()
        )

    def get_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> MarketplaceSettlement | None:
        """
        (company_id, idempotency_key) 복합 조회 — 2026-08-14 테넌트
        격리 감사로 idempotency 범위를 회사별로 분리(UNIQUE도 동일하게
        복합). 다른 회사가 동일한 문자열의 idempotency_key를 쓰더라도
        서로 간섭하지 않는다.
        """

        return (
            self.db.query(MarketplaceSettlement)
            .filter(MarketplaceSettlement.company_id == company_id)
            .filter(
                MarketplaceSettlement.idempotency_key
                == idempotency_key
            )
            .first()
        )

    def list_for_account(
        self,
        account_id: int,
        market: str | None = None,
        status: str | None = None,
        order_id: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MarketplaceSettlement]:
        """이 회사의 Funding Account(account_id)에 속한 Settlement만 조회."""

        query = self.db.query(MarketplaceSettlement).filter(
            MarketplaceSettlement.account_id == account_id,
        )

        if market is not None:
            query = query.filter(
                MarketplaceSettlement.market == market,
            )

        if status is not None:
            query = query.filter(
                MarketplaceSettlement.status == status,
            )

        if order_id is not None:
            query = query.filter(
                MarketplaceSettlement.order_id == order_id,
            )

        return (
            query.order_by(MarketplaceSettlement.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def list(
        self,
        market: str | None = None,
        status: str | None = None,
        order_id: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MarketplaceSettlement]:
        """
        회사 범위 없는 내부/테스트 전용 조회 — get()과 동일한 이유로
        Service 공개 API 경로에서는 쓰지 않는다.
        """

        query = self.db.query(MarketplaceSettlement)

        if market is not None:
            query = query.filter(
                MarketplaceSettlement.market == market,
            )

        if status is not None:
            query = query.filter(
                MarketplaceSettlement.status == status,
            )

        if order_id is not None:
            query = query.filter(
                MarketplaceSettlement.order_id == order_id,
            )

        return (
            query.order_by(MarketplaceSettlement.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add(
        self,
        settlement: MarketplaceSettlement,
    ) -> MarketplaceSettlement:

        self.db.add(settlement)
        self.db.commit()
        self.db.refresh(settlement)

        return settlement

    def save(
        self,
        settlement: MarketplaceSettlement,
    ) -> MarketplaceSettlement:

        self.db.add(settlement)
        self.db.commit()
        self.db.refresh(settlement)

        return settlement

    def confirm_deposit_conditional(
        self,
        settlement_id: int,
        account_id: int,
        funding_ledger_id: int,
        deposited_at: datetime,
        memo: str | None = None,
    ) -> int:
        """
        id 일치 + 이 회사의 account_id 일치 + 현재 status='PENDING'일
        때만 DEPOSITED로 조건부 갱신.

        오래된 ORM 객체를 그대로 save하지 않고, WHERE 절에 현재
        상태를 조건으로 건 UPDATE 한 번으로 처리한다. 반환값은
        실제로 갱신된 행 수(rowcount)이며, 정확히 1이어야 성공이다
        (0이면 그 사이 상태가 바뀐 것 — 호출자가 실패로 처리한다).
        account_id 조건은 Service.get()이 이미 회사 범위로 확인한
        settlement.account_id를 그대로 전달받아 이중으로 강제한다
        (방어적 depth — 조회 단계 우회 시에도 UPDATE 자체가 타사
        행을 건드리지 않는다).
        """

        values = {
            "status": "DEPOSITED",
            "deposited_at": deposited_at,
            "funding_ledger_id": funding_ledger_id,
        }

        if memo:
            values["memo"] = memo

        result = self.db.execute(
            update(MarketplaceSettlement)
            .where(MarketplaceSettlement.id == settlement_id)
            .where(MarketplaceSettlement.account_id == account_id)
            .where(MarketplaceSettlement.status == "PENDING")
            .values(**values)
        )

        return result.rowcount

    def hold_conditional(
        self,
        settlement_id: int,
        account_id: int,
        memo: str | None = None,
    ) -> int:
        """
        2026-08-15 V7 Gate 5(요구사항 4) — id 일치 + 이 회사의
        account_id 일치 + 현재 status='PENDING'일 때만 HELD로 조건부
        갱신. DEPOSITED/REVERSED 전이 규칙과 완전히 분리된 곁가지
        상태 — PENDING에서만 진입한다.
        """

        values = {"status": "HELD"}

        if memo:
            values["memo"] = memo

        result = self.db.execute(
            update(MarketplaceSettlement)
            .where(MarketplaceSettlement.id == settlement_id)
            .where(MarketplaceSettlement.account_id == account_id)
            .where(MarketplaceSettlement.status == "PENDING")
            .values(**values)
        )

        return result.rowcount

    def release_hold_conditional(
        self,
        settlement_id: int,
        account_id: int,
    ) -> int:
        """HELD → PENDING 조건부 갱신(2026-08-15 V7 Gate 5)."""

        result = self.db.execute(
            update(MarketplaceSettlement)
            .where(MarketplaceSettlement.id == settlement_id)
            .where(MarketplaceSettlement.account_id == account_id)
            .where(MarketplaceSettlement.status == "HELD")
            .values(status="PENDING")
        )

        return result.rowcount

    def flag_mismatch_conditional(
        self,
        settlement_id: int,
        account_id: int,
        memo: str | None = None,
    ) -> int:
        """
        PENDING 또는 HELD → MISMATCH 조건부 갱신(2026-08-15 V7 Gate 5,
        요구사항 4). 자동 보정하지 않는다 — 이 메서드는 상태를
        플래그만 한다.
        """

        values = {"status": "MISMATCH"}

        if memo:
            values["memo"] = memo

        result = self.db.execute(
            update(MarketplaceSettlement)
            .where(MarketplaceSettlement.id == settlement_id)
            .where(MarketplaceSettlement.account_id == account_id)
            .where(MarketplaceSettlement.status.in_(["PENDING", "HELD"]))
            .values(**values)
        )

        return result.rowcount

    def resolve_mismatch_conditional(
        self,
        settlement_id: int,
        account_id: int,
        memo: str | None = None,
    ) -> int:
        """
        MISMATCH → PENDING 조건부 갱신(2026-08-15 V7 Gate 5). 금액을
        자동으로 고치지 않는다 — 관리자가 수동 조사를 마쳤다는 명시적
        승인일 뿐이다(memo에 근거를 남겨야 한다, 서비스 계층에서 필수
        강제).
        """

        values = {"status": "PENDING"}

        if memo:
            values["memo"] = memo

        result = self.db.execute(
            update(MarketplaceSettlement)
            .where(MarketplaceSettlement.id == settlement_id)
            .where(MarketplaceSettlement.account_id == account_id)
            .where(MarketplaceSettlement.status == "MISMATCH")
            .values(**values)
        )

        return result.rowcount

    def reverse_conditional(
        self,
        settlement_id: int,
        account_id: int,
        memo: str | None = None,
    ) -> int:
        """
        id 일치 + 이 회사의 account_id 일치 + 현재 status='DEPOSITED'일
        때만 REVERSED로 조건부 갱신.
        """

        values = {"status": "REVERSED"}

        if memo:
            values["memo"] = memo

        result = self.db.execute(
            update(MarketplaceSettlement)
            .where(MarketplaceSettlement.id == settlement_id)
            .where(MarketplaceSettlement.account_id == account_id)
            .where(MarketplaceSettlement.status == "DEPOSITED")
            .values(**values)
        )

        return result.rowcount


__all__ = [
    "SettlementRepository",
]
