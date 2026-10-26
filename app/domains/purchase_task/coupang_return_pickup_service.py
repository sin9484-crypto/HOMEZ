"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_return_pickup_service.py

2026-10-27 — 쿠팡 쓰기 7번: **반품 회수 송장 등록**(외부 변경 — 직접 회수하는 판매자가 회수 택배 송장을 쿠팡에 알린다).
반품 입고 확인·승인(`coupang_return_action_service.py`)과 같은 틀·같은 허용("쿠팡 반품 처리")을 쓴다. 이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 택배사는 송장 등록과 같은 공식 이름표(`courier_code_for`)로만 코드로 바꾸고, 없는 이름은 막는다.
     송장번호·회수번호는 사용자가 넣은 그대로 쓴다. 반품 상태는 쿠팡 단건 조회로 그 자리에서 읽는다.
  2) 반품접수(RETURNS_UNCHECKED)일 때만 보낸다(공식). 이미 회수 송장이 붙어 있으면 보내지 않는다 — 같은 송장이면 '이미 등록', 다른 송장이면 막고
     붙어 있는 송장을 보여 준다. 미리보기에서 본 상태와 지금 상태가 다르면 보내지 않는다.
  3) 회수 송장을 등록해도 택배기사가 회수하기 전에는 고객이 반품을 철회할 수 있다(공식) — 사용자가 이 점과 "직접 회수를 맡긴 송장"임을
     확인(`confirm_pickup`)해야 보낸다. 굿스플로(반품 자동 연동)를 쓰는 판매자는 쓸 필요가 없다.
  4) 안전 — 비상 정지·운영자 승인 모드, 승인 센터 허용(쿠팡 반품 처리)이 없으면 읽지도 보내지도 않는다.
  5) 한 반품에 한 번만(작업 장부 키 = 접수번호 + PICKUP). 결과불명은 단건 조회로만 확인한다.
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
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_invoice_service import COURIER_CODES_BY_NAME
from app.domains.purchase_task.coupang_invoice_service import courier_code_for
from app.domains.purchase_task.coupang_return_action_provider import PICKUP_REQUIRED_STATUS
from app.domains.purchase_task.coupang_return_action_provider import valid_receipt_id
from app.domains.purchase_task.coupang_return_action_service import status_label
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")


def courier_code(value: Any) -> str | None:
    """택배사 이름(예: CJ대한통운) 또는 공식 코드(예: CJGLS)를 공식 코드로. 목록에 없으면 None(추측하지 않는다)."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if text in set(COURIER_CODES_BY_NAME.values()):
        return text
    return courier_code_for(text)


def _clean(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass
class PickupBlocker:
    code: str
    message: str


@dataclass
class PickupPlan:
    receipt_id: str
    courier_code: str | None
    invoice_number: str | None
    reg_number: str | None
    blockers: list[PickupBlocker] = field(default_factory=list)
    gate_blockers: list[PickupBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    coupang: dict | None = None
    status: str | None = None
    already_applied: bool = False
    live_checked: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_applied

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id, "courier_code": self.courier_code, "invoice_number": self.invoice_number,
            "reg_number": self.reg_number, "sendable": self.sendable, "live_checked": self.live_checked,
            "already_applied": self.already_applied, "required_status": PICKUP_REQUIRED_STATUS,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "coupang": self.coupang, "ledger": self.ledger,
        }


class CoupangReturnPickupService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    def plan(
        self, company_id: int, receipt_id: Any, courier: Any, invoice_number: Any, *, reg_number: Any = None, live: bool = True,
    ) -> PickupPlan:
        target = str(receipt_id or "").strip()
        code = courier_code(courier)
        number = _clean(invoice_number)
        reg = _clean(reg_number)
        plan = PickupPlan(receipt_id=target, courier_code=code, invoice_number=number, reg_number=reg)
        if not valid_receipt_id(receipt_id):
            plan.blockers.append(PickupBlocker("INVALID_RECEIPT_ID", "반품접수번호는 숫자여야 한다."))
        if code is None:
            plan.blockers.append(PickupBlocker("UNKNOWN_COURIER", "택배사가 쿠팡 공식 목록에 없다(예: CJ대한통운·한진택배·롯데택배·로젠택배·우체국택배)."))
        if number is None or not (6 <= len(number) <= 30 and all(c.isalnum() or c == "-" for c in number)):
            plan.blockers.append(PickupBlocker("INVALID_INVOICE_NUMBER", "회수 송장번호는 6~30자의 영문·숫자·하이픈이어야 한다."))
        if reg_number is not None and not (isinstance(reg_number, str) and (reg is None or (len(reg) <= 30 and reg.isalnum()))):
            plan.blockers.append(PickupBlocker("INVALID_REG_NUMBER", "회수번호는 비우거나 30자 이하의 영문·숫자여야 한다."))
        self._resolve_store(plan, company_id)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.ledger = self._ledger_summary(company_id, plan)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _resolve_store(self, plan: PickupPlan, company_id: int) -> None:
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
            plan.blockers.append(PickupBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다."))
        else:
            plan.blockers.append(PickupBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."))

    def _gate_checks(self, plan: PickupPlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(PickupBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(PickupBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: PickupPlan) -> dict | None:
        actions = self._actions_factory(self.db)
        row = actions._journal.get(company_id, ChannelActionType.RETURN_PICKUP_INVOICE, actions.pickup_invoice_key(plan.receipt_id))
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: PickupPlan, company_id: int) -> None:
        snapshot, failure = self._actions_factory(self.db).read_return(
            company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id,
        )
        if snapshot is None or not snapshot.ok:
            detail = failure.detail if failure is not None else (snapshot.detail if snapshot is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(PickupBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(PickupBlocker("RETURN_READ_FAILED", f"쿠팡 반품을 읽지 못해 보내지 않는다(고객이 철회했을 수 있다). {detail}".strip()))
            return
        plan.live_checked = True
        plan.coupang = snapshot.to_dict()
        plan.status = snapshot.status
        existing = [n for _, n in snapshot.return_invoices]
        if plan.invoice_number in existing:
            plan.already_applied = True
            return
        if existing:
            plan.blockers.append(PickupBlocker(
                "HAS_OTHER_INVOICE", f"이 반품에는 이미 회수 송장({', '.join(existing)})이 붙어 있어 보내지 않는다 — 바꾸려면 쿠팡 윙에서 처리해 주세요.",
            ))
            return
        if snapshot.status != PICKUP_REQUIRED_STATUS:
            plan.blockers.append(PickupBlocker(
                "WRONG_RETURN_STATUS",
                f"반품 상태가 '{status_label(snapshot.status)}'라 보내지 않는다(회수 송장은 '{status_label(PICKUP_REQUIRED_STATUS)}' 상태에서만 등록한다).",
            ))

    def send(
        self, company_id: int, user_id: int, receipt_id: Any, courier: Any, invoice_number: Any, *, reg_number: Any = None,
        expected_status: str | None, confirm_pickup: bool = False, retry_action_required: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> dict:
        plan = self.plan(company_id, receipt_id, courier, invoice_number, reg_number=reg_number, live=True)
        if plan.already_applied:
            return {"outcome": "ALREADY_DONE", "detail": "이 반품에 그 회수 송장이 이미 붙어 있어 보내지 않았다.", "executed": False,
                    "verified": True, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("회수 송장을 등록할 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if not isinstance(expected_status, str) or expected_status != plan.status:
            raise ConflictException("화면에서 확인한 반품 상태와 쿠팡의 지금 상태가 다르다 — 다시 확인해 주세요. 보내지 않았다.")
        if confirm_pickup is not True:
            raise BadRequestException("직접 회수를 맡긴 송장인지, 회수 전에는 고객이 철회할 수 있다는 점을 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        result = actions.register_return_pickup(
            company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id,
            courier_code=plan.courier_code, invoice_number=plan.invoice_number, reg_number=plan.reg_number,
            triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_RETURN_PICKUP_INVOICE", entity="coupang_return_request", entity_id=plan.receipt_id,
            description=f"쿠팡 반품 회수 송장 등록 {result.outcome}: {plan.courier_code} {plan.invoice_number}",
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 반품을 다시 읽어 그 송장이 붙었는지 본다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            after, _ = actions.read_return(company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id)
            if after is not None and after.ok:
                verified = any(n == plan.invoice_number for _, n in after.return_invoices)
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "plan": plan.to_dict()}

    def reconcile(self, company_id: int, receipt_id: Any, invoice_number: Any) -> dict:
        """결과불명(UNKNOWN)인 회수 송장 등록을 단건 조회로 확인한다(읽기, 보내지 않음)."""

        number = _clean(invoice_number)
        if not valid_receipt_id(receipt_id) or number is None:
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        plan = PickupPlan(receipt_id=str(receipt_id).strip(), courier_code=None, invoice_number=number, reg_number=None)
        self._resolve_store(plan, company_id)
        if plan.blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in plan.blockers))
        result = self._actions_factory(self.db).check_return_pickup(
            company_id=company_id, store_connection_id=plan.store_connection_id, receipt_id=plan.receipt_id, invoice_number=number,
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangReturnPickupService", "PickupPlan", "PickupBlocker", "courier_code"]
