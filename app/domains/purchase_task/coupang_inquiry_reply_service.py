"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_inquiry_reply_service.py

2026-10-25 — 쿠팡 쓰기 4번: **상품 문의 답변 등록**(외부 변경 — 고객에게 바로 보이고, 고칠 수 있는지 공식 문서에 없다).
재고·판매가·상품명 변경과 같은 틀을 따른다. 이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 답변 글은 사람이 쓴다(고객 응대 답변 초안을 쓰더라도 사람이 고친 글만). 초안의 가림 표시([전화번호] 등)가
     남아 있으면 보내지 않는다. 문의 상태는 쿠팡 문의 조회로 그 자리에서 읽는다(최근 7일 — 쿠팡 조회 한도).
  2) **한 문의에 한 번만** — 작업 장부의 대상 키가 문의 번호뿐이다(같은 문의에 HOMEZ가 두 번 답하지 않는다). 쿠팡에 이미 답이 달려 있으면
     (윙에서 답했을 수도 있다) 보내지 않는다.
  3) 고객에게 바로 보인다는 점을 사용자가 따로 확인(`confirm_public`)해야 보낸다. 답변자(replyBy)는 자격증명(없으면 판매자 고정값 — `coupang_seller_wing.py`)의 WING 아이디만 쓴다.
  4) 안전 — 비상 정지가 켜져 있거나 기능 모드가 "운영자 승인"이 아니면 보내지 않는다. 승인 센터의 "쿠팡 문의 답변" 허용이 없으면
     읽지도 보내지도 않는다(읽기·쓰기 각각 횟수형).
  5) 결과불명은 문의 조회로만 확인한다 — 우리 답이 붙었으면 성공, 답이 없으면 안 받음, 다른 답만 있으면 판단하지 않는다.
  6) 문의 글·답변 글은 저장하지 않는다. 감사 기록에는 문의 번호·결과·글자 수만 남긴다.
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
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.order.cs_reply_draft import PLACEHOLDERS
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_seller_wing import actions_with_seller_wing
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_inquiry_reply_provider import MAX_REPLY_CHARS
from app.domains.purchase_task.coupang_inquiry_reply_provider import normalize_reply
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

MAX_INQUIRY_ID_DIGITS = 20
DRAFT_FOOTER_MARK = "※ 초안입니다"


def reply_problem(content: Any) -> str | None:
    """답변 글 검사 — 문제가 있으면 막는 이유 코드(고쳐 쓰지 않는다)."""

    if not isinstance(content, str):
        return "INVALID_CONTENT"
    text = normalize_reply(content)
    if not text or len(text) > MAX_REPLY_CHARS:
        return "INVALID_CONTENT"
    if any(mark in text for mark in PLACEHOLDERS.values()) or DRAFT_FOOTER_MARK in text:
        return "DRAFT_MARK_LEFT"
    return None


@dataclass
class ReplyBlocker:
    code: str
    message: str


@dataclass
class ReplyPlan:
    inquiry_id: str
    content_chars: int = 0
    blockers: list[ReplyBlocker] = field(default_factory=list)
    gate_blockers: list[ReplyBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    inquiry: dict | None = None
    already_answered: bool = False
    live_checked: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_answered

    def to_dict(self) -> dict:
        return {
            "inquiry_id": self.inquiry_id, "content_chars": self.content_chars, "sendable": self.sendable,
            "live_checked": self.live_checked, "already_answered": self.already_answered,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "inquiry": self.inquiry, "ledger": self.ledger, "max_chars": MAX_REPLY_CHARS,
        }


class CoupangInquiryReplyService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or actions_with_seller_wing
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 답하지 않은 문의 목록(읽기)
    # ------------------------------------------------------------

    def unanswered(self, company_id: int) -> dict:
        store_id, blockers = self._store(company_id)
        if blockers:
            return {"ok": False, "rows": [], "blockers": [b.__dict__ for b in blockers]}
        read, failure = self._actions_factory(self.db).read_inquiries(company_id=company_id, store_connection_id=store_id)
        if read is None or not read.ok:
            detail = failure.detail if failure is not None else (read.detail if read is not None else "")
            code = "EXTERNAL_CALL_NOT_ALLOWED" if detail.startswith("EXTERNAL_") else "INQUIRY_READ_FAILED"
            return {"ok": False, "rows": [], "blockers": [{"code": code, "message": detail or "문의를 읽지 못했다."}]}
        rows = [{k: v for k, v in r.items() if k != "comments"} for r in read.rows if not r["answered"]]
        return {"ok": True, "rows": rows, "truncated": read.truncated, "blockers": []}

    # ------------------------------------------------------------
    # 계획(미리보기)
    # ------------------------------------------------------------

    def plan(self, company_id: int, inquiry_id: Any, content: Any, *, live: bool = True) -> ReplyPlan:
        target = str(inquiry_id or "").strip()
        plan = ReplyPlan(inquiry_id=target, content_chars=len(normalize_reply(content)) if isinstance(content, str) else 0)
        if not target.isdigit() or len(target) > MAX_INQUIRY_ID_DIGITS or isinstance(inquiry_id, bool):
            plan.blockers.append(ReplyBlocker("INVALID_INQUIRY_ID", "문의 번호는 숫자여야 한다."))
        problem = reply_problem(content)
        if problem == "INVALID_CONTENT":
            plan.blockers.append(ReplyBlocker(problem, f"답변은 1~{MAX_REPLY_CHARS:,}자로 써야 한다."))
        elif problem == "DRAFT_MARK_LEFT":
            plan.blockers.append(ReplyBlocker(problem, "초안의 가림 표시([전화번호] 등)나 초안 안내 문구가 남아 있다 — 고친 뒤 보내야 한다."))
        store_id, store_blockers = self._store(company_id)
        plan.store_connection_id = store_id
        plan.blockers.extend(store_blockers)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.ledger = self._ledger_summary(company_id, plan)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _store(self, company_id: int) -> tuple[int | None, list[ReplyBlocker]]:
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
            return None, [ReplyBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다.")]
        return None, [ReplyBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다.")]

    def _gate_checks(self, plan: ReplyPlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(ReplyBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(ReplyBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: ReplyPlan) -> dict | None:
        actions = self._actions_factory(self.db)
        row = actions._journal.get(company_id, ChannelActionType.INQUIRY_REPLY, actions.inquiry_reply_key(plan.inquiry_id))
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: ReplyPlan, company_id: int) -> None:
        found, failure = self._actions_factory(self.db).find_inquiry(
            company_id=company_id, store_connection_id=plan.store_connection_id, inquiry_id=plan.inquiry_id,
        )
        if found is None or not found.ok:
            detail = failure.detail if failure is not None else (found.detail if found is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(ReplyBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(ReplyBlocker("INQUIRY_READ_FAILED", f"쿠팡 문의를 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        if not found.found:
            message = "최근 7일 상품 문의에서 이 번호를 찾지 못했다 — 오래된 문의는 쿠팡 윙에서 답해 주세요."
            if found.truncated:
                message += " (문의가 많아 목록 일부만 읽었다)"
            plan.blockers.append(ReplyBlocker("INQUIRY_NOT_FOUND", message))
            return
        if not found.reply_ready:
            plan.blockers.append(ReplyBlocker(
                "MISSING_WING_USER_ID", "쿠팡 연결 정보와 판매자 고정값에 WING 아이디가 없어(또는 사용자마다 달라) 답변자를 정할 수 없다 — 상품 등록 5단계에서 WING 아이디를 고정값으로 저장해 주세요.",
            ))
        plan.live_checked = True
        row = found.row
        plan.inquiry = {k: row[k] for k in ("inquiry_id", "seller_product_id", "vendor_item_id", "inquiry_at", "question", "question_clipped", "order_count")}
        plan.inquiry["answer_count"] = len(row["comments"])
        if row["answered"]:
            plan.already_answered = True

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, inquiry_id: Any, content: str, *, confirm_public: bool = False,
        retry_action_required: bool = False, before_send: Callable[[], None] | None = None,
    ) -> dict:
        plan = self.plan(company_id, inquiry_id, content, live=True)
        if plan.already_answered:
            return {"outcome": "ALREADY_ANSWERED", "detail": "쿠팡에 이미 답변이 달려 있어 보내지 않았다(윙에서 확인해 주세요).",
                    "executed": False, "verified": None, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked:
            raise BadRequestException("답변을 보낼 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if confirm_public is not True:
            raise BadRequestException("답변은 고객에게 바로 보이고 고칠 수 있는지 알 수 없다 — 이 점을 따로 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        text = normalize_reply(content)
        actions = self._actions_factory(self.db)
        result = actions.reply_inquiry(
            company_id=company_id, store_connection_id=plan.store_connection_id, inquiry_id=plan.inquiry_id, content=text,
            triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_INQUIRY_REPLY", entity="coupang_online_inquiry", entity_id=plan.inquiry_id,
            description=f"쿠팡 상품 문의 답변 {result.outcome}: {len(text)}자",
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 문의를 다시 읽어 우리 답이 붙었는지 본다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            found, _ = actions.find_inquiry(company_id=company_id, store_connection_id=plan.store_connection_id, inquiry_id=plan.inquiry_id)
            if found is not None and found.ok and found.found:
                verified = any(normalize_reply(c["content"]) == text for c in found.row["comments"])
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "plan": plan.to_dict()}

    def reconcile(self, company_id: int, inquiry_id: Any, content: str) -> dict:
        """결과불명(UNKNOWN)인 답변을 쿠팡 문의 조회로 확인한다(읽기, 보내지 않음)."""

        target = str(inquiry_id or "").strip()
        if not target.isdigit() or len(target) > MAX_INQUIRY_ID_DIGITS or reply_problem(content) == "INVALID_CONTENT":
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        store_id, blockers = self._store(company_id)
        if blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in blockers))
        result = self._actions_factory(self.db).check_inquiry_reply(
            company_id=company_id, store_connection_id=store_id, inquiry_id=target, content=normalize_reply(content),
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangInquiryReplyService", "ReplyPlan", "ReplyBlocker", "reply_problem"]
