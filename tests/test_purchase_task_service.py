"""
=========================================================
Homez OS

File : tests/test_purchase_task_service.py

Gate PT-1(2026-08-22 15차 지시) 검증 — PurchaseTaskService A+E+F
Workflow. 실제 쇼핑몰·이메일·homez.db를 전혀 접촉하지 않는다(임시
SQLite 파일).
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.model import (
    PurchaseRecord,
    PurchaseTask,
    PurchaseTaskBudgetReservation,
    PurchaseTaskCandidate,
    PurchaseTaskCsvImportLog,
    PurchaseTaskEmailLog,
    PurchaseTaskEmailPreference,
    PurchaseTaskPolicySetting,
    PurchaseTaskTrackingInfo,
)
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.role.model import Role
from app.domains.user.model import User

NOW = datetime(2026, 8, 22, 12, 0, 0)

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)

_SOURCE_ATTRS = {
    "brand": "브랜드A", "manufacturer": "브랜드A", "model_name": "MODEL-1",
    "gtin": "1111111111111", "capacity": "100ml", "color_or_scent": "블랙",
    "options": ["기본"],
}
_MISMATCH_SOURCE_ATTRS = {
    "brand": "브랜드A", "manufacturer": "브랜드A", "model_name": "MODEL-1",
    "gtin": "9999999999999", "capacity": "100ml", "color_or_scent": "블랙",
    "options": ["기본"],
}


class PurchaseTaskServiceTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, FundingAccount.__table__,
                FundingLedger.__table__, PurchaseTask.__table__,
                PurchaseTaskCandidate.__table__,
                PurchaseTaskBudgetReservation.__table__,
                PurchaseRecord.__table__, PurchaseTaskTrackingInfo.__table__,
                PurchaseTaskEmailPreference.__table__,
                PurchaseTaskEmailLog.__table__,
                PurchaseTaskPolicySetting.__table__,
                PurchaseTaskCsvImportLog.__table__,
                AutomationModeState.__table__, EmergencyStop.__table__,
                ExecutionLimit.__table__, ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__, Role.__table__,
                User.__table__,
                Notification.__table__, NotificationRead.__table__,
            ],
        )

        with self.engine.begin() as conn:
            conn.execute(text(AUDIT_LOGS_DDL))

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()

        self.account = FundingAccount(
            company_id=self.company.id, total_funding=1000000.0,
        )
        self.db.add(self.account)
        self.db.commit()

        self.admin_user = User(
            company_id=self.company.id, email="admin@example.com",
            username="admin", password_hash="x", name="관리자",
        )
        self.db.add(self.admin_user)
        self.db.commit()

        self.policy_svc = PurchaseTaskPolicyService(self.db)
        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.min_net_profit = 0
        setting.min_margin_rate = 0
        setting.max_price_increase_rate = 0.5
        setting.require_return_allowed = True
        setting.min_seller_trust_score = 0.0 if hasattr(setting, "min_seller_trust_score") else None
        self.db.commit()

        self.service = PurchaseTaskService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_task(self, key="pt:1", quantity=1, sale=30000.0, fee=3000.0):

        return self.service.create_task(
            self.company.id, source_order_id=1, source_order_item_id=None,
            product_title="무선이어폰", brand="브랜드A", manufacturer="브랜드A",
            model_name="MODEL-1", gtin="1111111111111", capacity="100ml",
            quantity=quantity, color_or_scent="블랙", options=["기본"],
            coupang_sale_amount=sale, coupang_fee_amount=fee,
            purchase_deadline=NOW + timedelta(days=3),
            idempotency_key=key,
        )

    def _add_good_candidate(self, task_id, key_suffix="1", price=10000.0):

        return self.service.add_candidate(
            task_id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
            product_url=f"https://search.shopping.naver.com/product/{key_suffix}",
            candidate_title="무선이어폰 후보", brand="브랜드A",
            manufacturer="브랜드A", model_name="MODEL-1", gtin="1111111111111",
            capacity="100ml", color_or_scent="블랙", options=["기본"],
            estimated_price=price, estimated_shipping_fee=3000.0,
            estimated_delivery_days=2, seller_trust_score=0.9,
            return_allowed=True,
        )

    def _to_ready(self, key="pt:ready"):

        task = self._create_task(key=key)
        candidate = self._add_good_candidate(task.id, key_suffix=key)
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )
        return task, candidate, result


class HappyPathTestCase(PurchaseTaskServiceTestCaseBase):

    def test_create_task_is_idempotent(self):

        a = self._create_task(key="dup")
        b = self._create_task(key="dup")
        self.assertEqual(a.id, b.id)

    def test_search_links_use_more_than_product_name(self):

        task = self._create_task(key="search")
        links = self.service.get_search_links(task)
        self.assertIn("브랜드A", links["keyword"])
        self.assertIn("1111111111111", links["keyword"])
        self.assertEqual(len(links["urls"]), 4)
        for url in links["urls"].values():
            self.assertTrue(url.startswith("https://"))

    def test_add_candidate_transitions_to_candidates_ready(self):

        task = self._create_task(key="cand")
        self.assertEqual(task.status, PurchaseTaskStatus.SEARCH_REQUIRED)
        self._add_good_candidate(task.id, key_suffix="cand")
        self.db.refresh(task)
        self.assertEqual(task.status, PurchaseTaskStatus.CANDIDATES_READY)

    def test_full_flow_reaches_completed(self):

        task, candidate, result = self._to_ready(key="full")
        self.assertEqual(result.decision, "ALLOW")
        self.assertEqual(task.status, PurchaseTaskStatus.PURCHASE_READY)

        self.db.refresh(self.account)
        self.assertGreater(self.account.held_amount, 0.0)

        task = self.service.open_payment_page(task.id, self.company.id)
        self.assertEqual(task.status, PurchaseTaskStatus.USER_PAYMENT_PENDING)

        task = self.service.record_purchase(
            task.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
            external_order_number="N-ORDER-1", actual_amount=13000.0,
            actual_shipping_fee=3000.0, purchased_at=NOW,
            selected_option_note="기본", memo=None, recorded_by=1,
            idempotency_key="rec:full",
        )
        self.assertEqual(task.status, PurchaseTaskStatus.TRACKING_REQUIRED)

        task = self.service.record_tracking(
            task.id, self.company.id, courier="CJ대한통운",
            courier_confirmed=True, tracking_number="123456789",
            shipped_at=NOW, expected_arrival_at=NOW + timedelta(days=2),
            is_partial_shipment=False,
        )
        self.assertEqual(task.status, PurchaseTaskStatus.SHIPPED)

        task = self.service.mark_delivered(task.id, self.company.id)
        self.assertEqual(task.status, PurchaseTaskStatus.DELIVERED)

        task = self.service.complete_task(task.id, self.company.id)
        self.assertEqual(task.status, PurchaseTaskStatus.COMPLETED)


class MatchAndPolicyTestCase(PurchaseTaskServiceTestCaseBase):

    def test_product_mismatch_blocks(self):

        task = self._create_task(key="mismatch")
        candidate = self._add_good_candidate(task.id, key_suffix="mismatch")
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_MISMATCH_SOURCE_ATTRS, confirmed_by=1,
        )
        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_MISMATCH_SOURCE_ATTRS, evaluated_by=1,
        )
        self.assertEqual(task.status, PurchaseTaskStatus.BLOCKED)

    def test_stale_caution_reason_cleared_after_later_allow_decision(self):
        """실 브라우저 검증 중 발견한 결함 재발 방지 — 한 작업을 부분
        속성 후보로 먼저 평가해 REQUIRE_REVIEW(caution_reason 기록)로
        만든 뒤, 같은 작업을 완전한 속성의 다른 후보로 다시 평가해
        ALLOW(PURCHASE_READY)로 통과시키면 이전 caution_reason이
        화면에 남아있으면 안 된다."""

        task = self._create_task(key="stale-reason", sale=100000.0, fee=5000.0)
        weak_candidate = self._add_good_candidate(
            task.id, key_suffix="stale-reason-weak", price=50000.0,
        )
        partial_source = {
            "brand": "브랜드A", "manufacturer": None, "model_name": "MODEL-1",
            "gtin": None, "capacity": None, "color_or_scent": None,
            "options": [],
        }
        self.service.run_match_check(
            task.id, weak_candidate.id, self.company.id,
            source_attrs=partial_source, confirmed_by=1,
        )
        task, result = self.service.evaluate_and_prepare(
            task.id, weak_candidate.id, self.company.id,
            source_attrs=partial_source, evaluated_by=1,
        )
        self.assertEqual(task.status, PurchaseTaskStatus.REVIEW_REQUIRED)
        self.assertIsNotNone(task.caution_reason)

        strong_candidate = self._add_good_candidate(
            task.id, key_suffix="stale-reason-strong", price=40000.0,
        )
        self.service.run_match_check(
            task.id, strong_candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        task, result = self.service.evaluate_and_prepare(
            task.id, strong_candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )

        self.assertEqual(task.status, PurchaseTaskStatus.PURCHASE_READY)
        self.assertIsNone(task.caution_reason)
        self.assertIsNone(task.block_reason)
        self.assertIsNone(task.failure_code)

    def test_evaluate_without_match_check_is_rejected(self):

        task = self._create_task(key="nomatch")
        candidate = self._add_good_candidate(task.id, key_suffix="nomatch")
        with self.assertRaises(BadRequestException):
            self.service.evaluate_and_prepare(
                task.id, candidate.id, self.company.id,
                source_attrs=_SOURCE_ATTRS, evaluated_by=1,
            )

    def test_editing_candidate_after_match_check_is_detected_stale(self):

        task = self._create_task(key="stale")
        candidate = self._add_good_candidate(task.id, key_suffix="stale")
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        # 후보 속성이 등록 이후 바뀜(운영자가 다시 확인해 갱신).
        candidate.model_name = "MODEL-2"
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.evaluate_and_prepare(
                task.id, candidate.id, self.company.id,
                source_attrs=_SOURCE_ATTRS, evaluated_by=1,
            )

    def test_margin_shortfall_blocks(self):

        task = self._create_task(key="margin", sale=11000.0, fee=1000.0)
        candidate = self._add_good_candidate(
            task.id, key_suffix="margin", price=10000.0,
        )
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.min_net_profit = 100000  # 절대 못 넘는 기준
        self.db.commit()

        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )
        self.assertEqual(task.status, PurchaseTaskStatus.BLOCKED)

    def test_emergency_stop_blocks(self):

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )
        task = self._create_task(key="estop")
        candidate = self._add_good_candidate(task.id, key_suffix="estop")
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )
        self.assertEqual(task.status, PurchaseTaskStatus.BLOCKED)
        self.assertIn("EMERGENCY_STOP_ACTIVE", task.block_reason)


class PriceIncreaseBaselineTestCase(PurchaseTaskServiceTestCaseBase):
    """
    2026-09-10 Phase 10 — Phase 4에서 만들었지만 기준값(expected_
    amount_at_creation)을 채우는 호출부가 없어 절대 발동하지 않던
    PRICE_INCREASE_RATE_EXCEEDED 판정을 실제로 살렸다. 이 클래스는
    tests/test_purchase_task_price_increase_demotion.py처럼
    `_demote_price_change_on_increase()`를 직접 호출하는 게 아니라,
    `evaluate_and_prepare()`를 통해 이 판정이 실제로 발동하는지
    end-to-end로 검증한다.
    """

    def setUp(self):

        super().setUp()

        # 기반 클래스의 explicit 테이블 목록에는 Phase 3에서 추가된
        # function_automation_states가 없다(이 파일이 Phase 3보다
        # 먼저 존재했음) — 다른 테스트 클래스에 영향을 주지 않도록
        # 이 클래스 안에서만 추가로 만든다.
        from app.domains.automation_safety.model import FunctionAutomationState

        FunctionAutomationState.__table__.create(
            bind=self.engine, checkfirst=True,
        )

    def test_first_evaluation_sets_baseline_without_false_positive(self):

        task = self._create_task(key="baseline1")
        candidate = self._add_good_candidate(
            task.id, key_suffix="baseline1", price=10000.0,
        )
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )

        self.db.refresh(candidate)
        self.assertIsNotNone(candidate.expected_amount_at_creation)
        self.assertNotIn("PRICE_INCREASE_RATE_EXCEEDED", result.reasons)
        self.assertEqual(task.status, PurchaseTaskStatus.PURCHASE_READY)

    def test_baseline_stays_fixed_across_reevaluations(self):

        task = self._create_task(key="baseline2")
        candidate = self._add_good_candidate(
            task.id, key_suffix="baseline2", price=10000.0,
        )
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )
        self.db.refresh(candidate)
        first_baseline = candidate.expected_amount_at_creation
        self.assertIsNotNone(first_baseline)

        # 재평가를 위해 작업을 다시 연다(운영자가 후보를 재확인하는
        # 상황을 흉내낸다) — 가격은 그대로다.
        task.status = PurchaseTaskStatus.CANDIDATES_READY
        self.db.commit()

        self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )
        self.db.refresh(candidate)
        self.assertEqual(candidate.expected_amount_at_creation, first_baseline)

    def test_price_increase_beyond_tolerance_blocks_and_demotes_price_change(self):

        task = self._create_task(key="baseline3")
        candidate = self._add_good_candidate(
            task.id, key_suffix="baseline3", price=10000.0,
        )
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )
        self.assertEqual(result.decision, "ALLOW")

        # 매입처 가격이 크게 올랐다고 가정(정책 허용률 50%를 확실히
        # 넘도록 2배로 설정) — 재평가를 위해 작업을 다시 연다.
        candidate.estimated_price = 25000.0
        task.status = PurchaseTaskStatus.CANDIDATES_READY
        self.db.commit()

        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )

        self.assertIn("PRICE_INCREASE_RATE_EXCEEDED", result.reasons)
        self.assertEqual(task.status, PurchaseTaskStatus.BLOCKED)

        from app.domains.automation_safety.constants import FunctionCode
        from app.domains.automation_safety.constants import FunctionMode

        safety = SafetyService(self.db)
        self.assertEqual(
            safety.get_function_mode(self.company.id, FunctionCode.PRICE_CHANGE),
            FunctionMode.ERROR,
        )

    def test_price_increase_within_tolerance_does_not_block(self):

        task = self._create_task(key="baseline4")
        candidate = self._add_good_candidate(
            task.id, key_suffix="baseline4", price=10000.0,
        )
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, confirmed_by=1,
        )
        self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )

        # 정책 허용률(50%)보다 확실히 작은 변동(5%)은 차단하지 않아야
        # 한다.
        candidate.estimated_price = 10500.0
        task.status = PurchaseTaskStatus.CANDIDATES_READY
        self.db.commit()

        task, result = self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=_SOURCE_ATTRS, evaluated_by=1,
        )

        self.assertNotIn("PRICE_INCREASE_RATE_EXCEEDED", result.reasons)


class DuplicateAndUrlTestCase(PurchaseTaskServiceTestCaseBase):

    def test_duplicate_candidate_url_rejected(self):

        task = self._create_task(key="dupurl")
        self._add_good_candidate(task.id, key_suffix="dupurl")
        with self.assertRaises(BadRequestException):
            self._add_good_candidate(task.id, key_suffix="dupurl")

    def test_dangerous_scheme_rejected(self):

        task = self._create_task(key="danger")
        with self.assertRaises(Exception):
            self.service.add_candidate(
                task.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
                product_url="javascript:alert(1)",
            )

    def test_disallowed_domain_rejected(self):

        task = self._create_task(key="baddomain")
        with self.assertRaises(Exception):
            self.service.add_candidate(
                task.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
                product_url="https://evil.example.com/p/1",
            )

    def test_duplicate_purchase_order_number_rejected(self):

        task, candidate, _ = self._to_ready(key="duporder")
        self.service.open_payment_page(task.id, self.company.id)
        self.service.record_purchase(
            task.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
            external_order_number="DUP-1", actual_amount=13000.0,
            actual_shipping_fee=3000.0, purchased_at=NOW,
            selected_option_note=None, memo=None, recorded_by=1,
            idempotency_key="rec:duporder:1",
        )

        task2, candidate2, _ = self._to_ready(key="duporder2")
        self.service.open_payment_page(task2.id, self.company.id)
        with self.assertRaises(BadRequestException):
            self.service.record_purchase(
                task2.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
                external_order_number="DUP-1", actual_amount=13000.0,
                actual_shipping_fee=3000.0, purchased_at=NOW,
                selected_option_note=None, memo=None, recorded_by=1,
                idempotency_key="rec:duporder:2",
            )


class BudgetReservationTestCase(PurchaseTaskServiceTestCaseBase):

    def test_expired_reservation_releases_budget(self):

        task, candidate, _ = self._to_ready(key="expire")
        reservation = self.service.repository.get_reservation(
            task.budget_reservation_id, self.company.id,
        )
        reservation.expires_at = NOW - timedelta(hours=1)
        self.db.commit()

        released = self.service.release_expired_reservations(now=NOW)
        self.assertEqual(released, 1)

        self.db.refresh(self.account)
        self.assertEqual(self.account.held_amount, 0.0)

        self.db.refresh(task)
        self.assertEqual(task.status, PurchaseTaskStatus.PURCHASE_READY)

    def test_expired_reservation_past_deadline_while_payment_pending_is_uncertain(self):

        task, candidate, _ = self._to_ready(key="uncertain")
        self.service.open_payment_page(task.id, self.company.id)

        task.purchase_deadline = NOW - timedelta(hours=1)
        reservation = self.service.repository.get_reservation(
            task.budget_reservation_id, self.company.id,
        )
        reservation.expires_at = NOW - timedelta(minutes=30)
        self.db.commit()

        self.service.release_expired_reservations(now=NOW)

        self.db.refresh(task)
        self.assertEqual(task.status, PurchaseTaskStatus.UNCERTAIN)

        self.db.refresh(self.account)
        self.assertGreater(
            self.account.held_amount, 0.0,
            "결과가 불명확하면 예산을 반환하지 않아야 한다.",
        )

        with self.assertRaises(ConflictException):
            self.service.record_purchase(
                task.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
                external_order_number="RETRY-1", actual_amount=13000.0,
                actual_shipping_fee=3000.0, purchased_at=NOW,
                selected_option_note=None, memo=None, recorded_by=1,
                idempotency_key="rec:uncertain:retry",
            )

    def test_extend_reservation(self):

        task, candidate, _ = self._to_ready(key="extend")
        reservation = self.service.repository.get_reservation(
            task.budget_reservation_id, self.company.id,
        )
        original_expiry = reservation.expires_at

        self.service.extend_budget_reservation(
            task.id, self.company.id, extra_hours=24,
        )
        self.db.refresh(reservation)
        self.assertGreater(reservation.expires_at, original_expiry)


class NotificationWiringTestCase(PurchaseTaskServiceTestCaseBase):
    """작업 G/I — 이메일+콘솔 알림센터가 실제 상태 전이에 연결돼
    있는지 확인한다(FakePurchaseTaskEmailProvider만 사용, 실제
    발송 없음)."""

    def setUp(self):

        super().setUp()
        from app.domains.purchase_task.email_provider import (
            FakePurchaseTaskEmailProvider,
        )
        self.fake_email = FakePurchaseTaskEmailProvider()
        self.service = PurchaseTaskService(self.db, email_provider=self.fake_email)

    def test_create_task_notifies_admin_by_email_and_console(self):

        self._create_task(key="notif:create")

        self.assertEqual(len(self.fake_email.sent_messages), 1)
        self.assertEqual(
            self.fake_email.sent_messages[0].event_type, "TASK_CREATED",
        )

        notif = self.db.query(Notification).filter(
            Notification.company_id == self.company.id,
        ).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.category, "purchase_task")

    def test_review_required_notifies(self):
        """NEEDS_REVIEW 등급(부분 속성만 일치)이면 REQUIRE_REVIEW로
        낮아지고 그 사실을 알림으로 보낸다."""

        task = self._create_task(key="notif:review", quantity=1)
        candidate = self._add_good_candidate(task.id, key_suffix="notif:review")
        partial_source = {
            "brand": "브랜드A", "manufacturer": None, "model_name": "MODEL-1",
            "gtin": None, "capacity": None, "color_or_scent": None,
            "options": [],
        }
        self.service.run_match_check(
            task.id, candidate.id, self.company.id,
            source_attrs=partial_source, confirmed_by=1,
        )

        self.service.evaluate_and_prepare(
            task.id, candidate.id, self.company.id,
            source_attrs=partial_source, evaluated_by=1,
        )

        events = [m.event_type for m in self.fake_email.sent_messages]
        self.assertIn("REVIEW_REQUIRED", events)

    def test_full_flow_fires_payment_and_purchase_success_events(self):

        task, candidate, result = self._to_ready(key="notif:full")
        self.service.open_payment_page(task.id, self.company.id)
        self.service.record_purchase(
            task.id, self.company.id, shopping_mall_code="NAVER_SHOPPING",
            external_order_number="N-NOTIF-1", actual_amount=13000.0,
            actual_shipping_fee=3000.0, purchased_at=NOW,
            selected_option_note=None, memo=None, recorded_by=1,
            idempotency_key="rec:notif:full",
        )

        events = [m.event_type for m in self.fake_email.sent_messages]
        self.assertIn("PAYMENT_REQUIRED", events)
        self.assertIn("PURCHASE_SUCCESS", events)
        self.assertIn("TRACKING_REQUIRED", events)

    def test_check_deadlines_approaching_notifies_only_near_tasks(self):

        task_near, _, _ = self._to_ready(key="deadline:near")
        task_near.purchase_deadline = NOW + timedelta(hours=2)
        task_far, _, _ = self._to_ready(key="deadline:far")
        task_far.purchase_deadline = NOW + timedelta(days=10)
        self.db.commit()

        notified = self.service.check_deadlines_approaching(
            self.company.id, within_hours=24, now=NOW,
        )

        self.assertIn(task_near.id, notified)
        self.assertNotIn(task_far.id, notified)
        events = [m.event_type for m in self.fake_email.sent_messages]
        self.assertIn("DEADLINE_APPROACHING", events)

    def test_check_deadlines_approaching_is_a_one_time_notice(self):
        """반복 호출해도 같은 작업·사용자에게 두 번 발송하지 않는다
        (기존 _notify() idempotency_key 재사용 확인)."""

        task, _, _ = self._to_ready(key="deadline:repeat")
        task.purchase_deadline = NOW + timedelta(hours=1)
        self.db.commit()

        self.service.check_deadlines_approaching(
            self.company.id, within_hours=24, now=NOW,
        )
        self.service.check_deadlines_approaching(
            self.company.id, within_hours=24, now=NOW,
        )

        events = [
            m.event_type for m in self.fake_email.sent_messages
            if m.event_type == "DEADLINE_APPROACHING"
        ]
        self.assertEqual(len(events), 1)


class CompanyIsolationTestCase(PurchaseTaskServiceTestCaseBase):

    def test_company_b_cannot_see_company_a_task(self):

        from app.core.exceptions import NotFoundException

        task = self._create_task(key="isolation")

        other = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="서울",
        )
        self.db.add(other)
        self.db.commit()

        with self.assertRaises(NotFoundException):
            self.service.get_task(task.id, other.id)


if __name__ == "__main__":
    unittest.main()
