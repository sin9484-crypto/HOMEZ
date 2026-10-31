"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_call_center_reply_service.py

2026-10-26 — 쿠팡 쓰기 6번: **쿠팡 고객센터(콜센터) 이관 문의 답변**(외부 변경 — 고객 상담에 바로 쓰인다).
상품 문의 답변(`coupang_inquiry_reply_service.py`)과 같은 틀이다. 이 모듈이 지키는 것:

  1) 답변 글은 사람이 쓴다(2~1,000자, 초안 가림 표시가 남으면 보내지 않는다). 문의 상태는 단건 조회로 그 자리에서 읽는다.
  2) 답할 이관글은 쿠팡 상담사가 '답변 요청'으로 넘긴 글이 **정확히 하나**일 때만 고른다. 사용자가 미리보기에서 본 이관글과 지금 이관글이
     다르면 보내지 않는다. 문의가 끝났거나(complete) 답할 이관글이 없으면 보내지 않는다(24시간 지나 쿠팡이 닫은 경우 포함 — 윙에서 처리).
  3) 고객에게 바로 쓰인다는 확인(`confirm_public`)이 있어야 보낸다. 답변자는 자격증명(없으면 판매자 고정값 — `coupang_seller_wing.py`)의 WING 아이디만 쓴다.
  4) 안전 — 비상 정지·운영자 승인 모드 확인, 승인 센터의 "쿠팡 문의 답변" 허용(상품 문의 답변과 같은 목적)이 없으면 읽지도 보내지도 않는다.
     쿠팡이 과도한 조회를 막을 수 있어 조회는 미리보기·전송 직전·전송 뒤 1회씩만 한다.
  5) 한 이관글에 한 번만(작업 장부 키 = 상담번호 + 이관글 번호). 결과불명은 단건 조회로만 확인한다.
  6) 문의 글·답변 글은 저장하지 않는다. 감사 기록에는 상담번호·결과·글자 수만 남긴다.
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
from app.domains.purchase_task.coupang_seller_wing import actions_with_seller_wing
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_call_center_reply_provider import MAX_CC_REPLY_CHARS
from app.domains.purchase_task.coupang_call_center_reply_provider import MIN_CC_REPLY_CHARS
from app.domains.purchase_task.coupang_call_center_reply_provider import cc_reply_problem
from app.domains.purchase_task.coupang_call_center_reply_provider import valid_cc_id
from app.domains.purchase_task.coupang_inquiry_reply_provider import normalize_reply
from app.domains.purchase_task.coupang_inquiry_reply_service import reply_problem
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")


def call_center_text_problem(content: Any) -> str | None:
    """공식 길이(2~1,000자)와 초안 가림 표시 검사(상품 문의 답변과 같은 표시 규칙)."""

    problem = cc_reply_problem(content)
    if problem is not None:
        return problem
    return "DRAFT_MARK_LEFT" if reply_problem(content) == "DRAFT_MARK_LEFT" else None


@dataclass
class CcBlocker:
    code: str
    message: str


@dataclass
class CcPlan:
    inquiry_id: str
    content_chars: int = 0
    blockers: list[CcBlocker] = field(default_factory=list)
    gate_blockers: list[CcBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    inquiry: dict | None = None
    parent_answer_id: str | None = None
    already_answered: bool = False
    live_checked: bool = False
    ledger: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_answered

    def to_dict(self) -> dict:
        return {
            "inquiry_id": self.inquiry_id, "content_chars": self.content_chars, "sendable": self.sendable,
            "live_checked": self.live_checked, "already_answered": self.already_answered, "parent_answer_id": self.parent_answer_id,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "inquiry": self.inquiry, "ledger": self.ledger, "min_chars": MIN_CC_REPLY_CHARS, "max_chars": MAX_CC_REPLY_CHARS,
        }


def _public_row(row: dict) -> dict:
    """화면에 보낼 모양 — 이관글 목록 대신 답할 이관글 내용만."""

    keys = ("inquiry_id", "inquiry_status", "counseling_status", "inquiry_at", "order_id", "vendor_item_id", "item_name", "category",
            "question", "pending_count", "pending_answer_id", "pending_text", "agent_text")
    return {k: row.get(k) for k in keys}


class CoupangCallCenterReplyService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or actions_with_seller_wing
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 답하지 않은 고객센터 문의 목록(읽기)
    # ------------------------------------------------------------

    def unanswered(self, company_id: int) -> dict:
        store_id, blockers = self._store(company_id)
        if blockers:
            return {"ok": False, "rows": [], "blockers": [b.__dict__ for b in blockers]}
        read, failure = self._actions_factory(self.db).read_call_center_list(company_id=company_id, store_connection_id=store_id)
        if read is None or not read.ok:
            detail = failure.detail if failure is not None else (read.detail if read is not None else "")
            code = "EXTERNAL_CALL_NOT_ALLOWED" if detail.startswith("EXTERNAL_") else "INQUIRY_READ_FAILED"
            return {"ok": False, "rows": [], "blockers": [{"code": code, "message": detail or "고객센터 문의를 읽지 못했다."}]}
        rows = [_public_row(r) for r in read.rows if r["pending_count"] > 0 and r["inquiry_status"] == "progress"]
        return {"ok": True, "rows": rows, "truncated": read.truncated, "blockers": []}

    # ------------------------------------------------------------
    # 계획(미리보기)
    # ------------------------------------------------------------

    def plan(self, company_id: int, inquiry_id: Any, content: Any, *, live: bool = True) -> CcPlan:
        target = str(inquiry_id or "").strip()
        plan = CcPlan(inquiry_id=target, content_chars=len(normalize_reply(content)) if isinstance(content, str) else 0)
        if not valid_cc_id(inquiry_id):
            plan.blockers.append(CcBlocker("INVALID_INQUIRY_ID", "상담번호는 숫자여야 한다."))
        problem = call_center_text_problem(content)
        if problem == "INVALID_CONTENT":
            plan.blockers.append(CcBlocker(problem, f"답변은 {MIN_CC_REPLY_CHARS}~{MAX_CC_REPLY_CHARS:,}자로 써야 한다."))
        elif problem == "DRAFT_MARK_LEFT":
            plan.blockers.append(CcBlocker(problem, "초안의 가림 표시([전화번호] 등)·[확인 필요] 자리·초안 안내 문구가 남아 있다 — 고친 뒤 보내야 한다."))
        store_id, store_blockers = self._store(company_id)
        plan.store_connection_id = store_id
        plan.blockers.extend(store_blockers)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _store(self, company_id: int) -> tuple[int | None, list[CcBlocker]]:
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
            return None, [CcBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다.")]
        return None, [CcBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다.")]

    def _gate_checks(self, plan: CcPlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(CcBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(CcBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: CcPlan) -> dict | None:
        actions = self._actions_factory(self.db)
        row = actions._journal.get(company_id, ChannelActionType.CALL_CENTER_REPLY, actions.call_center_reply_key(plan.inquiry_id, plan.parent_answer_id))
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: CcPlan, company_id: int) -> None:
        snap, failure = self._actions_factory(self.db).read_call_center(
            company_id=company_id, store_connection_id=plan.store_connection_id, inquiry_id=plan.inquiry_id,
        )
        if snap is None or not snap.ok:
            detail = failure.detail if failure is not None else (snap.detail if snap is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(CcBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(CcBlocker("INQUIRY_READ_FAILED", f"쿠팡 고객센터 문의를 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        plan.live_checked = True
        row = snap.row
        plan.inquiry = _public_row(row)
        if row["inquiry_status"] != "progress":
            plan.blockers.append(CcBlocker("INQUIRY_CLOSED", "상담이 끝난 문의라 API로 답할 수 없다 — 쿠팡 윙 쿠팡CS문의에서 확인해 주세요."))
            return
        if row["pending_count"] == 0:
            plan.already_answered = True
            return
        if row["pending_count"] > 1:
            plan.blockers.append(CcBlocker("MULTIPLE_PENDING", "답변을 기다리는 상담사 글이 여러 개라 어느 글에 답할지 고르지 않는다 — 윙에서 답해 주세요."))
            return
        plan.parent_answer_id = row["pending_answer_id"]
        plan.ledger = self._ledger_summary(company_id, plan)
        if not snap.reply_ready:
            plan.blockers.append(CcBlocker(
                "MISSING_WING_USER_ID", "쿠팡 연결 정보와 판매자 고정값에 WING 아이디가 없어(또는 사용자마다 달라) 답변자를 정할 수 없다 — 상품 등록 5단계에서 WING 아이디를 고정값으로 저장해 주세요.",
            ))

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, inquiry_id: Any, content: str, *, expected_parent_answer_id: Any,
        confirm_public: bool = False, retry_action_required: bool = False, before_send: Callable[[], None] | None = None,
    ) -> dict:
        plan = self.plan(company_id, inquiry_id, content, live=True)
        if plan.already_answered:
            return {"outcome": "ALREADY_ANSWERED", "detail": "답변을 기다리는 상담사 글이 없어 보내지 않았다(이미 답했거나 쿠팡이 닫았을 수 있다).",
                    "executed": False, "verified": None, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked or plan.parent_answer_id is None:
            raise BadRequestException("답변을 보낼 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if not isinstance(expected_parent_answer_id, str) or expected_parent_answer_id != plan.parent_answer_id:
            raise ConflictException("화면에서 본 상담사 글과 지금 답변을 기다리는 글이 다르다 — 다시 불러와 확인해 주세요. 보내지 않았다.")
        if confirm_public is not True:
            raise BadRequestException("답변은 고객 상담에 바로 쓰이고 고칠 수 있는지 알 수 없다 — 이 점을 따로 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        text = normalize_reply(content)
        actions = self._actions_factory(self.db)
        result = actions.reply_call_center_inquiry(
            company_id=company_id, store_connection_id=plan.store_connection_id, inquiry_id=plan.inquiry_id,
            parent_answer_id=plan.parent_answer_id, content=text, triggered_by=user_id, retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_CALL_CENTER_REPLY", entity="coupang_call_center_inquiry", entity_id=plan.inquiry_id,
            description=f"쿠팡 고객센터 문의 답변 {result.outcome}: {len(text)}자",
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 다시 읽어 우리 답(판매자 글)이 붙었는지 본다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            snap, _ = actions.read_call_center(company_id=company_id, store_connection_id=plan.store_connection_id, inquiry_id=plan.inquiry_id)
            if snap is not None and snap.ok:
                verified = any(r["answer_type"] == "vendor" and normalize_reply(r["content"]) == text for r in snap.row["replies"])
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "plan": plan.to_dict()}

    def reconcile(self, company_id: int, inquiry_id: Any, parent_answer_id: Any, content: str) -> dict:
        """결과불명(UNKNOWN)인 답변을 단건 조회로 확인한다(읽기, 보내지 않음)."""

        if not valid_cc_id(inquiry_id) or not valid_cc_id(parent_answer_id) or cc_reply_problem(content) is not None:
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        store_id, blockers = self._store(company_id)
        if blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in blockers))
        result = self._actions_factory(self.db).check_call_center_reply(
            company_id=company_id, store_connection_id=store_id, inquiry_id=str(inquiry_id).strip(),
            parent_answer_id=str(parent_answer_id).strip(), content=normalize_reply(content),
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangCallCenterReplyService", "CcPlan", "CcBlocker", "call_center_text_problem"]
