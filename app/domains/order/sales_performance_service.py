"""
=========================================================
Homez OS

File : app/domains/order/sales_performance_service.py

2026-10-18 — 마케팅 AI M4(lite): **판매 성과 보기**. 쿠팡 매출내역을 최근 N일(1~31, 어제까지) 읽어 옵션별로 집계해 돌려준다.

  - 사용자가 버튼을 눌렀을 때만 읽는다(화면을 열거나 새로고침할 때 읽지 않는다). 읽기(GET)만 하고 쿠팡에 쓰지 않는다.
  - 조회는 `AFTER_SALES_CHECK` 허용(상시·시간당 상한)이 있어야만 나간다. 허용이 없으면 호출하지 않고 허용 요청만 남는다(기존 배송 이후 읽기와 같은 경로·같은 허용).
  - 한 번 읽기는 허용 한 번(쪽마다가 아니라 읽기 한 번)으로 세고, 쪽 사이에는 쿠팡 한도(초당 5회)를 지키려고 쉰다. 최대 10쪽(500주문)에서 멈추고 넘으면 `truncated`로 알린다.
  - 저장하지 않는다(DB 쓰기 없음). 읽기 실패면 그때까지 읽은 일부로 집계하지 않는다 — 일부만 보고 성과를 말하지 않는다.
  - 고객·주소·연락처는 읽는 단계에서 이미 버려진다(옵션 ID·주문번호·수량·금액·수수료만).
  - 집계는 `ai_learning/marketing_outcomes.summarize`(순수 함수)가 한다. 어떤 제안이 맞았는지는 판정하지 않는다.
=========================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.domains.ai_learning.marketing_outcomes import summarize
from app.domains.purchase_task.channel_action_service import CoupangChannelActions
from app.domains.purchase_task.coupang_after_sales_provider import MAX_REVENUE_RANGE_DAYS
from app.domains.store_connection.model import StoreConnection


class SalesPerformanceService:

    def __init__(self, db: Session, *, actions_factory: Callable[[Session], CoupangChannelActions] | None = None, now_factory=None):
        self.db = db
        self._actions_factory = actions_factory or (lambda session: CoupangChannelActions(session))
        self._now = now_factory or datetime.utcnow

    def _connection_id(self, company_id: int) -> int | None:
        row = (
            self.db.query(StoreConnection.id)
            .filter(StoreConnection.company_id == company_id, StoreConnection.marketplace_code == "COUPANG",
                    StoreConnection.connection_status == "CONNECTED")
            .order_by(StoreConnection.id.asc())
            .first()
        )
        return row[0] if row else None

    def read(self, company_id: int, days: int = 30) -> dict:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_REVENUE_RANGE_DAYS:
            raise BadRequestException(f"조회 기간은 1~{MAX_REVENUE_RANGE_DAYS}일이어야 합니다.")
        base = {"ok": False, "blocked": False, "no_connection": False, "detail": "", "days": days, "calls": 0, "truncated": False, "summary": None,
                "checked_at": self._now().isoformat(timespec="seconds")}
        store_id = self._connection_id(company_id)
        if store_id is None:
            return {**base, "no_connection": True, "detail": "연결된 쿠팡 상점이 없어요"}
        result, failure = self._actions_factory(self.db).read_after_sales(company_id=company_id, store_connection_id=store_id, what="revenue", days=days)
        if result is None:
            detail = failure.detail if failure is not None else ""
            blocked = str(detail).startswith("EXTERNAL_") or "허용" in str(detail)
            return {**base, "blocked": blocked, "detail": str(detail)[:300]}
        if not result.ok:
            return {**base, "calls": result.calls, "detail": str(result.detail)[:300]}
        return {**base, "ok": True, "calls": result.calls, "truncated": bool(result.truncated), "summary": summarize(list(result.rows))}
