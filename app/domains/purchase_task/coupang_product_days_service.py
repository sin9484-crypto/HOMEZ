"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_product_days_service.py

2026-10-15 — 이미 쿠팡에 등록된 상품의 **기준 출고일(outboundShippingTimeDay)** 을 바꾸는 업무 규칙(외부 변경).
무재고 중개라 공급처가 주문을 받은 다음 날 출고하므로 등록 상품의 1일(당일출고)을 2일로 바로잡는 용도다. 이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 바꿀 일수는 사용자가 정하고(1~14일), 현재 값·상태는 **쿠팡 상품 조회로 그 자리에서 읽는다**.
     사용자가 화면에서 본 현재 값이 지금 값과 다르면 보내지 않는다.
  2) 전송 전 조건 — 쿠팡에서 상품 상태가 승인완료(APPROVED)여야 한다(임시저장중·승인대기중 상품은 쿠팡이 수정을 받지 않는다).
     모든 옵션이 이미 그 값이면 보내지 않는다. 옵션마다 값이 다르면 그대로 알리고 사용자가 확인한 값 목록과 같을 때만 보낸다.
  3) 안전 — 비상 정지가 켜져 있거나 기능 모드가 "운영자 승인"이 아니면 보내지 않는다.
  4) 중복 방지·결과불명 — 작업 장부(`ChannelActionRequest`)가 전담한다(대상 키에 일수가 들어 있어 다른 값으로의 변경은 새 요청).
     전송이 성공해도 **상품을 다시 읽어** 실제로 바뀌었는지 확인해 알려 준다.
  5) 쿠팡 응답 모양은 실제로 받아 본 적이 없다 — 성공 판정은 최상위 code=SUCCESS와 사후 조회 두 가지로 확인한다.
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
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.marketplace_listing.coupang_live_provider import CANONICAL_STATUS_MAP
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import MAX_OUTBOUND_DAYS
from app.domains.purchase_task.coupang_channel_control_provider import MIN_OUTBOUND_DAYS
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")


@dataclass
class DaysBlocker:
    code: str
    message: str


@dataclass
class DaysPlan:
    seller_product_id: str
    days: int
    blockers: list[DaysBlocker] = field(default_factory=list)
    gate_blockers: list[DaysBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    coupang_status: str | None = None
    current_days: list[int] = field(default_factory=list)
    already_applied: bool = False
    live_checked: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_applied

    def to_dict(self) -> dict:
        return {
            "seller_product_id": self.seller_product_id, "days": self.days, "sendable": self.sendable,
            "live_checked": self.live_checked, "already_applied": self.already_applied,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "coupang": {"status": self.coupang_status, "current_days": self.current_days},
            "ledger": self.ledger,
        }


class CoupangProductDaysService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 계획(미리보기) — 로컬 판정 + 쿠팡 상품 읽기 1회
    # ------------------------------------------------------------

    def plan(self, company_id: int, seller_product_id: str, days: int, *, live: bool = True) -> DaysPlan:
        product = str(seller_product_id or "").strip()
        plan = DaysPlan(seller_product_id=product, days=days)
        if not product.isdigit():
            plan.blockers.append(DaysBlocker("INVALID_PRODUCT_ID", "쿠팡 등록상품번호는 숫자여야 한다."))
        if isinstance(days, bool) or not isinstance(days, int) or not MIN_OUTBOUND_DAYS <= days <= MAX_OUTBOUND_DAYS:
            plan.blockers.append(DaysBlocker(
                "INVALID_DAYS", f"기준 출고일은 {MIN_OUTBOUND_DAYS}~{MAX_OUTBOUND_DAYS}일의 정수여야 한다.",
            ))
        self._resolve_store(plan, company_id)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.ledger = self._ledger_summary(company_id, plan)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _resolve_store(self, plan: DaysPlan, company_id: int) -> None:
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
            plan.blockers.append(DaysBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다."))
        else:
            plan.blockers.append(DaysBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."))

    def _gate_checks(self, plan: DaysPlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(DaysBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(DaysBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: DaysPlan) -> dict | None:
        actions = self._actions_factory(self.db)
        row = actions._journal.get(
            company_id, ChannelActionType.PRODUCT_OUTBOUND_DAYS, actions.product_days_key(plan.seller_product_id, plan.days),
        )
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: DaysPlan, company_id: int) -> None:
        snapshot, failure = self._actions_factory(self.db).read_product_days(
            company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id,
        )
        if snapshot is None or not snapshot.ok:
            detail = failure.detail if failure is not None else (snapshot.detail if snapshot is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(DaysBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(DaysBlocker("PRODUCT_READ_FAILED", f"쿠팡 상품을 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        plan.live_checked = True
        plan.coupang_status = snapshot.status_name
        plan.current_days = list(snapshot.days)
        if CANONICAL_STATUS_MAP.get(str(snapshot.status_name or "").strip()) != "APPROVED":
            plan.blockers.append(DaysBlocker(
                "STATUS_NOT_ALLOWED", f"쿠팡 상품 상태가 승인완료가 아니라 수정할 수 없다(현재: {snapshot.status_name or '알 수 없음'}).",
            ))
            return
        if all(d == plan.days for d in plan.current_days):
            plan.already_applied = True

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, seller_product_id: str, days: int, *,
        expected_current_days: list[int], retry_action_required: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> dict:
        plan = self.plan(company_id, seller_product_id, days, live=True)
        if plan.already_applied:
            return {"outcome": "ALREADY_DONE", "detail": "쿠팡 상품이 이미 그 기준 출고일이라 보내지 않았다.", "executed": False, "verified": True, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("기준 출고일을 바꿀 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if list(expected_current_days) != plan.current_days:
            raise ConflictException("화면에서 확인한 현재 값과 쿠팡의 지금 값이 다르다 — 미리보기를 다시 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        result = actions.set_product_outbound_days(
            company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id,
            days=days, triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_PRODUCT_OUTBOUND_DAYS", entity="coupang_product",
            entity_id=plan.seller_product_id,
            description=f"쿠팡 상품 기준 출고일 변경 {result.outcome}: {plan.current_days} → {days}일",
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 상품을 다시 읽어 실제로 바뀌었는지 확인한다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            after, _ = actions.read_product_days(
                company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id,
            )
            if after is not None and after.ok:
                verified = all(d == days for d in after.days)
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "plan": plan.to_dict()}

    def reconcile(self, company_id: int, seller_product_id: str, days: int) -> dict:
        """결과불명(UNKNOWN)인 요청을 쿠팡 상품 조회로 확인한다(읽기 GET 1회, 보내지 않음)."""

        plan = self.plan(company_id, seller_product_id, days, live=False)
        if plan.blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in plan.blockers))
        result = self._actions_factory(self.db).check_product_outbound_days(
            company_id=company_id, store_connection_id=plan.store_connection_id,
            seller_product_id=plan.seller_product_id, days=days,
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangProductDaysService", "DaysPlan", "DaysBlocker"]
