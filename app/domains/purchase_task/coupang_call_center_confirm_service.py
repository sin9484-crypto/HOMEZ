"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_call_center_confirm_service.py

2026-10-27 — 쿠팡 쓰기 9번: **고객센터 업체 이관 문의 확인**(외부 변경 — 쿠팡이 상담을 마친 이관 문의를 판매자가 확인 처리).
고객센터 문의 답변(`coupang_call_center_reply_service.py`)과 같은 틀·같은 허용("쿠팡 문의 답변")을 쓴다. 이 모듈이 지키는 것:

  1) 확인을 기다리는 문의(미확인 TRANSFER)는 쿠팡 목록에서 그 자리에서 읽는다. 목록에 없는 번호는 보내지 않는다
     (이미 확인했거나 24시간이 지났을 수 있다 — 윙으로 안내).
  2) 사용자가 문의와 상담 내용을 봤다는 확인(`confirm_seen`)이 있어야 보낸다. 확인자(confirmBy)는 자격증명(없으면 판매자 고정값 — `coupang_seller_wing.py`)의 WING 아이디만 쓴다.
  3) 확인 뒤 상태 값은 공식 문서에 없다 — 보낸 뒤 미확인 목록에서 빠졌으면 확인으로 보고, 그대로면 '확인되지 않음'으로 알린다.
     결과불명은 목록에 그대로면 '안 받음', 빠졌으면 판단하지 않는다(다시 보내지 않는다).
  4) 안전 — 비상 정지·운영자 승인 모드, 승인 센터 허용이 없으면 읽지도 보내지도 않는다. 한 문의에 한 번만(작업 장부).
=========================================================
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Callable

from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.domains.automation_safety.constants import AutomationMode
from app.domains.automation_safety.service import SafetyService
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_seller_wing import actions_with_seller_wing
from app.domains.purchase_task.coupang_call_center_reply_provider import valid_cc_id
from app.domains.purchase_task.coupang_call_center_reply_service import _public_row
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")


class CoupangCallCenterConfirmService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or actions_with_seller_wing
        self.safety = safety or SafetyService(db)

    def _store(self, company_id: int) -> tuple[int | None, list[dict]]:
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
            return None, [{"code": "NO_COUPANG_STORE", "message": "연결된 쿠팡 판매 연결이 없다."}]
        return None, [{"code": "MULTIPLE_COUPANG_STORES", "message": "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."}]

    def _gate_blockers(self) -> list[dict]:
        blockers = []
        if self.safety.is_emergency_stop_active():
            blockers.append({"code": "ESTOP_ACTIVE", "message": "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."})
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            blockers.append({
                "code": "OPERATOR_APPROVAL_MODE_REQUIRED",
                "message": "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            })
        return blockers

    def _read(self, company_id: int, store_id: int):
        read, failure = self._actions_factory(self.db).read_call_center_transfer_list(company_id=company_id, store_connection_id=store_id)
        if read is None or not read.ok:
            detail = failure.detail if failure is not None else (read.detail if read is not None else "")
            code = "EXTERNAL_CALL_NOT_ALLOWED" if detail.startswith("EXTERNAL_") else "INQUIRY_READ_FAILED"
            return None, {"code": code, "message": detail or "고객센터 문의를 읽지 못했다."}
        return read, None

    def unconfirmed(self, company_id: int) -> dict:
        """확인을 기다리는 업체 이관 문의(최근 7일). 이 목록이 미리보기다 — 막는 이유(모드·비상 정지)도 함께 돌려준다."""

        store_id, blockers = self._store(company_id)
        if blockers:
            return {"ok": False, "rows": [], "blockers": blockers, "gate_blockers": []}
        read, problem = self._read(company_id, store_id)
        if problem is not None:
            return {"ok": False, "rows": [], "blockers": [problem], "gate_blockers": []}
        return {"ok": True, "rows": [_public_row(r) for r in read.rows], "truncated": read.truncated, "blockers": [],
                "gate_blockers": self._gate_blockers()}

    def send(
        self, company_id: int, user_id: int, inquiry_id: Any, *, confirm_seen: bool = False, retry_action_required: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> dict:
        if not valid_cc_id(inquiry_id):
            raise BadRequestException("상담번호는 숫자여야 한다. 보내지 않았다.")
        target = str(inquiry_id).strip()
        store_id, blockers = self._store(company_id)
        if blockers:
            raise BadRequestException("확인을 보낼 수 없는 상태다: " + ", ".join(b["code"] for b in blockers))
        gates = self._gate_blockers()
        if gates:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b["code"] for b in gates))
        read, problem = self._read(company_id, store_id)
        if problem is not None:
            raise BadRequestException(f"확인을 보낼 수 없는 상태다: {problem['code']} {problem['message']}".strip())
        if not any(r["inquiry_id"] == target for r in read.rows):
            return {"outcome": "NOT_PENDING", "detail": "확인을 기다리는 목록에 없어 보내지 않았다(이미 확인했거나 24시간이 지났을 수 있다 — 윙에서 확인해 주세요).",
                    "executed": False, "verified": None}
        if confirm_seen is not True:
            raise BadRequestException("문의와 쿠팡 상담 내용을 확인했다는 표시가 필요하다. 보내지 않았다.")
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        result = actions.confirm_call_center_inquiry(
            company_id=company_id, store_connection_id=store_id, inquiry_id=target, triggered_by=user_id,
            retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_CALL_CENTER_CONFIRM", entity="coupang_call_center_inquiry", entity_id=target,
            description=f"쿠팡 고객센터 이관 문의 확인 {result.outcome}", company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 확인 뒤 상태 값이 문서에 없어 미확인 목록에서 빠졌는지로 본다(읽기 1회). 읽지 못하면 확인 안 됨(None).
            after, _ = self._read(company_id, store_id)
            if after is not None:
                verified = not any(r["inquiry_id"] == target for r in after.rows)
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified}

    def reconcile(self, company_id: int, inquiry_id: Any) -> dict:
        if not valid_cc_id(inquiry_id):
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        store_id, blockers = self._store(company_id)
        if blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b["code"] for b in blockers))
        result = self._actions_factory(self.db).check_call_center_confirm(
            company_id=company_id, store_connection_id=store_id, inquiry_id=str(inquiry_id).strip(),
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = ["CoupangCallCenterConfirmService"]
