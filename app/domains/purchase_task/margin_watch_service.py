"""
=========================================================
Homez OS

File : app/domains/purchase_task/margin_watch_service.py

2026-10-23 — 상품 관리 AI P2: 구매 작업의 저장된 경제성을 **읽기만 해서** `margin_watch.summarize`에 넘긴다(DB 쓰기·외부 호출·AI 없음).
회사 범위(`company_id`)만 읽고, 매입이 필요 없어 생략된 건(재고 보유)·결제 전 취소된 건은 경제성이 의미 없으므로 빼며, 최근 `limit`건(작업 번호 큰 순)까지만 본다.
=========================================================
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.margin_watch import DEFAULT_MIN_MARGIN
from app.domains.purchase_task.margin_watch import summarize
from app.domains.purchase_task.margin_watch import to_economics
from app.domains.purchase_task.model import PurchaseTask

EXCLUDED_STATUSES = (PurchaseTaskStatus.SKIPPED_INVENTORY_AVAILABLE, PurchaseTaskStatus.SOURCE_ORDER_CANCELLED)
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

WING_NOTE = (
    "하한가는 저장된 경제성에서 거꾸로 계산한 대략의 값입니다(반품 예비가 판매가에 비례해 수십 원 오차가 있어 원 단위로 올림). "
    "쿠팡 윙의 자동가격조정을 쓰는 상품은 최저가를 이 값 이상으로 두세요 — 윙 설정은 앱이 읽거나 바꿀 수 없어 직접 확인해야 합니다."
)


class MarginWatchService:

    def __init__(self, db: Session):
        self.db = db

    def report(self, company_id: int, min_margin: Decimal = DEFAULT_MIN_MARGIN, limit: int = DEFAULT_LIMIT) -> dict:
        limit = max(1, min(int(limit), MAX_LIMIT))
        rows = (
            self.db.query(PurchaseTask)
            .filter(PurchaseTask.company_id == company_id, PurchaseTask.status.notin_(EXCLUDED_STATUSES))
            .order_by(PurchaseTask.id.desc())
            .limit(limit)
            .all()
        )
        tasks = [to_economics(r.id, r.product_title, r.status, r.coupang_sale_amount, r.coupang_fee_amount, r.expected_net_profit, r.expected_margin_rate) for r in rows]
        report = summarize(tasks, min_margin)
        return {**report, "checked_tasks": len(tasks), "limit": limit, "wing_note": WING_NOTE}


__all__ = ["MarginWatchService", "EXCLUDED_STATUSES", "DEFAULT_LIMIT", "MAX_LIMIT", "WING_NOTE"]
