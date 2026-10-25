"""
=========================================================
Homez OS

File : app/domains/purchase_task/coupang_product_name_service.py

2026-10-25 — 쿠팡 쓰기 3번: **등록 상품의 상품명 수정**(외부 변경 — 쿠팡 "상품 수정(승인필요)", 승인 뒤 반영).
판매가·재고 변경과 같은 틀을 따른다. 이 모듈이 지키는 것:

  1) 값은 만들어 내지 않는다 — 새 노출상품명(필수)·등록상품명(선택)은 사용자가 정한다. 지금 이름·상태는 쿠팡 상품 조회로 그 자리에서 읽고,
     미리보기에서 본 이름과 지금 이름이 다르면 보내지 않는다.
  2) **상품 전체를 다시 보내는 API**다 — 본문은 방금 읽은 상품 조회 응답을 그대로 복사해 이름 두 칸(과 승인 요청 표시)만 바꾼다.
     보내기 직전에 원본과 본문을 비교해 그 밖의 값이 하나라도 다르거나, 옵션에 옵션 번호(sellerProductItemId)가 없으면(쿠팡이 새 옵션으로 본다) 보내지 않는다.
  3) 쿠팡 상태가 승인완료(변경 요청)나 승인반려(고쳐서 다시 제출)일 때만 보낸다. 바꾼 이름은 **쿠팡 승인 뒤에 반영**되고 다시 반려될 수 있어
     사용자가 그 점을 따로 확인(`confirm_review`)해야 보낸다. 승인 중 판매 유지 여부는 공식 문서에 없다(🔴 미확인).
  4) 안전 — 비상 정지가 켜져 있거나 기능 모드가 "운영자 승인"이 아니면 보내지 않는다. 승인 센터의 "쿠팡 상품명 수정" 허용이 없으면
     읽지도 보내지도 않는다(읽기·쓰기 각각 횟수형).
  5) 중복 방지·결과불명 — 작업 장부가 전담한다(대상 키 = 등록상품번호 + 새 이름 지문 + 요청 참조값). 결과불명은 상품 조회로 확인하되,
     이름이 아직 그대로면 '안 받음'으로 단정하지 않고 결과불명으로 둔다(승인 대기일 수 있다) — 다시 보내지 않는다.
=========================================================
"""

from __future__ import annotations

import copy
import logging
import re
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
from app.domains.marketplace_listing.coupang_live_provider import CANONICAL_STATUS_MAP
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.coupang_channel_control_provider import MAX_PRODUCT_NAME_CHARS
from app.domains.purchase_task.coupang_channel_control_provider import MAX_VENDOR_ITEM_ID_DIGITS
from app.domains.purchase_task.coupang_stock_service import REQUEST_REF
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection

logger = logging.getLogger("homez")

EDITABLE_STATUSES = {"APPROVED": "CHANGE_REQUEST", "DENIED": "RESUBMIT"}
CHANGED_KEYS_ALLOWED = frozenset({"displayProductName", "sellerProductName", "requested"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_name(value: Any) -> str | None:
    """앞뒤 공백만 걷는다(고쳐 쓰지 않는다). 비었거나 100자 초과·제어 문자가 있으면 None."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_PRODUCT_NAME_CHARS or _CONTROL.search(text):
        return None
    return text


def build_name_body(data: dict, display_name: str, seller_name: str | None) -> dict:
    """상품 조회 응답(data)을 그대로 복사해 이름 두 칸과 승인 요청 표시만 바꾼다. 원본은 건드리지 않는다."""

    body = copy.deepcopy(data)
    body["displayProductName"] = display_name
    if seller_name is not None:
        body["sellerProductName"] = seller_name
    body["requested"] = True  # 수정분의 승인 요청(쿠팡 문서: true는 저장과 동시에 승인 요청)
    return body


def body_guard(data: dict, body: dict) -> str | None:
    """보내기 직전 검사 — 문제가 있으면 막는 이유 코드. 이름·승인 요청 표시 말고는 아무것도 달라지면 안 된다."""

    keys = set(data) | set(body)
    changed = {k for k in keys if data.get(k, _MISSING) != body.get(k, _MISSING)}
    if not changed <= CHANGED_KEYS_ALLOWED:
        return "BODY_CHANGED_OTHER_FIELDS"
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return "BODY_NO_OPTIONS"
    if not all(isinstance(i, dict) and str(i.get("sellerProductItemId") or "").strip().isdigit() for i in items):
        return "BODY_OPTION_ID_MISSING"
    return None


_MISSING = object()


@dataclass
class NameBlocker:
    code: str
    message: str


@dataclass
class NamePlan:
    seller_product_id: str
    display_name: str | None
    seller_name: str | None
    request_ref: str | None = None
    blockers: list[NameBlocker] = field(default_factory=list)
    gate_blockers: list[NameBlocker] = field(default_factory=list)
    store_connection_id: int | None = None
    status_name: str | None = None
    mode: str | None = None
    current_display_name: str | None = None
    current_seller_name: str | None = None
    option_count: int | None = None
    already_applied: bool = False
    live_checked: bool = False
    ledger: dict | None = None
    body: dict | None = None

    @property
    def sendable(self) -> bool:
        return self.live_checked and not self.blockers and not self.gate_blockers and not self.already_applied

    def to_dict(self) -> dict:
        # 상품 본문(body)은 화면에 내보내지 않는다 — 이름·상태·옵션 수만.
        return {
            "seller_product_id": self.seller_product_id, "display_name": self.display_name, "seller_name": self.seller_name,
            "request_ref": self.request_ref, "sendable": self.sendable, "live_checked": self.live_checked,
            "already_applied": self.already_applied, "mode": self.mode,
            "blockers": [b.__dict__ for b in self.blockers], "gate_blockers": [b.__dict__ for b in self.gate_blockers],
            "coupang": {"status_name": self.status_name, "display_name": self.current_display_name,
                        "seller_name": self.current_seller_name, "option_count": self.option_count},
            "ledger": self.ledger, "max_chars": MAX_PRODUCT_NAME_CHARS,
        }


class CoupangProductNameService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        safety: SafetyService | None = None,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.safety = safety or SafetyService(db)

    # ------------------------------------------------------------
    # 계획(미리보기) — 로컬 판정 + 쿠팡 상품 조회 1회
    # ------------------------------------------------------------

    def plan(
        self, company_id: int, seller_product_id: str, display_name: Any, *, seller_name: Any = None,
        request_ref: str | None = None, live: bool = True,
    ) -> NamePlan:
        product = str(seller_product_id or "").strip()
        display = clean_name(display_name)
        # 등록상품명은 선택 — 비우면(None·빈 칸) 그대로 둔다. 무언가 넣었는데 규칙에 안 맞으면 막는다.
        seller_given = not (seller_name is None or (isinstance(seller_name, str) and not seller_name.strip()))
        seller = clean_name(seller_name) if seller_given else None
        plan = NamePlan(seller_product_id=product, display_name=display, seller_name=seller, request_ref=request_ref)
        if not product.isdigit() or len(product) > MAX_VENDOR_ITEM_ID_DIGITS:
            plan.blockers.append(NameBlocker("INVALID_SELLER_PRODUCT_ID", "쿠팡 등록상품번호는 숫자여야 한다."))
        if display is None:
            plan.blockers.append(NameBlocker(
                "INVALID_DISPLAY_NAME", f"노출상품명은 1~{MAX_PRODUCT_NAME_CHARS}자이고 줄바꿈·제어 문자가 없어야 한다.",
            ))
        if seller_given and seller is None:
            plan.blockers.append(NameBlocker(
                "INVALID_SELLER_NAME", f"등록상품명은 비우거나 1~{MAX_PRODUCT_NAME_CHARS}자이고 줄바꿈·제어 문자가 없어야 한다.",
            ))
        if request_ref is not None and not REQUEST_REF.match(str(request_ref)):
            plan.blockers.append(NameBlocker("INVALID_REQUEST_REF", "요청 참조값 형식이 올바르지 않다(미리보기를 다시 열어 주세요)."))
        self._resolve_store(plan, company_id)
        self._gate_checks(plan)
        if plan.blockers:
            return plan
        plan.ledger = self._ledger_summary(company_id, plan)
        if live:
            self._live_checks(plan, company_id)
        return plan

    def _resolve_store(self, plan: NamePlan, company_id: int) -> None:
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
            plan.blockers.append(NameBlocker("NO_COUPANG_STORE", "연결된 쿠팡 판매 연결이 없다."))
        else:
            plan.blockers.append(NameBlocker("MULTIPLE_COUPANG_STORES", "연결된 쿠팡 판매 연결이 여러 개라 자동으로 고르지 않는다."))

    def _gate_checks(self, plan: NamePlan) -> None:
        if self.safety.is_emergency_stop_active():
            plan.gate_blockers.append(NameBlocker("ESTOP_ACTIVE", "비상 정지가 켜져 있어 외부 변경을 보내지 않는다."))
        if self.safety.get_current_mode() != AutomationMode.OPERATOR_APPROVAL:
            plan.gate_blockers.append(NameBlocker(
                "OPERATOR_APPROVAL_MODE_REQUIRED", "기능 모드가 '운영자 승인'이 아니라 외부 변경을 보내지 않는다(모드는 사용자가 직접 바꾼다).",
            ))

    def _ledger_summary(self, company_id: int, plan: NamePlan) -> dict | None:
        if plan.request_ref is None:
            return None
        actions = self._actions_factory(self.db)
        key = actions.product_name_key(plan.seller_product_id, plan.display_name, plan.request_ref)
        row = actions._journal.get(company_id, ChannelActionType.PRODUCT_NAME_UPDATE, key)
        if row is None:
            return None
        return {
            "status": row.status, "attempt_count": row.attempt_count, "detail": row.detail,
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def _live_checks(self, plan: NamePlan, company_id: int) -> None:
        snapshot, failure = self._actions_factory(self.db).read_product(
            company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id,
        )
        if snapshot is None or not snapshot.ok:
            detail = failure.detail if failure is not None else (snapshot.detail if snapshot is not None else "")
            if detail.startswith("EXTERNAL_"):
                plan.blockers.append(NameBlocker("EXTERNAL_CALL_NOT_ALLOWED", detail))
                return
            plan.blockers.append(NameBlocker("PRODUCT_READ_FAILED", f"쿠팡 상품을 읽지 못해 보내지 않는다. {detail}".strip()))
            return
        plan.live_checked = True
        plan.status_name = snapshot.status_name
        plan.current_display_name = snapshot.display_name
        plan.current_seller_name = snapshot.seller_name
        plan.option_count = len(snapshot.data.get("items") or [])
        plan.mode = EDITABLE_STATUSES.get(CANONICAL_STATUS_MAP.get(str(snapshot.status_name or "").strip(), ""))
        if plan.display_name == plan.current_display_name and plan.seller_name in (None, plan.current_seller_name):
            plan.already_applied = True
            return
        if plan.mode is None:
            plan.blockers.append(NameBlocker(
                "NOT_EDITABLE_STATUS",
                f"쿠팡 상품 상태가 '{snapshot.status_name or '알 수 없음'}'라 보내지 않는다(승인완료·승인반려 상품만 이름을 고칠 수 있다).",
            ))
            return
        body = build_name_body(snapshot.data, plan.display_name, plan.seller_name)
        problem = body_guard(snapshot.data, body)
        if problem is not None:
            plan.blockers.append(NameBlocker(problem, "상품 수정 본문 검사에 걸려 보내지 않는다(이름 말고 다른 값이 달라지거나 옵션 번호가 없다)."))
            return
        plan.body = body

    # ------------------------------------------------------------
    # 전송
    # ------------------------------------------------------------

    def send(
        self, company_id: int, user_id: int, seller_product_id: str, display_name: str, *, seller_name: str | None = None,
        expected_display_name: str | None, request_ref: str, confirm_review: bool = False, retry_action_required: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> dict:
        if not REQUEST_REF.match(str(request_ref or "")):
            raise BadRequestException("요청 참조값 형식이 올바르지 않다 — 미리보기를 다시 열어 주세요. 보내지 않았다.")
        plan = self.plan(company_id, seller_product_id, display_name, seller_name=seller_name, request_ref=request_ref, live=True)
        if plan.already_applied:
            return {"outcome": "ALREADY_DONE", "detail": "쿠팡 상품명이 이미 그 이름이라 보내지 않았다.", "executed": False, "verified": True,
                    "after": {"display_name": plan.current_display_name, "status_name": plan.status_name}, "plan": plan.to_dict()}
        if plan.gate_blockers:
            raise ForbiddenException("외부 변경 조건이 충족되지 않았다: " + ", ".join(b.code for b in plan.gate_blockers))
        if plan.blockers or not plan.live_checked or plan.body is None:
            raise BadRequestException("상품명을 바꿀 수 없는 상태다: " + ", ".join(b.code for b in plan.blockers))
        if not isinstance(expected_display_name, str) or expected_display_name != plan.current_display_name:
            raise ConflictException("화면에서 확인한 지금 상품명과 쿠팡의 지금 상품명이 다르다 — 미리보기를 다시 확인해 주세요. 보내지 않았다.")
        if confirm_review is not True:
            raise BadRequestException("바꾼 이름은 쿠팡 승인 뒤에 반영되고 반려될 수 있다 — 이 점을 따로 확인해 주세요. 보내지 않았다.")
        if before_send is not None:
            before_send()
        actions = self._actions_factory(self.db)
        result = actions.set_product_name(
            company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id,
            display_name=plan.display_name, body=plan.body, request_ref=request_ref, triggered_by=user_id,
            retry_action_required=retry_action_required,
        )
        write_audit_log(
            self.db, user_id=user_id, action="COUPANG_PRODUCT_NAME_UPDATE", entity="coupang_seller_product", entity_id=plan.seller_product_id,
            description=f"쿠팡 상품명 수정 {result.outcome}({plan.mode}): {plan.current_display_name} → {plan.display_name}"[:490],
            company_id=company_id,
        )
        self.db.commit()
        verified: bool | None = None
        after: dict | None = None
        if result.executed and result.outcome == ActionOutcome.SUCCEEDED:
            # 성공 응답만 믿지 않고 상품을 다시 읽는다(읽기 1회). 이름이 아직 그대로면 쿠팡 승인 대기일 수 있어 '확인 못 함'(None)으로 둔다.
            snap, _ = actions.read_product(company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=plan.seller_product_id)
            if snap is not None and snap.ok:
                after = {"display_name": snap.display_name, "status_name": snap.status_name}
                verified = True if snap.display_name == plan.display_name else None
        return {"outcome": result.outcome, "detail": result.detail, "executed": result.executed, "verified": verified, "after": after,
                "plan": plan.to_dict()}

    def reconcile(self, company_id: int, seller_product_id: str, display_name: str, request_ref: str) -> dict:
        """결과불명(UNKNOWN)인 요청을 쿠팡 상품 조회로 확인한다(읽기 GET 1회, 보내지 않음)."""

        if not REQUEST_REF.match(str(request_ref or "")):
            raise BadRequestException("요청 참조값 형식이 올바르지 않다.")
        product = str(seller_product_id or "").strip()
        display = clean_name(display_name)
        if not product.isdigit() or len(product) > MAX_VENDOR_ITEM_ID_DIGITS or display is None:
            raise BadRequestException("확인할 수 없는 요청이다: INVALID_TARGET")
        plan = NamePlan(seller_product_id=product, display_name=display, seller_name=None, request_ref=request_ref)
        self._resolve_store(plan, company_id)
        if plan.blockers:
            raise BadRequestException("확인할 수 없는 요청이다: " + ", ".join(b.code for b in plan.blockers))
        result = self._actions_factory(self.db).check_product_name(
            company_id=company_id, store_connection_id=plan.store_connection_id, seller_product_id=product,
            display_name=display, request_ref=request_ref,
        )
        return {"outcome": result.outcome, "detail": result.detail}


__all__ = [
    "CoupangProductNameService", "NamePlan", "NameBlocker", "build_name_body", "body_guard", "clean_name",
    "EDITABLE_STATUSES", "CHANGED_KEYS_ALLOWED",
]
