"""
2026-11-01 — AI 통합 계획 6단계(남은 것): 아침 브리핑을 정해진 시간에 앱 안 알림으로.

고정하는 약속
  - 7시(한국 시각) 전에는 보내지 않고, 같은 사람·같은 날은 한 번만, 알림 설정에서 끈 사람·다른 회사·비활성 사용자에게는 보내지 않는다.
  - 개수는 '오늘 할 일' 서버 집계·승인 센터·탐색 흐름·시스템 점검이 이미 계산한 값에서 센다(새 판단 없음). 0인 줄은 빼고,
    못 읽은 칸은 0으로 꾸미지 않고 '읽지 못함'에 이름을 남긴다.
  - 앱 안 알림만 — 이메일은 기본으로 보내지 않는다. 바깥 호출·쓰기(알림 외) 없음.
  - 한 회사의 실패가 다른 회사·스케줄러를 막지 않는다.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.main  # noqa: F401 — 모든 모델을 메타데이터에 올린다
from app.database.base import Base
from app.domains.decision.model import DecisionEvaluation
from app.domains.external_call.constants import AllowanceMode
from app.domains.external_call.constants import CallKind
from app.domains.external_call.constants import CallPurpose
from app.domains.external_call.constants import ExternalSystem
from app.domains.external_call.model import ExternalCallAllowance
from app.domains.external_call.model import ExternalCallLog
from app.domains.external_call.service import ExternalCallPolicyService
from app.domains.notification_center.event_catalog import EVENT_CATALOG
from app.domains.notification_center.event_catalog import NotificationSeverity
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationEmailLog
from app.domains.notification_center.model import NotificationEventPreference
from app.domains.orchestration import morning_brief_service as brief_mod
from app.domains.orchestration.morning_brief_service import MorningBriefService
from app.domains.orchestration.morning_brief_service import inquiry_age_hours
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService
from app.domains.scheduler import jobs
from app.domains.user.model import User

ROOT = Path(__file__).resolve().parents[1]
A, B = 1, 2
MORNING = datetime(2026, 10, 31, 22, 0)    # 한국 시각 11-01 07:00
DAWN = datetime(2026, 10, 31, 21, 59)      # 한국 시각 11-01 06:59
NEXT_DAY = datetime(2026, 11, 1, 22, 0)    # 한국 시각 11-02 07:00

_TEMPLATE = {}


def _template_db() -> str:
    if "path" not in _TEMPLATE:
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "template.db")
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE audit_logs (id INTEGER NOT NULL PRIMARY KEY, company_id INTEGER, user_id INTEGER, action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), ip_address VARCHAR(50), created_at DATETIME)"))
        engine.dispose()
        _TEMPLATE["path"], _TEMPLATE["folder"] = path, folder
    return _TEMPLATE["path"]


def tearDownModule():
    if "folder" in _TEMPLATE:
        shutil.rmtree(_TEMPLATE["folder"], ignore_errors=True)
    _TEMPLATE.clear()  # 같은 실행에서 모듈이 다시 돌아도 지운 틀을 가리키지 않게


def today_payload(**over):
    payload = {
        "pipeline": {"new_orders": 2},
        "invoice_todo": [{"task_id": 1}],
        "outbound_days_todo": [],
        "supplier_returns": [{"supplier_state": "NOT_REQUESTED"}, {"supplier_state": "REQUESTED"}, {"supplier_state": "CONFIRMED"}],
        "product_status": [{"status": "DENIED"}, {"status": "UNDER_REVIEW"}, {"status": "APPROVED"}, {"status": None}],
        "after_sales": {
            "returns_open": [{"id": 1}],
            # 한국 시각 문의 시각: 30시간 전, 3시간 전, 모름
            "inquiries_open": [{"t": "2026-10-31T01:00:00"}, {"t": "2026-11-01T04:00:00"}, {"t": None}],
        },
    }
    payload.update(over)
    return payload


class DbCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copyfile(_template_db(), f"{self.tmp}/t.db")
        self.engine = create_engine(f"sqlite:///{self.tmp}/t.db", connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()
        self.now = MORNING

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def user(self, company_id, name, active=True):
        u = User(company_id=company_id, email=f"{name}@example.com", username=name, password_hash="x", is_active=active)
        self.db.add(u)
        self.db.commit()
        return u.id

    def service(self, quiet_system=True):
        svc = MorningBriefService(self.db, now_factory=lambda: self.now)
        if quiet_system:
            svc._system_counts = lambda: {"system": 0}  # 시험 환경의 스케줄러 상태에 따라 달라지지 않게
        return svc

    def notifications(self, company_id=None):
        self.db.expire_all()
        q = self.db.query(Notification)
        if company_id is not None:
            q = q.filter(Notification.company_id == company_id)
        return q.order_by(Notification.id).all()


# ---------------------------------------------------------------------------
# 무엇을 세나
# ---------------------------------------------------------------------------

class CountTests(DbCase):

    def test_inquiry_age_reads_korean_time_like_the_screen(self):
        self.assertEqual(inquiry_age_hours("2026-10-31T01:00:00", MORNING), 30)   # 한국 01:00 = UTC 전날 16:00
        self.assertEqual(inquiry_age_hours("2026-11-01T07:30", MORNING), 0)       # 미래(시계 차이)는 0
        self.assertIsNone(inquiry_age_hours(None, MORNING))
        self.assertIsNone(inquiry_age_hours("어제", MORNING))
        self.assertIsNone(inquiry_age_hours("2026-13-40T01:00", MORNING))

    def test_today_counts_come_from_the_existing_today_summary(self):
        with mock.patch("app.domains.easy_view.service.EasyViewService.build_today", return_value=today_payload()):
            counts = self.service()._today_counts(A, MORNING)
        self.assertEqual(counts, {
            "inquiries_old": 1, "inquiries": 2, "product_denied": 1, "new_orders": 2, "invoice": 1, "returns": 1,
            "supplier_returns": 1, "outbound_days": 0,
        })

    def test_the_old_inquiry_line_starts_at_twenty_four_hours(self):
        payload = today_payload(after_sales={"inquiries_open": [{"t": "2026-10-31T07:00"}, {"t": "2026-10-31T07:01"}]})
        with mock.patch("app.domains.easy_view.service.EasyViewService.build_today", return_value=payload):
            counts = self.service()._today_counts(A, MORNING)
        self.assertEqual((counts["inquiries_old"], counts["inquiries"]), (1, 1))

    def test_approval_counts_pending_requests_and_ending_allowances(self):
        policy = ExternalCallPolicyService(self.db, now_factory=lambda: MORNING)
        policy.ensure_pending_request(A, ExternalSystem.NAVER, CallKind.READ, CallPurpose.TREND_LOOKUP)
        policy.ensure_pending_request(B, ExternalSystem.NAVER, CallKind.READ, CallPurpose.TREND_LOOKUP)  # 다른 회사
        policy.grant(A, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.ORDER_DETECTION,
                     mode=AllowanceMode.STANDING, per_hour_limit=12, hours=5, user_id=1)
        policy.grant(A, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.AFTER_SALES_CHECK,
                     mode=AllowanceMode.STANDING, per_hour_limit=12, hours=20, user_id=1)  # 20시간 — 승인 센터 연장 목록(12시간)보다 넓게
        policy.grant(A, system=ExternalSystem.COUPANG, kind=CallKind.READ, purpose=CallPurpose.PRODUCT_STATUS_CHECK,
                     mode=AllowanceMode.STANDING, per_hour_limit=12, hours=100, user_id=1)  # 아직 멀다
        self.db.commit()
        self.assertEqual(self.service()._approval_counts(A), {"approvals": 1, "allowances_expired": 0, "allowances_soon": 2})
        self.now = MORNING + timedelta(hours=6)
        self.assertEqual(self.service()._approval_counts(A), {"approvals": 1, "allowances_expired": 1, "allowances_soon": 1})
        self.now = MORNING + timedelta(hours=77)  # 100시간짜리가 23시간 남음
        self.assertEqual(self.service()._approval_counts(A), {"approvals": 1, "allowances_expired": 2, "allowances_soon": 1})

    def test_discovery_counts_reviews_and_evaluations_of_this_company(self):
        svc = ProductCandidateService(self.db)
        made = []
        for company_id, name in ((A, "생수"), (A, "무릎보호대"), (B, "양말")):
            c, _ = svc.register_private_candidate(ProductCandidatePrivateCreate(
                source_reference=f"r-{company_id}-{name}", market="COUPANG", product_name=name), company_id=company_id, correlation_id="c")
            made.append(c)
        self.db.add(DecisionEvaluation(
            company_id=A, candidate_id=made[0].id, policy_version="v1", input_snapshot_json="{}", input_fingerprint="f" * 64,
            total_score=0.5, confidence=0.6, recommendation="RECOMMEND_HOLD", recommendation_reason="r", status="PENDING_REVIEW",
            idempotency_key="k1", evaluator_kind="RULE", evaluator_version="1"))
        self.db.commit()
        counts = self.service()._discovery_counts(A)
        self.assertEqual(counts["discovery_review"], 1)
        self.assertEqual(counts["discovery_evaluate"] + counts["discovery_review"], 2 - self._trend_or_new(A))

    def _trend_or_new(self, company_id):
        from app.domains.product_candidate.discovery_flow_service import DiscoveryFlowService

        c = DiscoveryFlowService(self.db).list_flow(company_id)["counts"]
        return c["TREND"] + c["NEW_PRODUCT"]

    def test_system_counts_failures_and_warnings_only(self):
        summary = {"checks": [{"code": "A", "level": "FAIL"}, {"code": "B", "level": "WARN"}, {"code": "C", "level": "INFO"}]}
        with mock.patch("app.domains.diagnostics.health_service.HealthService.summary", return_value=summary):
            self.assertEqual(self.service(quiet_system=False)._system_counts(), {"system": 2})

    def test_the_real_system_check_is_read_as_a_number(self):
        counts = self.service(quiet_system=False)._system_counts()
        self.assertIsInstance(counts["system"], int)


# ---------------------------------------------------------------------------
# 문구
# ---------------------------------------------------------------------------

class MessageTests(DbCase):

    def build(self, today=None, fail=()):
        svc = self.service()
        for name in fail:
            setattr(svc, name, mock.Mock(side_effect=RuntimeError("boom")))
        with mock.patch("app.domains.easy_view.service.EasyViewService.build_today", return_value=today or today_payload()):
            return svc.build(A)

    def test_lines_follow_the_fixed_order_and_zero_lines_are_left_out(self):
        b = self.build()
        self.assertEqual(b["title"], "오늘 브리핑 — 11월 1일")
        self.assertTrue(b["message"].startswith("24시간 넘은 문의 1건 · 답변 기다리는 문의 2건 · 쿠팡 승인반려 1건 · 공급처에 넣을 주문 2건 · 송장 등록 1건 · 반품 1건 · 공급처 반품 요청 1건."),
                        b["message"])
        self.assertNotIn("출고일 확인", b["message"])
        self.assertNotIn("시스템 점검", b["message"])
        self.assertIn("브리핑은 아무것도 보내거나 바꾸지 않아요", b["message"])
        self.assertEqual(b["unknown"], [])

    def test_nothing_waiting_says_so(self):
        empty = {"pipeline": {}, "after_sales": {}}
        b = self.build(today=empty)
        self.assertTrue(b["message"].startswith("오늘은 쌓인 일이 없어요."))

    def test_a_part_that_cannot_be_read_is_named_not_shown_as_zero(self):
        b = self.build(fail=("_approval_counts", "_system_counts"))
        self.assertEqual(b["unknown"], ["승인 센터", "시스템 점검"])
        self.assertIn("읽지 못함: 승인 센터, 시스템 점검.", b["message"])
        self.assertNotIn("approvals", b["counts"])
        self.assertIn("24시간 넘은 문의 1건", b["message"])  # 나머지는 그대로

    def test_a_broken_today_summary_keeps_the_rest(self):
        with mock.patch("app.domains.easy_view.service.EasyViewService.build_today", side_effect=RuntimeError("db")):
            b = self.service().build(A)
        self.assertEqual(b["unknown"], ["주문·문의"])
        self.assertTrue(b["message"].startswith("오늘은 쌓인 일이 없어요. 읽지 못함: 주문·문의."), b["message"])

    def test_the_real_sources_read_an_empty_database_without_errors(self):
        b = self.service(quiet_system=False).build(A)
        self.assertEqual(b["unknown"], [])


# ---------------------------------------------------------------------------
# 언제·누구에게
# ---------------------------------------------------------------------------

class SendTests(DbCase):

    def setUp(self):
        super().setUp()
        self.a1 = self.user(A, "a1")
        self.a2 = self.user(A, "a2")
        self.gone = self.user(A, "gone", active=False)
        self.b1 = self.user(B, "b1")
        self.patch = mock.patch("app.domains.easy_view.service.EasyViewService.build_today", return_value=today_payload())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def test_nothing_before_seven_in_the_morning_korean_time(self):
        self.now = DAWN
        self.assertEqual(self.service().run(A), {"sent": 0, "reason": "TOO_EARLY"})
        self.assertEqual(self.notifications(), [])

    def test_each_active_user_of_the_company_gets_one_in_app_notification(self):
        result = self.service().run(A)
        self.assertEqual(result["sent"], 2)
        rows = self.notifications()
        self.assertEqual(sorted(n.user_id for n in rows), [self.a1, self.a2])
        self.assertTrue(all(n.company_id == A and n.link_path == "easy-today" and n.title == "오늘 브리핑 — 11월 1일" for n in rows))
        self.assertTrue(rows[0].message.startswith("24시간 넘은 문의 1건"))

    def test_once_a_day_then_again_the_next_morning(self):
        self.service().run(A)
        self.assertEqual(self.service().run(A), {"sent": 0, "reason": "NOTHING_TO_SEND"})
        self.now = MORNING + timedelta(hours=10)  # 같은 날(한국 17:00)
        self.assertEqual(self.service().run(A)["sent"], 0)
        self.now = NEXT_DAY
        self.assertEqual(self.service().run(A)["sent"], 2)
        self.assertEqual(len(self.notifications()), 4)

    def test_nothing_is_counted_when_nobody_needs_it(self):
        self.service().run(A)
        svc = self.service()
        svc.build = mock.Mock(side_effect=AssertionError("세지 않아야 한다"))
        self.assertEqual(svc.run(A)["sent"], 0)

    def test_a_user_who_turned_the_briefing_off_is_skipped(self):
        self.db.add(NotificationEventPreference(company_id=A, user_id=self.a2, event_code="DAILY_BRIEFING", enabled=False))
        self.db.commit()
        self.assertEqual(self.service().run(A)["sent"], 1)
        self.assertEqual([n.user_id for n in self.notifications()], [self.a1])

    def test_turning_off_another_event_does_not_matter(self):
        self.db.add(NotificationEventPreference(company_id=A, user_id=self.a2, event_code="ESTOP_ACTIVATED", enabled=False))
        self.db.commit()
        self.assertEqual(self.service().run(A)["sent"], 2)

    def test_in_app_only_no_email_and_no_outside_call(self):
        with mock.patch("app.domains.notification_center.delivery_service.NotificationDeliveryService._send_email_now",
                        side_effect=AssertionError("이메일을 보내면 안 된다")):
            self.service().run(A)
        self.db.expire_all()
        statuses = {r.status for r in self.db.query(NotificationEmailLog).all()}
        self.assertEqual(statuses, {"SKIPPED_IN_APP_ONLY"})
        self.assertEqual(self.db.query(ExternalCallLog).count(), 0)
        self.assertEqual(self.db.query(ExternalCallAllowance).count(), 0)

    def test_without_the_delivery_table_nothing_happens(self):
        with self.engine.begin() as conn:
            conn.execute(text("DROP TABLE notification_email_logs"))
        self.assertEqual(self.service().run(A), {"sent": 0, "reason": "NOT_INSTALLED"})
        self.assertEqual(self.notifications(), [])

    def test_candidate_companies_are_those_with_active_users(self):
        self.user(3, "only_inactive", active=False)
        self.assertEqual(MorningBriefService.candidate_companies(self.db), [A, B])


# ---------------------------------------------------------------------------
# Job·카탈로그
# ---------------------------------------------------------------------------

class JobTests(DbCase):

    def setUp(self):
        super().setUp()
        self.a1 = self.user(A, "a1")
        self.b1 = self.user(B, "b1")

    def run_job(self):
        with mock.patch("app.domains.easy_view.service.EasyViewService.build_today", return_value=today_payload()), \
             mock.patch.object(MorningBriefService, "_system_counts", lambda self: {"system": 0}):
            jobs.run_morning_brief_job(session_factory=self.SessionLocal, now_factory=lambda: self.now)

    def test_the_job_sends_to_every_company_once(self):
        self.run_job()
        self.run_job()
        self.assertEqual(sorted((n.company_id, n.user_id) for n in self.notifications()), [(A, self.a1), (B, self.b1)])

    def test_one_company_failing_does_not_stop_the_next(self):
        original = MorningBriefService.run

        def flaky(self_, company_id):
            if company_id == A:
                raise RuntimeError("boom")
            return original(self_, company_id)

        with mock.patch.object(MorningBriefService, "run", flaky):
            self.run_job()
        self.assertEqual([(n.company_id, n.user_id) for n in self.notifications()], [(B, self.b1)])

    def test_a_broken_session_does_not_escape_the_job(self):
        class Broken:
            def get_bind(self):
                raise RuntimeError("db gone")

            def close(self):
                pass

        jobs.run_morning_brief_job(session_factory=Broken)  # 예외가 밖으로 나오면 실패

    def test_the_job_is_registered_hourly_and_watched_by_the_health_check(self):
        from app.domains.diagnostics.health_service import expected_job_ids

        self.assertEqual(jobs.MORNING_BRIEF_INTERVAL_MINUTES, 60)
        self.assertIn(jobs.MORNING_BRIEF_JOB_ID, expected_job_ids())
        source = (ROOT / "app/domains/scheduler/jobs.py").read_text(encoding="utf-8").split("def register_all_jobs")[1]
        self.assertIn("run_morning_brief_job,\n        minutes=MORNING_BRIEF_INTERVAL_MINUTES,\n        job_id=MORNING_BRIEF_JOB_ID,",
                      source.replace("\r\n", "\n"))
        self.assertEqual(brief_mod.RUN_FROM_HOUR_KST, 7)

    def test_the_catalog_event_is_low_in_app_only_and_can_be_turned_off(self):
        d = EVENT_CATALOG["DAILY_BRIEFING"]
        self.assertEqual(d.severity, NotificationSeverity.LOW)
        self.assertFalse(d.default_email_immediate)
        self.assertIsNone(d.default_unconfirmed_wait_minutes)
        self.assertFalse(d.critical_cannot_disable)
        self.assertTrue(d.wired)

    def test_the_bell_list_fits_a_narrow_screen(self):
        # 좁은 화면에서는 벨이 왼쪽에 있어 오른쪽 정렬 드롭다운이 화면 밖으로 나갔다(E2E에서 발견) — 브리핑은 이 목록으로 읽는다.
        css = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
        block = css.split("@media (max-width: 640px) {\n  .topbar-notif-dropdown {", 1)[1].split("}", 1)[0]
        for rule in ("position: fixed", "left: 8px", "right: 8px", "top: auto", "min-width: 0"):
            self.assertIn(rule, block)

    def test_the_link_opens_the_today_screen(self):
        js = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
        self.assertIn('"easy-today"', js)
        self.assertEqual(brief_mod.LINK_PATH, "easy-today")


if __name__ == "__main__":
    unittest.main()
