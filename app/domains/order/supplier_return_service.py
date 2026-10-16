"""
=========================================================
Homez OS

File : app/domains/order/supplier_return_service.py

2026-10-16 — 쿠팡에 반품이 접수되면 **공급처(온채널)에 반품을 요청**하고, 그 요청과 결과를 HOMEZ에 이어서 기록한다.

온채널 OpenAPI(v0.1, 2026-10-16 전체 재확인)에는 취소·반품·환불 요청 API가 없다. 그래서 이 서비스는:
  - 요청할 내용을 **준비**한다: 쿠팡 반품(접수번호·사유·책임)과 그 주문의 공급처 발주(온채널 주문 코드·상품·수량)를 한데 묶는다.
  - 요청 여부를 **기록**한다("요청했어요", 감사 기록 `HOMEZ_SUPPLIER_RETURN_EVENT`). 실제 접수는 온채널 판매자 화면에서 한다.
  - 결과는 온채널 API의 **주문 상태 읽기**로 확인한다(상태가 `취소반품`/환불이면 확인됨으로 기록).
  - 온채널로 아무것도 **보내지 않는다**. 구매 작업 상태·금액·메일·정산도 건드리지 않는다.
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit_db import write_audit_log
from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.order.after_sales_monitor_service import AfterSalesMonitorService
from app.domains.order.after_sales_monitor_service import parse_event
from app.domains.order.model import Order
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseTask

EVENT_ACTION = "HOMEZ_SUPPLIER_RETURN_EVENT"
NOT_REQUESTED, REQUESTED, CONFIRMED, REFUNDED = "NOT_REQUESTED", "REQUESTED", "CONFIRMED", "REFUNDED"

# 온채널 주문 상태 문구(주문 상세·목록의 상태)에서 반품 진행을 읽는다.
RETURN_WORDS = ("취소반품", "반품")
REFUND_WORDS = ("환불", "반품완료")


class SupplierReturnService:

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------
    # 기록(상태가 바뀐 것만)
    # ------------------------------------------------------------

    def _latest(self, receipt_id: str) -> dict | None:
        row = self.db.execute(
            text("SELECT description FROM audit_logs WHERE action = :a AND entity_id = :e ORDER BY id DESC LIMIT 1"),
            {"a": EVENT_ACTION, "e": f"SUPRET:{receipt_id}"},
        ).first()
        return parse_event(row[0]) if row else None

    def _write(self, company_id: int, receipt_id: str, payload: dict, user_id: int | None) -> None:
        write_audit_log(
            self.db, user_id=user_id, action=EVENT_ACTION, entity="supplier_return", entity_id=f"SUPRET:{receipt_id}",
            description=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:490], company_id=company_id,
        )

    # ------------------------------------------------------------
    # 보기(읽기 전용) — 쿠팡에서 끝나지 않은 반품과, 그것에 대한 공급처 요청 상태
    # ------------------------------------------------------------

    def candidates(self, company_id: int) -> list[dict]:
        try:
            events = AfterSalesMonitorService.latest_events(self.db)
        except Exception:  # noqa: BLE001
            return []
        open_returns = [e for e in events if e.get("k") == "RETURN" and e.get("s") != "RETURNS_COMPLETED" and e.get("o")]
        result = []
        for event in open_returns:
            receipt_id = str(event.get("id") or event["entity_id"].split(":", 1)[1])
            order = self.db.query(Order).filter(Order.company_id == company_id, Order.channel_order_id == str(event["o"])).first()
            task = (
                self.db.query(PurchaseTask).filter(PurchaseTask.company_id == company_id, PurchaseTask.source_order_id == order.id)
                .order_by(PurchaseTask.id.asc()).first() if order else None
            )
            attempt = (
                self.db.query(PurchaseOrderSubmissionAttempt)
                .filter(PurchaseOrderSubmissionAttempt.company_id == company_id, PurchaseOrderSubmissionAttempt.purchase_task_id == task.id,
                        PurchaseOrderSubmissionAttempt.status == "SUCCEEDED")
                .order_by(PurchaseOrderSubmissionAttempt.id.desc()).first() if task else None
            )
            latest = self._latest(receipt_id) or {}
            result.append({
                "receipt_id": receipt_id, "channel_order_id": str(event["o"]), "coupang_status": event.get("s"),
                "reason_category": event.get("rc"), "fault_by": event.get("f"), "quantity_returned": event.get("n"),
                "task_id": task.id if task else None, "product_title": task.product_title if task else None,
                "task_quantity": task.quantity if task else None,
                "supplier_order_code": getattr(attempt, "external_order_code", None) if attempt else None,
                "supplier": "ONCH" if attempt else None,
                "supplier_state": latest.get("s") or NOT_REQUESTED, "supplier_updated_at": latest.get("t"),
                "supplier_status_text": latest.get("x"),
            })
        return result

    def candidate(self, company_id: int, receipt_id: str) -> dict:
        return self._candidate(company_id, receipt_id)

    def _candidate(self, company_id: int, receipt_id: str) -> dict:
        for c in self.candidates(company_id):
            if c["receipt_id"] == str(receipt_id):
                return c
        raise NotFoundException("진행 중인 쿠팡 반품 접수를 찾을 수 없습니다.")

    # ------------------------------------------------------------
    # 기록하기
    # ------------------------------------------------------------

    def mark_requested(self, company_id: int, receipt_id: str, user_id: int | None) -> dict:
        """"온채널 화면에서 반품을 요청했다"고 기록한다. 온채널로 아무것도 보내지 않고, 같은 접수에는 한 번만 기록한다."""

        c = self._candidate(company_id, receipt_id)
        if not c["supplier_order_code"]:
            raise BadRequestException("이 주문의 공급처(온채널) 발주를 찾지 못해 반품 요청을 기록할 수 없습니다.")
        if c["supplier_state"] != NOT_REQUESTED:
            return c
        self._write(company_id, c["receipt_id"], {
            # 온채널 주문 코드(`GO_1018547475`)는 긴 숫자라 중앙 가림 규칙이 가려 버리므로 기록에 넣지 않는다(발주 기록에서 읽는다).
            "s": REQUESTED, "t": datetime.utcnow().isoformat(timespec="seconds"), "u": user_id,
        }, user_id)
        self.db.commit()
        return self._candidate(company_id, receipt_id)

    def apply_supplier_status(self, company_id: int, receipt_id: str, status_text: str | None, user_id: int | None = None) -> dict:
        """온채널 주문 상태(API로 읽은 문구)를 받아 반품 진행을 기록한다. 요청 기록이 먼저 있어야 하고, 상태가 바뀔 때만 쓴다."""

        c = self._candidate(company_id, receipt_id)
        text_value = str(status_text or "")
        state = c["supplier_state"]
        if c["supplier_state"] == NOT_REQUESTED:
            return {**c, "supplier_status_text": text_value}
        new_state = state
        if any(w in text_value for w in REFUND_WORDS):
            new_state = REFUNDED
        elif any(w in text_value for w in RETURN_WORDS):
            new_state = CONFIRMED if state in (REQUESTED, CONFIRMED) else state
        if new_state != state:
            self._write(company_id, c["receipt_id"], {
                "s": new_state, "x": text_value[:30], "t": datetime.utcnow().isoformat(timespec="seconds"), "u": user_id,
            }, user_id)
            self.db.commit()
        return {**self._candidate(company_id, receipt_id), "supplier_status_text": text_value}


__all__ = ["SupplierReturnService", "EVENT_ACTION", "NOT_REQUESTED", "REQUESTED", "CONFIRMED", "REFUNDED"]
