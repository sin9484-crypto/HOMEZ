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
import unittest.mock as mock
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
from app.domains.funding.service import FundingService
from app.domains.purchase_task.constants import BudgetReservationStatus
from app.domains.purchase_task.constants import OrderSubmissionStatus
from app.domains.purchase_task.constants import PurchaseTaskStatus
from app.domains.purchase_task.model import (
    PurchaseChannelConnection,
    PurchaseChannelConnectionEvent,
    PurchaseOrderSubmissionAttempt,
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


class OnchannelCostReconciliationTestCaseBase(PurchaseTaskServiceTestCaseBase):
    """2026-09-19 항목 3(매입 실비용 연결) — 온채널 실 발주 트랙은
    record_purchase()(USER_PAYMENT_PENDING 전용)를 타지 않아, 정책
    평가 단계에서 이미 잡힌 예산 예약(_reserve_budget, 양쪽 트랙
    공통)이 확정도 해제도 되지 않은 채 RESERVED로 영원히 남는 결함이
    있었다. 이 테스트들은 `refresh_tracking_live()`가 실 API
    (GET seller/order/{code})의 sum_product_price/sum_delivery_price/
    sum_add_price로 그 예약만 확정·조정하는 새 연결
    (`_reconcile_onchannel_order_reservation`)을 검증한다.
    PurchaseRecord는 만들지 않는다(모델 자체의 "사람이 직접 입력"
    전제와 충돌 — 별도 승인 대상으로 남겨둠, 보고서 참고)."""

    def setUp(self):

        super().setUp()
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                PurchaseChannelConnection.__table__,
                PurchaseChannelConnectionEvent.__table__,
                PurchaseOrderSubmissionAttempt.__table__,
            ],
        )

        self.connection = PurchaseChannelConnection(
            company_id=self.company.id, mall_code="ONCHANNEL",
            account_label="테스트 계정", status="CONNECTED",
            connection_method="CREDENTIAL", idempotency_key="conn:oc-cost:1",
        )
        self.db.add(self.connection)
        self.db.commit()

    def _ready_onchannel_task(self, key):
        """PURCHASE_READY까지(양쪽 트랙 공통 예산 예약 포함) 실제
        파이프라인으로 진행한 뒤, 온채널 실 발주 성공을 흉내 낸다
        (record_purchase()는 호출하지 않음 — 그게 이 결함의 정의다)."""

        task, candidate, result = self._to_ready(key=key)
        self.assertEqual(result.decision, "ALLOW")
        self.db.refresh(self.account)
        reservation = self.repository_reservation(task)
        self.assertEqual(reservation.status, BudgetReservationStatus.RESERVED)

        attempt = PurchaseOrderSubmissionAttempt(
            company_id=self.company.id, connection_id=self.connection.id,
            purchase_task_id=task.id, idempotency_key=f"{key}-attempt",
            mall_code="ONCHANNEL", product_code="CH1",
            options_json="[]", status=OrderSubmissionStatus.SUCCEEDED,
            external_order_code=f"OC-{key}",
        )
        self.db.add(attempt)
        task.status = PurchaseTaskStatus.TRACKING_REQUIRED
        self.db.add(PurchaseTaskTrackingInfo(
            company_id=self.company.id, purchase_task_id=task.id,
        ))
        self.db.commit()
        return task, reservation, attempt

    def repository_reservation(self, task):

        return self.service.repository.get_reservation(
            task.budget_reservation_id, self.company.id,
        )

    def _patch_lookup_tracking(self, *, result=None, error=None):

        from app.domains.purchase_task.channel_connection_service import (
            PurchaseChannelConnectionService,
        )

        patcher = mock.patch.object(
            PurchaseChannelConnectionService, "lookup_tracking",
            side_effect=error, return_value=result,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _tracking_result(
        self, *, sum_product_price=None, sum_delivery_price=None,
        sum_add_price=None, tracking_number="111222333", courier="CJ대한통운",
    ):

        from app.domains.purchase_task.channel_adapter import TrackingLookupResult

        return TrackingLookupResult(
            support="SUPPORTED", courier=courier, tracking_number=tracking_number,
            delivery_status="IN_TRANSIT", detail="ok",
            sum_product_price=sum_product_price,
            sum_delivery_price=sum_delivery_price,
            sum_add_price=sum_add_price,
        )


class OnchannelCostReconciliationTestCase(OnchannelCostReconciliationTestCaseBase):

    def test_exact_match_confirms_reservation_and_appends_ledger_once(self):

        task, reservation, attempt = self._ready_onchannel_task("oc-exact")
        reserved_amount = reservation.amount
        held_before = self.account.held_amount

        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=int(reserved_amount) - 3000, sum_delivery_price=3000,
            sum_add_price=0,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        self.db.refresh(reservation)
        self.db.refresh(self.account)
        self.assertEqual(reservation.status, BudgetReservationStatus.CONFIRMED)
        self.assertEqual(self.account.held_amount, held_before, "정확히 일치하면 조정이 없다.")

        commit_ledgers = (
            self.db.query(FundingLedger)
            .filter(
                FundingLedger.reference_id == task.id,
                FundingLedger.type == FundingService.TYPE_HOLD_COMMIT,
            )
            .all()
        )
        self.assertEqual(len(commit_ledgers), 1, "확정 원장은 정확히 1건만 생겨야 한다.")
        self.assertEqual(commit_ledgers[0].amount, reserved_amount)

    def test_actual_higher_than_reserved_reserves_the_difference(self):

        task, reservation, attempt = self._ready_onchannel_task("oc-higher")
        reserved_amount = reservation.amount
        held_before = self.account.held_amount

        higher_product_price = int(reserved_amount) - 3000 + 500
        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=higher_product_price, sum_delivery_price=3000,
            sum_add_price=0,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        self.db.refresh(reservation)
        self.db.refresh(self.account)
        self.assertEqual(reservation.status, BudgetReservationStatus.CONFIRMED)
        self.assertEqual(self.account.held_amount, held_before + 500)

    def test_actual_lower_than_reserved_releases_the_difference(self):

        task, reservation, attempt = self._ready_onchannel_task("oc-lower")
        reserved_amount = reservation.amount
        held_before = self.account.held_amount

        lower_product_price = int(reserved_amount) - 3000 - 1000
        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=lower_product_price, sum_delivery_price=3000,
            sum_add_price=0,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        self.db.refresh(reservation)
        self.db.refresh(self.account)
        self.assertEqual(reservation.status, BudgetReservationStatus.CONFIRMED)
        self.assertEqual(self.account.held_amount, held_before - 1000)

    def test_insufficient_funds_blocks_without_corrupting_state_or_raising(self):

        task, reservation, attempt = self._ready_onchannel_task("oc-insufficient")
        reserved_amount = reservation.amount
        held_before = self.account.held_amount

        # 실제 결제금액이 예약보다 훨씬 크고, 남은 운영가능금액을
        # 초과하도록 만든다.
        huge_product_price = int(self.account.total_funding) + 999999
        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=huge_product_price, sum_delivery_price=0, sum_add_price=0,
        ))

        tracking = self.service.refresh_tracking_live(task.id, self.company.id)
        self.assertIsNotNone(tracking, "배송조회 자체는 실패하지 않아야 한다.")

        self.db.refresh(reservation)
        self.db.refresh(self.account)
        self.assertEqual(
            reservation.status, BudgetReservationStatus.RESERVED,
            "자금 부족이면 확정하지 않고 그대로 재시도 가능한 상태로 남긴다.",
        )
        self.assertEqual(self.account.held_amount, held_before, "부분 조정 없이 완전히 원상태.")

        commit_ledgers = (
            self.db.query(FundingLedger)
            .filter(
                FundingLedger.reference_id == task.id,
                FundingLedger.type == FundingService.TYPE_HOLD_COMMIT,
            )
            .all()
        )
        self.assertEqual(len(commit_ledgers), 0, "실패한 조정은 절대 원장에 기록하지 않는다.")

    def test_repeated_refresh_does_not_double_commit(self):

        task, reservation, attempt = self._ready_onchannel_task("oc-repeat")
        reserved_amount = reservation.amount

        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=int(reserved_amount) - 3000, sum_delivery_price=3000,
            sum_add_price=0,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        # 반복 클릭 방지 간격을 우회해 "재시작 후 다시 조회" 상황을
        # 재현한다 — 예약이 이미 CONFIRMED이므로 두 번째 호출은
        # 아무 것도 추가하지 않아야 한다.
        tracking = self.service.repository.get_tracking(task.id, self.company.id)
        tracking.last_live_refresh_at = None
        self.db.commit()

        self.service.refresh_tracking_live(task.id, self.company.id)

        commit_ledgers = (
            self.db.query(FundingLedger)
            .filter(
                FundingLedger.reference_id == task.id,
                FundingLedger.type == FundingService.TYPE_HOLD_COMMIT,
            )
            .all()
        )
        self.assertEqual(len(commit_ledgers), 1, "이미 CONFIRMED면 재조회해도 중복 커밋하지 않는다.")

    def test_missing_price_fields_leaves_reservation_untouched(self):

        task, reservation, attempt = self._ready_onchannel_task("oc-missing-price")

        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=None, sum_delivery_price=None, sum_add_price=None,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        self.db.refresh(reservation)
        self.assertEqual(
            reservation.status, BudgetReservationStatus.RESERVED,
            "확인되지 않은 금액을 0으로 채우거나 추정하지 않는다 — 그대로 둔다.",
        )

    def test_partial_price_fields_do_not_get_zero_filled(self):
        """상품가는 왔지만 배송비 합계가 응답에 없는(스펙상 필수가
        아님) 경우 — 배송비를 0으로 간주해 조기 확정하지 않는다."""

        task, reservation, attempt = self._ready_onchannel_task("oc-partial-price")

        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=int(reservation.amount) - 3000,
            sum_delivery_price=None, sum_add_price=0,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        self.db.refresh(reservation)
        self.assertEqual(
            reservation.status, BudgetReservationStatus.RESERVED,
            "일부 금액 필드가 없으면 나머지를 0으로 채워 조정하지 않는다.",
        )

    def test_unknown_order_confirmed_path_also_reconciles(self):
        """정상 SUCCEEDED 경로가 아니라 사람이 RESULT_UNKNOWN을
        ORDER_CONFIRMED로 직접 확정한 경로에서도 동일하게 동작해야
        한다(지시문 명시 검증 대상)."""

        task, candidate, result = self._to_ready(key="oc-unknown-confirmed")
        reservation = self.repository_reservation(task)
        reserved_amount = reservation.amount

        attempt = PurchaseOrderSubmissionAttempt(
            company_id=self.company.id, connection_id=self.connection.id,
            purchase_task_id=task.id, idempotency_key="oc-unknown-confirmed-attempt",
            mall_code="ONCHANNEL", product_code="CH1", options_json="[]",
            status=OrderSubmissionStatus.RESULT_UNKNOWN,
            unknown_resolution_status="ORDER_CONFIRMED",
            unknown_resolved_order_code="OC-UNKNOWN-CONFIRMED",
        )
        self.db.add(attempt)
        task.status = PurchaseTaskStatus.TRACKING_REQUIRED
        self.db.add(PurchaseTaskTrackingInfo(
            company_id=self.company.id, purchase_task_id=task.id,
        ))
        self.db.commit()

        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=int(reserved_amount) - 3000, sum_delivery_price=3000,
            sum_add_price=0,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        self.db.refresh(reservation)
        self.assertEqual(reservation.status, BudgetReservationStatus.CONFIRMED)


class OnchannelRefundAfterReconciliationTestCase(OnchannelCostReconciliationTestCaseBase):
    """환불 조정(지시문 3번) — 온채널 실비용 확정 이후 request_cancel/
    record_refund가 (수동 트랙과 동일하게) 안전하게 동작하는지 —
    이번 라운드에서 새로 만든 예약-확정 연결이 기존 환불 경로와
    충돌하지 않는지 확인한다(신규 환불 코드는 추가하지 않음)."""

    def test_refund_after_reconciliation_releases_correct_amount_only(self):

        task, reservation, attempt = self._ready_onchannel_task("oc-refund")
        reserved_amount = reservation.amount

        self._patch_lookup_tracking(result=self._tracking_result(
            sum_product_price=int(reserved_amount) - 3000, sum_delivery_price=3000,
            sum_add_price=0,
        ))
        self.service.refresh_tracking_live(task.id, self.company.id)

        self.db.refresh(self.account)
        held_after_confirm = self.account.held_amount

        task.status = PurchaseTaskStatus.CANCEL_REQUIRED
        self.db.commit()

        self.service.record_refund(
            task.id, self.company.id, refund_amount=reserved_amount, recorded_by=1,
        )

        self.db.refresh(self.account)
        self.assertEqual(
            self.account.held_amount, held_after_confirm - reserved_amount,
            "확정된 예약 금액만큼만 정확히 해제돼야 한다(과다·과소 해제 금지).",
        )

        ledgers = (
            self.db.query(FundingLedger)
            .filter(FundingLedger.reference_id == task.id)
            .order_by(FundingLedger.id)
            .all()
        )
        types = [ledger.type for ledger in ledgers]
        self.assertEqual(
            types,
            [
                FundingService.TYPE_HOLD_CREATE, FundingService.TYPE_HOLD_COMMIT,
                FundingService.TYPE_HOLD_RELEASE,
            ],
            "예약 생성(정책평가) + 확정(온채널 실비용) + 해제(환불), 그 외 자동 생성 없음.",
        )


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
