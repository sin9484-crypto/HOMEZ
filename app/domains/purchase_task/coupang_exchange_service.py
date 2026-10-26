"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_exchange_service.py

2026-10-27 — 쿠팡 쓰기 8번: **교환 요청 거부(매진·고객 철회)와 교환 회수 송장 등록**(외부 변경). 반품 처리와 같은 틀·같은 허용("쿠팡 반품 처리")을 쓴다.
이 모듈이 지키는 것:

  1) 교환 상태는 쿠팡 교환 목록(최근 7일 미만 — 단건 조회 API가 없다)에서 그 자리에서 찾는다. 못 찾으면 보내지 않고 윙으로 안내한다.
     미리보기에서 본 상태(거부는 교환 상태, 회수 송장은 회수 상태)와 지금 상태가 다르면 보내지 않는다.
  2) 거부 — 사유는 쿠팡이 받는 두 가지뿐이다: SOLDOUT(교환할 상품 매진)·WITHDRAW(고객이 교환 철회). 쿠팡 목록의 거부 가능 표시(rejectable)가
     참이고 교환이 열려 있을(접수·진행) 때만 보낸다. 거부하면 고객 교환이 이뤄지지 않는다 — 사용자가 따로 확인(`confirm_reject`)해야 보낸다.
  3) 회수 송장 — 회수상태가 회수연동전(BeforeDirection)이고 회수 송장이 아직 없을 때만 보낸다(공식). 택배사는 공식 이름표로만 코드로 바꾼다.
     직접 회수 확인(`confirm_pickup`)이 있어야 보낸다.
  4) 안전 — 비상 정지·운영자 승인 모드, 승인 센터 허용이 없으면 읽지도 보내지도 않는다. 한 교환에 동작마다 한 번만(작업 장부).
  5) 만들지 않은 것: 교환상품 입고 확인·교환상품 송장 업로드(공식 문서에 처리 조건·요청 본문이 없다 — 윙에서 처리).
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
from app.domains.purchase_task.coupang_exchange_provider import OPEN_EXCHANGE_STATUSES
from app.domains.purchase_task.coupang_exchange_provider import PICKUP_COLLECT_STATUS
from app.domains.purchase_task.coupang_exchange_provider import REJECT_CODES
from app.domains.purchase_task.coupang_exchange_provider import valid_exchange_id
from app.domains.purchase_task.coupang_return_pickup_service import courier_code
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

REJECT = "REJECT"
PICKUP = "PICKUP"
ACTIONS = (REJECT, PICKUP)
STATUS_LABELS_KO = {"RECEIPT": "교환 접수", "PROGRESS": "교환 진행", "SUCCESS": "교환 완료", "REJECT": "교환 거부", "CANCEL": "교환 철회"}


def _clean(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass
class ExBlocker:
    code: str
    message: str


@dataclass
class ExchangePlan:
    exchange_id: str
    action: str
    reject_code: str | None = None
    courier_code: str | None = None
    invoice_number: str | None = None
    blockers: list[ExBlocker] = field(default_factory=list)
    gate_blockers: list[ExBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    exchange: dict | None = None
    already_applied: bool = False
    live_checked: bool = False

    @property
    def watched_status(self) -> str | None:
        """미리보기와 전송 사이에 같아야 하는 값 — 거부는 교환 상태, 회수 송장은 회수 상태."""

        if self.exchange is None:
            return None
        return self.exchange["status"] if self.action == REJECT else self.exchange["collect_status"]

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_applied

    def to_dict(self) -> dict:
        return {
            "exchange_id": self.exchange_id, "action": self.action, "reject_code": self.reject_code, "courier_code": self.courier_code,
            "invoice_number": self.invoice_number, "sendable": self.sendable, "live_checked": self.live_checked,
            "already_applied": self.already_applied, "watched_status": self.watched_status,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "exchange": self.exchange,
        }


class CoupangExchangeService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 열린 교환 목록(읽기)
    # ------------------------------------------------------------

    def open_exchanges(self, company_id: int) -> dict:
        store_id, blockers = self._store(company_id)
        if blockers:
            return {"ok": False, "rows": [], "blockers": [b.__dict__ for b in blockers]}
        read, failure = self._actions_factory(self.db).read_exchange_list(company_id=company_id, store_connection_id=store_id)
        if read is None or not read.ok:
            detail = failure.detail if failure is not None else (read.detail if read is not None else "")
            code = "EXTERNAL_CALL_NOT_ALLOWED" if detail.startswith("EXTERNAL_") else "EXCHANGE_READ_FAILED"
            return {"ok": False, "rows": [], "blockers": [{"code": code, "message": detail or "교환 요청을 읽지 못했다."}]}
        rows = [r for r in read.rows if r["status"] in OPEN_EXCHANGE_STATUSES]
        return {"ok": True, "rows": rows, "truncated": read.truncated, "blockers": []}

    # ------------------------------------------------------------
    # 계획(미리보기)
    # ------------------------------------------------------------

    def plan(
        self, company_id: int, exchange_id: Any, action: Any, *, reject_code: Any = None, courier: Any = None,
        invoice_number: Any = None, live: bool = True,
    ) -> ExchangePlan:
        target = str(exchange_id or "").strip()
        plan = ExchangePlan(exchange_id=target, action=action if action in ACTIONS else str(action))
        if not valid_exchange_id(exchange_id):
            plan.blockers.append(ExBlocker("INVALID_EXCHANGE_ID", "교환 접수번호는 숫자여야 한다."))
        if action == REJECT:
            plan.reject_code = reject_code if reject_code in REJECT_CODES else None
            if plan.reject_code is None:
                plan.blockers.append(ExBlocker("INVALID_REJECT_CODE", "거부 사유는 매진(SOLDOUT)이나 고객 철회(WITHDRAW)만 고를 수 있다."))
        elif action == PICKUP:
            plan.courier_code = courier_code(courier)
            plan.invoice_number = _clean(invoice_number)
            if plan.courier_code is None:
                plan.blockers.append(ExBlocker("UNKNOWN_COURIER", "택배사가 쿠팡 공식 목록에 없다(예: CJ대한통운·한진택배·롯데택배·로젠택배·우체국택배)."))
            number = plan.invoice_number
            if number is None or not (6 <= len(number) <= 30 and all(c.isalnum() or c == "-" for c in number)):
                plan.blockers.append(ExBlocker("INVALID_INVOICE_NUMBER", "회수 송장번호는 6~30자의 영문·숫자·하이픈이어야 한다."))
        else:
            plan.blockers.append(ExBlocker("INVALID_ACTION", "교환 처리는 거부(REJECT)나 회수 송장 등록(PICKUP)만 할 수 있다."))
        store_id, store_blockers = self._store(company_id)
        plan.store_connection_id = store_id
        plan.blockers.extend(store_blockers)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _store(self, company_id: int) -> tuple[int | None, list[ExBlocker]]:
        stores = (
            self.db.query(StoreConnection)
            .filter(
                StoreConnection.company_id == company_id, StoreConnection.marketplace_code == "COUPANG",
                StoreConnection.connection_status == ConnectionStatus.CONNECTED,
            )
            .all()
        )
        if len(stores) == 1:
            return stores[0].id, []
        if not stores:
            return None, [ExBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다.")]
        return None, [ExBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다.")]

    def _gate_checks(self, plan: ExchangePlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(ExBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(ExBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _live_checks(self, plan: ExchangePlan, company_id: int) -> None:
        found, failure = self._actions_factory(self.db).find_exchange(
            company_id=company_id, store_connection_id=plan.store_connection_id, exchange_id=plan.exchange_id,
        )
        if found is None or not found.ok:
            detail = failure.detail if failure is not None else (found.detail if found is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(ExBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(ExBlocker("EXCHANGE_READ_FAILED", f"쿠팡 교환을 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        if not found.found:
            message = "최근 7일 교환에서 이 번호를 찾지 못했다 — 오래된 교환은 쿠팡 윙에서 처리해 주세요."
            if found.truncated:
                message += " (교환이 많아 목록 일부만 읽었다)"
            plan.blockers.append(ExBlocker("EXCHANGE_NOT_FOUND", message))
            return
        plan.live_checked = True
        row = found.row
        plan.exchange = row
        if plan.action == REJECT:
            if row["status"] == "REJECT":
                plan.already_applied = True
                return
            if row["status"] not in OPEN_EXCHANGE_STATUSES or not row["rejectable"]:
                plan.blockers.append(ExBlocker(
                    "NOT_REJECTABLE",
                    f"쿠팡이 이 교환({STATUS_LABELS_KO.get(row['status'], row['status'])})을 거부할 수 없는 상태로 알려 보내지 않는다.",
                ))
            return
        invoices = [i["invoice"] for i in row["return_invoices"]]
        if plan.invoice_number in invoices:
            plan.already_applied = True
            return
        if invoices:
            plan.blockers.append(ExBlocker(
                "HAS_OTHER_INVOICE", f"이 교환에는 이미 회수 송장({', '.join(invoices)})이 붙어 있어 보내지 않는다 — 바꾸려면 쿠팡 윙에서 처리해 주세요.",
            ))
            return
        if row["collect_status"] != PICKUP_COLLECT_STATUS:
            plan.blockers.append(ExBlocker(
                "WRONG_COLLECT_STATUS", f"회수 상태가 '{row['collect_status'] or '알 수 없음'}'라 보내지 않는다(회수 송장은 '회수연동전' 상태에서만 등록한다).",
            ))

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, exchange_id: Any, action: str, *, reject_code: Any = None, courier: Any = None,
        invoice_number: Any = None, expected_status: str | None, confirm: bool = False, retry_action_required: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> dict:
        plan = self.plan(company_id, exchange_id, action, reject_code=reject_code, courier=courier, invoice_number=invoice_number, live=True)
        if plan.already_applied:
            return {"outcome": "ALREADY_DONE", "detail": "쿠팡에 이미 반영돼 있어 보내지 않았다.", "executed": False, "verified": True,
                    "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("교환을 처리할 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if not isinstance(expected_status, str) or expected_status != plan.watched_status:
            raise ConflictException("화면에서 확인한 교환 상태와 쿠팡의 지금 상태가 다르다 — 다시 확인해 주세요. 보내지 않았다.")
        if confirm is not True:
            raise BadRequestException(
                ("거부하면 고객 교환이 이뤄지지 않는다" if action == REJECT else "직접 회수를 맡긴 송장인지")
                + " — 이 점을 따로 확인해 주세요. 보내지 않았다.",
            )
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        if action == REJECT:
            result = actions.reject_exchange_request(
                company_id=company_id, store_connection_id=plan.store_connection_id, exchange_id=plan.exchange_id,
                reject_code=plan.reject_code, triggered_by=user_id, retry_action_required=retry_action_required,
            )
            description = f"쿠팡 교환 거부 {result.outcome}: 사유 {plan.reject_code}"
        else:
            result = actions.register_exchange_pickup(
                company_id=company_id, store_connection_id=plan.store_connection_id, exchange_id=plan.exchange_id,
                courier_code=plan.courier_code, invoice_number=plan.invoice_number, triggered_by=user_id,
                retry_action_required=retry_action_required,
            )
            description = f"쿠팡 교환 회수 송장 등록 {result.outcome}: {plan.courier_code} {plan.invoice_number}"
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_EXCHANGE_" + action, entity="coupang_exchange_request", entity_id=plan.exchange_id,
            description=description, company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 교환을 다시 찾아 확인한다(읽기 1회 이상 — 목록). 찾지 못하면 확인 안 됨(None).
            found, _ = actions.find_exchange(company_id=company_id, store_connection_id=plan.store_connection_id, exchange_id=plan.exchange_id)
            if found is not None and found.ok and found.found:
                if action == REJECT:
                    verified = found.row["status"] == "REJECT"
                else:
                    verified = any(i["invoice"] == plan.invoice_number for i in found.row["return_invoices"])
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "plan": plan.to_dict()}

    def reconcile(self, company_id: int, exchange_id: Any, action: str, invoice_number: Any = None) -> dict:
        """결과불명(UNKNOWN)인 교환 처리를 교환 목록으로 확인한다(읽기, 보내지 않음)."""

        number = _clean(invoice_number)
        if not valid_exchange_id(exchange_id) or action not in ACTIONS or (action == PICKUP and number is None):
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        store_id, blockers = self._store(company_id)
        if blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in blockers))
        actions = self._actions_factory(self.db)
        target = str(exchange_id).strip()
        if action == REJECT:
            result = actions.check_exchange_reject(company_id=company_id, store_connection_id=store_id, exchange_id=target)
        else:
            result = actions.check_exchange_pickup(company_id=company_id, store_connection_id=store_id, exchange_id=target, invoice_number=number)
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangExchangeService", "ExchangePlan", "ExBlocker", "ACTIONS", "REJECT", "PICKUP", "STATUS_LABELS_KO"]
