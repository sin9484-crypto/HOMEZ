"""
=========================================================
Homez OS

File : app/domains/orchestration/morning_brief_service.py

2026-11-01 — AI 통합 계획 6단계(남은 것): 아침 브리핑을 서버가 정해진 시간에 만들어 **앱 안 알림**으로 남긴다. 화면을 열지 않아도
알림 종에 하루 한 줄 요약이 온다. 새 판단은 없다 — '오늘 할 일' 서버 집계(`EasyViewService.build_today`), 승인 센터, 시스템 점검,
3단계 탐색 흐름이 이미 계산한 값의 개수만 센다. 저장된 값만 읽고 쿠팡·온채널·네이버·Claude를 부르지 않는다.

  - 언제: 1시간마다 확인해 한국 시각 7시 이후 하루 한 번(6시 상품 발굴 자동 실행 결과가 들어간 뒤).
  - 누구에게: 회사의 활성 사용자마다 한 건. 기존 알림 설정 화면의 '아침 브리핑'을 끈 사용자는 건너뛴다.
  - 어디로: 기존 알림 센터(`NotificationDeliveryService.dispatch`)의 앱 안 알림. 같은 사람·같은 날은 한 번만(알림 센터의 멱등성 키).
    이메일(사용자 결정 D5): 그 사람이 '아침 브리핑 이메일'을 켠 경우에만 같은 내용을 보낸다(`BriefEmailService`, 승인 센터 허용 1회).
    메일이 실패해도 앱 안 알림은 남고, 같은 날 다시 보내지 않는다(실패 사실은 설정 화면·앱 안 알림으로 알린다).
  - 읽지 못한 칸은 0으로 쓰지 않고 '읽지 못함'에 이름을 남긴다.
=========================================================
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from datetime import timedelta
from typing import Callable

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

logger = logging.getLogger("homez")

BRIEF_EVENT = "DAILY_BRIEFING"
RUN_FROM_HOUR_KST = 7
SOON_HOURS = 24  # '24시간 안에 끝나는 허용'
OLD_INQUIRY_HOURS = 24  # 쉬운 보기 문의 화면의 '오래됨'(INQ_OLD_HOURS)과 같은 값 — 쿠팡 기한이 아니다
LINK_PATH = "easy-today"
_KST = timedelta(hours=9)

# 요약 줄의 순서와 문구. 먼저 볼 것(오래된 문의) → 주문·배송 → 승인 → 상품 발굴 → 점검 순.
LINES = (
    ("inquiries_old", "24시간 넘은 문의 {n}건"),
    ("inquiries", "답변 기다리는 문의 {n}건"),
    ("product_denied", "쿠팡 승인반려 {n}건"),
    ("new_orders", "공급처에 넣을 주문 {n}건"),
    ("invoice", "송장 등록 {n}건"),
    ("returns", "반품 {n}건"),
    ("supplier_returns", "공급처 반품 요청 {n}건"),
    ("outbound_days", "출고일 확인 {n}건"),
    ("approvals", "승인 센터 대기 {n}건"),
    ("allowances_expired", "끝난 허용 {n}건"),
    ("allowances_soon", "24시간 안에 끝나는 허용 {n}건"),
    ("discovery_review", "상품 발굴 검토 {n}건"),
    ("discovery_evaluate", "상품 발굴 평가 {n}건"),
    ("system", "시스템 점검 이상 {n}건"),
)
SOURCE_NAMES = {"today": "주문·문의", "approvals": "승인 센터", "discovery": "상품 발굴", "system": "시스템 점검"}


def inquiry_age_hours(value, now_utc: datetime) -> int | None:
    """쿠팡 문의 시각(한국 시각 'YYYY-MM-DDTHH:MM…')이 받은 지 몇 시간인지 — 화면 `inquiryAgeHours`와 같은 계산. 못 읽으면 None."""

    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", str(value or ""))
    if not match:
        return None
    try:
        at_utc = datetime(*(int(x) for x in match.groups())) - _KST
    except ValueError:
        return None
    return max(0, int((now_utc - at_utc).total_seconds() // 3600))


def _has(db: Session, table: str) -> bool:
    return sa_inspect(db.get_bind()).has_table(table)


class MorningBriefService:

    def __init__(self, db: Session, *, now_factory: Callable[[], datetime] | None = None, email_service=None):
        self.db = db
        self._email_service = email_service
        self._now = now_factory or datetime.utcnow

    # ---------------- 무엇을 셀까 ----------------
    def _today_counts(self, company_id: int, now: datetime) -> dict:
        from app.domains.easy_view.service import EasyViewService

        today = EasyViewService(self.db).build_today(company_id)
        after = today.get("after_sales") or {}
        inquiries = after.get("inquiries_open") or []
        ages = [inquiry_age_hours(q.get("t"), now) for q in inquiries]
        old = sum(1 for h in ages if h is not None and h >= OLD_INQUIRY_HOURS)
        return {
            "inquiries_old": old,
            "inquiries": len(inquiries) - old,
            "product_denied": sum(1 for p in today.get("product_status") or [] if p.get("status") == "DENIED"),
            "new_orders": int((today.get("pipeline") or {}).get("new_orders") or 0),
            "invoice": len(today.get("invoice_todo") or []),
            "returns": len(after.get("returns_open") or []),
            "supplier_returns": sum(1 for c in today.get("supplier_returns") or [] if c.get("supplier_state") == "NOT_REQUESTED"),
            "outbound_days": len(today.get("outbound_days_todo") or []),
        }

    def _approval_counts(self, company_id: int) -> dict:
        from app.domains.external_call.constants import AllowanceStatus
        from app.domains.external_call.model import ExternalCallAllowance
        from app.domains.external_call.service import ExternalCallPolicyService

        if not ExternalCallPolicyService.installed(self.db):
            return {"approvals": 0, "allowances_expired": 0, "allowances_soon": 0}
        pending = (
            self.db.query(ExternalCallAllowance)
            .filter(ExternalCallAllowance.company_id == company_id, ExternalCallAllowance.status == AllowanceStatus.PENDING)
            .count()
        )
        # 승인 센터 '연장' 목록과 같은 규칙(정기 조회 허용, 같은 목적은 최신 것만)에 창만 24시간으로 — 줄 문구가 '24시간 안에'다.
        renewable = ExternalCallPolicyService(self.db, now_factory=self._now).renewable(company_id, within_hours=SOON_HOURS)
        return {
            "approvals": pending,
            "allowances_expired": sum(1 for r in renewable if r["state"] == "EXPIRED"),
            "allowances_soon": sum(1 for r in renewable if r["state"] == "EXPIRING"),
        }

    def _discovery_counts(self, company_id: int) -> dict:
        from app.domains.product_candidate.discovery_flow_service import NEXT_EVALUATE
        from app.domains.product_candidate.discovery_flow_service import NEXT_REVIEW
        from app.domains.product_candidate.discovery_flow_service import DiscoveryFlowService

        counts = DiscoveryFlowService(self.db).list_flow(company_id)["counts"]
        return {"discovery_review": int(counts.get(NEXT_REVIEW, 0)), "discovery_evaluate": int(counts.get(NEXT_EVALUATE, 0))}

    def _system_counts(self) -> dict:
        from app.domains.diagnostics.health_service import HealthService

        checks = HealthService(self.db, now_factory=self._now).summary().get("checks") or []
        return {"system": sum(1 for c in checks if c.get("level") in ("FAIL", "WARN"))}

    def build(self, company_id: int) -> dict:
        """{"counts": {...}, "unknown": [못 읽은 칸 이름], "title", "message"} — 쓰지 않는다."""

        now = self._now()
        counts: dict[str, int] = {}
        unknown: list[str] = []
        sources = (
            ("today", lambda: self._today_counts(company_id, now)),
            ("approvals", lambda: self._approval_counts(company_id)),
            ("discovery", lambda: self._discovery_counts(company_id)),
            ("system", self._system_counts),
        )
        for name, read in sources:
            try:
                counts.update(read())
            except Exception as exc:  # noqa: BLE001 — 한 칸을 못 읽어도 나머지는 보낸다(0으로 꾸미지 않고 '읽지 못함'에 남긴다)
                self.db.rollback()
                unknown.append(SOURCE_NAMES[name])
                logger.warning("아침 브리핑: %s 읽기 실패: %s", name, exc)
        kst = now + _KST
        title = f"오늘 브리핑 — {kst.month}월 {kst.day}일"
        parts = [text.format(n=counts[key]) for key, text in LINES if counts.get(key)]
        message = (" · ".join(parts) + ".") if parts else "오늘은 쌓인 일이 없어요."
        if unknown:
            message += f" 읽지 못함: {', '.join(unknown)}."
        message += " 자세한 내용과 처리는 '오늘 할 일'에서 해요(브리핑은 아무것도 보내거나 바꾸지 않아요)."
        return {"counts": counts, "unknown": unknown, "title": title, "message": message, "date": kst.date().isoformat()}

    # ---------------- 언제·누구에게 ----------------
    @staticmethod
    def installed(db: Session) -> bool:
        return _has(db, "notification_email_logs")

    @staticmethod
    def candidate_companies(db: Session) -> list[int]:
        from app.domains.user.model import User

        rows = db.query(User.company_id).filter(User.is_active.is_(True), User.company_id.isnot(None)).distinct().all()
        return sorted(r[0] for r in rows)

    def idempotency_key(self, company_id: int, user_id: int, date_text: str) -> str:
        return f"{BRIEF_EVENT}:{company_id}:{user_id}:{date_text}"

    def recipients(self, company_id: int, date_text: str) -> list[int]:
        """오늘 아직 받지 않았고 '아침 브리핑'을 끄지 않은 활성 사용자."""

        from app.domains.notification_center.repository import NotificationDeliveryRepository
        from app.domains.user.model import User

        repo = NotificationDeliveryRepository(self.db)
        users = (
            self.db.query(User.id)
            .filter(User.company_id == company_id, User.is_active.is_(True))
            .order_by(User.id)
            .all()
        )
        result = []
        for (user_id,) in users:
            pref = repo.get_event_preference(company_id, user_id, BRIEF_EVENT)
            if pref is not None and not pref.enabled:
                continue
            if repo.get_email_log_by_idempotency(company_id, self.idempotency_key(company_id, user_id, date_text)) is not None:
                continue
            result.append(user_id)
        return result

    def _email_for(self, user_id: int, company_id: int):
        """(메일 Provider, 받는 주소). 켜지 않았거나 설정을 읽지 못하면 (None, None) — 앱 안 알림은 그대로 나간다."""

        from app.domains.notification_center.brief_email_service import BriefEmailService

        try:
            found = (self._email_service or BriefEmailService(self.db)).delivery_for(user_id, company_id)
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            logger.warning("아침 브리핑: 사용자 %s 이메일 설정 읽기 실패: %s", user_id, type(exc).__name__)
            return None, None
        return found if found is not None else (None, None)

    def run(self, company_id: int) -> dict:
        """7시(한국 시각) 전이거나 보낼 사람이 없으면 아무것도 하지 않는다. 보낸 사람 수를 돌려준다."""

        if not self.installed(self.db):
            return {"sent": 0, "reason": "NOT_INSTALLED"}
        kst = self._now() + _KST
        if kst.hour < RUN_FROM_HOUR_KST:
            return {"sent": 0, "reason": "TOO_EARLY"}
        date_text = kst.date().isoformat()
        users = self.recipients(company_id, date_text)
        if not users:
            return {"sent": 0, "reason": "NOTHING_TO_SEND"}
        brief = self.build(company_id)

        from app.domains.notification_center.delivery_service import NotificationDeliveryService

        sent = 0
        for user_id in users:
            provider, to_email = self._email_for(user_id, company_id)
            result = NotificationDeliveryService(self.db, provider=provider).dispatch(
                BRIEF_EVENT, company_id=company_id, user_id=user_id,
                idempotency_key=self.idempotency_key(company_id, user_id, date_text),
                title=brief["title"], message=brief["message"], link_path=LINK_PATH,
                entity_ref=f"daily_briefing:{date_text}",
                to_email=to_email, entity_summary=brief["message"],
            )
            if result.status != "SKIPPED_DUPLICATE":
                sent += 1
        return {"sent": sent, "reason": None, "unknown": brief["unknown"]}


__all__ = ["MorningBriefService", "BRIEF_EVENT", "RUN_FROM_HOUR_KST", "inquiry_age_hours"]
