"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_invoice_service.py

2026-10-15 — HOMEZ에서 쿠팡 발주서에 **송장을 등록**하는 업무 규칙(외부 변경). 온채널이 발송한 송장(HOMEZ 구매 작업에
기록한 택배사·송장번호)을 쿠팡 주문에 옮긴다. 이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 택배사 코드는 확인된 택배사 이름에서 고정 표로만 얻고(없으면 막는다), 옵션번호·상태는
     **쿠팡 발주서를 그 자리에서 읽어** 정한다. 보낼 내용은 사용자가 화면에서 본 값과 한 글자도 다르면 보내지 않는다.
  2) 전송 전 조건 — 구매 작업이 송장을 기록한 상태(SHIPPED)이고 택배사가 확인되어 있으며, 분리배송이 아니고, 쿠팡 주문이
     하나의 배송번호·하나의 구매 작업으로 이루어져 있고, 쿠팡 발주서가 상품준비중(INSTRUCT)이어야 한다. 결제완료(ACCEPT)는
     먼저 쿠팡에서 상품준비중으로 바꿔야 한다. 이미 같은 송장이 있으면 보내지 않고 "이미 등록됨"으로 끝낸다.
  3) 안전 — 비상 정지가 켜져 있거나 기능 모드가 "운영자 승인"이 아니면 보내지 않는다(모드·재인증은 호출자가 확인한다).
  4) 중복 방지·결과불명 처리 — 작업 장부(`ChannelActionRequest`)가 전담한다. 결과불명은 자동 재요청하지 않고 발주서의 송장번호로만
     확인한다.

이 모듈은 자격증명·고객 개인정보·송장번호를 오류 문구에 싣지 않는다(장부 `detail`은 쿠팡 응답 요약만).
=========================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Callable

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.coupang_channel_control_provider import INVOICE_UPLOAD_ALLOWED_RAW_STATUS
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo

logger = logging.getLogger("homez")

# 쿠팡 택배사 코드(앱의 5단계 화면이 쓰는 공식 목록과 같다). 이름은 공백을 없애고 소문자로 맞춰 찾는다.
# 목록에 없는 이름은 추측하지 않고 막는다.
COURIER_CODES_BY_NAME: dict[str, str] = {
    "한진택배": "HANJIN", "한진": "HANJIN",
    "롯데택배": "HYUNDAI", "롯데글로벌로지스": "HYUNDAI", "롯데": "HYUNDAI",
    "로젠택배": "KGB", "로젠": "KGB",
    "우체국": "EPOST", "우체국택배": "EPOST",
    "cj대한통운": "CJGLS", "대한통운": "CJGLS", "cj택배": "CJGLS", "cj": "CJGLS",
    "경동택배": "KDEXP", "경동": "KDEXP",
    "일양택배": "ILYANG", "일양로지스": "ILYANG",
    "천일택배": "CHUNIL", "천일": "CHUNIL",
    "아주택배": "AJOU",
    "sc로지스": "CSLOGIS", "sc로지스택배": "CSLOGIS",
    "대신택배": "DAESIN", "대신": "DAESIN",
    "cvs택배": "CVS", "편의점택배": "CVS",
    "합동택배": "HDEXP", "합동": "HDEXP",
    "dhl": "DHL",
}


def courier_code_for(name: str | None) -> str | None:
    key = "".join(str(name or "").split()).lower()
    return COURIER_CODES_BY_NAME.get(key)


@dataclass
class InvoiceBlocker:
    code: str
    message: str


@dataclass
class InvoicePlan:
    task_id: int
    blockers: list[InvoiceBlocker] = field(default_factory=list)
    gate_blockers: list[InvoiceBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    channel_order_id: str | None = None
    shipment_box_id: str | None = None
    courier_name: str | None = None
    courier_code: str | None = None
    invoice_number: str | None = None
    vendor_item_ids: list[str] = field(default_factory=list)
    coupang_status: str | None = None
    coupang_invoice_number: str | None = None
    already_registered: bool = False
    live_checked: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_registered

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "sendable": self.sendable, "live_checked": self.live_checked,
            "already_registered": self.already_registered,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "values": {
                "channel_order_id": self.channel_order_id, "shipment_box_id": self.shipment_box_id,
                "courier_name": self.courier_name, "delivery_company_code": self.courier_code,
                "invoice_number": self.invoice_number, "vendor_item_ids": self.vendor_item_ids,
            },
            "coupang": {"status": self.coupang_status, "invoice_number": self.coupang_invoice_number},
            "ledger": self.ledger,
        }


class CoupangInvoiceService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 계획(미리보기) — 로컬 판정 + 쿠팡 발주서 읽기 1회
    # ------------------------------------------------------------

    def plan(self, task_id: int, company_id: int, *, live: bool = True) -> InvoicePlan:
        task = (
            self.db.query(PurchaseTask)
            .filter(PurchaseTask.id == task_id, PurchaseTask.company_id == company_id)
            .first()
        )
        if task is None:
            raise NotFoundException("구매 작업을 찾을 수 없습니다.")
        plan = InvoicePlan(task_id=task.id)
        self._local_checks(plan, task, company_id)
        self._gate_checks(plan)
        if plan.channel_order_id and plan.shipment_box_id:
            plan.ledger = self._ledger_summary(company_id, plan)
        if live and not plan.blockers:
            self._live_checks(plan, company_id)
        return plan

    def _local_checks(self, plan: InvoicePlan, task: PurchaseTask, company_id: int) -> None:
        def block(code: str, message: str) -> None:
            plan.blockers.append(InvoiceBlocker(code, message))

        tracking = (
            self.db.query(PurchaseTaskTrackingInfo)
            .filter(PurchaseTaskTrackingInfo.purchase_task_id == task.id, PurchaseTaskTrackingInfo.company_id == company_id)
            .first()
        )
        if task.status != PurchaseTaskStatus.SHIPPED or tracking is None:
            block("TRACKING_NOT_RECORDED", "HOMEZ 구매 작업에 송장이 기록되어 있지 않다 — 먼저 송장을 기록해야 한다.")
        if tracking is not None:
            plan.courier_name = (tracking.courier or "").strip() or None
            plan.invoice_number = (tracking.tracking_number or "").strip() or None
            if not plan.invoice_number:
                block("NO_INVOICE_NUMBER", "기록된 송장번호가 없다.")
            if not tracking.courier_confirmed:
                block("COURIER_NOT_CONFIRMED", "택배사가 확인되지 않았다 — 송장번호 형식만으로 택배사를 정하지 않는다.")
            plan.courier_code = courier_code_for(plan.courier_name)
            if plan.courier_name and plan.courier_code is None:
                block("COURIER_NOT_MAPPED", f"택배사 '{plan.courier_name}'에 해당하는 쿠팡 택배사 코드를 모른다 — 추측하지 않는다.")
            if not plan.courier_name:
                block("NO_COURIER", "기록된 택배사가 없다.")
            if tracking.is_partial_shipment:
                block("PARTIAL_SHIPMENT", "분리배송은 지원하지 않는다 — 쿠팡 WING에서 직접 처리해야 한다.")

        if task.source_order_id is None:
            block("NO_COUPANG_ORDER", "이 구매 작업은 쿠팡 주문에서 만들어지지 않았다.")
            return
        fulfillments = (
            self.db.query(OrderChannelFulfillment)
            .filter(OrderChannelFulfillment.order_id == task.source_order_id, OrderChannelFulfillment.company_id == company_id)
            .all()
        )
        if not fulfillments:
            block("NO_COUPANG_ORDER", "이 주문에 연결된 쿠팡 배송번호가 없다.")
            return
        if len(fulfillments) > 1:
            block("MULTIPLE_BOXES", "한 주문에 배송번호가 여러 개다 — 박스별로 확인해야 해서 자동 처리하지 않는다.")
            return
        box = fulfillments[0]
        plan.store_connection_id = box.store_connection_id
        plan.channel_order_id = box.channel_order_id
        plan.shipment_box_id = box.shipment_box_id
        other_tasks = (
            self.db.query(PurchaseTask)
            .filter(PurchaseTask.source_order_id == task.source_order_id, PurchaseTask.company_id == company_id, PurchaseTask.id != task.id)
            .count()
        )
        if other_tasks:
            block("MULTIPLE_TASKS_IN_ORDER", "같은 주문에 구매 작업이 더 있다 — 박스 전체 송장을 한 번에 정해야 해서 자동 처리하지 않는다.")

    def _gate_checks(self, plan: InvoicePlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(InvoiceBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(InvoiceBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: InvoicePlan) -> dict | None:
        actions = self._actions_factory(self.db)
        row = actions._journal.get(company_id, ChannelActionType.INVOICE_UPLOAD, actions.invoice_key(plan.channel_order_id, plan.shipment_box_id))
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: InvoicePlan, company_id: int) -> None:
        actions = self._actions_factory(self.db)
        sheet, failure = actions.read_order_sheet(
            company_id=company_id, store_connection_id=plan.store_connection_id, shipment_box_id=plan.shipment_box_id,
        )
        if sheet is None or not sheet.ok:
            detail = failure.detail if failure is not None else (sheet.detail if sheet is not None else "")
            if detail.startswith("EXTERNAL_"):
                # 허용이 없거나 비상 정지 — 쿠팡을 부르지 않았다. 승인 센터에서 허용하면 다시 확인할 수 있다.
                plan.blockers.append(InvoiceBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(InvoiceBlocker("SHEET_READ_FAILED", f"쿠팡 발주서를 읽지 못했다 — 결과를 확인할 수 없어 보내지 않는다. {detail}".strip()))
            return
        plan.live_checked = True
        plan.coupang_status = sheet.status
        plan.coupang_invoice_number = sheet.invoice_number
        active = [item[0] for item in sheet.items if item[1] - item[2] > 0]
        if sheet.invoice_number:
            if sheet.invoice_number == plan.invoice_number:
                plan.already_registered = True
                return
            plan.blockers.append(InvoiceBlocker(
                "INVOICE_DIFFERS", "쿠팡에 이미 다른 송장번호가 등록되어 있다 — 쿠팡은 송장 재등록을 받지 않으므로 WING에서 확인해야 한다.",
            ))
            return
        if sheet.status == "ACCEPT":
            plan.blockers.append(InvoiceBlocker(
                "NEEDS_PREPARATION", "쿠팡 주문이 결제완료(ACCEPT) 상태다 — 먼저 쿠팡에서 '상품준비중'으로 바꿔야 송장을 등록할 수 있다.",
            ))
        elif sheet.status != INVOICE_UPLOAD_ALLOWED_RAW_STATUS:
            plan.blockers.append(InvoiceBlocker(
                "STATUS_NOT_ALLOWED", f"쿠팡 주문 상태가 '{sheet.status}'라 송장을 등록할 수 없다(상품준비중일 때만 가능).",
            ))
        if not active:
            plan.blockers.append(InvoiceBlocker("NO_ACTIVE_ITEMS", "쿠팡 발주서에 취소되지 않은 품목이 없다."))
        plan.vendor_item_ids = sorted(active)

    # ------------------------------------------------------------
    # 전송 — 사용자가 본 값과 같을 때만, 장부로 한 번만
    # ------------------------------------------------------------

    def send(
        self, task_id: int, company_id: int, user_id: int, *, delivery_company_code: str,
        invoice_number: str, shipment_box_id: str, vendor_item_ids: list[str],
        retry_action_required: bool = False, before_send: Callable[[], None] | None = None,
    ) -> dict:
        """`before_send`: 모든 조건이 통과한 **직후, 외부 요청 직전에만** 호출된다(예: 재인증 토큰 소모 — 거절될 요청에 토큰을
        쓰지 않는다). 예외를 내면 아무것도 보내지 않는다."""

        plan = self.plan(task_id, company_id, live=True)
        if plan.already_registered:
            return {"outcome": "ALREADY_REGISTERED", "detail": "쿠팡에 같은 송장이 이미 등록되어 있다 — 보내지 않았다.", "executed": False, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("송장을 등록할 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        confirmed = (
            str(delivery_company_code).strip(), str(invoice_number).strip(), str(shipment_box_id).strip(),
            sorted(str(v).strip() for v in vendor_item_ids),
        )
        current = (plan.courier_code, plan.invoice_number, plan.shipment_box_id, sorted(plan.vendor_item_ids))
        if confirmed != current:
            raise ConflictException("화면에서 확인한 내용과 지금 내용이 다르다 — 미리보기를 다시 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        result = self._actions_factory(self.db).register_invoice(
            company_id=company_id, store_connection_id=plan.store_connection_id,
            channel_order_id=plan.channel_order_id, shipment_box_id=plan.shipment_box_id,
            vendor_item_ids=plan.vendor_item_ids, delivery_company_code=plan.courier_code,
            invoice_number=plan.invoice_number, triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_INVOICE_UPLOAD", entity="purchase_task", entity_id=str(task_id),
            description=f"쿠팡 송장 등록 {result.outcome}: 배송번호 {plan.shipment_box_id}, 택배사 {plan.courier_code}",
            company_id=company_id,
        )
        self.db.commit()
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "plan": plan.to_dict()}

    def mark_registered(self, task_id: int, company_id: int, user_id: int) -> dict:
        """사용자가 쿠팡 WING에서 **직접 송장을 등록했다고 확인**했을 때 장부에 성공으로 남긴다(쿠팡을 부르지 않는다).
        같은 주문에 다시 송장을 보내지 않게 하고 주문 진행 화면의 "송장" 단계를 끝으로 바꾼다. 송장 기록이 없는 작업은 거절한다."""

        plan = self.plan(task_id, company_id, live=False)
        if plan.blockers:
            raise BadRequestException("송장 등록 여부를 기록할 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        result = self._actions_factory(self.db).record_manual_success(
            company_id=company_id, store_connection_id=plan.store_connection_id, action_type=ChannelActionType.INVOICE_UPLOAD,
            target_key=CoupangChannelActions.invoice_key(plan.channel_order_id, plan.shipment_box_id), triggered_by=user_id,
            detail="사용자가 쿠팡 WING에서 직접 등록했다고 확인함(HOMEZ는 쿠팡에 보내지 않음)",
        )
        if result.outcome == ActionOutcome.SUCCEEDED:
            write_audit_log(
                self.db, user_id=user_id, action="COUPANG_INVOICE_MARKED_REGISTERED", entity="purchase_task",
                entity_id=str(task_id), description=f"쿠팡 송장 직접 등록 확인 기록: 배송번호 {plan.shipment_box_id}",
                company_id=company_id,
            )
            self.db.commit()
        return {"outcome": result.outcome, "detail": result.detail}

    def reconcile(self, task_id: int, company_id: int) -> dict:
        """결과불명(UNKNOWN)인 요청을 쿠팡 발주서의 송장번호로 확인한다(읽기 GET 1회, 보내지 않음)."""

        plan = self.plan(task_id, company_id, live=False)
        if not plan.channel_order_id or not plan.shipment_box_id or not plan.invoice_number:
            raise BadRequestException("확인할 송장 요청 기록이 없다.")
        result = self._actions_factory(self.db).check_invoice_registered(
            company_id=company_id, store_connection_id=plan.store_connection_id,
            channel_order_id=plan.channel_order_id, shipment_box_id=plan.shipment_box_id, invoice_number=plan.invoice_number,
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangInvoiceService", "InvoicePlan", "InvoiceBlocker", "courier_code_for", "COURIER_CODES_BY_NAME"]
