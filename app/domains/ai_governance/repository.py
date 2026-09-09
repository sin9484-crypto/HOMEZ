"""
=========================================================
Homez OS

File : app/domains/ai_governance/repository.py

AG-4(2026-08-21) — ProposedAction 조회/쓰기 헬퍼(비즈니스 판단은
proposed_action_service.py가 전담). FK를 쓰지 않는 이 코드베이스
전역 컨벤션을 그대로 따른다.
=========================================================
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.ai_governance.model import ProposedAction


class ProposedActionRepository:

    def __init__(self, db: Session):

        self.db = db

    def get_by_company_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> ProposedAction | None:

        return self.db.execute(
            select(ProposedAction).where(
                ProposedAction.company_id == company_id,
                ProposedAction.idempotency_key == idempotency_key,
            ),
        ).scalar_one_or_none()

    def get_for_company(
        self, action_id: int, company_id: int,
    ) -> ProposedAction | None:

        return self.db.execute(
            select(ProposedAction).where(
                ProposedAction.id == action_id,
                ProposedAction.company_id == company_id,
            ),
        ).scalar_one_or_none()

    def add_no_commit(self, action: ProposedAction) -> ProposedAction:

        self.db.add(action)
        self.db.flush()

        return action

    def transition_conditional(
        self,
        action_id: int,
        company_id: int,
        *,
        from_statuses: tuple[str, ...],
        to_status: str,
        decided_by: int | None = None,
        decision_reason: str | None = None,
        executed_reference: str | None = None,
    ) -> int:
        """
        상태 머신 전이를 원자적 조건부 UPDATE로 강제한다(app/domains/
        inventory/repository.py::transition_reservation_conditional과
        동일한 원칙) — WHERE status IN from_statuses가 이미 처리된
        제안을 다시 처리하는 경쟁을 DB 레벨에서 차단한다.
        """

        values = {"status": to_status, "updated_at": datetime.utcnow()}
        if decided_by is not None:
            values["decided_by"] = decided_by
            values["decided_at"] = datetime.utcnow()
        if decision_reason is not None:
            values["decision_reason"] = decision_reason
        if executed_reference is not None:
            values["executed_reference"] = executed_reference
            values["executed_at"] = datetime.utcnow()

        stmt = (
            update(ProposedAction)
            .where(ProposedAction.id == action_id)
            .where(ProposedAction.company_id == company_id)
            .where(ProposedAction.status.in_(from_statuses))
            .values(**values)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def list_for_company(
        self, company_id: int, *, status: str | None = None,
    ) -> list[ProposedAction]:

        stmt = select(ProposedAction).where(
            ProposedAction.company_id == company_id,
        )
        if status is not None:
            stmt = stmt.where(ProposedAction.status == status)

        return list(
            self.db.execute(
                stmt.order_by(ProposedAction.id.desc()),
            ).scalars(),
        )


__all__ = ["ProposedActionRepository"]
