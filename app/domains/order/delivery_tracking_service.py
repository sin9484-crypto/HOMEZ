"""
=========================================================
Homez OS

File : app/domains/order/delivery_tracking_service.py

2026-10-16 — 진행 중인 쿠팡 주문의 상태를 "실시간"(10분 주기 조회)으로 따라간다.

배경: 5분 주문 감지는 신규(ACCEPT) 주문만 보고, 이미 들어온 주문이 준비중→배송중→배송완료로 바뀌거나 고객이 취소하는 것은
아무도 읽지 않았다(송장 입력 때의 `IN_TRANSIT`이 그대로 남았다). 이 서비스가 그 공백을 메운다.

규칙:
  - 쿠팡 발주서 **단건 조회(GET)** 만 한다. 쿠팡에 쓰지 않는다. 조회는 `ORDER_TRACKING` 허용(상시·시간당 상한)이 있어야만 나가고,
    허용이 없으면 호출하지 않고 허용 요청만 남긴다(`ChannelActionService.read_order_sheet`의 기존 허용 확인을 그대로 쓴다).
  - HOMEZ에 반영하는 것은 출고 상자의 쿠팡 원문 상태(`raw_status`)와 확인 시각, 배송 기록의 `IN_TRANSIT`, 그리고 쿠팡이
    **배송완료(FINAL_DELIVERY)** 라고 알린 SHIPPED 작업의 기존 `mark_delivered` 처리뿐이다. 정산·환불·금액은 건드리지 않는다.
  - 쿠팡이 전량 취소를 알리면 자동으로 취소·환불 처리하지 않고 감사 기록(`COUPANG_ORDER_CANCEL_DETECTED`)만 남겨 사람이 보게 한다.
  - 한 번에 최대 `max_per_run`건, 허용이 막으면(허용 없음·비상 정지) 즉시 멈춘다.
=========================================================
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import NotFoundException
from app.domains.external_call.constants import CallPurpose as ExtPurpose
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo

logger = logging.getLogger("homez")

# 쿠팡이 이미 배송을 마쳤다고 알린 상태 — 더 읽을 필요가 없다.
FINAL_STATUS = "FINAL_DELIVERY"
IN_TRANSIT_STATUSES = frozenset({"DEPARTURE", "DELIVERING"})

STATUS_LABELS = {
    "ACCEPT": "결제완료", "INSTRUCT": "상품준비중", "DEPARTURE": "배송지시", "DELIVERING": "배송중",
    "FINAL_DELIVERY": "배송완료", "NONE_TRACKING": "업체직송(추적 없음)",
}

# 따라갈 구매 작업 — 아직 고객에게 도착하지 않았고 끝나지 않은 것(배송완료 후의 반품·민원·정산은 별도 단계).
TRACKED_TASK_STATUSES = (
    PurchaseTaskStatus.SEARCH_REQUIRED, PurchaseTaskStatus.CANDIDATES_READY, PurchaseTaskStatus.REVIEW_REQUIRED,
    PurchaseTaskStatus.PURCHASE_READY, PurchaseTaskStatus.USER_PAYMENT_PENDING, PurchaseTaskStatus.TRACKING_REQUIRED,
    PurchaseTaskStatus.SHIPPED, PurchaseTaskStatus.UNCERTAIN,
)

DEFAULT_MAX_PER_RUN = 20


class DeliveryTrackingService:

    def __init__(
        self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None,
        max_per_run: int = DEFAULT_MAX_PER_RUN,
    ):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self.max_per_run = max_per_run

    # ------------------------------------------------------------
    # 한 건
    # ------------------------------------------------------------

    def refresh_task(self, task_id: int, company_id: int, *, triggered_by: int | None = None) -> dict:
        task = (
            self.db.query(PurchaseTask)
            .filter(PurchaseTask.id == task_id, PurchaseTask.company_id == company_id)
            .first()
        )
        if task is None:
            raise NotFoundException("구매 작업을 찾을 수 없습니다.")
        result = {
            "task_id": task.id, "ok": False, "blocked": False, "changed": False, "delivered_marked": False,
            "cancelled": False, "coupang_status": None, "previous_status": None, "detail": "",
        }
        box = None
        if task.source_order_id:
            box = (
                self.db.query(OrderChannelFulfillment)
                .filter(OrderChannelFulfillment.company_id == company_id, OrderChannelFulfillment.order_id == task.source_order_id)
                .order_by(OrderChannelFulfillment.id.desc())
                .first()
            )
        if box is None or not str(box.shipment_box_id or "").isdigit():
            result["detail"] = "쿠팡 배송번호가 없어 상태를 읽지 못했다"
            return result
        result["previous_status"] = box.raw_status

        sheet, failure = self._actions_factory(self.db).read_order_sheet(
            company_id=company_id, store_connection_id=box.store_connection_id,
            shipment_box_id=box.shipment_box_id, purpose=ExtPurpose.ORDER_TRACKING,
        )
        if sheet is None or not sheet.ok:
            detail = failure.detail if failure is not None else (sheet.detail if sheet is not None else "")
            result["detail"] = detail or "쿠팡 발주서를 읽지 못했다"
            # 허용이 없거나 비상 정지면 쿠팡을 부르지 않은 것이다 — 호출자는 더 이상 시도하지 않는다.
            result["blocked"] = str(detail).startswith("EXTERNAL_") or "허용" in str(detail)
            return result

        new_status = str(sheet.status or "").strip()
        result.update(ok=True, coupang_status=new_status)
        box.updated_at = datetime.utcnow()  # 상태가 같아도 "방금 확인했다"는 시각은 남긴다
        if new_status and new_status != box.raw_status:
            old = box.raw_status
            box.raw_status = new_status
            result["changed"] = True
            write_audit_log(
                self.db, user_id=triggered_by, action="COUPANG_ORDER_STATUS_CHANGED", entity="purchase_task", entity_id=str(task.id),
                description=f"쿠팡 주문 상태 {old}→{new_status}({STATUS_LABELS.get(new_status, '알 수 없는 상태')})", company_id=company_id,
            )

        tracking = (
            self.db.query(PurchaseTaskTrackingInfo)
            .filter(PurchaseTaskTrackingInfo.company_id == company_id, PurchaseTaskTrackingInfo.purchase_task_id == task.id)
            .first()
        )
        if tracking is not None and new_status in IN_TRANSIT_STATUSES and (tracking.delivery_status or "") not in ("DELIVERED", "IN_TRANSIT"):
            tracking.delivery_status = "IN_TRANSIT"

        # 전량 취소 — 자동으로 취소·환불하지 않고 사람이 보게 기록만 한다(같은 작업에 한 번만).
        if sheet.items and all(shipping > 0 and cancel >= shipping for _vid, shipping, cancel in sheet.items):
            result["cancelled"] = True
            already = self.db.execute(
                text("SELECT 1 FROM audit_logs WHERE action = :a AND entity_id = :e LIMIT 1"),
                {"a": "COUPANG_ORDER_CANCEL_DETECTED", "e": str(task.id)},
            ).first()
            if already is None:
                write_audit_log(
                    self.db, user_id=triggered_by, action="COUPANG_ORDER_CANCEL_DETECTED", entity="purchase_task", entity_id=str(task.id),
                    description="쿠팡 발주서의 모든 품목이 취소로 표시됨 — 자동 처리하지 않음, 확인 필요", company_id=company_id,
                )

        self.db.commit()

        # 쿠팡이 배송완료라고 알리면 기존 배송완료 처리(SHIPPED → DELIVERED)를 그대로 쓴다.
        if new_status == FINAL_STATUS and task.status == PurchaseTaskStatus.SHIPPED and not result["cancelled"]:
            try:
                from app.domains.purchase_task.service import PurchaseTaskService

                PurchaseTaskService(self.db).mark_delivered(task.id, company_id, marked_by=triggered_by)
                result["delivered_marked"] = True
                write_audit_log(
                    self.db, user_id=triggered_by, action="COUPANG_DELIVERY_CONFIRMED", entity="purchase_task", entity_id=str(task.id),
                    description="쿠팡이 배송완료(FINAL_DELIVERY)로 알려 배송완료 처리", company_id=company_id,
                )
                self.db.commit()
            except Exception as exc:  # noqa: BLE001 — 상태 읽기 결과는 이미 저장됐다. 전이 실패는 기록만 하고 다음에 다시 본다.
                self.db.rollback()
                logger.warning("쿠팡 배송완료 반영 실패: %s", type(exc).__name__)
                result["detail"] = "배송완료 반영에 실패했다 — 다음 확인 때 다시 시도한다"
        return result

    # ------------------------------------------------------------
    # 진행 중인 전부
    # ------------------------------------------------------------

    def tracked_task_ids(self, company_id: int) -> list[int]:
        rows = (
            self.db.query(PurchaseTask.id)
            .join(OrderChannelFulfillment, (OrderChannelFulfillment.order_id == PurchaseTask.source_order_id)
                  & (OrderChannelFulfillment.company_id == PurchaseTask.company_id))
            .filter(
                PurchaseTask.company_id == company_id,
                PurchaseTask.status.in_(TRACKED_TASK_STATUSES),
                PurchaseTask.source_order_id.isnot(None),
                OrderChannelFulfillment.raw_status != FINAL_STATUS,
            )
            .order_by(PurchaseTask.id.asc())
            .distinct()
            .all()
        )
        return [r[0] for r in rows]

    def refresh_all(self, company_id: int, *, triggered_by: int | None = None) -> dict:
        summary = {"checked": 0, "changed": 0, "delivered": 0, "cancelled": 0, "failed": 0, "blocked": False, "results": []}
        for task_id in self.tracked_task_ids(company_id)[: self.max_per_run]:
            try:
                result = self.refresh_task(task_id, company_id, triggered_by=triggered_by)
            except Exception as exc:  # noqa: BLE001 — 한 건의 실패가 나머지를 막지 않는다
                self.db.rollback()
                logger.warning("쿠팡 주문 상태 확인 예외: %s", type(exc).__name__)
                summary["failed"] += 1
                continue
            summary["results"].append(result)
            if result["blocked"]:
                summary["blocked"] = True
                break
            if not result["ok"]:
                summary["failed"] += 1
                continue
            summary["checked"] += 1
            summary["changed"] += int(result["changed"])
            summary["delivered"] += int(result["delivered_marked"])
            summary["cancelled"] += int(result["cancelled"])
        return summary


__all__ = ["DeliveryTrackingService", "FINAL_STATUS", "STATUS_LABELS", "TRACKED_TASK_STATUSES"]
