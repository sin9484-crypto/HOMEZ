"""
=========================================================
Homez OS

File : app/domains/refund/repository.py

2026-09-10 Phase 8 — Refund 저장소 계층. `app/domains/return_order/
repository.py`의 원자적 조건부 UPDATE 패턴(transition_status_
conditional)을 그대로 재사용한다 — 동시 요청이 같은 Refund를 두 번
승인/실행하는 경쟁을 DB 레벨에서 막는다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.refund.model import Refund
from app.domains.refund.model import RefundStatusEvent


class RefundRepository:

    def __init__(self, db: Session):

        self.db = db

    def create(self, refund: Refund) -> Refund:

        self.db.add(refund)
        self.db.commit()
        self.db.refresh(refund)

        return refund

    def get(self, refund_id: int, company_id: int) -> Refund | None:

        return (
            self.db.query(Refund)
            .filter(Refund.id == refund_id, Refund.company_id == company_id)
            .first()
        )

    def list_for_company(
        self, company_id: int, *, status: str | None = None,
    ) -> list[Refund]:

        query = self.db.query(Refund).filter(
            Refund.company_id == company_id,
        )

        if status is not None:
            query = query.filter(Refund.status == status)

        return query.order_by(Refund.created_at.desc()).all()

    def transition_status_conditional(
        self,
        refund_id: int,
        company_id: int,
        from_status: str,
        to_status: str,
        *,
        extra_values: dict | None = None,
    ) -> int:

        values: dict = {"status": to_status}
        if extra_values:
            values.update(extra_values)

        stmt = (
            update(Refund)
            .where(Refund.id == refund_id)
            .where(Refund.company_id == company_id)
            .where(Refund.status == from_status)
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def add_status_event_no_commit(
        self, event: RefundStatusEvent,
    ) -> RefundStatusEvent:

        self.db.add(event)

        return event

    def list_status_events(self, refund_id: int) -> list[RefundStatusEvent]:

        return (
            self.db.query(RefundStatusEvent)
            .filter(RefundStatusEvent.refund_id == refund_id)
            .order_by(RefundStatusEvent.id.asc())
            .all()
        )


__all__ = ["RefundRepository"]
