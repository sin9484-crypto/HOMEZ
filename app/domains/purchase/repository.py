"""
=========================================================
Homez OS

File : app/domains/purchase/repository.py

Purchase Repository — V7 Gate 4(2026-08-15) 처음부터 재설계.
=========================================================
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.purchase.model import Purchase
from app.domains.purchase.model import PurchaseItem


class PurchaseRepository:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db

    # --------------------------------------------------
    # Purchase
    # --------------------------------------------------

    def get_purchase(
        self,
        purchase_id: int,
    ) -> Purchase | None:

        return (
            self.db.query(Purchase)
            .filter(Purchase.id == purchase_id)
            .first()
        )

    def get_purchase_for_company(
        self,
        purchase_id: int,
        company_id: int,
    ) -> Purchase | None:

        return (
            self.db.query(Purchase)
            .filter(Purchase.id == purchase_id)
            .filter(Purchase.company_id == company_id)
            .first()
        )

    def get_purchase_by_idempotency(
        self,
        company_id: int,
        idempotency_key: str,
    ) -> Purchase | None:

        return (
            self.db.query(Purchase)
            .filter(Purchase.company_id == company_id)
            .filter(Purchase.idempotency_key == idempotency_key)
            .first()
        )

    def list_purchases_for_order(
        self,
        order_id: int,
        company_id: int,
    ) -> list[Purchase]:

        return (
            self.db.query(Purchase)
            .filter(Purchase.order_id == order_id)
            .filter(Purchase.company_id == company_id)
            .order_by(Purchase.id.asc())
            .all()
        )

    def list_purchases_for_company(
        self,
        company_id: int,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Purchase]:

        query = self.db.query(Purchase).filter(
            Purchase.company_id == company_id,
        )

        if status is not None:
            query = query.filter(Purchase.status == status)

        return (
            query
            .order_by(Purchase.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_purchase_no_commit(
        self,
        purchase: Purchase,
    ) -> Purchase:

        self.db.add(purchase)
        self.db.flush()

        return purchase

    def save_purchase_no_commit(
        self,
        purchase: Purchase,
    ) -> Purchase:

        self.db.add(purchase)
        self.db.flush()

        return purchase

    def transition_purchase_status_conditional(
        self,
        purchase_id: int,
        company_id: int,
        from_status: str,
        to_status: str,
        *,
        extra_values: dict | None = None,
    ) -> int:
        """
        상태 전이 + 타임스탬프 컬럼(confirmed_at 등)을 하나의 원자적
        UPDATE에 함께 넣는다 — 별도로 ORM 객체 속성을 바꿔 add()/
        flush()하면 이 raw UPDATE 이전에 로드된 stale 값(예: 이전
        status)까지 함께 덮어써버릴 위험이 있다(Inventory Gate 3의
        transition_reservation_conditional()과 달리 이쪽은 부가
        타임스탬프가 있어 한 단계 더 필요).
        """

        values: dict = {"status": to_status}
        if extra_values:
            values.update(extra_values)

        stmt = (
            update(Purchase)
            .where(Purchase.id == purchase_id)
            .where(Purchase.company_id == company_id)
            .where(Purchase.status == from_status)
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def claim_submission_conditional(
        self,
        purchase_id: int,
        company_id: int,
        approval_fingerprint: str,
    ) -> int:
        """
        submission_status가 아직 비어 있는(NULL) 행만 PENDING으로
        조건부 전이시킨다 — 동시 이중 전송 요청 중 정확히 하나만
        성공한다(다른 하나는 rowcount 0을 받아 already-claimed로
        처리한다)."""

        stmt = (
            update(Purchase)
            .where(Purchase.id == purchase_id)
            .where(Purchase.company_id == company_id)
            .where(Purchase.submission_status.is_(None))
            .values(
                submission_status="PENDING",
                approval_fingerprint=approval_fingerprint,
            )
        )

        return self.db.execute(stmt).rowcount

    def reclaim_failed_submission_conditional(
        self,
        purchase_id: int,
        company_id: int,
        approval_fingerprint: str,
    ) -> int:
        """2026-08-21 작업 1(항목 19, 실패 건 재시도) — claim_submission_
        conditional()과 정확히 같은 동시성 보장(원자적 조건부 UPDATE,
        정확히 하나의 동시 재시도 요청만 성공)을 FAILED 상태에서도
        제공한다. submission_status가 정확히 'FAILED'인 행만 다시
        PENDING으로 전이시킨다 — SUBMITTED/PARTIALLY_ACCEPTED/REJECTED
        처럼 이미 공급처가 실제로 응답한 상태는 재시도 대상이 아니다
        (그 상태를 재시도하면 같은 발주가 공급처에 중복 접수될 수
        있다 — 이 코드베이스의 "중복 발주 방지" 원칙 위반)."""

        stmt = (
            update(Purchase)
            .where(Purchase.id == purchase_id)
            .where(Purchase.company_id == company_id)
            .where(Purchase.submission_status == "FAILED")
            .values(
                submission_status="PENDING",
                approval_fingerprint=approval_fingerprint,
            )
        )

        return self.db.execute(stmt).rowcount

    def save_submission_result_no_commit(
        self,
        purchase_id: int,
        company_id: int,
        values: dict,
    ) -> int:

        stmt = (
            update(Purchase)
            .where(Purchase.id == purchase_id)
            .where(Purchase.company_id == company_id)
            .values(**values)
        )

        return self.db.execute(stmt).rowcount

    # --------------------------------------------------
    # PurchaseItem
    # --------------------------------------------------

    def add_purchase_item_no_commit(
        self,
        item: PurchaseItem,
    ) -> PurchaseItem:

        self.db.add(item)
        self.db.flush()

        return item

    def list_items_for_purchase(
        self,
        purchase_id: int,
        company_id: int,
    ) -> list[PurchaseItem]:

        return (
            self.db.query(PurchaseItem)
            .filter(PurchaseItem.purchase_id == purchase_id)
            .filter(PurchaseItem.company_id == company_id)
            .order_by(PurchaseItem.id.asc())
            .all()
        )


__all__ = [
    "PurchaseRepository",
]
