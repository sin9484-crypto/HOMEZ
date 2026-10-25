"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_return_action_service.py

2026-10-26 — 쿠팡 쓰기 5번: **반품 입고 확인·반품 승인**(외부 변경 — 고객 환불로 이어진다). 재고·판매가·상품명·문의 답변과 같은 틀을 따른다.
이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 반품 상태·접수 수량은 쿠팡 단건 조회로 그 자리에서 읽는다. 미리보기에서 본 상태(와 승인 수량)가 지금 값과
     다르면 보내지 않는다. 승인 수량은 쿠팡이 준 접수 수량(returnItems[].cancelCount 합)만 쓴다.
  2) 동작마다 쿠팡이 받는 상태에서만 보낸다 — 입고 확인은 반품접수(RETURNS_UNCHECKED), 승인은 입고완료(VENDOR_WAREHOUSE_CONFIRM).
  3) **무재고 중개라 반품 상품은 공급처(또는 판매자 창고)로 간다** — 입고 확인은 "상품을 실제로 받은 것을 확인했다"(`confirm_received`)를
     받아야 보낸다. 빠른환불 대상이면 입고 확인만으로 바로 환불된다는 것을 화면에 알린다. 승인은 "고객에게 환불된다"(`confirm_refund`)를
     받아야 보낸다. HOMEZ는 환불 금액·정산을 계산하거나 기록하지 않는다(쿠팡 정산은 정산 확인이 따로 읽는다 — Refund·Settlement 분리).
  4) 안전 — 비상 정지가 켜져 있거나 기능 모드가 "운영자 승인"이 아니면 보내지 않는다. 승인 센터의 "쿠팡 반품 처리" 허용이 없으면
     읽지도 보내지도 않는다(읽기·쓰기 각각 횟수형).
  5) 한 반품접수번호에 동작마다 한 번만(작업 장부 키 = 접수번호 + 동작). 결과불명은 단건 조회로만 확인한다 — 적용 뒤 상태면 성공,
     처리 전 상태면 안 받음, 쿠팡 확인 요청 등 다른 상태는 판단하지 않는다(다시 보내지 않는다).
=========================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_return_action_provider import APPLIED_STATUSES
from app.domains.purchase_task.coupang_return_action_provider import APPROVE
from app.domains.purchase_task.coupang_return_action_provider import RECEIVE
from app.domains.purchase_task.coupang_return_action_provider import REQUIRED_STATUS
from app.domains.purchase_task.coupang_return_action_provider import valid_receipt_id
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

ACTIONS = (RECEIVE, APPROVE)
# 화면 문구용 한글 이름(쿠팡 문서의 상태 설명). 모르는 상태는 코드 그대로 보인다.
STATUS_LABELS_KO = {
    "RELEASE_STOP_UNCHECKED": "출고중지 요청", "RETURNS_UNCHECKED": "반품접수", "VENDOR_WAREHOUSE_CONFIRM": "입고완료",
    "REQUEST_COUPANG_CHECK": "쿠팡 확인 요청", "RETURNS_COMPLETED": "반품완료",
}


def status_label(status: str | None) -> str:
    return STATUS_LABELS_KO.get(status or "", status or "알 수 없음")


@dataclass
class ReturnBlocker:
    code: str
    message: str


@dataclass
class ReturnPlan:
    receipt_id: str
    action: str
    blockers: list[ReturnBlocker] = field(default_factory=list)
    gate_blockers: list[ReturnBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    coupang: dict | None = None
    status: str | None = None
    cancel_count: int | None = None
    already_applied: bool = False
    live_checked: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_applied

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id, "action": self.action, "sendable": self.sendable, "live_checked": self.live_checked,
            "already_applied": self.already_applied, "required_status": REQUIRED_STATUS.get(self.action),
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "coupang": self.coupang, "ledger": self.ledger,
        }


class CoupangReturnActionService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 계획(미리보기) — 로컬 판정 + 쿠팡 반품 단건 조회 1회
    # ------------------------------------------------------------

    def plan(self, company_id: int, receipt_id: Any, action: Any, *, live: bool = True) -> ReturnPlan:
        target = str(receipt_id or "").strip()
        plan = ReturnPlan(receipt_id=target, action=action if action in ACTIONS else str(action))
        if not valid_receipt_id(receipt_id):
            plan.blockers.append(ReturnBlocker("INVALID_RECEIPT_ID", "반품접수번호는 숫자여야 한다."))
        if action not in ACTIONS:
            plan.blockers.append(ReturnBlocker("INVALID_ACTION", "반품 처리는 입고 확인(RECEIVE)이나 승인(APPROVE)만 할 수 있다."))
        self._resolve_store(plan, company_id)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.ledger = self._ledger_summary(company_id, plan)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _resolve_store(self, plan: ReturnPlan, company_id: int) -> None:
        stores = (
            self.db.query(StoreConnection)
            .filter(
                StoreConnection.company_id == company_id, StoreConnection.marketplace_code == "COUPANG",
                StoreConnection.connection_status == ConnectionStatus.CONNECTED,
            )
            .all()
        )
        if len(stores) == 1:
            plan.store_connection_id = stores[0].id
        elif not stores:
            plan.blockers.append(ReturnBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다."))
        else:
            plan.blockers.append(ReturnBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."))

    def _gate_checks(self, plan: ReturnPlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(ReturnBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(ReturnBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: ReturnPlan) -> dict | None:
        actions = self._actions_factory(self.db)
        action_type = actions.return_action_type(plan.action)
        row = actions._journal.get(company_id, action_type, actions.return_action_key(plan.receipt_id, plan.action))
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: ReturnPlan, company_id: int) -> None:
        snapshot, failure = self._actions_factory(self.db).read_return(
            company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id,
        )
        if snapshot is None or not snapshot.ok:
            detail = failure.detail if failure is not None else (snapshot.detail if snapshot is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(ReturnBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(ReturnBlocker("RETURN_READ_FAILED", f"쿠팡 반품을 읽지 못해 보내지 않는다(고객이 철회했을 수 있다). {detail}".strip()))
            return
        plan.live_checked = True
        plan.coupang = snapshot.to_dict()
        plan.status = snapshot.status
        plan.cancel_count = snapshot.cancel_count
        if snapshot.status in APPLIED_STATUSES[plan.action]:
            plan.already_applied = True
            return
        if snapshot.status != REQUIRED_STATUS[plan.action]:
            plan.blockers.append(ReturnBlocker(
                "WRONG_RETURN_STATUS",
                f"반품 상태가 '{status_label(snapshot.status)}'라 보내지 않는다(이 처리는 '{status_label(REQUIRED_STATUS[plan.action])}' 상태에서만 할 수 있다).",
            ))
            return
        if plan.action == APPROVE and snapshot.cancel_count is None:
            plan.blockers.append(ReturnBlocker("CANCEL_COUNT_UNKNOWN", "쿠팡 응답에서 반품 접수 수량을 읽지 못해 승인하지 않는다."))

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, receipt_id: Any, action: str, *, expected_status: str | None,
        expected_cancel_count: int | None = None, confirm_received: bool = False, confirm_refund: bool = False,
        retry_action_required: bool = False, before_send: Callable[[], None] | None = None,
    ) -> dict:
        plan = self.plan(company_id, receipt_id, action, live=True)
        if plan.already_applied:
            return {"outcome": "ALREADY_DONE", "detail": f"쿠팡 반품 상태가 이미 '{status_label(plan.status)}'라 보내지 않았다.", "executed": False,
                    "verified": True, "after_status": plan.status, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("반품을 처리할 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if not isinstance(expected_status, str) or expected_status != plan.status:
            raise ConflictException("화면에서 확인한 반품 상태와 쿠팡의 지금 상태가 다르다 — 다시 확인해 주세요. 보내지 않았다.")
        if action == APPROVE and (isinstance(expected_cancel_count, bool) or expected_cancel_count != plan.cancel_count):
            raise ConflictException("화면에서 확인한 반품 수량과 쿠팡의 접수 수량이 다르다 — 다시 확인해 주세요. 보내지 않았다.")
        if confirm_received is not True:
            raise BadRequestException("반품 상품을 실제로 받은 것(공급처·창고)을 확인해 주세요. 보내지 않았다.")
        if action == APPROVE and confirm_refund is not True:
            raise BadRequestException("승인하면 고객에게 환불된다 — 이 점을 따로 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        result = actions.process_return(
            company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id, action=action,
            cancel_count=plan.cancel_count if action == APPROVE else None, triggered_by=user_id,
            retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_RETURN_" + action, entity="coupang_return_request", entity_id=plan.receipt_id,
            description=f"쿠팡 반품 {'입고 확인' if action == RECEIVE else '승인'} {result.outcome}: 상태 {plan.status}"
                        + (f", 수량 {plan.cancel_count}" if action == APPROVE else ""),
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        after_status: str | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 반품을 다시 읽는다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            after, _ = actions.read_return(company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id)
            if after is not None and after.ok:
                after_status = after.status
                verified = after.status in APPLIED_STATUSES[action]
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified,
                "after_status": after_status, "plan": plan.to_dict()}

    def reconcile(self, company_id: int, receipt_id: Any, action: str) -> dict:
        """결과불명(UNKNOWN)인 반품 처리를 단건 조회로 확인한다(읽기, 보내지 않음)."""

        if not valid_receipt_id(receipt_id) or action not in ACTIONS:
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        plan = ReturnPlan(receipt_id=str(receipt_id).strip(), action=action)
        self._resolve_store(plan, company_id)
        if plan.blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in plan.blockers))
        result = self._actions_factory(self.db).check_return(
            company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id, action=action,
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangReturnActionService", "ReturnPlan", "ReturnBlocker", "ACTIONS", "STATUS_LABELS_KO", "status_label"]
