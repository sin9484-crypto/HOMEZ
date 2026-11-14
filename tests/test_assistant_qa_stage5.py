"""
2026-11-01 — AI 통합 계획 5단계(비용 0 부분): 비서에게 묻기.

고정하는 약속
  - 정해진 질문 번호에만 답한다. 숫자는 실제 기록을 규칙으로 센 값이다(Claude 없음, 바깥 호출 없음, DB 쓰기 없음).
  - '오늘'은 한국 시각 0시부터, '이번 주'는 한국 시각 월요일 0시부터. 시각을 읽을 수 없는 기록은 '시각 모름'으로 따로 센다.
  - 못 읽은 답은 0으로 꾸미지 않고 unknown으로 돌려준다.
  - 화면: 적은 문장은 화면 안에서만 질문 목록과 맞추고, 서버에는 질문 번호만 GET으로 보낸다. 서버 글·이름은 이스케이프한다.
  - 2026-11-14(조사 문서 3-7 읽기 도구 묶음): 정산(쿠팡 정산 기록의 항목별 최신 상태 — 지급 예정·가장 가까운 지급일·지급일 지났는데 완료
    아님·이번 달 지급 완료·마지막으로 읽은 뒤 지난 시간, 한 번도 안 읽었으면 못 읽음)과 주문 진행('오늘 할 일' 단계 숫자 그대로).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
from app.domains.easy_view.service import EasyViewService
from app.domains.external_call.model import ExternalCallLog
from app.domains.orchestration import assistant_qa_service as qa_mod
from app.domains.orchestration.assistant_qa_service import QUESTIONS
from app.domains.orchestration.assistant_qa_service import AssistantQaService
from app.domains.orchestration.assistant_qa_service import kst_wall
from app.domains.orchestration.morning_brief_service import LINES
from app.domains.orchestration.morning_brief_service import MorningBriefService
from app.domains.order.after_sales_monitor_service import EVENT_ACTION
from app.domains.order.model import Order
from app.domains.product_candidate.discovery_flow_service import DiscoveryFlowService
from app.domains.user.model import User
from tests.test_easy_briefing_ui import HARNESS
from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import NODE
from tests.test_easy_briefing_ui import NOW
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/web/easy/easy_view.css").read_text(encoding="utf-8")
A, B = 1, 2
# 2026-11-04(수) 한국 시각 12:00. 이번 주 = 11-02(월) 0시부터, 오늘 = 11-04 0시부터.
WEDNESDAY_NOON = datetime(2026, 11, 4, 3, 0)

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
    _TEMPLATE.clear()


class _NoKeyStore:
    """AI 설정 저장 시험용 — 키는 저장하지 않는다."""

    def exists(self, target):
        return False


def facts(result):
    return {f["key"]: f["n"] for f in result["facts"]}


class DbCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copyfile(_template_db(), f"{self.tmp}/t.db")
        self.engine = create_engine(f"sqlite:///{self.tmp}/t.db", connect_args={"check_same_thread": False})
        self.db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.now = WEDNESDAY_NOON
        self.seq = 0

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ask(self, question_id, company_id=A, user_id=1):
        return AssistantQaService(self.db, now_factory=lambda: self.now).answer(company_id, user_id, question_id)

    def order(self, ordered_at, company_id=A):
        self.seq += 1
        self.db.add(Order(
            company_id=company_id, channel_code="COUPANG", channel_order_id=f"C{self.seq}", order_number=f"N{self.seq}", status="PENDING", buyer_name="x", receiver_name="x",
            receiver_phone="x", receiver_address="x", receiver_zipcode="x", total_amount=1.0, ordered_at=ordered_at,
        ))
        self.db.commit()

    def audit(self, action, entity_id, payload):
        description = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.db.execute(text("INSERT INTO audit_logs (action, entity, entity_id, description) VALUES (:a, 'x', :e, :d)"),
                        {"a": action, "e": entity_id, "d": description})
        self.db.commit()

    def audit_rows(self):
        return self.db.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar()


# ---------------------------------------------------------------------------
# 무엇을 세나
# ---------------------------------------------------------------------------

class CountTests(DbCase):

    def test_korean_time_text_is_read_as_korean_wall_time_or_none(self):
        self.assertEqual(kst_wall("2026-11-02T00:00:00"), datetime(2026, 11, 2, 0, 0))
        self.assertEqual(kst_wall("2026-11-02 09:30:12.5"), datetime(2026, 11, 2, 9, 30))
        for bad in (None, "", "어제", "2026-13-40T00:00", "2026-11-02"):
            self.assertIsNone(kst_wall(bad), bad)

    def test_orders_today_count_from_korean_midnight_and_this_week_from_monday(self):
        for at in (datetime(2026, 11, 4, 0, 0), datetime(2026, 11, 4, 11, 59),    # 오늘
                   datetime(2026, 11, 3, 23, 59), datetime(2026, 11, 2, 0, 0),     # 이번 주(오늘 아님)
                   datetime(2026, 11, 1, 23, 59)):                                  # 지난주 일요일
            self.order(at)
        self.order(datetime(2026, 11, 4, 1, 0), company_id=B)                      # 다른 회사
        today = self.ask("ORDERS_TODAY")
        week = self.ask("ORDERS_WEEK")
        self.assertEqual(facts(today), {"orders_today": 2})
        self.assertEqual(today["since"], "2026-11-04")
        self.assertEqual(facts(week), {"orders_week": 4})
        self.assertEqual(week["since"], "2026-11-02")
        self.assertEqual((today["link"], today["unknown"]), ("easy-orders", False))

    def test_korean_midnight_is_used_not_the_utc_date(self):
        self.now = datetime(2026, 11, 3, 16, 30)   # 한국 시각 11-04 01:30 — UTC 날짜는 아직 11-03
        self.order(datetime(2026, 11, 4, 0, 10))
        self.order(datetime(2026, 11, 3, 23, 0))
        self.assertEqual(facts(self.ask("ORDERS_TODAY")), {"orders_today": 1})

    def test_a_sunday_night_belongs_to_the_week_that_started_on_monday(self):
        self.now = datetime(2026, 11, 8, 14, 0)    # 한국 시각 11-08(일) 23:00
        self.order(datetime(2026, 11, 2, 0, 0))
        self.order(datetime(2026, 11, 1, 23, 0))
        result = self.ask("ORDERS_WEEK")
        self.assertEqual((facts(result), result["since"]), ({"orders_week": 1}, "2026-11-02"))

    def test_returns_this_week_use_the_latest_state_and_count_unreadable_times_apart(self):
        self.audit(EVENT_ACTION, "RETURN:1", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": "2026-11-02T00:00:00"})
        self.audit(EVENT_ACTION, "RETURN:1", {"k": "RETURN", "s": "RETURNS_COMPLETED", "t": "2026-11-02T00:00:00"})  # 같은 접수의 최신 상태
        self.audit(EVENT_ACTION, "RETURN:2", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": "2026-11-04T10:00:00"})
        self.audit(EVENT_ACTION, "RETURN:3", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": "2026-11-01T23:59:00"})  # 지난주
        self.audit(EVENT_ACTION, "RETURN:4", {"k": "RETURN", "s": "VENDOR_WAREHOUSE_CONFIRM", "t": None})          # 시각 모름
        self.audit(EVENT_ACTION, "INQ_ONLINE:9", {"k": "INQ_ONLINE", "s": "UNANSWERED", "t": "2026-11-04T10:00:00"})
        result = self.ask("RETURNS_WEEK")
        self.assertEqual(result["facts"], [
            {"key": "returns_week", "n": 2}, {"key": "returns_open", "n": 3}, {"key": "returns_no_time", "n": 1},
        ])
        self.assertEqual(result["since"], "2026-11-02")

    def test_the_unreadable_time_line_is_left_out_when_every_time_was_read(self):
        self.audit(EVENT_ACTION, "RETURN:1", {"k": "RETURN", "s": "RETURNS_COMPLETED", "t": "2026-11-03T08:00:00"})
        self.assertEqual(facts(self.ask("RETURNS_WEEK")), {"returns_week": 1, "returns_open": 0})

    def test_waiting_inquiries_give_the_old_ones_and_the_oldest_age(self):
        self.audit(EVENT_ACTION, "INQ_ONLINE:1", {"k": "INQ_ONLINE", "s": "UNANSWERED", "t": "2026-11-03T12:00:00"})  # 24시간
        self.audit(EVENT_ACTION, "INQ_CALL:2", {"k": "INQ_CALL", "s": "UNANSWERED", "t": "2026-11-01T02:00:00"})    # 82시간
        self.audit(EVENT_ACTION, "INQ_ONLINE:3", {"k": "INQ_ONLINE", "s": "UNANSWERED", "t": "2026-11-04T11:00:00"})  # 1시간
        self.audit(EVENT_ACTION, "INQ_ONLINE:4", {"k": "INQ_ONLINE", "s": "UNANSWERED", "t": "모름"})
        self.audit(EVENT_ACTION, "INQ_ONLINE:5", {"k": "INQ_ONLINE", "s": "ANSWERED", "t": "2026-10-01T00:00:00"})    # 답변함
        self.assertEqual(self.ask("INQUIRIES_OPEN")["facts"], [
            {"key": "inquiries_open", "n": 4}, {"key": "inquiries_old", "n": 2},
            {"key": "inquiries_oldest_hours", "n": 82}, {"key": "inquiries_no_time", "n": 1},
        ])

    def test_no_waiting_inquiry_gives_zero_and_no_oldest_line(self):
        self.assertEqual(self.ask("INQUIRIES_OPEN")["facts"], [{"key": "inquiries_open", "n": 0}, {"key": "inquiries_old", "n": 0}])

    def test_approvals_use_the_same_counts_as_the_morning_brief(self):
        with mock.patch.object(MorningBriefService, "_approval_counts", return_value={"approvals": 3, "allowances_expired": 1, "allowances_soon": 2}) as counts:
            result = self.ask("APPROVALS")
        counts.assert_called_once_with(A)
        self.assertEqual(result["facts"], [{"key": "approvals", "n": 3}, {"key": "allowances_expired", "n": 1}, {"key": "allowances_soon", "n": 2}])
        self.assertEqual(result["link"], "easy-approvals")

    def test_rejected_products_give_the_count_and_up_to_five_names(self):
        status = [{"status": "DENIED", "product_name": f"상품{i}"} for i in range(6)] + [
            {"status": "DENIED", "product_name": None}, {"status": "APPROVED", "product_name": "승인"}, {"status": None, "product_name": "모름"}]
        with mock.patch.object(EasyViewService, "build_today", return_value={"product_status": status}):
            result = self.ask("PRODUCT_DENIED")
        self.assertEqual(facts(result), {"product_denied": 7})
        self.assertEqual(result["names"], ["상품0", "상품1", "상품2", "상품3", "상품4"])

    def test_ai_spend_this_month_reads_the_usage_records_and_the_askers_limit(self):
        from app.domains.marketplace_listing.ai_draft_service import USAGE_ACTION
        from app.domains.marketplace_listing.ai_draft_service import AiDraftService

        user_id = self.user()
        AiDraftService(self.db, _NoKeyStore()).save_settings(user_id, A, model="claude-opus-5-5", monthly_limit_krw=30000, in_price=6000, out_price=30000)
        self.audit(USAGE_ACTION, "202611:a", {"c": 12.4})
        self.audit(USAGE_ACTION, "202611:b", {"c": 100.3})
        self.audit(USAGE_ACTION, "202610:c", {"c": 5000})       # 지난달
        self.audit(USAGE_ACTION, "202611:d", "깨진 기록")        # 합계 규칙(AI 사용 설정 화면)과 같게 건너뛴다
        self.assertEqual(self.ask("AI_COST_MONTH", user_id=user_id)["facts"],
                         [{"key": "ai_calls", "n": 2}, {"key": "ai_used_krw", "n": 113}, {"key": "ai_limit_krw", "n": 30000}])

    def test_ai_spend_without_settings_says_no_limit_and_never_opens_the_key_store(self):
        with mock.patch("app.core.windows_credential_store.WindowsCredentialStore", side_effect=AssertionError("키 저장소를 열면 안 된다")):
            result = self.ask("AI_COST_MONTH")
        self.assertEqual(result["facts"], [{"key": "ai_calls", "n": 0}, {"key": "ai_used_krw", "n": 0}, {"key": "ai_limit_none", "n": 0}])
        self.assertFalse(result["unknown"])

    def test_discovery_gives_each_next_step_in_flow_order(self):
        with mock.patch.object(DiscoveryFlowService, "list_flow", return_value={"counts": {"REVIEW": 2, "TREND": 4, "NEW_PRODUCT": 1}}):
            result = self.ask("DISCOVERY")
        self.assertEqual(result["facts"], [{"key": "discovery_review", "n": 2}, {"key": "discovery_evaluate", "n": 0},
                                           {"key": "discovery_new_product", "n": 1}, {"key": "discovery_trend", "n": 4}])
        self.assertEqual(result["link"], "easy-analysis")

    def test_todo_first_gives_the_first_three_non_zero_brief_lines_in_brief_order(self):
        counts = {key: 0 for key, _ in LINES}
        counts.update({"system": 1, "invoice": 2, "inquiries_old": 1, "approvals": 5})
        with mock.patch.object(MorningBriefService, "build", return_value={"counts": counts, "unknown": ["상품 발굴"]}):
            result = self.ask("TODO_FIRST")
        self.assertEqual(result["facts"], [{"key": "inquiries_old", "n": 1}, {"key": "invoice", "n": 2}, {"key": "approvals", "n": 5}])
        self.assertEqual(result["unknown_sources"], ["상품 발굴"])

    def test_todo_first_with_nothing_waiting_gives_no_lines(self):
        with mock.patch.object(MorningBriefService, "build", return_value={"counts": {key: 0 for key, _ in LINES}, "unknown": []}):
            result = self.ask("TODO_FIRST")
        self.assertEqual((result["facts"], result["unknown_sources"], result["unknown"]), ([], [], False))

    def user(self):
        u = User(company_id=A, email="a@example.com", username="a", password_hash="x", is_active=True)
        self.db.add(u)
        self.db.commit()
        return u.id


class SettlementAndFlowTests(DbCase):
    """2026-11-14 — 정산·주문 진행(조사 문서 3-7). 지금 = 2026-11-04(수) 한국 시각 12:00."""

    def settlement(self, key, status, pay_day, fin):
        self.audit(EVENT_ACTION, f"SETTLEMENT:{key}:{pay_day}", {"k": "SETTLEMENT", "id": key, "st": status, "ty": "WEEKLY", "d": pay_day, "fin": fin})

    def checked(self, hours_ago, outcome="SUCCEEDED", target="settlement"):
        self.db.add(ExternalCallLog(company_id=A, system="COUPANG", kind="READ", purpose="AFTER_SALES_CHECK", target=target, outcome=outcome,
                                    created_at=self.now - timedelta(hours=hours_ago, minutes=10)))
        self.db.commit()

    def test_never_read_settlement_is_unknown_not_zero(self):
        self.checked(1, outcome="FAILED")
        self.checked(1, target="returns")
        result = self.ask("SETTLEMENT")
        self.assertEqual((result["unknown"], result["facts"]), (True, []))
        self.assertNotIn("next_date", result)

    def test_settlement_counts_use_the_latest_state_per_item(self):
        self.checked(9)
        self.checked(3)
        self.settlement("2026-10:A", "SUBJECT", "2026-11-02", 7452.4)
        self.settlement("2026-10:A", "DONE", "2026-11-02", 7452.4)            # 같은 항목은 마지막 상태(지급 완료)만
        self.settlement("2026-10:B", "SUBJECT", "2026-12-01", 3193)
        self.settlement("2026-10:C", "SUBJECT", "2026-11-09", None)           # 금액 못 읽음 — 0으로 더하지 않고 따로 센다
        self.settlement("2026-09:D", "SUBJECT", "2026-11-01", 100)            # 지급일 지났는데 완료로 읽히지 않음
        self.settlement("2026-09:E", "DONE", "2026-10-20", 999)               # 지난달 지급 — 이번 달에 넣지 않는다
        self.settlement("2026-09:F", "DONE", "날짜없음", 555)                  # 지급일 못 읽음 — 이번 달에 넣지 않는다
        self.audit(EVENT_ACTION, "RETURN:1", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": "2026-11-03T08:00:00"})
        result = self.ask("SETTLEMENT")
        self.assertEqual([f["key"] for f in result["facts"]], [
            "settlement_upcoming", "settlement_upcoming_krw", "settlement_next_days", "settlement_overdue",
            "settlement_paid_month", "settlement_paid_month_krw", "settlement_amount_unknown", "settlement_checked_hours"])
        self.assertEqual(facts(result), {
            "settlement_upcoming": 3, "settlement_upcoming_krw": 3293, "settlement_next_days": 5, "settlement_overdue": 1,
            "settlement_paid_month": 1, "settlement_paid_month_krw": 7452, "settlement_amount_unknown": 1, "settlement_checked_hours": 3,
        })
        self.assertEqual((result["next_date"], result["link"], result["unknown"]), ("2026-11-09", "easy-money", False))

    def test_a_payout_today_is_zero_days_away_not_overdue_and_quiet_lines_are_left_out(self):
        self.checked(0)
        self.settlement("2026-10:A", "SUBJECT", "2026-11-04", 10)
        self.assertEqual(facts(self.ask("SETTLEMENT")), {
            "settlement_upcoming": 1, "settlement_upcoming_krw": 10, "settlement_next_days": 0,
            "settlement_paid_month": 0, "settlement_paid_month_krw": 0, "settlement_checked_hours": 0,
        })

    def test_any_status_other_than_paid_is_still_waiting_and_an_unreadable_amount_is_not_added(self):
        self.checked(1)
        self.settlement("2026-10:A", "PENDING", "2026-11-20", float("nan"))   # 처음 보는 상태도 '지급 완료'가 아니면 지급 전
        self.settlement("2026-10:B", None, "2026-11-21", "12,000")            # 상태 없음·숫자 아닌 금액
        self.assertEqual(facts(self.ask("SETTLEMENT")), {
            "settlement_upcoming": 2, "settlement_upcoming_krw": 0, "settlement_next_days": 16,
            "settlement_paid_month": 0, "settlement_paid_month_krw": 0, "settlement_amount_unknown": 2, "settlement_checked_hours": 1,
        })

    def test_records_without_a_read_log_are_still_counted_without_the_age_line(self):
        self.settlement("2026-10:A", "SUBJECT", "2026-11-20", 10)
        self.assertNotIn("settlement_checked_hours", facts(self.ask("SETTLEMENT")))

    def test_order_flow_uses_the_today_screen_stage_numbers(self):
        today = {"pipeline": {"new_orders": 2, "shipping": 4, "settlement": 5, "returning": 1, "prepare": 9}, "invoice_todo": [{"task_id": 1}, {"task_id": 2}, {"task_id": 3}]}
        with mock.patch.object(EasyViewService, "build_today", return_value=today) as build:
            result = self.ask("ORDER_FLOW")
        build.assert_called_once_with(A)
        self.assertEqual(result["facts"], [
            {"key": "new_orders", "n": 2}, {"key": "invoice", "n": 3}, {"key": "shipping", "n": 4},
            {"key": "delivered_waiting", "n": 5}, {"key": "returning", "n": 1}])
        self.assertEqual(result["link"], "easy-orders")

    def test_order_flow_on_an_empty_shop_is_all_zero(self):
        self.assertEqual(facts(self.ask("ORDER_FLOW")), {"new_orders": 0, "invoice": 0, "shipping": 0, "delivered_waiting": 0, "returning": 0})

    def test_pay_dates_are_read_or_none(self):
        self.assertEqual(qa_mod.pay_date("2026-12-01"), datetime(2026, 12, 1).date())
        self.assertEqual(qa_mod.pay_date("2026-12-01T00:00:00"), datetime(2026, 12, 1).date())
        for bad in (None, "", "2026-13-01", "12/01"):
            self.assertIsNone(qa_mod.pay_date(bad))


# ---------------------------------------------------------------------------
# 모름·읽기 전용
# ---------------------------------------------------------------------------

class SafetyTests(DbCase):

    def test_an_unreadable_answer_is_unknown_not_zero_and_the_session_still_works(self):
        with mock.patch.object(AssistantQaService, "_orders_since", side_effect=RuntimeError("no table")), \
                mock.patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback:
            result = self.ask("ORDERS_TODAY")
        self.assertEqual((result["unknown"], result["facts"]), (True, []))
        self.assertNotIn("since", result)
        rollback.assert_called_once()
        self.assertEqual(facts(self.ask("ORDERS_WEEK")), {"orders_week": 0})

    def test_an_unknown_question_number_is_refused(self):
        for bad in ("", "orders_today", "DROP", "__init__"):
            with self.assertRaises(KeyError):
                self.ask(bad)

    def test_asking_every_question_writes_nothing_and_calls_no_outside_service(self):
        self.audit(EVENT_ACTION, "RETURN:1", {"k": "RETURN", "s": "RETURNS_UNCHECKED", "t": "2026-11-03T08:00:00"})
        before = (self.audit_rows(), self.db.query(ExternalCallLog).count(), self.db.query(Order).count())
        for question_id in QUESTIONS:
            result = self.ask(question_id)
            self.assertEqual(result["question_id"], question_id)
            self.assertEqual(result["as_of"], "2026-11-04T03:00:00")
        self.db.expire_all()
        self.assertEqual((self.audit_rows(), self.db.query(ExternalCallLog).count(), self.db.query(Order).count()), before)

    def test_the_service_source_has_no_write_or_outside_call(self):
        source = Path(qa_mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("commit(", ".add(", ".delete(", "write_audit_log", "client_factory", "requests", "httpx", "urllib", "authorize(",
                          "dispatch(", "_read_key", "CredentialStore"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_every_question_has_a_reader_and_opens_a_screen_that_exists(self):
        views = set(re.findall(r'"(easy-[a-z-]+)"', re.search(r"const EASY_VIEWS = new Set\(\[([^\]]+)\]\)", JS).group(1)))
        for question_id, view in QUESTIONS.items():
            self.assertTrue(callable(getattr(AssistantQaService, f"_{question_id.lower()}", None)), question_id)
            self.assertIn(view, views, question_id)


class RouterTests(unittest.TestCase):

    def test_the_answer_is_an_admin_only_get_with_only_a_question_number(self):
        from app.core.guard import admin_guard

        routes = [r for r in app.main.app.routes if getattr(r, "path", "") == "/orchestration/assistant/answer"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].methods, {"GET"})
        params = {p.name for p in routes[0].dependant.query_params}
        self.assertEqual(params, {"question_id"})
        self.assertIn(admin_guard, [d.call for d in routes[0].dependant.dependencies])

    def test_an_unknown_question_number_is_a_bad_request(self):
        from app.core.exceptions import BadRequestException
        from app.domains.orchestration.router import get_assistant_answer

        user = mock.Mock(company_id=A, id=1)
        with self.assertRaises(BadRequestException):
            get_assistant_answer("NOPE", current_user=user, db=mock.Mock())


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------

def ask_texts(lang="ko-KR"):
    block = re.search(rf'"{lang}": \{{(.*?)\n  \}}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:ask\.[a-z_]+|error))": ("(?:[^"\\]|\\.)*")', block)}


def ask_constants():
    return re.search(r"  const ASK_QUESTIONS = \[.*?\n  \];", JS, re.S).group(0) + "\n" + re.search(r"  const ASK_MAX_CHARS = \d+;", JS).group(0)


def server_fact_keys():
    """서버 답이 낼 수 있는 사실 키 전부(소스의 고정 키 + 아침 브리핑 줄 + 탐색 흐름 단계)."""

    from app.domains.product_candidate import discovery_flow_service as flow

    source = Path(qa_mod.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'"key": "([a-z_]+)"', source))
    keys |= set(re.findall(r'"(ai_limit_[a-z]+)"', source))
    keys |= {key for key, _ in LINES}
    keys |= {f"discovery_{step.lower()}" for step in (flow.NEXT_REVIEW, flow.NEXT_EVALUATE, flow.NEXT_NEW_PRODUCT, flow.NEXT_TREND)}
    return keys


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenFunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join([ask_constants()] + [function_source(n) for n in ("parseUtcMs", "askMatch", "askPick", "askChipsHtml", "askSectionHtml", "askAnswerHtml")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__NOW__", str(NOW)).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(ask_texts())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def match(self, sentence):
        return self.run_js(f"askMatch({json.dumps(sentence)})")

    def test_a_typed_question_finds_its_one_question(self):
        cases = {
            "이번 주 반품 몇 건?": "RETURNS_WEEK", "오늘 주문 몇개 들어왔어": "ORDERS_TODAY", "이번주 주문은?": "ORDERS_WEEK",
            "오늘 할 일 뭐야": "TODO_FIRST", "승인 대기 있어?": "APPROVALS", "쿠팡 승인 반려된 거": "PRODUCT_DENIED",
            "이번 달 AI 얼마 썼어": "AI_COST_MONTH", "상품 발굴 어디까지": "DISCOVERY", "How many returns this week?": "RETURNS_WEEK",
            "정산 언제 들어와?": "SETTLEMENT", "다음 지급일": "SETTLEMENT", "배송 어디까지 갔어": "ORDER_FLOW", "송장 넣을 거 있어?": "ORDER_FLOW",
            "When is the next payout?": "SETTLEMENT",
        }
        for sentence, question_id in cases.items():
            self.assertEqual(self.match(sentence)["exact"], [question_id], sentence)

    def test_a_question_missing_a_word_offers_the_near_ones_and_nonsense_offers_none(self):
        self.assertEqual(self.match("주문 몇 건?"), {"exact": [], "near": ["ORDERS_TODAY", "ORDERS_WEEK"]})
        self.assertEqual(self.match("날씨 어때"), {"exact": [], "near": []})
        self.assertEqual(self.match("   "), {"exact": [], "near": []})
        self.assertEqual(self.match("AI 답변 비용")["exact"], ["INQUIRIES_OPEN", "AI_COST_MONTH"])  # 둘 다 맞으면 고르게 한다

    def test_one_exact_match_is_asked_at_once_otherwise_the_user_picks(self):
        self.assertEqual(self.run_js("askPick(askMatch('이번 주 반품 몇 건'))"), {"run": "RETURNS_WEEK", "choose": []})
        self.assertEqual(self.run_js("askPick(askMatch('AI 답변 비용'))"), {"run": None, "choose": ["INQUIRIES_OPEN", "AI_COST_MONTH"]})
        self.assertEqual(self.run_js("askPick(askMatch('주문 몇 건?'))"), {"run": None, "choose": ["ORDERS_TODAY", "ORDERS_WEEK"]})
        # 여럿이 다 맞으면 반쯤 맞은 것(이번 주 주문)은 넣지 않는다
        self.assertEqual(self.run_js("askMatch('오늘 주문이랑 반품')")["near"], ["ORDERS_TODAY", "ORDERS_WEEK", "RETURNS_WEEK"])
        self.assertEqual(self.run_js("askPick(askMatch('오늘 주문이랑 반품'))"), {"run": None, "choose": ["ORDERS_TODAY", "RETURNS_WEEK"]})
        self.assertEqual(self.run_js("askPick(askMatch('날씨'))"), {"run": None, "choose": []})

    def test_the_submit_uses_the_pick(self):
        bind = function_source("bindAsk")
        self.assertIn("const pick = askPick(askMatch(", bind)
        self.assertIn("if (pick.run) { askRun(pick.run, out); return; }", bind)
        self.assertIn("const ids = pick.choose;", bind)

    def test_a_settlement_answer_shows_amounts_the_nearest_payout_date_and_its_age(self):
        res = {"question_id": "SETTLEMENT", "link": "easy-money", "as_of": "2026-11-04T03:00:00", "unknown": False, "next_date": "<2026-11-09>",
               "facts": [{"key": "settlement_upcoming_krw", "n": 3293}, {"key": "settlement_checked_hours", "n": 3}]}
        html = self.run_js(f"askAnswerHtml('SETTLEMENT', {json.dumps(res)})")
        self.assertIn("정산 지급은 언제?", html)
        self.assertIn("지급 예정 금액 3,293원", html)
        self.assertIn("가장 가까운 지급 예정일: &lt;2026-11-09&gt;", html)
        self.assertIn("3시간 전에 마지막으로 읽었어요", html)
        self.assertIn('data-ez-ask-go="easy-money"', html)
        self.assertNotIn("지급 예정일:", self.run_js("askAnswerHtml('ORDER_FLOW', {unknown: false, facts: [{key: 'shipping', n: 1}], as_of: '2026-11-04T03:00:00'})"))

    def test_the_answer_lists_each_fact_with_names_period_and_basis_escaped(self):
        res = {"question_id": "PRODUCT_DENIED", "link": "easy-products", "as_of": "2026-11-04T03:00:00", "unknown": False,
               "facts": [{"key": "product_denied", "n": 1200}], "names": ["<b>상품</b>"], "since": "2026-11-02", "unknown_sources": ["상품 발굴"]}
        html = self.run_js(f"askAnswerHtml('PRODUCT_DENIED', {json.dumps(res)})")
        self.assertIn("쿠팡 승인반려 상품은?", html)
        self.assertIn("쿠팡 승인반려 1,200건", html)
        self.assertIn("&lt;b&gt;상품&lt;/b&gt;", html)
        self.assertNotIn("<b>상품", html)
        self.assertIn("2026-11-02부터", html)
        self.assertIn("읽지 못함: 상품 발굴", html)
        self.assertIn("기록을 규칙으로 센 숫자", html)
        self.assertIn('data-ez-ask-go="easy-products"', html)

    def test_an_unreadable_answer_never_shows_zero(self):
        for res in ({"unknown": True, "facts": []}, None):
            html = self.run_js(f"askAnswerHtml('ORDERS_TODAY', {json.dumps(res)})")
            self.assertIn("0건이라는 뜻이 아니에요", html)
            self.assertNotIn("0건<", html)
            self.assertNotIn("data-ez-ask-go", html)

    def test_an_empty_answer_says_nothing_is_waiting(self):
        html = self.run_js("askAnswerHtml('TODO_FIRST', {unknown: false, facts: [], as_of: '2026-11-04T03:00:00', link: 'easy-today'})")
        self.assertIn("지금 쌓인 일이 없어요.", html)

    def test_the_section_has_a_limited_text_box_and_a_button_per_question(self):
        html = self.run_js("askSectionHtml()")
        self.assertIn('maxlength="100"', html)
        self.assertEqual(len(re.findall(r"data-ez-ask=\"", html)), len(QUESTIONS))
        self.assertIn("서버로 보내지 않아요", html)
        self.assertIn("'Claude에게 묻기'를 눌러 확인할 때만 보내요", html)  # 2026-11-01 Claude 부분: 보내는 길은 확인 창 하나뿐


class ScreenWiringTests(unittest.TestCase):

    def test_screen_questions_are_the_server_questions(self):
        screen = re.findall(r'\{ id: "([A-Z_]+)", words:', ask_constants())
        self.assertEqual(screen, list(QUESTIONS))

    def test_only_the_question_number_goes_to_the_server_with_a_get(self):
        run = function_source("askRun")
        self.assertIn("api(`/orchestration/assistant/answer?question_id=${encodeURIComponent(id)}`)", run)
        self.assertEqual(run.count("api("), 1)
        self.assertNotIn("method:", run)
        bind = function_source("bindAsk")
        self.assertNotIn("api(", bind)
        self.assertIn('const text = form.querySelector("[data-ez-ask-text]").value;', bind)
        self.assertIn("askPick(askMatch(text))", bind)
        self.assertEqual(bind.count("[data-ez-ask-text]"), 1)
        # 적은 문장은 맞춰 보는 데와, 목록에 없을 때 사용자가 누르는 'Claude에게 묻기'(확인 창 → askAiRun)에만 쓴다
        self.assertEqual(len(re.findall(r"\btext\b", bind)), 4)  # 칸 이름·담기·맞춰 보기·Claude에게 묻기
        self.assertIn("askAiRun(text, out)", bind)
        for name in ("askMatch", "askPick", "askChipsHtml", "askSectionHtml", "askAnswerHtml"):
            for forbidden in ("api(", "apiFetch", "fetch(", "XMLHttpRequest", "localStorage", "method:"):
                self.assertNotIn(forbidden, function_source(name), (name, forbidden))

    def test_the_today_screen_shows_the_section_under_the_brief_and_binds_it(self):
        body = JS.split("async function loadToday()")[1]
        self.assertLess(body.index("${briefingHtml(brief)}"), body.index("${askSectionHtml()}"))
        self.assertLess(body.index("${askSectionHtml()}"), body.index('class="ez-flow"'))
        self.assertIn("bindAsk(root);", body)

    def test_every_fact_the_server_can_give_has_a_text_in_both_languages_with_its_number(self):
        ko, en = ask_texts("ko-KR"), ask_texts("en-US")
        keys = server_fact_keys()
        self.assertEqual({k[len("easy.ask.f_"):] for k in ko if k.startswith("easy.ask.f_")}, keys)
        for key in keys:
            if key == "ai_limit_none":
                continue
            self.assertIn("{n}", ko[f"easy.ask.f_{key}"], key)
            self.assertIn("{n}", en[f"easy.ask.f_{key}"], key)

    def test_every_question_has_a_label_in_both_languages(self):
        ko, en = ask_texts("ko-KR"), ask_texts("en-US")
        for question_id in QUESTIONS:
            self.assertIn(f"easy.ask.q_{question_id.lower()}", ko)
            self.assertIn(f"easy.ask.q_{question_id.lower()}", en)

    def test_the_texts_do_not_claim_a_coupang_deadline_or_an_ai_answer(self):
        for value in ask_texts("ko-KR").values():
            self.assertNotIn("기한", value)
        # 규칙 답 문구는 AI가 썼다고 하지 않는다(AI 글 문구는 'Claude에게 묻기'의 easy.ask.ai_*에만)
        self.assertNotIn("AI가", " ".join(v for k, v in ask_texts("ko-KR").items() if not k.startswith("easy.ask.ai_")))

    def test_the_text_box_shrinks_on_a_narrow_screen(self):
        self.assertRegex(CSS, r"\.ez-ask-form \{[^}]*flex-wrap: wrap")
        self.assertRegex(CSS, r"\.ez-ask-in \{[^}]*min-width: 0")


if __name__ == "__main__":
    unittest.main()
