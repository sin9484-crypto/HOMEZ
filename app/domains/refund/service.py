"""
=========================================================
Homez OS

File : app/domains/refund/service.py

2026-09-10 Phase 8(HOMEZ_USER_OPERATION_SETTINGS.md 8·9번) — 환불
상태 관리·승인 게이트.

**"returns proceed only after user notice + approval"를 구조적으로
강제한다**: `AWAITING_APPROVAL → APPROVED` 전이는 오직
`approve_refund()`를 통해서만 일어나고, 이 메서드는 항상 사람
(SuperAdmin)의 명시적 호출을 전제한다 — `FunctionCode.REFUND`가
Phase 3에서 AUTOMATIC으로 설정돼 있어도 이 전이를 자동으로
건너뛰지 않는다. 이건 `app/domains/payment/service.py`(PAYMENT
기능이 AUTOMATIC이면 한도 안에서 자동 실행 판정을 내려주는)와
의도적으로 다른 설계다 — Phase 8 지시 문구 자체가 "승인 후에만
진행한다"고 예외 없이 명시하기 때문이다. `FunctionMode.REFUND`
값은 이 도메인에서 승인 자동화에 쓰이지 않는다(단순 참고용으로만
Refund 행에 남길 수 있으나, 이번 구현에서는 그마저도 하지 않는다 —
불필요한 결합을 늘리지 않기 위함).

**절대 하지 않는 것**: 실제 환불 실행. `mark_executed()`는
`FakeRefundExecutor`만 호출한다(app/domains/refund/executor.py) —
실제 Provider·실제 마켓플레이스 API 호출은 이 세션 범위 밖이다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.refund.constants import RefundStatus
from app.domains.refund.constants import RefundType
from app.domains.refund.executor import RefundExecutor
from app.domains.refund.model import Refund
from app.domains.refund.model import RefundStatusEvent
from app.domains.refund.repository import RefundRepository


class RefundService:

    def __init__(self, db: Session, executor: RefundExecutor):

        self.db = db
        self.repository = RefundRepository(db)
        self.executor = executor

    def _get_required(self, refund_id: int, company_id: int) -> Refund:

        refund = self.repository.get(refund_id, company_id)

        if refund is None:
            raise NotFoundException("해당 환불 건을 찾을 수 없습니다.")

        return refund

    def _append_status_event_no_commit(
        self,
        refund: Refund,
        *,
        previous_status: str | None,
        new_status: str,
        reason: str | None = None,
        triggered_by: int | None,
    ) -> None:

        self.repository.add_status_event_no_commit(
            RefundStatusEvent(
                company_id=refund.company_id,
                refund_id=refund.id,
                previous_status=previous_status,
                new_status=new_status,
                reason=reason,
                triggered_by=triggered_by,
            ),
        )

    # ------------------------------
    # 생성 — 항상 AWAITING_APPROVAL로 시작한다(예외 없음).
    # ------------------------------

    def create_refund_request(
        self,
        *,
        company_id: int,
        order_id: int,
        return_order_id: int | None,
        refund_type: str,
        amount: float,
        currency: str,
        reason: str,
        requested_by: int,
        idempotency_key: str,
    ) -> Refund:

        if refund_type not in RefundType.ALL:
            raise BadRequestException(
                f"알 수 없는 환불 종류: {refund_type}",
            )

        if amount <= 0:
            raise BadRequestException("환불 금액은 0보다 커야 합니다.")

        if not reason or not reason.strip():
            raise BadRequestException("환불 사유가 필요합니다.")

        refund = Refund(
            company_id=company_id,
            order_id=order_id,
            return_order_id=return_order_id,
            refund_type=refund_type,
            status=RefundStatus.AWAITING_APPROVAL,
            amount=amount,
            currency=currency,
            reason=reason.strip(),
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )

        return self.repository.create(refund)

    # ------------------------------
    # 승인 — 항상 사람이 명시적으로 호출해야 한다.
    # ------------------------------

    def approve_refund(
        self,
        *,
        refund_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
        now: datetime | None = None,
    ) -> Refund:

        if not is_admin:
            raise ForbiddenException("환불 승인은 관리자만 가능합니다.")

        now = now or datetime.utcnow()
        refund = self._get_required(refund_id, company_id)

        rowcount = self.repository.transition_status_conditional(
            refund_id, company_id,
            RefundStatus.AWAITING_APPROVAL, RefundStatus.APPROVED,
            extra_values={"approved_at": now, "approved_by": user_id},
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "승인 대기 상태가 아닌 환불은 승인할 수 없습니다 — "
                f"현재 상태: {refund.status}",
            )

        self._append_status_event_no_commit(
            refund, previous_status=RefundStatus.AWAITING_APPROVAL,
            new_status=RefundStatus.APPROVED, triggered_by=user_id,
        )

        self.db.commit()
        self.db.refresh(refund)

        return refund

    def reject_refund(
        self,
        *,
        refund_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
        reason: str,
    ) -> Refund:

        if not is_admin:
            raise ForbiddenException("환불 거부는 관리자만 가능합니다.")

        refund = self._get_required(refund_id, company_id)

        rowcount = self.repository.transition_status_conditional(
            refund_id, company_id,
            RefundStatus.AWAITING_APPROVAL, RefundStatus.REJECTED,
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "승인 대기 상태가 아닌 환불은 거부할 수 없습니다 — "
                f"현재 상태: {refund.status}",
            )

        self._append_status_event_no_commit(
            refund, previous_status=RefundStatus.AWAITING_APPROVAL,
            new_status=RefundStatus.REJECTED, reason=reason,
            triggered_by=user_id,
        )

        self.db.commit()
        self.db.refresh(refund)

        return refund

    # ------------------------------
    # 실행 — Fake Executor만 호출한다(실제 실행은 이 세션 범위 밖).
    # ------------------------------

    def mark_executed(
        self,
        *,
        refund_id: int,
        company_id: int,
        user_id: int,
        is_admin: bool,
        now: datetime | None = None,
    ) -> Refund:

        if not is_admin:
            raise ForbiddenException(
                "환불 실행 확정은 관리자만 가능합니다.",
            )

        now = now or datetime.utcnow()
        refund = self._get_required(refund_id, company_id)

        if refund.status != RefundStatus.APPROVED:
            raise BadRequestException(
                "승인된 환불만 실행 확정할 수 있습니다 — 현재 상태: "
                f"{refund.status}",
            )

        # 실행 자체(Fake)는 상태 전이 이전에 호출한다 — 실행이
        # 실패하면(FakeRefundExecutor는 amount<=0에서만 실패하지만,
        # 미래의 실제 Executor는 다양한 이유로 실패할 수 있다) 상태를
        # 전혀 바꾸지 않는다.
        self.executor.execute(refund.id, refund.amount, refund.currency)

        rowcount = self.repository.transition_status_conditional(
            refund_id, company_id,
            RefundStatus.APPROVED, RefundStatus.EXECUTED,
            extra_values={"executed_at": now},
        )
        if rowcount != 1:
            self.db.rollback()
            raise BadRequestException(
                "승인된 환불만 실행 확정할 수 있습니다 — 현재 상태: "
                f"{refund.status}",
            )

        self._append_status_event_no_commit(
            refund, previous_status=RefundStatus.APPROVED,
            new_status=RefundStatus.EXECUTED, triggered_by=user_id,
        )

        self.db.commit()
        self.db.refresh(refund)

        return refund

    # ------------------------------
    # 조회
    # ------------------------------

    def get(self, refund_id: int, company_id: int) -> Refund | None:

        return self.repository.get(refund_id, company_id)

    def list_for_company(
        self, company_id: int, *, status: str | None = None,
    ) -> list[Refund]:

        return self.repository.list_for_company(company_id, status=status)


__all__ = ["RefundService"]
