"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_stock_service.py

2026-10-24 — 쿠팡 쓰기 1번(사용자 지시 "1번부터 시작"): **옵션별 재고 수량 변경**(외부 변경).
출고일 변경(`coupang_product_days_service.py`)과 같은 틀을 따른다. 이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 바꿀 수량은 사용자가 정하고(0~99,999), 현재 재고는 **쿠팡 재고 조회로 그 자리에서 읽는다**.
     사용자가 미리보기에서 본 현재 재고가 지금 값과 다르면(그사이 주문 등) 보내지 않는다.
  2) **0으로 바꾸면 그 옵션은 바로 품절처럼 팔리지 않는다** — 사용자가 0임을 따로 확인(`confirm_zero`)해야만 보낸다.
  3) 안전 — 비상 정지가 켜져 있거나 기능 모드가 "운영자 승인"이 아니면 보내지 않는다. 바깥 호출 허용(승인 센터의 "쿠팡 재고 수량 변경")이
     없으면 읽지도 보내지도 않는다(읽기·쓰기 각각 횟수형).
  4) 중복 방지·결과불명 — 작업 장부가 전담한다. 재고는 같은 수량으로 다시 맞추는 일이 흔해 **사용자 요청마다 참조값**(`request_ref`,
     화면이 미리보기마다 새로 만든다)을 대상 키에 넣는다. 같은 요청을 두 번 보내면 막고, 결과불명은 재고 조회로만 확인한다.
  5) 전송이 성공해도 **재고를 다시 읽어** 실제로 바뀌었는지 알려 준다. 그사이 주문으로 줄었으면 다르게 보일 수 있어 그 가능성을 함께 알린다.
  6) 쿠팡 응답 모양은 공식 문서 예시로만 안다(실제로 받아 본 적 없음) — 성공 판정은 최상위 code=SUCCESS와 사후 조회 두 가지로 확인한다.
=========================================================
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field
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
from app.domains.purchase_task.coupang_channel_control_provider import MAX_STOCK_QUANTITY
from app.domains.purchase_task.coupang_channel_control_provider import MAX_VENDOR_ITEM_ID_DIGITS
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

REQUEST_REF = re.compile(r"^[A-Za-z0-9-]{8,40}$")


@dataclass
class StockBlocker:
    code: str
    message: str


@dataclass
class StockPlan:
    vendor_item_id: str
    quantity: int
    request_ref: str | None = None
    blockers: list[StockBlocker] = field(default_factory=list)
    gate_blockers: list[StockBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    current_stock: int | None = None
    sale_price: int | None = None
    on_sale: bool | None = None
    already_applied: bool = False
    live_checked: bool = False
    zero_warning: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_applied

    def to_dict(self) -> dict:
        return {
            "vendor_item_id": self.vendor_item_id, "quantity": self.quantity, "request_ref": self.request_ref, "sendable": self.sendable,
            "live_checked": self.live_checked, "already_applied": self.already_applied, "zero_warning": self.zero_warning,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "coupang": {"current_stock": self.current_stock, "sale_price": self.sale_price, "on_sale": self.on_sale},
            "ledger": self.ledger, "max_quantity": MAX_STOCK_QUANTITY,
        }


class CoupangStockService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 계획(미리보기) — 로컬 판정 + 쿠팡 재고 읽기 1회
    # ------------------------------------------------------------

    def plan(self, company_id: int, vendor_item_id: str, quantity: int, *, request_ref: str | None = None, live: bool = True) -> StockPlan:
        item = str(vendor_item_id or "").strip()
        plan = StockPlan(vendor_item_id=item, quantity=quantity, request_ref=request_ref)
        if not item.isdigit() or len(item) > MAX_VENDOR_ITEM_ID_DIGITS:
            plan.blockers.append(StockBlocker("INVALID_VENDOR_ITEM_ID", "쿠팡 옵션번호(옵션 ID)는 숫자여야 한다."))
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 <= quantity <= MAX_STOCK_QUANTITY:
            plan.blockers.append(StockBlocker("INVALID_QUANTITY", f"재고 수량은 0~{MAX_STOCK_QUANTITY:,}의 정수여야 한다."))
        elif quantity == 0:
            plan.zero_warning = True
        if request_ref is not None and not REQUEST_REF.match(str(request_ref)):
            plan.blockers.append(StockBlocker("INVALID_REQUEST_REF", "요청 참조값 형식이 올바르지 않다(미리보기를 다시 열어 주세요)."))
        self._resolve_store(plan, company_id)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.ledger = self._ledger_summary(company_id, plan)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _resolve_store(self, plan: StockPlan, company_id: int) -> None:
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
            plan.blockers.append(StockBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다."))
        else:
            plan.blockers.append(StockBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."))

    def _gate_checks(self, plan: StockPlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(StockBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(StockBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: StockPlan) -> dict | None:
        if plan.request_ref is None:
            return None
        actions = self._actions_factory(self.db)
        row = actions._journal.get(company_id, ChannelActionType.STOCK_UPDATE, actions.stock_key(plan.vendor_item_id, plan.quantity, plan.request_ref))
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: StockPlan, company_id: int) -> None:
        snapshot, failure = self._actions_factory(self.db).read_stock(
            company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=plan.vendor_item_id,
        )
        if snapshot is None or not snapshot.ok:
            detail = failure.detail if failure is not None else (snapshot.detail if snapshot is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(StockBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(StockBlocker("STOCK_READ_FAILED", f"쿠팡 재고를 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        plan.live_checked = True
        plan.current_stock = snapshot.amount_in_stock
        plan.sale_price = snapshot.sale_price
        plan.on_sale = snapshot.on_sale
        if plan.current_stock == plan.quantity:
            plan.already_applied = True

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, vendor_item_id: str, quantity: int, *,
        expected_current_stock: int, request_ref: str, confirm_zero: bool = False, retry_action_required: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> dict:
        if not REQUEST_REF.match(str(request_ref or "")):
            raise BadRequestException("요청 참조값 형식이 올바르지 않다 — 미리보기를 다시 열어 주세요. 보내지 않았다.")
        plan = self.plan(company_id, vendor_item_id, quantity, request_ref=request_ref, live=True)
        if plan.already_applied:
            return {"outcome": "ALREADY_DONE", "detail": "쿠팡 재고가 이미 그 수량이라 보내지 않았다.", "executed": False, "verified": True,
                    "after_stock": plan.current_stock, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("재고를 바꿀 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if isinstance(expected_current_stock, bool) or expected_current_stock != plan.current_stock:
            raise ConflictException("화면에서 확인한 현재 재고와 쿠팡의 지금 재고가 다르다(그사이 주문 등) — 미리보기를 다시 확인해 주세요. 보내지 않았다.")
        if plan.zero_warning and confirm_zero is not True:
            raise BadRequestException("재고를 0으로 바꾸면 이 옵션은 바로 팔리지 않는다 — 0으로 바꾸는 것을 따로 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        result = actions.set_stock(
            company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=plan.vendor_item_id,
            quantity=quantity, request_ref=request_ref, triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_STOCK_UPDATE", entity="coupang_vendor_item", entity_id=plan.vendor_item_id,
            description=f"쿠팡 옵션 재고 수량 변경 {result.outcome}: {plan.current_stock} → {quantity}",
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        after_stock: int | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 재고를 다시 읽는다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            after, _ = actions.read_stock(company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=plan.vendor_item_id)
            if after is not None and after.ok:
                after_stock = after.amount_in_stock
                verified = after_stock == quantity
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "after_stock": after_stock,
                "plan": plan.to_dict()}

    def reconcile(self, company_id: int, vendor_item_id: str, quantity: int, request_ref: str) -> dict:
        """결과불명(UNKNOWN)인 요청을 쿠팡 재고 조회로 확인한다(읽기 GET 1회, 보내지 않음)."""

        if not REQUEST_REF.match(str(request_ref or "")):
            raise BadRequestException("요청 참조값 형식이 올바르지 않다.")
        plan = self.plan(company_id, vendor_item_id, quantity, request_ref=request_ref, live=False)
        if plan.blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in plan.blockers))
        result = self._actions_factory(self.db).check_stock(
            company_id=company_id, store_connection_id=plan.store_connection_id, vendor_item_id=plan.vendor_item_id,
            quantity=quantity, request_ref=request_ref,
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangStockService", "StockPlan", "StockBlocker", "REQUEST_REF"]
