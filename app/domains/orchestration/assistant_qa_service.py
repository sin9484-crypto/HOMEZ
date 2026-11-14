"""
=========================================================
Homez OS

File : app/domains/orchestration/assistant_qa_service.py

2026-11-01 — AI 통합 계획 5단계(비용 0 부분): **비서에게 묻기**. "이번 주 반품 몇 건?" 같은 정해진 질문에 실제 기록을 규칙으로 세어
답한다. Claude를 부르지 않는다(자유 문장 답변은 사용자 결정 D2 — 키·월 한도 — 뒤의 일). 새 판단은 없고, '오늘 할 일'·승인 센터·
탐색 흐름·AI 사용 기록이 이미 가진 값의 개수만 센다.

  - 질문은 고정 목록(`QUESTIONS`)의 번호로만 받는다. 사용자가 적은 문장은 화면 안에서만 이 목록과 맞춰 보고 서버로 보내지 않는다.
  - 읽기만 한다: DB에 쓰지 않고 쿠팡·온채널·네이버·Claude를 부르지 않는다.
  - 못 읽은 답은 0으로 꾸미지 않고 `unknown`으로 돌려준다. 시각을 읽을 수 없는 기록은 '시각 모름'으로 따로 센다.
  - 시각: 주문 시각(`Order.ordered_at`)은 쿠팡이 준 한국 시각 그대로 저장돼 있다. 쿠팡 반품 접수 시각(`t`)도 한국 시각 글자다.
    '오늘'은 한국 시각 0시부터, '이번 주'는 한국 시각 월요일 0시부터.

2026-11-14 — 조사 문서 `docs/research/20261109_AI_FOUNDATIONS_AND_GROWTH.md` 3-7 '운영 비서 2단계는 읽기 도구 묶음부터'(사용자 지시
"다음 작업 진행해라"). 질문 하나가 겹치지 않는 읽기 도구 하나다. 빠져 있던 **정산**과 **주문 진행(배송)**을 더했다.
  - 정산(`SETTLEMENT`): 배송 이후 감시가 6시간마다 쿠팡에서 읽어 남긴 정산 기록(`COUPANG_AFTER_SALES_EVENT`, 항목별 최신 상태)만 센다.
    지급 예정(완료가 아닌 것)·가장 가까운 지급일·지급일이 지났는데 완료로 읽히지 않은 것·이번 달 지급 완료, 그리고 **마지막으로 읽은 뒤
    지난 시간**을 함께 준다(오래된 기록을 지금 값처럼 보이지 않게). 한 번도 읽은 적이 없으면 0이 아니라 '못 읽음'이다.
  - 주문 진행(`ORDER_FLOW`): '오늘 할 일'의 단계 숫자(공급처에 넣을 주문·송장 등록·배송 중·배송 끝나 정산 기다림·반품 진행)를 그대로 쓴다.
  - 재고는 넣지 않았다: 무재고 중개라 우리 재고 수량(`inventory_skus`)은 모두 0이고, 공급처 재고는 저장돼 있지 않다 — 세면 '품절'로 잘못 읽힌다.
=========================================================
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy.orm import Session

from app.domains.orchestration.morning_brief_service import LINES
from app.domains.orchestration.morning_brief_service import OLD_INQUIRY_HOURS
from app.domains.orchestration.morning_brief_service import MorningBriefService
from app.domains.orchestration.morning_brief_service import inquiry_age_hours

logger = logging.getLogger("homez")

_KST = timedelta(hours=9)
TODO_FIRST_LINES = 3
DENIED_NAMES = 5

# 질문 번호 → 답을 보여 줄 화면. 질문 문구와 맞춰 볼 낱말은 화면(`easy_view.js`의 ASK_QUESTIONS)에 있다.
QUESTIONS = {
    "TODO_FIRST": "easy-today",
    "ORDERS_TODAY": "easy-orders",
    "ORDERS_WEEK": "easy-orders",
    "RETURNS_WEEK": "easy-orders",
    "INQUIRIES_OPEN": "easy-orders",
    "APPROVALS": "easy-approvals",
    "PRODUCT_DENIED": "easy-products",
    "AI_COST_MONTH": "easy-more",
    "DISCOVERY": "easy-analysis",
    "SETTLEMENT": "easy-money",
    "ORDER_FLOW": "easy-orders",
}


def kst_wall(value) -> datetime | None:
    """한국 시각 글자('YYYY-MM-DDTHH:MM…' 또는 'YYYY-MM-DD HH:MM…')를 시간대 없는 한국 시각으로. 못 읽으면 None."""

    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", str(value or ""))
    if not match:
        return None
    try:
        return datetime(*(int(x) for x in match.groups()))
    except ValueError:
        return None


SETTLEMENT_DONE = "DONE"   # 쿠팡 정산 상태 — 지급 완료. 그 밖(SUBJECT 등)은 아직 지급 전으로 본다.


def pay_date(value):
    """정산 지급일 글자('YYYY-MM-DD…')를 날짜로. 못 읽으면 None."""

    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return None
    try:
        return datetime(*(int(x) for x in match.groups())).date()
    except ValueError:
        return None


def _same_month(day, today) -> bool:
    return day is not None and (day.year, day.month) == (today.year, today.month)


def _amount(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None   # NaN은 못 읽은 것


def _won(events: list[dict]) -> int:
    """읽은 금액만 더한다(못 읽은 금액은 0으로 꾸미지 않고 따로 센다)."""

    return int(round(sum(a for a in (_amount(e.get("fin")) for e in events) if a is not None)))


class AssistantQaService:

    def __init__(self, db: Session, *, now_factory: Callable[[], datetime] | None = None):
        self.db = db
        self._now = now_factory or datetime.utcnow

    # ---------------- 기준 시각 ----------------
    def _kst_now(self) -> datetime:
        return self._now() + _KST

    def _today_start(self) -> datetime:
        kst = self._kst_now()
        return datetime(kst.year, kst.month, kst.day)

    def _week_start(self) -> datetime:
        start = self._today_start()
        return start - timedelta(days=start.weekday())

    # ---------------- 질문별 답 ----------------
    def _todo_first(self, company_id: int, user_id: int) -> dict:
        brief = MorningBriefService(self.db, now_factory=self._now).build(company_id)
        counts = brief["counts"]
        facts = [{"key": key, "n": counts[key]} for key, _text in LINES if counts.get(key)][:TODO_FIRST_LINES]
        return {"facts": facts, "unknown_sources": list(brief["unknown"])}

    def _orders_since(self, company_id: int, since: datetime) -> int:
        from app.domains.order.model import Order

        return self.db.query(Order).filter(Order.company_id == company_id, Order.ordered_at >= since).count()

    def _orders_today(self, company_id: int, user_id: int) -> dict:
        start = self._today_start()
        return {"facts": [{"key": "orders_today", "n": self._orders_since(company_id, start)}], "since": start.date().isoformat()}

    def _orders_week(self, company_id: int, user_id: int) -> dict:
        start = self._week_start()
        return {"facts": [{"key": "orders_week", "n": self._orders_since(company_id, start)}], "since": start.date().isoformat()}

    def _after_sales(self) -> dict:
        from app.domains.easy_view.service import EasyViewService

        return EasyViewService(self.db).build_after_sales()

    def _returns_week(self, company_id: int, user_id: int) -> dict:
        from app.domains.order.after_sales_monitor_service import AfterSalesMonitorService

        start = self._week_start()
        events = [e for e in AfterSalesMonitorService.latest_events(self.db) if e.get("k") == "RETURN"]
        times = [kst_wall(e.get("t")) for e in events]
        facts = [
            {"key": "returns_week", "n": sum(1 for t in times if t is not None and t >= start)},
            {"key": "returns_open", "n": sum(1 for e in events if e.get("s") != "RETURNS_COMPLETED")},
        ]
        no_time = sum(1 for t in times if t is None)
        if no_time:
            facts.append({"key": "returns_no_time", "n": no_time})
        return {"facts": facts, "since": start.date().isoformat()}

    def _inquiries_open(self, company_id: int, user_id: int) -> dict:
        now = self._now()
        ages = [inquiry_age_hours(q.get("t"), now) for q in self._after_sales().get("inquiries_open") or []]
        known = [h for h in ages if h is not None]
        facts = [
            {"key": "inquiries_open", "n": len(ages)},
            {"key": "inquiries_old", "n": sum(1 for h in known if h >= OLD_INQUIRY_HOURS)},
        ]
        if known:
            facts.append({"key": "inquiries_oldest_hours", "n": max(known)})
        if len(known) < len(ages):
            facts.append({"key": "inquiries_no_time", "n": len(ages) - len(known)})
        return {"facts": facts}

    def _approvals(self, company_id: int, user_id: int) -> dict:
        counts = MorningBriefService(self.db, now_factory=self._now)._approval_counts(company_id)
        return {"facts": [{"key": key, "n": counts[key]} for key in ("approvals", "allowances_expired", "allowances_soon")]}

    def _product_denied(self, company_id: int, user_id: int) -> dict:
        from app.domains.easy_view.service import EasyViewService

        denied = [p for p in EasyViewService(self.db).build_today(company_id).get("product_status") or [] if p.get("status") == "DENIED"]
        names = [str(p.get("product_name") or "—") for p in denied][:DENIED_NAMES]
        return {"facts": [{"key": "product_denied", "n": len(denied)}], "names": names}

    def _ai_cost_month(self, company_id: int, user_id: int) -> dict:
        from app.domains.marketplace_listing.ai_draft_service import AiDraftService

        # 사용 기록 합계와 저장된 설정만 읽는다 — 키 저장소는 열지 않는다(저장소 자리는 비워 둔다).
        service = AiDraftService(self.db, None, now_factory=self._now)
        used, calls = service._month_usage()
        limit = int(service._settings(user_id, company_id).get("monthly_limit_krw") or 0)
        return {"facts": [
            {"key": "ai_calls", "n": calls},
            {"key": "ai_used_krw", "n": int(round(used))},
            {"key": "ai_limit_krw" if limit > 0 else "ai_limit_none", "n": limit},
        ]}

    def _discovery(self, company_id: int, user_id: int) -> dict:
        from app.domains.product_candidate.discovery_flow_service import NEXT_EVALUATE
        from app.domains.product_candidate.discovery_flow_service import NEXT_NEW_PRODUCT
        from app.domains.product_candidate.discovery_flow_service import NEXT_REVIEW
        from app.domains.product_candidate.discovery_flow_service import NEXT_TREND
        from app.domains.product_candidate.discovery_flow_service import DiscoveryFlowService

        counts = DiscoveryFlowService(self.db).list_flow(company_id)["counts"]
        return {"facts": [{"key": f"discovery_{step.lower()}", "n": int(counts.get(step, 0))}
                          for step in (NEXT_REVIEW, NEXT_EVALUATE, NEXT_NEW_PRODUCT, NEXT_TREND)]}

    def _settlement(self, company_id: int, user_id: int) -> dict:
        from app.domains.easy_view.service import EasyViewService
        from app.domains.order.after_sales_monitor_service import AfterSalesMonitorService

        checked = EasyViewService(self.db)._settlement_checked_at(company_id)
        events = [e for e in AfterSalesMonitorService.latest_events(self.db) if e.get("k") == "SETTLEMENT"]
        if checked is None and not events:
            raise LookupError("쿠팡 정산 내역을 아직 읽은 적이 없다")
        today = self._today_start().date()
        upcoming = [e for e in events if e.get("st") != SETTLEMENT_DONE]
        paid_month = [e for e in events if e.get("st") == SETTLEMENT_DONE and _same_month(pay_date(e.get("d")), today)]
        dates = [d for d in (pay_date(e.get("d")) for e in upcoming) if d is not None]
        ahead = sorted(d for d in dates if d >= today)
        facts = [
            {"key": "settlement_upcoming", "n": len(upcoming)},
            {"key": "settlement_upcoming_krw", "n": _won(upcoming)},
        ]
        if ahead:
            facts.append({"key": "settlement_next_days", "n": (ahead[0] - today).days})
        overdue = sum(1 for d in dates if d < today)
        if overdue:
            facts.append({"key": "settlement_overdue", "n": overdue})
        facts += [
            {"key": "settlement_paid_month", "n": len(paid_month)},
            {"key": "settlement_paid_month_krw", "n": _won(paid_month)},
        ]
        no_amount = sum(1 for e in upcoming + paid_month if _amount(e.get("fin")) is None)
        if no_amount:
            facts.append({"key": "settlement_amount_unknown", "n": no_amount})
        if checked is not None:
            at = datetime.fromisoformat(str(checked).replace("Z", "+00:00")).replace(tzinfo=None)
            facts.append({"key": "settlement_checked_hours", "n": max(0, int((self._now() - at).total_seconds() // 3600))})
        out: dict = {"facts": facts}
        if ahead:
            out["next_date"] = ahead[0].isoformat()
        return out

    def _order_flow(self, company_id: int, user_id: int) -> dict:
        from app.domains.easy_view.service import EasyViewService

        today = EasyViewService(self.db).build_today(company_id)
        pipeline = today.get("pipeline") or {}
        return {"facts": [
            {"key": "new_orders", "n": int(pipeline.get("new_orders") or 0)},
            {"key": "invoice", "n": len(today.get("invoice_todo") or [])},
            {"key": "shipping", "n": int(pipeline.get("shipping") or 0)},
            {"key": "delivered_waiting", "n": int(pipeline.get("settlement") or 0)},
            {"key": "returning", "n": int(pipeline.get("returning") or 0)},
        ]}

    # ---------------- 묻기 ----------------
    def answer(self, company_id: int, user_id: int, question_id: str) -> dict:
        """{"question_id", "link", "as_of", "unknown", "facts": [{"key", "n"}], ...} — 쓰지 않는다. 모르는 질문 번호는 KeyError."""

        if question_id not in QUESTIONS:
            raise KeyError(question_id)
        read = getattr(self, f"_{question_id.lower()}")
        result = {"question_id": question_id, "link": QUESTIONS[question_id], "as_of": self._now().isoformat(timespec="seconds")}
        try:
            result.update(read(company_id, user_id))
            result["unknown"] = False
        except Exception as exc:  # noqa: BLE001 — 못 읽은 답은 0으로 꾸미지 않는다
            self.db.rollback()
            logger.warning("비서에게 묻기: %s 읽기 실패: %s", question_id, exc)
            result.update({"facts": [], "unknown": True})
        return result


__all__ = ["AssistantQaService", "QUESTIONS", "SETTLEMENT_DONE", "kst_wall", "pay_date"]
