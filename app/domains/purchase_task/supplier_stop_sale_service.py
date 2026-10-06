"""
=========================================================
Homez OS

File : app/domains/purchase_task/supplier_stop_sale_service.py

2026-10-05 사용자 확정(HOMEZ_USER_OPERATION_SETTINGS.md "온채널형 공급처
문의·판매중지 처리") — 공급처가 상품의 **판매중단을 명시**해 공급 불가가 확정되면, 대응
판매 옵션을 판매 중지하고 공급 불가인 관련 미출고 고객 주문을 취소 처리한다.

이 모듈이 하는 일(기존 옵션 연결·주문 취소·감사로그 재사용 + 작업 장부
`channel_action_requests` — 추가형 Migration 1개):
  1) 판정: 공급처 상세 응답의 판매 상태가 **명시적 판매중단(`prd_state` 3)** 일
     때만 확정이다(`is_explicit_stop_sale`). 통신·인증 오류·응답 형식 오류·조회 실패·
     일시품절(4)·품절(5)·단종(2)·상태 없음은 판매중지가 아니다. 공식 스펙의 상품 상태
     enum은 "1 정상판매, 2 단종, 3 판매중단, 4 일시품절, 5 품절"이다.
  2) 범위: 같은 회사·같은 매입 연결·같은 공급처 상품코드의 옵션 연결
     (`supplier_option_links`)만. 연결이 없으면 대응 판매 옵션을 특정할 수 없으므로 확인
     대상으로 남긴다(추측으로 다른 상품을 건드리지 않는다).
  3) 보호: 해당 연결을 NEEDS_REVIEW로 바꿔 이후 자동 발주 후보에서 빼는 것은 내부 안전
     조치라 모드와 무관하게 항상 수행한다.
  4) 외부 효과가 있는 단계(쿠팡 옵션 판매중지, 쿠팡 고객 주문 취소)는 기존 기능별 실행
     모드와 비상정지를 따른다: 비상정지·일시중지·오류는 실행하지 않고(BLOCKED), 수동은
     준비만 하며(PREPARED), 반자동은 사람 승인(`approved_by`)이 있을 때만, 자동은 바로
     실행한다. **PREPARED는 성공이 아니다.**
  5) 고객 주문 취소는 **채널(쿠팡) 취소가 성공한 뒤에만** HOMEZ 내부 주문을 취소로 바꾼다.
     채널 취소(CHANNEL_ORDER_CANCEL)와 내부 반영(ORDER_CANCEL)은 별도 단계로 기록한다.
     공급처(온채널) 발주 취소는 이 모듈의 범위가 아니다 — 이미 발주 성공·진행 중·결과불명
     이거나 출고 이후인 주문, 다른 품목이 섞인 주문, 박스가 여럿인 주문은 취소하지 않고
     상태 확인·검토 대상으로 분리한다.
  6) 환불은 별도로 요청하지 않는다(쿠팡 공식 문서에 취소와 환불의 관계가 없다 — 요청도
     완료 표시도 하지 않고 확인 대상으로 남긴다).
  7) 재시도·중복: 외부 요청은 작업 장부 선점(요청 전 commit) 뒤에만 보낸다. 429만 한도·
     간격 안에서 재시도하고, 요청·권한·미지원 오류는 원인 해결 전 반복하지 않으며,
     결과불명(UNKNOWN)은 자동 재요청하지 않고 읽기 조회로 대조한다.
=========================================================
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import AppException
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.constants import OrderStatus
from app.domains.order.model import Order
from app.domains.order.model import OrderItem
from app.domains.purchase_task.channel_action_service import ActionOutcome
from app.domains.purchase_task.channel_action_service import ActionResult
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import CapabilitySupport
from app.domains.purchase_task.constants import OrderSubmissionStatus
from app.domains.purchase_task.constants import SupplierOptionLinkStatus
from app.domains.purchase_task.coupang_channel_control_provider import (
    CANCEL_AUTO_ALLOWED_RAW_STATUSES,
)
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseRecord
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import SupplierOptionLink
from app.domains.purchase_task.supplier_option_link_service import (
    SupplierOptionLinkService,
)

logger = logging.getLogger("homez")

# 온채널 상품 상태 enum(공식 스펙 `components.schemas.product.status`):
# 1 정상판매, 2 단종, 3 판매중단, 4 일시품절, 5 품절. 이 저장소의 이전 정리 문서에는
# "1 판매전, 2 판매"로 적힌 곳도 있어 서로 다르지만 **3=판매중단은 어느 기록이든 같다** —
# 그래서 3만 명시적 판매중지로 자동 처리한다. 2(단종)와 4·5(품절)는 의미가 확정되지
# 않았거나 재고 사안이라 자동 처리하지 않고 기존 "가격·재고 변경" 규칙(사용자 확인)을 따른다.
EXPLICIT_STOP_SALE_STATUSES = frozenset({"3"})

REASON_BLOCKED = "SUPPLIER_STOP_SALE"
REASON_CHANNEL_STOPPED = "SUPPLIER_STOP_SALE_CHANNEL_STOPPED"
REASON_CHANNEL_UNKNOWN = "SUPPLIER_STOP_SALE_CHANNEL_UNKNOWN"
REASON_CHANNEL_ACTION_REQUIRED = "SUPPLIER_STOP_SALE_CHANNEL_ACTION_REQUIRED"
REASON_CHANNEL_RETRY_WAIT = "SUPPLIER_STOP_SALE_CHANNEL_RETRY_WAIT"
_ALL_REASONS = (
    REASON_BLOCKED, REASON_CHANNEL_STOPPED, REASON_CHANNEL_UNKNOWN,
    REASON_CHANNEL_ACTION_REQUIRED, REASON_CHANNEL_RETRY_WAIT,
)

_PURCHASE_IN_PROGRESS = (
    OrderSubmissionStatus.PENDING, OrderSubmissionStatus.IN_FLIGHT,
    OrderSubmissionStatus.SUCCEEDED, OrderSubmissionStatus.RESULT_UNKNOWN,
)


class StopSaleStep:
    DETECTION = "DETECTION"
    LINK_BLOCK = "LINK_BLOCK"
    SALE_STOP = "SALE_STOP"                      # 쿠팡 옵션 판매중지(채널)
    CHANNEL_ORDER_CANCEL = "CHANNEL_ORDER_CANCEL"  # 쿠팡 고객 주문 취소(채널)
    ORDER_CANCEL = "ORDER_CANCEL"                # HOMEZ 내부 주문 상태 반영
    REFUND = "REFUND"


class StopSaleOutcome:
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"                       # 내부 처리 실패(채널 외부 요청 오류는 ACTION_REQUIRED)
    UNKNOWN = "UNKNOWN"                     # 결과불명 — 자동 재요청 금지, 조회·대조로 확인
    PREPARED = "PREPARED"                   # 준비만 함(수동/반자동 미승인/Provider 없음) — 성공 아님
    BLOCKED = "BLOCKED"                     # 비상정지·일시중지·오류로 실행하지 않음
    ALREADY_DONE = "ALREADY_DONE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_REQUESTED = "NOT_REQUESTED"         # 의도적으로 요청하지 않음(환불)
    NEEDS_STATUS_CHECK = "NEEDS_STATUS_CHECK"   # 발주 성공·진행·결과불명·출고 이후
    NEEDS_REVIEW = "NEEDS_REVIEW"               # 대상 특정 불가·다른 품목 혼재
    ACTION_REQUIRED = "ACTION_REQUIRED"     # 요청·권한·미지원·상태 오류 — 원인 해결 전 반복 금지
    RETRY_WAIT = "RETRY_WAIT"               # 적용 안 됨이 확실한 일시 오류 — 한도·간격 안에서 재시도


_DONE_OUTCOMES = frozenset({
    StopSaleOutcome.SUCCEEDED, StopSaleOutcome.ALREADY_DONE, StopSaleOutcome.NOT_APPLICABLE,
})


@dataclass(frozen=True)
class StepResult:
    step: str
    target: str
    outcome: str
    detail: str = ""


# 이전 이름 호환 — 채널 실행기의 결과 타입과 같다.
ChannelStopResult = ActionResult


class ChannelActions(Protocol):
    """판매채널에 실제 외부 변경을 보내는 실행기. 결과는 성공/재시도 대기/조치 필요/결과불명을
    구분해서 돌려줘야 한다(통신 오류를 성공이나 확정 실패로 단정하지 않는다)."""

    def stop_sale(
        self, *, company_id: int, store_connection_id: int, vendor_item_id: str,
        triggered_by: int | None = None, retry_action_required: bool = False,
    ) -> ActionResult: ...

    def check_sale_stopped(
        self, *, company_id: int, store_connection_id: int, vendor_item_id: str,
    ) -> ActionResult: ...

    def cancel_order(
        self, *, company_id: int, store_connection_id: int, channel_order_id: str,
        shipment_box_id: str, items: list[tuple[str, int]],
        triggered_by: int | None = None, retry_action_required: bool = False,
    ) -> ActionResult: ...

    def check_order_cancelled(
        self, *, company_id: int, store_connection_id: int, channel_order_id: str,
        shipment_box_id: str, items: list[tuple[str, int]],
    ) -> ActionResult: ...


class UnconfiguredChannelActions:
    """외부 호출 없이 "준비만 했다(수동 처리 필요)"를 돌려주는 실행기(테스트·미연결 환경)."""

    _MSG = "판매채널 실행기가 연결되지 않아 실행하지 않았다(수동 처리 필요)"

    def stop_sale(self, **_kwargs) -> ActionResult:
        return ActionResult(StopSaleOutcome.PREPARED, self._MSG)

    check_sale_stopped = stop_sale

    def cancel_order(self, **_kwargs) -> ActionResult:
        return ActionResult(StopSaleOutcome.PREPARED, self._MSG)

    check_order_cancelled = cancel_order


@dataclass
class StopSaleReport:
    confirmed: bool
    product_code: str
    results: list[StepResult] = field(default_factory=list)

    def add(self, step: str, target: str, outcome: str, detail: str = "") -> None:
        self.results.append(StepResult(step, target, outcome, detail))

    def by_step(self, step: str) -> list[StepResult]:
        return [r for r in self.results if r.step == step]

    def channel_done(self, step: str) -> bool:
        """해당 채널 단계(판매중지·고객 주문 취소) 결과가 있고 전부 완료인가."""

        rows = self.by_step(step)
        return bool(rows) and all(r.outcome in _DONE_OUTCOMES for r in rows)

    def summary(self) -> str:
        counts: dict[tuple[str, str], int] = {}
        for r in self.results:
            counts[(r.step, r.outcome)] = counts.get((r.step, r.outcome), 0) + 1
        return ", ".join(
            f"{step}:{outcome}x{n}" for (step, outcome), n in sorted(counts.items())
        )

    def incomplete(self) -> list[StepResult]:
        """완료되지 않은 항목(사람이 확인·조치해야 하는 것) — 환불 미요청도 포함한다."""

        return [
            r for r in self.results
            if r.step != StopSaleStep.DETECTION and r.outcome not in _DONE_OUTCOMES
        ]


class SupplierStopSaleService:

    def __init__(
        self, db: Session, *, channel_actions: ChannelActions | None = None,
        order_service=None, link_service: SupplierOptionLinkService | None = None,
        safety_service: SafetyService | None = None,
    ):
        self.db = db
        self._actions = channel_actions or CoupangChannelActions(db)
        self._order_service = order_service
        self._links = link_service or SupplierOptionLinkService(db)
        self._safety = safety_service or SafetyService(db)

    # ------------------------------------------------------------
    # 판정
    # ------------------------------------------------------------

    @staticmethod
    def is_explicit_stop_sale(lookup_result) -> bool:
        """공급처 상세 조회 결과가 **명시적 판매중단**인가. 조회가 성공(SUPPORTED)했고
        상태 값이 명시적 판매중단일 때만 True — 그 밖의 모든 경우(미지원·오류·
        상태 없음·품절·일시품절·알 수 없는 값)는 판매중지가 아니다."""

        if getattr(lookup_result, "support", None) != CapabilitySupport.SUPPORTED:
            return False
        status = getattr(lookup_result, "status", None)
        if status is None:
            return False
        return str(status).strip() in EXPLICIT_STOP_SALE_STATUSES

    def detect_and_handle(
        self, adapter, *, company_id: int, connection_id: int, product_code: str,
        triggered_by: int | None = None,
    ) -> StopSaleReport | None:
        """발주 진입에서 **발주 허용 게이트와 무관하게** 공급처 상태를 읽어 명시적 판매중단이면
        처리한다. 읽기 전용 조회이며 발주를 허용하지 않는다(이 함수가 돌려준 보고서가 있으면
        호출자는 발주를 진행하지 않는다). 조회 실패·미지원·판매중단이 아닌 상태는 None —
        그 경우의 발주 차단은 기존 게이트가 맡는다(오류를 판매중단으로 해석하지 않는다)."""

        try:
            product = adapter.lookup_product(product_code)
        except Exception as exc:  # noqa: BLE001 — 통신·인증 오류는 판매중단이 아니다
            logger.info("판매중단 사전 조회 실패(판정 없음): %s", type(exc).__name__)
            return None
        if not self.is_explicit_stop_sale(product):
            return None
        return self.handle_explicit_stop_sale(
            company_id=company_id, connection_id=connection_id, product_code=product_code,
            supplier_status=getattr(product, "status", None), triggered_by=triggered_by,
        )

    # ------------------------------------------------------------
    # 처리
    # ------------------------------------------------------------

    def handle_explicit_stop_sale(
        self, *, company_id: int, connection_id: int, product_code: str,
        supplier_status, triggered_by: int | None = None,
        approved_by: int | None = None, retry_action_required: bool = False,
    ) -> StopSaleReport:
        """명시적 판매중단이 확정된 사건을 처리한다. 판정을 다시 확인하며, 확정이
        아니면 아무것도 하지 않는다. 같은 사건을 다시 처리해도 중복 요청·취소가 없다.
        `retry_action_required=True`는 사람이 원인(권한·요청 오류 등)을 해결한 뒤 조치 필요로
        남은 요청을 다시 실행하겠다는 명시적 의사다(`approved_by`와 별개)."""

        code = (product_code or "").strip()
        report = StopSaleReport(
            confirmed=str(supplier_status).strip() in EXPLICIT_STOP_SALE_STATUSES
            if supplier_status is not None else False,
            product_code=code,
        )
        if not report.confirmed or not code:
            return report

        connection = (
            self.db.query(PurchaseChannelConnection)
            .filter(
                PurchaseChannelConnection.id == connection_id,
                PurchaseChannelConnection.company_id == company_id,
            )
            .first()
        )
        if connection is None:
            report.confirmed = False  # 이 회사의 연결이 아니면 아무것도 하지 않는다
            return report

        self._audit(
            "SUPPLIER_STOP_SALE_DETECTED", f"{connection_id}:{code}", company_id,
            triggered_by,
            f"공급처 명시적 판매중단 확인(status={str(supplier_status).strip()}) — "
            "통신·인증 오류나 결과불명이 아닌 응답으로 확정",
        )
        report.add(StopSaleStep.DETECTION, code, StopSaleOutcome.SUCCEEDED)

        links = self._affected_links(company_id, connection_id, code, report)
        self._block_links(links, company_id, triggered_by, report)
        self._stop_channel_sales(
            links, company_id, triggered_by, approved_by, retry_action_required, report,
        )
        self._handle_orders(
            links, company_id, triggered_by, approved_by, retry_action_required, report,
        )

        self._notify(company_id, connection_id, code, report)
        return report


    # ------------------------------------------------------------
    # 운영자 조회·대조 — 외부 **읽기만** 한다(쓰기 메서드를 호출하지 않는다)
    # ------------------------------------------------------------

    def reconcile_pending(
        self, *, company_id: int, connection_id: int, product_code: str,
        triggered_by: int | None = None, approved_by: int | None = None,
    ) -> StopSaleReport:
        """이전 판매중지·고객 주문 취소 요청의 결과를 읽기 조회로 대조해 확인된 사실만 반영한다.
        외부 변경 요청(`stop_sale`·`cancel_order`)은 이 메서드에서 호출되지 않는다 — 미적용이
        확인돼도 재요청하지 않고 재시도 가능 상태로만 되돌린다(재요청은 별도 명령).

        비상정지 정책(HOMEZ_USER_OPERATION_SETTINGS.md 232·296행): 긴급 중지 시 상품등록·가격변경·
        결제·발주 등 **변경은 멈추고 조회·진단은 유지**한다. 그래서 이 읽기 전용 대조는 비상정지
        중에도 실행된다. 변경은 계속 차단된다 — 외부 변경 요청은 이 경로에 없고, 내부 주문 취소
        반영은 `_step_gate`가 비상정지를 BLOCKED로 막는다(쿠팡 취소가 확인돼도 보류). 장부·연결
        사유 갱신은 확인된 사실의 기록이다(주문·자금 변경 아님). 내부 주문 취소는 쿠팡 취소가
        확인됐고 취소 기능 모드·승인 범위가 허용할 때만 반영한다."""

        code = (product_code or "").strip()
        report = StopSaleReport(confirmed=False, product_code=code)
        connection = (
            self.db.query(PurchaseChannelConnection)
            .filter(
                PurchaseChannelConnection.id == connection_id,
                PurchaseChannelConnection.company_id == company_id,
            )
            .first()
        )
        if connection is None or not code:
            return report

        links = self._affected_links(company_id, connection_id, code, report)
        for link in links:
            target = f"link:{link.id}"
            if link.status == SupplierOptionLinkStatus.DISABLED:
                continue
            if not link.coupang_vendor_item_id:
                report.add(StopSaleStep.SALE_STOP, target, StopSaleOutcome.NEEDS_REVIEW,
                           "쿠팡 옵션번호(vendorItemId)가 연결에 없어 대조할 수 없다")
                continue
            checked = self._safe_check(
                lambda: self._actions.check_sale_stopped(
                    company_id=company_id, store_connection_id=link.store_connection_id,
                    vendor_item_id=link.coupang_vendor_item_id,
                ),
            )
            outcome = checked.outcome
            reason = self._link_reason_for(outcome)
            if reason is not None:
                link.status_reason = reason
            self._audit(
                f"SUPPLIER_STOP_SALE_RECONCILE_STOP_{outcome}", target, company_id,
                triggered_by, f"판매중지 결과 대조(sku={link.channel_sku}): {checked.detail}",
            )
            self.db.commit()
            report.add(StopSaleStep.SALE_STOP, target, outcome, checked.detail)

        links_by_key = {(l.store_connection_id, l.channel_sku): l for l in links}
        by_order: dict[int, list[PurchaseTask]] = {}
        for task in self._related_tasks(links, company_id):
            by_order.setdefault(task.source_order_id, []).append(task)
        for order_id, order_tasks in sorted(by_order.items()):
            target = f"order:{order_id}"
            order = self.db.query(Order).filter(
                Order.id == order_id, Order.company_id == company_id,
            ).first()
            if order is None:
                continue
            if order.status == OrderStatus.CANCELLED:
                report.add(StopSaleStep.ORDER_CANCEL, target, StopSaleOutcome.ALREADY_DONE)
                continue
            plan = self._channel_cancel_plan(order, links_by_key, company_id)
            if isinstance(plan, tuple):
                report.add(StopSaleStep.CHANNEL_ORDER_CANCEL, target, plan[0], plan[1])
                continue
            checked = self._safe_check(
                lambda: self._actions.check_order_cancelled(
                    company_id=company_id, **plan,
                ),
            )
            report.add(StopSaleStep.CHANNEL_ORDER_CANCEL, target, checked.outcome, checked.detail)
            self._audit(
                f"SUPPLIER_STOP_SALE_RECONCILE_CANCEL_{checked.outcome}", target, company_id,
                triggered_by, f"쿠팡 고객 주문 취소 결과 대조: {checked.detail}",
            )
            self.db.commit()
            if checked.outcome not in (StopSaleOutcome.SUCCEEDED, StopSaleOutcome.ALREADY_DONE):
                continue
            # 쿠팡 취소가 확인됐다 — 내부 반영은 취소 기능 모드·승인 범위가 허용할 때만
            blocked = self._not_cancellable_reason(order, order_tasks, company_id)
            if blocked is not None:
                report.add(StopSaleStep.ORDER_CANCEL, target, blocked[0], blocked[1])
                continue
            allowed, gate_outcome, why = self._step_gate(
                company_id, FunctionCode.CANCELLATION, approved_by,
            )
            if not allowed:
                report.add(
                    StopSaleStep.ORDER_CANCEL, target, gate_outcome,
                    "쿠팡 취소는 확인됐으나 내부 반영은 보류했다: " + why,
                )
                continue
            self._apply_internal_cancel(order_id, target, company_id, triggered_by, report)
        return report

    @staticmethod
    def _safe_check(call) -> ActionResult:
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — 대조 조회 실패는 결과불명 유지
            logger.warning("대조 조회 예외: %s", type(exc).__name__)
            return ActionResult(StopSaleOutcome.UNKNOWN, type(exc).__name__)

    # ------------------------------------------------------------
    # 내부: 범위
    # ------------------------------------------------------------

    def _affected_links(
        self, company_id: int, connection_id: int, code: str, report: StopSaleReport,
    ) -> list[SupplierOptionLink]:

        if not SupplierOptionLinkService.is_store_available(self.db):
            report.add(
                StopSaleStep.SALE_STOP, code, StopSaleOutcome.NEEDS_REVIEW,
                "옵션 연결 저장소가 없어 대응 판매 옵션을 특정할 수 없다",
            )
            return []
        links = (
            self.db.query(SupplierOptionLink)
            .filter(
                SupplierOptionLink.company_id == company_id,
                SupplierOptionLink.purchase_connection_id == connection_id,
                SupplierOptionLink.supplier_product_code == code,
            )
            .order_by(SupplierOptionLink.id.asc())
            .all()
        )
        if not links:
            report.add(
                StopSaleStep.SALE_STOP, code, StopSaleOutcome.NEEDS_REVIEW,
                "이 공급처 상품과 연결된 판매 옵션이 없다 — 대응 판매 상품을 확인해야 한다",
            )
        return links

    # ------------------------------------------------------------
    # 내부: 연결 차단(항상 수행 — 내부 안전 조치)
    # ------------------------------------------------------------

    def _block_links(
        self, links, company_id: int, triggered_by, report: StopSaleReport,
    ) -> None:

        for link in links:
            target = f"link:{link.id}"
            if link.status_reason in _ALL_REASONS:
                report.add(StopSaleStep.LINK_BLOCK, target, StopSaleOutcome.ALREADY_DONE)
                continue
            if link.status == SupplierOptionLinkStatus.DISABLED:
                report.add(
                    StopSaleStep.LINK_BLOCK, target, StopSaleOutcome.NOT_APPLICABLE,
                    "이미 해제된 연결",
                )
                continue
            link.status = SupplierOptionLinkStatus.NEEDS_REVIEW
            link.status_reason = REASON_BLOCKED
            self._audit(
                "SUPPLIER_STOP_SALE_LINK_BLOCKED", target, company_id, triggered_by,
                f"공급처 판매중단으로 옵션 연결을 재확인 필요 상태로 전환(sku={link.channel_sku})",
            )
            self.db.commit()
            report.add(StopSaleStep.LINK_BLOCK, target, StopSaleOutcome.SUCCEEDED)

    # ------------------------------------------------------------
    # 내부: 모드 게이트
    # ------------------------------------------------------------

    def _step_gate(
        self, company_id: int, function_code: str, approved_by: int | None,
    ) -> tuple[bool, str, str]:
        """(실행 가능 여부, 불가 시 결과 코드, 설명). 비상정지·일시중지·오류는 BLOCKED,
        수동·반자동 미승인은 PREPARED."""

        if self._safety.is_emergency_stop_active():
            return False, StopSaleOutcome.BLOCKED, "비상정지가 활성화돼 실행하지 않았다"
        mode = self._safety.get_function_mode(company_id, function_code)
        label = FunctionMode.LABELS_KO.get(mode, mode)
        if mode in (FunctionMode.PAUSED, FunctionMode.ERROR):
            return False, StopSaleOutcome.BLOCKED, f"기능 모드가 {label}라 실행하지 않았다"
        if mode == FunctionMode.AUTOMATIC:
            return True, "", ""
        if mode == FunctionMode.SEMI_AUTOMATIC and approved_by is not None:
            return True, "", ""
        return (
            False, StopSaleOutcome.PREPARED,
            f"기능 모드가 {label}라 준비만 했다(사람이 실행/승인해야 한다)",
        )

    @staticmethod
    def _link_reason_for(outcome: str) -> str | None:
        return {
            StopSaleOutcome.SUCCEEDED: REASON_CHANNEL_STOPPED,
            StopSaleOutcome.ALREADY_DONE: REASON_CHANNEL_STOPPED,
            StopSaleOutcome.UNKNOWN: REASON_CHANNEL_UNKNOWN,
            StopSaleOutcome.ACTION_REQUIRED: REASON_CHANNEL_ACTION_REQUIRED,
            StopSaleOutcome.RETRY_WAIT: REASON_CHANNEL_RETRY_WAIT,
        }.get(outcome)

    # ------------------------------------------------------------
    # 내부: 채널 판매중지
    # ------------------------------------------------------------

    def _stop_channel_sales(
        self, links, company_id: int, triggered_by, approved_by,
        retry_action_required: bool, report: StopSaleReport,
    ) -> None:

        for link in links:
            target = f"link:{link.id}"
            if link.status == SupplierOptionLinkStatus.DISABLED:
                continue
            if not link.coupang_vendor_item_id:
                # 쿠팡 판매중지 API의 대상은 옵션번호(vendorItemId)다. 상품번호만으로는 옵션을
                # 특정할 수 없어 요청하지 않는다.
                report.add(
                    StopSaleStep.SALE_STOP, target, StopSaleOutcome.NEEDS_REVIEW,
                    "쿠팡 옵션번호(vendorItemId)가 연결에 없어 대상을 특정할 수 없다",
                )
                continue

            allowed, blocked_outcome, why = self._step_gate(
                company_id, FunctionCode.INVENTORY_RESPONSE, approved_by,
            )
            if not allowed:
                report.add(StopSaleStep.SALE_STOP, target, blocked_outcome, why)
                continue

            common = dict(
                company_id=company_id, store_connection_id=link.store_connection_id,
                vendor_item_id=link.coupang_vendor_item_id,
            )
            try:
                result = self._actions.stop_sale(
                    **common, triggered_by=triggered_by,
                    retry_action_required=retry_action_required,
                )
                # 이전 요청의 결과불명 상태를 장부에서 읽은 경우에만(이번에 막 난 불명이 아니라)
                # 조회로 대조한다. 미적용이 확인되면 한도 안에서 다시 요청한다.
                if result.outcome == StopSaleOutcome.UNKNOWN and not result.executed:
                    checked = self._actions.check_sale_stopped(**common)
                    if checked.outcome in (StopSaleOutcome.SUCCEEDED, StopSaleOutcome.ALREADY_DONE):
                        result = ActionResult(StopSaleOutcome.SUCCEEDED, checked.detail)
                    elif checked.outcome == StopSaleOutcome.RETRY_WAIT:
                        result = self._actions.stop_sale(
                            **common, triggered_by=triggered_by,
                            retry_action_required=retry_action_required,
                        )
                    else:
                        result = ActionResult(StopSaleOutcome.UNKNOWN, checked.detail or result.detail)
            except Exception as exc:  # noqa: BLE001 — 실행기 내부 예외는 결과불명으로 기록
                logger.warning("판매중지 처리 예외(결과불명 처리): %s", type(exc).__name__)
                result = ActionResult(StopSaleOutcome.UNKNOWN, type(exc).__name__)

            outcome = result.outcome
            reason = self._link_reason_for(outcome)
            if reason is not None:
                link.status_reason = reason
            self._audit(
                f"SUPPLIER_STOP_SALE_CHANNEL_STOP_{outcome}", target, company_id,
                triggered_by, f"판매채널 판매중지 요청 결과(sku={link.channel_sku}): {result.detail}",
            )
            self.db.commit()
            report.add(StopSaleStep.SALE_STOP, target, outcome, result.detail)

    # ------------------------------------------------------------
    # 내부: 관련 주문
    # ------------------------------------------------------------

    def _related_tasks(self, links, company_id: int):
        """연결된 판매 옵션(SKU)의 주문 품목 → 구매 작업 → (판매 계정 포함) 연결 해석이
        이 연결로 귀결되는 작업만."""

        skus = {l.channel_sku for l in links}
        if not skus:
            return []
        item_ids = [
            row[0] for row in self.db.query(OrderItem.id).filter(
                OrderItem.company_id == company_id, OrderItem.channel_sku.in_(skus),
            ).all()
        ]
        if not item_ids:
            return []
        tasks = (
            self.db.query(PurchaseTask)
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.source_order_item_id.in_(item_ids),
            )
            .all()
        )
        link_ids = {l.id for l in links}
        affected = []
        for task in tasks:
            resolution = self._links.resolve_for_task(task)
            if resolution.link is not None and resolution.link.id in link_ids:
                affected.append(task)
        return affected

    def _handle_orders(
        self, links, company_id: int, triggered_by, approved_by,
        retry_action_required: bool, report: StopSaleReport,
    ) -> None:

        tasks = self._related_tasks(links, company_id)
        by_order: dict[int, list[PurchaseTask]] = {}
        for task in tasks:
            by_order.setdefault(task.source_order_id, []).append(task)
        # 같은 SKU가 같은 회사의 다른 판매 계정에도 연결될 수 있다 — (판매 연결, SKU)가 키다.
        links_by_key = {(l.store_connection_id, l.channel_sku): l for l in links}

        for order_id, order_tasks in sorted(by_order.items()):
            target = f"order:{order_id}"
            order = self.db.query(Order).filter(
                Order.id == order_id, Order.company_id == company_id,
            ).first()
            if order is None:
                continue

            if order.status == OrderStatus.CANCELLED:
                report.add(StopSaleStep.ORDER_CANCEL, target, StopSaleOutcome.ALREADY_DONE)
                continue

            reason = self._not_cancellable_reason(order, order_tasks, company_id)
            if reason is not None:
                outcome, detail = reason
                report.add(StopSaleStep.ORDER_CANCEL, target, outcome, detail)
                self._audit(
                    f"SUPPLIER_STOP_SALE_ORDER_{outcome}", target, company_id,
                    triggered_by, detail,
                )
                self.db.commit()
                if self._has_supplier_payment(order_tasks, company_id):
                    report.add(
                        StopSaleStep.REFUND, target, StopSaleOutcome.NEEDS_REVIEW,
                        "이미 매입 대금이 나간 주문 — 공급처 환급·주문 상태를 확인해야 한다(자동 환불 요청 없음)",
                    )
                continue

            allowed, blocked_outcome, why = self._step_gate(
                company_id, FunctionCode.CANCELLATION, approved_by,
            )
            if not allowed:
                report.add(StopSaleStep.ORDER_CANCEL, target, blocked_outcome, why)
                continue

            plan = self._channel_cancel_plan(order, links_by_key, company_id)
            if isinstance(plan, tuple) and len(plan) == 2 and isinstance(plan[0], str):
                outcome, detail = plan
                report.add(StopSaleStep.ORDER_CANCEL, target, outcome, detail)
                self._audit(
                    f"SUPPLIER_STOP_SALE_ORDER_{outcome}", target, company_id, triggered_by, detail,
                )
                self.db.commit()
                continue

            channel_result = self._cancel_on_channel(
                plan, company_id, triggered_by, retry_action_required,
            )
            report.add(
                StopSaleStep.CHANNEL_ORDER_CANCEL, target, channel_result.outcome,
                channel_result.detail,
            )
            self._audit(
                f"SUPPLIER_STOP_SALE_CHANNEL_CANCEL_{channel_result.outcome}", target,
                company_id, triggered_by, f"쿠팡 고객 주문 취소 요청 결과: {channel_result.detail}",
            )
            self.db.commit()
            if channel_result.outcome not in (
                StopSaleOutcome.SUCCEEDED, StopSaleOutcome.ALREADY_DONE,
            ):
                # 외부 취소가 확인되지 않았으므로 내부 상태를 바꾸지 않는다.
                continue

            self._apply_internal_cancel(order_id, target, company_id, triggered_by, report)
            if self._has_supplier_payment(order_tasks, company_id):
                report.add(
                    StopSaleStep.REFUND, target, StopSaleOutcome.NEEDS_REVIEW,
                    "이미 매입 대금이 나간 주문 — 공급처 환급·주문 상태를 확인해야 한다(자동 환불 요청 없음)",
                )
            else:
                report.add(
                    StopSaleStep.REFUND, target, StopSaleOutcome.NOT_REQUESTED,
                    "쿠팡 공식 문서에 주문 취소와 환불의 관계가 명시돼 있지 않아 환불을 별도로 "
                    "요청하지도, 완료로 표시하지도 않았다 — 쿠팡의 환불 상태를 확인해야 한다",
                )

    def _channel_cancel_plan(self, order: Order, links_by_key: dict, company_id: int):
        """쿠팡 취소 요청을 만들 수 있는 주문인가. 만들 수 없으면 (결과 코드, 설명) 튜플.
        지원 범위: 쿠팡 발주서(박스)가 정확히 하나이고 상태가 결제완료(ACCEPT)이며 모든
        품목이 같은 판매 계정의 옵션 연결(vendorItemId 확인됨)로 대응되는 주문."""

        fulfillments = (
            self.db.query(OrderChannelFulfillment)
            .filter(
                OrderChannelFulfillment.company_id == company_id,
                OrderChannelFulfillment.order_id == order.id,
            )
            .all()
        )
        if len(fulfillments) != 1:
            return (
                StopSaleOutcome.NEEDS_REVIEW,
                "쿠팡 발주서(박스) 연결이 없거나 둘 이상이라 취소 대상 박스를 확정할 수 없다 — 쿠팡에서 직접 확인",
            )
        fulfillment = fulfillments[0]
        if fulfillment.raw_status not in CANCEL_AUTO_ALLOWED_RAW_STATUSES:
            return (
                StopSaleOutcome.NEEDS_STATUS_CHECK,
                f"쿠팡 발주서 상태가 {fulfillment.raw_status}라 자동 취소하지 않는다"
                "(결제완료 상태만 자동 대상 — 상품준비중 이후는 출고 중지라 사람이 확인)",
            )
        items = (
            self.db.query(OrderItem)
            .filter(OrderItem.order_id == order.id, OrderItem.company_id == company_id)
            .all()
        )
        plan_items: list[tuple[str, int]] = []
        for item in items:
            link = links_by_key.get((fulfillment.store_connection_id, item.channel_sku))
            if link is None or not link.coupang_vendor_item_id or int(item.quantity) < 1:
                return (
                    StopSaleOutcome.NEEDS_REVIEW,
                    "주문 품목을 이 판매 계정의 쿠팡 옵션번호로 확정할 수 없어 취소 요청을 만들지 않았다",
                )
            plan_items.append((str(link.coupang_vendor_item_id), int(item.quantity)))
        if not plan_items:
            return (StopSaleOutcome.NEEDS_REVIEW, "취소할 주문 품목이 없다")
        return {
            "store_connection_id": fulfillment.store_connection_id,
            "channel_order_id": fulfillment.channel_order_id,
            "shipment_box_id": fulfillment.shipment_box_id, "items": plan_items,
        }

    def _cancel_on_channel(
        self, plan: dict, company_id: int, triggered_by, retry_action_required: bool,
    ) -> ActionResult:
        try:
            result = self._actions.cancel_order(
                company_id=company_id, triggered_by=triggered_by,
                retry_action_required=retry_action_required, **plan,
            )
            if result.outcome == StopSaleOutcome.UNKNOWN and not result.executed:
                checked = self._actions.check_order_cancelled(
                    company_id=company_id, **plan,
                )
                if checked.outcome in (StopSaleOutcome.SUCCEEDED, StopSaleOutcome.ALREADY_DONE):
                    return ActionResult(StopSaleOutcome.SUCCEEDED, checked.detail)
                if checked.outcome == StopSaleOutcome.RETRY_WAIT:
                    return self._actions.cancel_order(
                        company_id=company_id, triggered_by=triggered_by,
                        retry_action_required=retry_action_required, **plan,
                    )
                return ActionResult(StopSaleOutcome.UNKNOWN, checked.detail or result.detail)
            return result
        except Exception as exc:  # noqa: BLE001 — 보냈는지 모르면 결과불명
            logger.warning("쿠팡 주문 취소 처리 예외(결과불명 처리): %s", type(exc).__name__)
            return ActionResult(StopSaleOutcome.UNKNOWN, type(exc).__name__)

    def _apply_internal_cancel(
        self, order_id: int, target: str, company_id: int, triggered_by,
        report: StopSaleReport,
    ) -> None:
        """외부(쿠팡) 취소가 확인된 뒤에만 HOMEZ 내부 주문을 취소로 바꾼다. 내부 반영이
        실패해도 작업 장부에 외부 성공이 남아 있어 재실행은 쿠팡에 다시 요청하지 않고
        내부 반영만 다시 시도한다."""

        try:
            self._orders().cancel_order(
                order_id, company_id, "공급처 명시적 판매중단으로 공급 불가", triggered_by,
            )
        except AppException as exc:
            self.db.rollback()
            report.add(
                StopSaleStep.ORDER_CANCEL, target, StopSaleOutcome.FAILED,
                "쿠팡 취소는 확인됐으나 HOMEZ 내부 반영 실패 — 재실행 시 내부 반영만 다시 시도한다: "
                + str(getattr(exc, "detail", exc))[:150],
            )
            self._audit(
                "SUPPLIER_STOP_SALE_ORDER_FAILED", target, company_id, triggered_by,
                "쿠팡 취소 확인 후 내부 주문 취소 반영 실패 — 주문 상태를 확인해야 한다",
            )
            self.db.commit()
            return
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            logger.warning("내부 주문 취소 예외: %s", type(exc).__name__)
            report.add(
                StopSaleStep.ORDER_CANCEL, target, StopSaleOutcome.UNKNOWN,
                "쿠팡 취소는 확인됐으나 내부 반영 결과를 확인하지 못했다: " + type(exc).__name__,
            )
            self._audit(
                "SUPPLIER_STOP_SALE_ORDER_UNKNOWN", target, company_id, triggered_by,
                "내부 주문 취소 반영 결과를 확인하지 못했다 — 주문 상태를 확인해야 한다",
            )
            self.db.commit()
            return

        self._audit(
            "SUPPLIER_STOP_SALE_ORDER_CANCELLED", target, company_id, triggered_by,
            "쿠팡 고객 주문 취소 확인 후 HOMEZ 내부 주문을 취소 처리",
        )
        self.db.commit()
        report.add(StopSaleStep.ORDER_CANCEL, target, StopSaleOutcome.SUCCEEDED)

    def _not_cancellable_reason(
        self, order: Order, order_tasks: list[PurchaseTask], company_id: int,
    ) -> tuple[str, str] | None:
        """자동 취소하면 안 되는 이유(있으면 (결과 코드, 설명))."""

        if order.status in OrderStatus.NOT_CANCELLABLE:
            return (
                StopSaleOutcome.NEEDS_STATUS_CHECK,
                f"주문이 이미 {order.status} 상태라 자동 취소하지 않는다",
            )

        all_item_ids = {
            row[0] for row in self.db.query(OrderItem.id).filter(
                OrderItem.order_id == order.id, OrderItem.company_id == company_id,
            ).all()
        }
        affected_item_ids = {t.source_order_item_id for t in order_tasks}
        if all_item_ids != affected_item_ids:
            return (
                StopSaleOutcome.NEEDS_REVIEW,
                "공급 불가 품목 외 다른 품목이 함께 있어 주문 전체를 자동 취소하지 않는다",
            )

        task_ids = [t.id for t in order_tasks]
        in_progress = (
            self.db.query(PurchaseOrderSubmissionAttempt.id)
            .filter(
                PurchaseOrderSubmissionAttempt.company_id == company_id,
                PurchaseOrderSubmissionAttempt.purchase_task_id.in_(task_ids),
                PurchaseOrderSubmissionAttempt.status.in_(_PURCHASE_IN_PROGRESS),
            )
            .first()
        )
        if in_progress is not None or self._has_supplier_payment(order_tasks, company_id):
            return (
                StopSaleOutcome.NEEDS_STATUS_CHECK,
                "이미 발주가 성공했거나 진행 중·결과불명이라 일괄 취소하지 않는다 — 공급처 주문 상태 확인",
            )
        return None

    def _has_supplier_payment(self, order_tasks, company_id: int) -> bool:
        task_ids = [t.id for t in order_tasks]
        return (
            self.db.query(PurchaseRecord.id)
            .filter(
                PurchaseRecord.company_id == company_id,
                PurchaseRecord.purchase_task_id.in_(task_ids),
            )
            .first()
        ) is not None

    def _orders(self):
        if self._order_service is None:
            from app.domains.order.service import OrderService

            self._order_service = OrderService(self.db)
        return self._order_service

    # ------------------------------------------------------------
    # 내부: 기록·알림
    # ------------------------------------------------------------

    def _audit(
        self, action: str, entity_id: str, company_id: int, user_id: int | None,
        description: str,
    ) -> None:
        write_audit_log(
            self.db, user_id=user_id, action=action, entity="supplier_stop_sale",
            entity_id=entity_id, description=description[:500], company_id=company_id,
        )

    @staticmethod
    def build_notification_message(code: str, report: StopSaleReport) -> str:
        """사용자 알림 문구. 판매중지·주문 취소가 쿠팡에 **반영됐는지**를 사실대로 적고, 환불을
        요청·확인하지 않았다는 사실을 주문 취소 완료 표시와 함께 숨기지 않는다."""

        pending = report.incomplete()
        stop_done = (
            "쿠팡 판매중지 반영됨" if report.channel_done(StopSaleStep.SALE_STOP)
            else "쿠팡 판매중지 미반영(확인·조치 필요)"
        )
        cancel_rows = report.by_step(StopSaleStep.CHANNEL_ORDER_CANCEL)
        cancel_done = (
            "쿠팡 주문 취소 반영됨"
            if cancel_rows and report.channel_done(StopSaleStep.CHANNEL_ORDER_CANCEL)
            else "쿠팡 주문 취소 미반영 또는 대상 없음"
        )
        refund_open = [
            r for r in report.by_step(StopSaleStep.REFUND) if r.outcome not in _DONE_OUTCOMES
        ]
        refund_note = (
            f" 환불은 요청하지도 확인하지도 않았습니다({len(refund_open)}건) — 쿠팡에서 환불 상태를 직접 확인해 주세요."
            if refund_open else ""
        )
        return (
            f"상품 {code}: 공급처가 판매중단을 명시해 발주하지 않았습니다. "
            f"{stop_done}; {cancel_done}.{refund_note} 확인·조치가 필요한 항목 {len(pending)}건 "
            f"({report.summary()}). 직접 확인해 주세요."
        )

    def _notify(
        self, company_id: int, connection_id: int, code: str, report: StopSaleReport,
    ) -> None:
        """사용자에게 알린다. 알림이 실패해도 처리 결과는 되돌리지 않는다. 판매중지·주문 취소가
        쿠팡에 **반영됐는지**를 사실대로 적는다(준비만 한 것을 완료로 쓰지 않는다)."""

        try:
            from app.domains.notification_center.operational_events import (
                dispatch_operational_event,
            )
            from app.domains.user.model import User

            if not sa_inspect(self.db.get_bind()).has_table("users"):
                return
            message = self.build_notification_message(code, report)
            for user in (
                self.db.query(User)
                .filter(User.company_id == company_id, User.is_active.is_(True))
                .all()
            ):
                if (getattr(user, "role", None) or "").strip().upper() != "SUPER_ADMIN":
                    continue
                dispatch_operational_event(
                    self.db, "SUPPLIER_STOP_SALE_CONFIRMED",
                    company_id=company_id, user_id=user.id,
                    # 결과 요약이 달라지면(예: 재시도 뒤 성공·조치 필요) 새 알림이 나가고,
                    # 같은 결과의 반복 처리는 한 번만 알린다.
                    idempotency_key=(
                        f"supplier-stop-sale:{connection_id}:{code}:"
                        + hashlib.sha1(report.summary().encode("utf-8")).hexdigest()[:10]
                    ),
                    title="공급처가 상품 판매중단을 명시했습니다",
                    message=message,
                    link_path="purchase-tasks", entity_ref=f"supplier_stop_sale:{code}",
                    reason="SUPPLIER_EXPLICIT_STOP_SALE",
                )
        except Exception as exc:  # noqa: BLE001 — 알림은 best-effort
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("판매중단 알림 실패(처리 결과는 유지): %s", type(exc).__name__)


__all__ = [
    "EXPLICIT_STOP_SALE_STATUSES", "StopSaleStep", "StopSaleOutcome", "StepResult",
    "StopSaleReport", "ChannelStopResult", "ChannelActions",
    "UnconfiguredChannelActions", "SupplierStopSaleService",
]
