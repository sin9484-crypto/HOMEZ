"""
=========================================================
Homez OS

File : app/domains/easy_view/service.py

2026-10-16 — "쉬운 보기" 화면이 쓰는 **읽기 전용 집계**. 화면이 5~6개 API를 따로 불러 조립하지 않도록 한곳에서 계산하고 서버
테스트로 고정한다. 이 모듈은 DB에 쓰지 않고 쿠팡·온채널을 부르지 않는다(저장된 값만 읽는다). 모든 조회는 회사 범위다.

  - `build_today()`   : 흐름 숫자(올릴 준비→판매 중→새 주문→보내는 중→정산), 출고일 확인이 필요한 등록 상품, 송장 등록 대기 주문
  - `build_orders()`  : 주문별 5단계 진행(주문→발주→발송→송장→도착)과 다음 할 일
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.domains.marketplace_listing.model import ListingWizard
from app.domains.marketplace_listing.model import MarketplaceSubmission
from app.domains.order.collection_model import OrderChannelFulfillment
from app.domains.order.collection_model import UnresolvedOrderItem
from app.domains.order.model import Order
from app.domains.order.model import OrderItem
from app.domains.purchase_task.constants import ChannelActionStatus
from app.domains.purchase_task.constants import ChannelActionType
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.model import ChannelActionRequest
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.model import PurchaseTask
from app.domains.purchase_task.model import PurchaseTaskTrackingInfo

STEP_KEYS = ("ordered", "supplier_order", "supplier_shipped", "invoice", "delivered")

# 위저드가 "아직 올리는 중"인 상태(끝났거나 지운 것은 제외).
_OPEN_WIZARD_STATUSES = (
    "DRAFT", "NEEDS_CORRECTION", "READY_FOR_APPROVAL", "APPROVED", "FAILED", "PARTIALLY_SUCCEEDED", "SUBMITTING", "VALIDATING",
)
# 쿠팡 주문 상태 중 송장이 이미 쿠팡에 반영된 것(출고 이후).
_COUPANG_AFTER_INVOICE = {"DEPARTURE", "DELIVERING", "FINAL_DELIVERY"}
# 구매 작업 중 "공급처에 넣어야 하는" 초기 상태.
_TASK_BEFORE_SUPPLIER = {
    PurchaseTaskStatus.SEARCH_REQUIRED, PurchaseTaskStatus.CANDIDATES_READY, PurchaseTaskStatus.REVIEW_REQUIRED,
    PurchaseTaskStatus.PURCHASE_READY, PurchaseTaskStatus.USER_PAYMENT_PENDING, PurchaseTaskStatus.TRACKING_REQUIRED,
}


def _has(db: Session, table: str) -> bool:
    return sa_inspect(db.get_bind()).has_table(table)


def mask_name(name: str | None) -> str:
    """고객 이름은 첫·끝 글자만 남긴다(김영준 → 김*준, 두 글자는 김*)."""

    text = (name or "").strip()
    if not text:
        return ""
    if len(text) == 1:
        return text
    if len(text) == 2:
        return text[0] + "*"
    return text[0] + "*" * (len(text) - 2) + text[-1]


def _json(raw) -> object:
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


class EasyViewService:

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------
    # 오늘 할 일
    # ------------------------------------------------------------

    def build_today(self, company_id: int) -> dict:
        wizards = (
            self.db.query(ListingWizard)
            .filter(ListingWizard.company_id == company_id)
            .all()
        )
        open_wizards = [w for w in wizards if w.status in _OPEN_WIZARD_STATUSES and getattr(w, "deleted_at", None) is None]
        submitted = self._submitted_products(company_id, wizards)

        tasks = self.db.query(PurchaseTask).filter(PurchaseTask.company_id == company_id).all()
        unresolved = self._unresolved_orders(company_id)
        pipeline = {
            "prepare": len(open_wizards),
            "selling": len(submitted),
            # 실제 주문에서 생긴 작업만(주문번호 없는 수동·시험 작업은 "새 주문"이 아니다).
            # 상품이 재고 SKU에 연결되지 않아 아직 구매 작업이 되지 못한 새 주문도 "새 주문"이다(조용히 빠지지 않게 함께 센다).
            "new_orders": len([t for t in tasks if t.status in _TASK_BEFORE_SUPPLIER and (t.source_order_id or 0) > 0]) + len(unresolved),
            "shipping": len([t for t in tasks if t.status == PurchaseTaskStatus.SHIPPED]),
            "settlement": len([t for t in tasks if t.status in (PurchaseTaskStatus.DELIVERED, PurchaseTaskStatus.REFUND_PENDING)]),
        }
        return {
            "pipeline": pipeline,
            "outbound_days_todo": self._outbound_days_todo(company_id, submitted),
            # 같은 상품을 여러 번 시도한 이력이 있어도 화면에는 상품당 한 건만 보이도록 "현재" 위저드 번호를 준다.
            "unresolved_orders": unresolved,
            "selling_wizard_ids": [p["wizard_id"] for p in submitted if p["wizard_id"] is not None],
            "invoice_todo": [
                {
                    "task_id": o["task"]["id"], "order_id": o["order_id"], "channel_order_id": o["channel_order_id"],
                    "product_name": o["product_name"],
                }
                for o in self.build_orders(company_id)["items"]
                if o["next_action"]["code"] == "REGISTER_INVOICE"
            ],
        }

    def _unresolved_orders(self, company_id: int) -> list[dict]:
        """쿠팡에서 들어왔지만 상품이 재고 SKU에 연결되지 않아 구매 작업이 되지 못한 주문 품목(수량 0은 제외)."""

        if not (_has(self.db, UnresolvedOrderItem.__tablename__) and _has(self.db, OrderChannelFulfillment.__tablename__)):
            return []
        rows = (
            self.db.query(UnresolvedOrderItem, OrderChannelFulfillment)
            .join(OrderChannelFulfillment, OrderChannelFulfillment.id == UnresolvedOrderItem.fulfillment_id)
            .filter(
                UnresolvedOrderItem.company_id == company_id,
                UnresolvedOrderItem.status == "MAPPING_REQUIRED",
                UnresolvedOrderItem.quantity > 0,
            )
            .order_by(UnresolvedOrderItem.id.asc())
            .all()
        )
        return [
            {
                "id": item.id, "channel_order_id": fulfillment.channel_order_id, "product_name": item.product_name_snapshot,
                "quantity": item.quantity, "channel_sku": item.channel_sku,
                "ordered_at": fulfillment.ordered_at.isoformat() if fulfillment.ordered_at else None,
            }
            for item, fulfillment in rows
        ]

    def _submitted_products(self, company_id: int, wizards: list) -> list[dict]:
        """쿠팡에 제출 성공한 상품(상품번호별 1건) — 어느 위저드에서 만들었는지와 저장된 기준 출고일을 함께."""

        rows = (
            self.db.query(MarketplaceSubmission)
            .filter(
                MarketplaceSubmission.company_id == company_id,
                MarketplaceSubmission.status == "SUBMITTED",
                MarketplaceSubmission.external_submission_ref.isnot(None),
            )
            .order_by(MarketplaceSubmission.id.asc())
            .all()
        )
        by_listing: dict[int, list] = {}
        for wizard in wizards:
            data = _json(wizard.materialized_listing_ids_json)
            if isinstance(data, list):
                for entry in data:
                    listing_id = entry.get("listing_id") if isinstance(entry, dict) else None
                    if isinstance(listing_id, int):
                        by_listing.setdefault(listing_id, []).append(wizard)
        products: dict[str, dict] = {}
        for submission in rows:
            ref = str(submission.external_submission_ref).strip()
            if not ref.isdigit():
                continue
            owners = sorted(by_listing.get(submission.listing_id, []), key=lambda w: w.id)
            wizard = owners[-1] if owners else None
            saved_days = None
            name = ""
            if wizard is not None:
                draft = _json(wizard.draft_json)
                name = (draft or {}).get("product_name", "") if isinstance(draft, dict) else ""
                selections = _json(wizard.channel_selections_json)
                if isinstance(selections, list) and selections:
                    fields = (selections[0] or {}).get("required_fields") or {}
                    value = fields.get("outboundShippingTimeDay")
                    saved_days = value if isinstance(value, int) and not isinstance(value, bool) else None
            products[ref] = {
                "seller_product_id": ref, "product_name": name or f"#{ref}", "wizard_id": wizard.id if wizard else None,
                "saved_days": saved_days, "submission_id": submission.id,
            }
        return list(products.values())

    def _outbound_days_todo(self, company_id: int, submitted: list[dict]) -> list[dict]:
        done: set[str] = set()
        if _has(self.db, ChannelActionRequest.__tablename__):
            rows = (
                self.db.query(ChannelActionRequest)
                .filter(
                    ChannelActionRequest.company_id == company_id,
                    ChannelActionRequest.action_type == ChannelActionType.PRODUCT_OUTBOUND_DAYS,
                    ChannelActionRequest.status == ChannelActionStatus.SUCCEEDED,
                )
                .all()
            )
            for row in rows:
                parts = str(row.target_key).split(":")  # product:<id>:days:<n>
                if len(parts) == 4 and parts[3].isdigit() and int(parts[3]) >= 2:
                    done.add(parts[1])
        return [
            p for p in submitted
            if p["seller_product_id"] not in done and (p["saved_days"] is None or p["saved_days"] < 2)
        ]

    # ------------------------------------------------------------
    # 주문 진행
    # ------------------------------------------------------------

    def build_orders(self, company_id: int, limit: int = 50) -> dict:
        orders = (
            self.db.query(Order)
            .filter(Order.company_id == company_id)
            .order_by(Order.ordered_at.desc(), Order.id.desc())
            .limit(limit)
            .all()
        )
        ids = [o.id for o in orders]
        items_by_order: dict[int, list] = {}
        fulfillments: dict[int, list] = {}
        tasks_by_order: dict[int, list] = {}
        if ids:
            for item in self.db.query(OrderItem).filter(OrderItem.company_id == company_id, OrderItem.order_id.in_(ids)).all():
                items_by_order.setdefault(item.order_id, []).append(item)
            for row in self.db.query(OrderChannelFulfillment).filter(
                OrderChannelFulfillment.company_id == company_id, OrderChannelFulfillment.order_id.in_(ids),
            ).all():
                fulfillments.setdefault(row.order_id, []).append(row)
            for task in self.db.query(PurchaseTask).filter(
                PurchaseTask.company_id == company_id, PurchaseTask.source_order_id.in_(ids),
            ).all():
                tasks_by_order.setdefault(task.source_order_id, []).append(task)
        task_ids = [t.id for tasks in tasks_by_order.values() for t in tasks]
        tracking: dict[int, PurchaseTaskTrackingInfo] = {}
        attempts_ok: set[int] = set()
        if task_ids:
            for row in self.db.query(PurchaseTaskTrackingInfo).filter(
                PurchaseTaskTrackingInfo.company_id == company_id, PurchaseTaskTrackingInfo.purchase_task_id.in_(task_ids),
            ).all():
                tracking[row.purchase_task_id] = row
            for row in self.db.query(PurchaseOrderSubmissionAttempt).filter(
                PurchaseOrderSubmissionAttempt.company_id == company_id,
                PurchaseOrderSubmissionAttempt.purchase_task_id.in_(task_ids),
                PurchaseOrderSubmissionAttempt.status == "SUCCEEDED",
            ).all():
                attempts_ok.add(row.purchase_task_id)
        invoice_done = self._invoice_ledger(company_id)

        result = []
        for order in orders:
            result.append(self._order_row(
                order, items_by_order.get(order.id, []), fulfillments.get(order.id, []),
                tasks_by_order.get(order.id, []), tracking, attempts_ok, invoice_done,
            ))
        return {"items": result}

    def _invoice_ledger(self, company_id: int) -> set[str]:
        """작업 장부에서 송장 등록이 끝난(SUCCEEDED) 대상 키 집합."""

        if not _has(self.db, ChannelActionRequest.__tablename__):
            return set()
        rows = (
            self.db.query(ChannelActionRequest.target_key)
            .filter(
                ChannelActionRequest.company_id == company_id,
                ChannelActionRequest.action_type == ChannelActionType.INVOICE_UPLOAD,
                ChannelActionRequest.status == ChannelActionStatus.SUCCEEDED,
            )
            .all()
        )
        return {r[0] for r in rows}

    def _order_row(self, order, items, fulfillments, tasks, tracking, attempts_ok, invoice_done) -> dict:
        task = sorted(tasks, key=lambda t: t.id)[0] if tasks else None
        track = tracking.get(task.id) if task else None
        has_tracking = bool(track and track.tracking_number)
        supplier_ordered = bool(task and (task.id in attempts_ok or has_tracking or task.status in (
            PurchaseTaskStatus.SHIPPED, PurchaseTaskStatus.DELIVERED, PurchaseTaskStatus.COMPLETED,
        )))
        coupang_raw = {f.raw_status for f in fulfillments}
        box = fulfillments[0] if fulfillments else None
        ledger_key = f"order:{box.channel_order_id}:box:{box.shipment_box_id}" if box and box.channel_order_id and box.shipment_box_id else None
        invoice_registered = bool(
            (ledger_key and ledger_key in invoice_done) or (coupang_raw & _COUPANG_AFTER_INVOICE)
        )
        delivered = bool(
            (track and (track.delivery_status or "").upper() == "DELIVERED")
            or str(order.status or "").upper() == "DELIVERED"
            or (task and task.status in (PurchaseTaskStatus.DELIVERED, PurchaseTaskStatus.COMPLETED))
        )
        done = [True, supplier_ordered, has_tracking, invoice_registered, delivered]
        # 앞 단계가 끝나지 않았는데 뒤 단계가 끝났다고 나오면(예: 쿠팡에서 직접 처리) 앞 단계도 지나온 것으로 본다.
        for index in range(len(done) - 2, -1, -1):
            done[index] = done[index] or any(done[index + 1:])
        now_index = next((i for i, flag in enumerate(done) if not flag), len(done))
        details = {
            "ordered": order.ordered_at.isoformat() if order.ordered_at else None,
            "supplier_order": None,
            "supplier_shipped": f"{track.courier or ''} {track.tracking_number}".strip() if has_tracking else None,
            "invoice": "쿠팡 반영 확인" if (coupang_raw & _COUPANG_AFTER_INVOICE) else ("HOMEZ 기록" if invoice_registered else None),
            "delivered": (track.delivery_status if track and track.delivery_status else None),
        }
        steps = []
        for index, key in enumerate(STEP_KEYS):
            state = "done" if done[index] else ("now" if index == now_index else "todo")
            steps.append({"key": key, "state": state, "detail": details.get(key)})

        if now_index >= len(done):
            next_action = {"code": "DONE", "view": None}
        elif now_index == 1:
            next_action = {"code": "ORDER_FROM_SUPPLIER", "view": "purchase-task-detail" if task else "purchase-task"}
        elif now_index == 2:
            next_action = {"code": "WAIT_SUPPLIER_SHIPMENT", "view": "purchase-task-detail" if task else None}
        elif now_index == 3:
            next_action = {"code": "REGISTER_INVOICE" if (task and has_tracking and box) else "CHECK_COUPANG_ORDER", "view": None}
        else:
            next_action = {"code": "WAIT_DELIVERY", "view": None}

        name = items[0].product_name_snapshot if items else (task.product_title if task else "")
        return {
            "order_id": order.id, "channel_order_id": order.channel_order_id, "channel": str(order.channel_code or "").split("#")[0],
            "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None, "status": order.status,
            "total_amount": float(order.total_amount) if order.total_amount is not None else None,
            "buyer_masked": mask_name(order.buyer_name), "product_name": name or "",
            "quantity": sum(int(i.quantity or 0) for i in items) or (int(task.quantity) if task else 0),
            "task": {"id": task.id, "status": task.status} if task else None,
            "tracking": (
                {"courier": track.courier, "number": track.tracking_number, "delivery_status": track.delivery_status}
                if has_tracking else None
            ),
            "coupang_status": sorted(coupang_raw)[0] if coupang_raw else None,
            "coupang_checked_at": (max(f.updated_at for f in fulfillments if f.updated_at).isoformat() if any(f.updated_at for f in fulfillments) else None),
            "shipment_box_id": box.shipment_box_id if box else None,
            "steps": steps, "now_step": now_index + 1 if now_index < len(done) else len(done),
            "next_action": next_action,
        }


__all__ = ["EasyViewService", "mask_name", "STEP_KEYS"]
