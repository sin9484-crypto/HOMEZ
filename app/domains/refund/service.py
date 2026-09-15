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
값은 **이 승인 게이트에는** 쓰이지 않는다 — 자동 모드라고 해서
사람의 승인을 건너뛰지 않는다는 뜻이다.

2026-09-15 전면 감사 후속(11단계 지시문 Phase 8, 실행 게이트 배선)
— 단, `mark_executed()`(APPROVED→EXECUTED, 실제 Executor를 호출하는
최종 지점)는 별도로 `is_emergency_stop_active()`와
`get_function_mode(company_id, FunctionCode.REFUND)`(PAUSED/ERROR
차단)를 검사한다 — purchase_task 발주 실행과 동일한 성격의 "최종
외부 쓰기 직전" 방어선이다. 승인 게이트(사람 필수)와 실행 게이트
(자동화 상태 반영)는 서로 다른 목적이라 별도로 존재한다: 승인은
"이 환불이 정당한가"를, 실행 게이트는 "지금 이 순간 외부에 실제로
쓰기를 내보내도 되는가"를 묻는다 — 이미 승인된 환불이라도 승인
이후 비상정지가 걸리거나 REFUND 기능이 일시중지됐다면 실행하지
않는다.

**절대 하지 않는 것**: 실제 환불 실행. `mark_executed()`는
`FakeRefundExecutor`만 호출한다(app/domains/refund/executor.py) —
실제 Provider·실제 마켓플레이스 API 호출은 이 세션 범위 밖이다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.refund.constants import RefundStatus
from app.domains.refund.constants import RefundType
from app.domains.refund.executor import RefundExecutionError
from app.domains.refund.executor import RefundExecutor
from app.domains.refund.model import Refund
from app.domains.refund.model import RefundStatusEvent
from app.domains.refund.repository import RefundRepository


class RefundService:

    def __init__(self, db: Session, executor: RefundExecutor):

        self.db = db
        self.repository = RefundRepository(db)
        self.executor = executor
        self._safety_service = SafetyService(db)

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
        confirm_retry_after_uncertain_execution: bool = False,
    ) -> Refund:

        if not is_admin:
            raise ForbiddenException(
                "환불 실행 확정은 관리자만 가능합니다.",
            )

        # 2026-09-15 전면 감사 후속(Phase 8, 실행 게이트 배선) —
        # AWAITING_APPROVAL→APPROVED 전이(approve_refund())는 항상
        # 사람이 승인해야 하므로 자동화 모드와 무관하다(이 파일 상단
        # docstring 참고, 의도적 설계). 하지만 이 메서드(APPROVED→
        # EXECUTED, 실제 Executor 호출 — 돈이 움직이는 최종 지점)는
        # purchase_task 발주 실행과 같은 성격의 "최종 외부 쓰기
        # 직전" 지점이다. 이미 승인된 환불이라도 비상정지가 걸려
        # 있거나 REFUND 기능이 PAUSED/ERROR 상태면 실행하지 않는다 —
        # 승인 이후에 상황이 바뀌었을 수 있다는 것을 승인 단계
        # 하나만으로는 반영할 수 없기 때문이다.
        if self._safety_service.is_emergency_stop_active():
            raise ConflictException(
                "비상정지가 활성화되어 있습니다 — 환불 실행을 진행하지 "
                "않습니다.",
            )
        function_mode = self._safety_service.get_function_mode(
            company_id, FunctionCode.REFUND,
        )
        if function_mode in (FunctionMode.PAUSED, FunctionMode.ERROR):
            raise ConflictException(
                f"환불 기능이 현재 \"{FunctionMode.LABELS_KO.get(function_mode, function_mode)}\" "
                f"상태입니다({FunctionMode.DESCRIPTIONS_KO.get(function_mode, '')}) — 이미 승인된 "
                "환불이라도 실행을 진행하지 않습니다.",
            )

        now = now or datetime.utcnow()
        refund = self._get_required(refund_id, company_id)

        if refund.status != RefundStatus.APPROVED:
            raise BadRequestException(
                "승인된 환불만 실행 확정할 수 있습니다 — 현재 상태: "
                f"{refund.status}",
            )

        # 2026-09-15 전면 감사 후속(Phase 4, IA-011) — status가 여전히
        # APPROVED인데 execution_attempt_started_at이 이미 채워져
        # 있다면, 직전 mark_executed() 호출이 Executor를 실제로
        # 호출한 뒤 상태 전이를 확정하지 못한 채 끝났다는 뜻이다
        # (프로세스 중단 등 — "결과불명" 구간). 이 경우 사람이 실제
        # Provider 쪽 기록을 직접 확인했다는 명시적 확인
        # (confirm_retry_after_uncertain_execution=True) 없이는
        # Executor를 다시 호출하지 않는다 — 자동 재시도가 이중 환불로
        # 이어질 위험을 구조적으로 막는다(현재 Fake 단계에서는
        # 직접 재현되지 않지만, 실제 Provider로 교체되기 전에 반드시
        # 필요한 방어선이다).
        if (
            refund.execution_attempt_started_at is not None
            and not confirm_retry_after_uncertain_execution
        ):
            raise ConflictException(
                "이 환불은 이전 실행 시도가 성공/실패 어느 쪽으로도 "
                "확정되지 못한 채 남아 있습니다(시도 시각: "
                f"{refund.execution_attempt_started_at}) — 실제 Provider "
                "기록을 직접 확인하기 전까지 자동으로 다시 실행하지 "
                "않습니다. 확인 후 다시 실행하려면 "
                "confirm_retry_after_uncertain_execution=True로 호출하세요.",
            )

        # 2026-09-15 전면 감사 후속(Phase 4, IA-011) — 외부 Executor를
        # 호출하기 "직전"에 이 시도 자체를 durable하게 먼저 commit한다
        # (purchase_task 발주 시도가 IN_FLIGHT를 네트워크 호출 전에
        # commit하는 것과 같은 설계). 그래야 Executor 호출 이후
        # 프로세스가 중단돼도 "실행을 시도한 적이 있다"는 사실이
        # 사라지지 않는다.
        refund.execution_attempt_started_at = now
        self.db.commit()
        self.db.refresh(refund)

        # 실행 자체(Fake)는 상태 전이 이전에 호출한다 — 실행이
        # 실패하면(FakeRefundExecutor는 amount<=0에서만 실패하지만,
        # 미래의 실제 Executor는 다양한 이유로 실패할 수 있다) 상태를
        # 전혀 바꾸지 않는다.
        try:
            self.executor.execute(refund.id, refund.amount, refund.currency)
        except RefundExecutionError:
            # 2026-09-15 전면 감사 후속(Phase 4, IA-011) — 이 예외는
            # Executor가 실제로 외부에 도달하기 전에 확정적으로
            # 거부했다는 뜻이다(예: amount<=0 사전 검증). "결과불명"이
            # 아니라 "확실히 안 됐다"이므로 시도 마커를 지워 다음
            # mark_executed() 호출이 사람의 별도 확인 없이도 정상
            # 진행되게 한다.
            refund.execution_attempt_started_at = None
            self.db.commit()
            raise

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
