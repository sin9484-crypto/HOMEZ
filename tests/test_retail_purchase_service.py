"""
=========================================================
Homez OS

File : tests/test_retail_purchase_service.py

Gate RP-1(2026-08-22) 검증 — RetailPurchaseService 상태머신,
RetailPurchasePolicyService 정책 판단, 예산 원자적 예약. FakeRetail
PurchaseProvider만 사용하며 실제 네트워크·실제 쇼핑몰을 전혀
접촉하지 않는다. 실제 homez.db도 전혀 열지 않는다(임시 SQLite 파일).
=========================================================
"""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from decimal import Decimal

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
from app.domains.retail_purchase.constants import RetailPurchaseOrderStatus
from app.domains.retail_purchase.model import PaymentAccountReference
from app.domains.retail_purchase.model import RetailPurchaseOrder
from app.domains.retail_purchase.model import RetailPurchasePolicySetting
from app.domains.retail_purchase.policy_service import (
    RetailPurchasePolicyCheckInput,
)
from app.domains.retail_purchase.policy_service import RetailPurchasePolicyService
from app.domains.retail_purchase.product_matching import ProductAttributes
from app.domains.retail_purchase.product_matching import evaluate_same_product
from app.domains.retail_purchase.provider import FakeRetailPurchaseProvider
from app.domains.retail_purchase.provider import FakeRetailPurchaseScenario
from app.domains.retail_purchase.service import RetailPurchaseService

NOW = datetime(2026, 8, 22, 12, 0, 0)

_AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


def _perfect_match():

    attrs = ProductAttributes(
        brand="브랜드A", manufacturer="브랜드A", model_name="MODEL-1",
        gtin="1111111111111", capacity="100ml", quantity=1,
        color_or_scent="블랙", options=("기본",), components=("본품",),
    )
    return evaluate_same_product(attrs, attrs)


class RetailPurchaseServiceTestCaseBase(unittest.TestCase):

    def setUp(self):

        FakeRetailPurchaseProvider.reset_state()

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, FundingAccount.__table__,
                FundingLedger.__table__, RetailPurchaseOrder.__table__,
                PaymentAccountReference.__table__,
                RetailPurchasePolicySetting.__table__,
                AutomationModeState.__table__, EmergencyStop.__table__,
                ExecutionLimit.__table__, ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(_AUDIT_LOGS_DDL))

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

        self.policy_svc = RetailPurchasePolicyService(self.db)
        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.allowed_provider_codes_json = json.dumps(["FAKE"])
        setting.min_net_profit = 0
        setting.min_margin_rate = 0
        setting.max_price_increase_rate = 0.10
        setting.max_delivery_days = None
        setting.require_return_allowed = True
        setting.min_seller_trust_score = 0.5
        self.db.commit()

        self.service = RetailPurchaseService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        FakeRetailPurchaseProvider.reset_state()

    def _create_request(self, key="rp:1", quantity=1):

        return self.service.create_purchase_request(
            self.company.id, source_order_id=1, provider_code="FAKE",
            product_url="https://fake.example/p1",
            external_product_id="FAKE-PRD-p1",
            selected_option=None, quantity=quantity,
            idempotency_key=key, match_confidence=1.0,
            match_evidence_json="[]", expected_amount=10000.0,
        )

    def _good_policy_input(self, **overrides):

        defaults = dict(
            provider_code="FAKE", match_result=_perfect_match(),
            in_stock=True, estimated_delivery_days=2,
            return_allowed=True, seller_trust_score=0.9,
            coupang_sale_amount=Decimal("30000"),
            retail_actual_amount=Decimal("13000"),
            shipping_fee=Decimal("3000"),
            coupang_fee_amount=Decimal("3000"),
            expected_amount_at_proposal=Decimal("13000"),
        )
        defaults.update(overrides)
        return RetailPurchasePolicyCheckInput(**defaults)


class HappyPathTestCase(RetailPurchaseServiceTestCaseBase):

    def test_full_state_machine_reaches_ordered_and_confirms_budget(self):

        order = self._create_request()
        self.assertEqual(order.status, RetailPurchaseOrderStatus.PROPOSED)

        order, _policy_result = self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.POLICY_CHECKED)

        order = self.service.reserve_budget(
            order.id, self.company.id, Decimal("16000"),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BUDGET_RESERVED)

        self.db.refresh(self.account)
        self.assertEqual(self.account.held_amount, 16000.0)

        order, quote = self.service.request_quote(order.id, self.company.id)
        self.assertEqual(order.status, RetailPurchaseOrderStatus.QUOTED)

        order = self.service.place_order(
            order.id, self.company.id, quote_id=quote.quote_id,
            shipping_address_reference="addr-ref-1",
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.ORDERED)
        self.assertIsNotNone(order.external_order_id)

        ledger_types = [
            l.type for l in self.db.query(FundingLedger).order_by(FundingLedger.id).all()
        ]
        self.assertIn("HOLD_CREATE", ledger_types)
        self.assertIn("HOLD_COMMIT", ledger_types)

    def test_create_purchase_request_is_idempotent(self):

        first = self._create_request(key="rp:dup")
        second = self._create_request(key="rp:dup")
        self.assertEqual(first.id, second.id)

        count = (
            self.db.query(RetailPurchaseOrder)
            .filter(RetailPurchaseOrder.idempotency_key == "rp:dup")
            .count()
        )
        self.assertEqual(count, 1, "중복 구매 요청이 두 번째 행을 만들면 안 된다.")


class PolicyBlockTestCase(RetailPurchaseServiceTestCaseBase):

    def test_out_of_stock_blocks(self):

        order = self._create_request(key="rp:oos")
        order, _policy_result = self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(in_stock=False),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BLOCKED)
        self.assertIn("OUT_OF_STOCK", order.failure_code)

    def test_margin_shortfall_blocks(self):

        order = self._create_request(key="rp:margin")
        order, _policy_result = self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(
                coupang_sale_amount=Decimal("15000"),
                retail_actual_amount=Decimal("13000"),
                shipping_fee=Decimal("3000"),
                coupang_fee_amount=Decimal("1000"),
            ),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BLOCKED)
        self.assertIn("MIN_PROFIT_NOT_MET", order.failure_code)

    def test_retailer_not_allowed_blocks(self):

        order = self._create_request(key="rp:retailer")
        order, _policy_result = self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(provider_code="OFFICIAL_MARKETPLACE"),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BLOCKED)
        self.assertIn("RETAILER_NOT_ALLOWED", order.failure_code)

    def test_evidence_required_when_money_fields_missing(self):

        order = self._create_request(key="rp:evidence")
        order, _policy_result = self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(coupang_fee_amount=None),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BLOCKED)
        self.assertIn("EVIDENCE_REQUIRED", order.failure_code)

    def test_needs_review_tier_does_not_block(self):

        review_attrs_source = ProductAttributes(
            brand="브랜드A", model_name="MODEL-1",
        )
        review_attrs_candidate = ProductAttributes(
            brand="브랜드A", model_name="MODEL-1",
            manufacturer="브랜드A", capacity="100ml",
        )
        match = evaluate_same_product(review_attrs_source, review_attrs_candidate)

        result = self.policy_svc.evaluate(
            self.company.id, self._good_policy_input(match_result=match),
        )
        self.assertNotEqual(result.decision, "BLOCK")

    def test_emergency_stop_blocks_policy_check(self):

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )
        order = self._create_request(key="rp:estop")
        order, _policy_result = self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BLOCKED)
        self.assertIn("EMERGENCY_STOP_ACTIVE", order.failure_code)


class BudgetTestCase(RetailPurchaseServiceTestCaseBase):

    def test_budget_insufficient_blocks_reservation(self):

        order = self._create_request(key="rp:budget")
        order, _policy_result = self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        order = self.service.reserve_budget(
            order.id, self.company.id, Decimal("999999999"),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BLOCKED)
        self.assertEqual(order.failure_code, "BUDGET_INSUFFICIENT")

        self.db.refresh(self.account)
        self.assertEqual(self.account.held_amount, 0.0, "예약 실패 시 held_amount가 변하면 안 된다.")

    def test_concurrent_budget_reservation_does_not_double_reserve(self):
        """동시 두 요청이 계좌 잔액을 초과해 동시에 예약되지 않아야
        한다 — 원자적 조건부 UPDATE로 방어한다."""

        self.account.total_funding = 20000.0
        self.db.commit()

        order_a = self._create_request(key="rp:conc:a")
        order_b = self._create_request(key="rp:conc:b")

        self.service.run_policy_check(
            order_a.id, self.company.id, self._good_policy_input(),
        )
        self.service.run_policy_check(
            order_b.id, self.company.id, self._good_policy_input(),
        )

        results = {}
        barrier = threading.Barrier(2)
        # self.company.id를 스레드 시작 전에 미리 평가해 둔다 —
        # self.company는 메인 스레드의 self.db 세션에 바인딩된 ORM
        # 객체라, 두 스레드가 barrier 해제 직후 동시에 그 속성에
        # 접근하면(commit 이후 만료된 속성의 지연 재조회가 같은
        # 커넥션을 동시에 두드리게 돼) SQLite 커넥션 공유 경쟁이
        # 발생할 수 있다 — 이 경쟁은 검증 대상인 _reserve_budget_
        # conditional()의 원자성과 무관한 테스트 자체의 결함이므로,
        # 평범한 int를 미리 캡처해 스레드에 넘기는 방식으로 제거한다.
        company_id = self.company.id

        def reserve(order_id, key):

            engine2 = create_engine(
                f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
            )
            SessionLocal2 = sessionmaker(bind=engine2)
            thread_db = SessionLocal2()
            service = RetailPurchaseService(thread_db)
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            try:
                order = service.reserve_budget(
                    order_id, company_id, Decimal("15000"),
                )
                results[key] = order.status
            except Exception as e:  # noqa: BLE001
                results[key] = f"error:{type(e).__name__}:{e}"
            finally:
                thread_db.close()
                engine2.dispose()

        t1 = threading.Thread(target=reserve, args=(order_a.id, "a"))
        t2 = threading.Thread(target=reserve, args=(order_b.id, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        statuses = list(results.values())
        self.assertEqual(len(statuses), 2)
        self.assertEqual(
            statuses.count(RetailPurchaseOrderStatus.BUDGET_RESERVED), 1,
            f"정확히 하나만 예약에 성공해야 한다: {results}",
        )
        self.assertEqual(statuses.count(RetailPurchaseOrderStatus.BLOCKED), 1)

        self.db.refresh(self.account)
        self.assertLessEqual(
            self.account.held_amount, self.account.total_funding,
            "held_amount가 total_funding을 초과하면 예산이 이중 예약된 것이다.",
        )

    def test_failed_order_releases_budget(self):

        order = self._create_request(key="rp:fail-release")
        self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.service.reserve_budget(order.id, self.company.id, Decimal("16000"))

        # place_order 전에 실패 시나리오로 바꾸려면 Provider를 다시
        # 만들어야 하므로, 여기서는 request_quote까지는 정상 진행하고
        # place_order에서 max_total_amount를 의도적으로 낮춰
        # PRICE_EXCEEDS_LIMIT 실패를 재현한다(실제 Provider 로직
        # 그대로 사용, mock 없음).
        order, quote = self.service.request_quote(order.id, self.company.id)

        from app.domains.retail_purchase import provider as provider_module

        original_get = provider_module.get_retail_purchase_provider

        def _low_budget_provider(code):
            p = original_get(code)
            return p

        # actual_amount를 낮춰 재검증 실패를 유도(place_order 내부가
        # order.actual_amount를 max_total_amount로 쓰므로).
        order.actual_amount = 1.0
        self.db.commit()

        result_order = self.service.place_order(
            order.id, self.company.id, quote_id=quote.quote_id,
            shipping_address_reference="addr-1",
        )
        self.assertEqual(result_order.status, RetailPurchaseOrderStatus.FAILED)

        self.db.refresh(self.account)
        self.assertEqual(
            self.account.held_amount, 0.0,
            "실패 시 예약된 예산이 전부 해제되어야 한다.",
        )


class UncertainResultTestCase(RetailPurchaseServiceTestCaseBase):

    def test_uncertain_result_does_not_release_budget_and_blocks_retry(self):

        order = self._create_request(key="rp:uncertain")
        self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.service.reserve_budget(order.id, self.company.id, Decimal("16000"))
        order, quote = self.service.request_quote(order.id, self.company.id)

        import app.domains.retail_purchase.provider as provider_module

        original_registry = dict(provider_module._PROVIDER_REGISTRY)

        class _UncertainFakeProvider(provider_module.FakeRetailPurchaseProvider):
            def __init__(self):
                super().__init__(scenario=FakeRetailPurchaseScenario.ORDER_UNCERTAIN)

        provider_module._PROVIDER_REGISTRY["FAKE"] = _UncertainFakeProvider
        try:
            result_order = self.service.place_order(
                order.id, self.company.id, quote_id=quote.quote_id,
                shipping_address_reference="addr-1",
            )
        finally:
            provider_module._PROVIDER_REGISTRY.clear()
            provider_module._PROVIDER_REGISTRY.update(original_registry)

        self.assertEqual(result_order.status, RetailPurchaseOrderStatus.UNCERTAIN)

        self.db.refresh(self.account)
        self.assertGreater(
            self.account.held_amount, 0.0,
            "결과가 불명확하면 예산을 해제하지 않아야 한다(중복 결제 방지 우선).",
        )

        with self.assertRaises(ConflictException):
            self.service.place_order(
                order.id, self.company.id, quote_id="another-quote",
                shipping_address_reference="addr-1",
            )


class PolicyFingerprintInvalidationTestCase(RetailPurchaseServiceTestCaseBase):
    """2026-08-22 14차 지시(작업 5) — 정책 검사 이후 정책이 바뀌면
    이전 판정을 더 진행시키지 않는다."""

    def test_reserve_budget_blocked_after_policy_changed_since_check(self):

        order = self._create_request(key="rp:fp:1")
        order, _result = self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.POLICY_CHECKED)
        self.assertIsNotNone(order.policy_fingerprint)

        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.min_net_profit = 999999
        self.db.commit()

        with self.assertRaises(ConflictException):
            self.service.reserve_budget(
                order.id, self.company.id, Decimal("16000"),
            )

    def test_reserve_budget_succeeds_when_policy_unchanged(self):

        order = self._create_request(key="rp:fp:2")
        order, _result = self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        order = self.service.reserve_budget(
            order.id, self.company.id, Decimal("16000"),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BUDGET_RESERVED)


class NewPolicyFieldsTestCase(RetailPurchaseServiceTestCaseBase):
    """2026-08-22 14차 지시(작업 2) — 운영자 입력 UI가 실제로 다뤄야
    하는 새 정책 필드(상품별 최대 수량/자동 실행 여부/승인 필요 금액
    기준)의 서비스 계층 동작."""

    def test_max_quantity_per_product_blocks(self):

        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.max_quantity_per_product = 2
        self.db.commit()

        order = self._create_request(key="rp:qty", quantity=5)
        order, result = self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(quantity=5),
        )
        self.assertEqual(order.status, RetailPurchaseOrderStatus.BLOCKED)
        self.assertIn("MAX_QUANTITY_PER_PRODUCT_EXCEEDED", order.failure_code)

    def test_auto_execute_disabled_demotes_allow_to_require_review(self):

        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.auto_execute_enabled = False
        self.db.commit()

        order = self._create_request(key="rp:auto-off")
        order, result = self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.assertEqual(result.decision, "REQUIRE_REVIEW")
        # REQUIRE_REVIEW는 BLOCK이 아니므로 여전히 다음 단계(예산예약)
        # 로 진행할 수 있다 — 사람 확인은 UI 책임이다.
        self.assertEqual(order.status, RetailPurchaseOrderStatus.POLICY_CHECKED)

    def test_approval_required_amount_threshold_demotes_allow_to_require_review(self):

        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.approval_required_amount_threshold = 1000
        self.db.commit()

        order = self._create_request(key="rp:threshold")
        order, result = self.service.run_policy_check(
            order.id, self.company.id,
            self._good_policy_input(),  # required_budget = 13000+3000 = 16000
        )
        self.assertEqual(result.decision, "REQUIRE_REVIEW")

    def test_amount_below_threshold_still_allows(self):

        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.approval_required_amount_threshold = 999999
        self.db.commit()

        order = self._create_request(key="rp:threshold:2")
        order, result = self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.assertEqual(result.decision, "ALLOW")


class UncertainAutoReleaseTestCase(RetailPurchaseServiceTestCaseBase):
    """2026-08-22 14차 지시(작업 2) — UNCERTAIN 상태 처리정책.
    구매(Provider 재호출)는 어떤 경우에도 자동 재시도하지 않는다 —
    이 정책은 예산 반환 시점만 다룬다."""

    def _make_uncertain_order(self, key: str):

        order = self._create_request(key=key)
        self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.service.reserve_budget(order.id, self.company.id, Decimal("16000"))
        order, quote = self.service.request_quote(order.id, self.company.id)

        import app.domains.retail_purchase.provider as provider_module

        original_registry = dict(provider_module._PROVIDER_REGISTRY)

        class _UncertainFakeProvider(provider_module.FakeRetailPurchaseProvider):
            def __init__(self):
                super().__init__(scenario=FakeRetailPurchaseScenario.ORDER_UNCERTAIN)

        provider_module._PROVIDER_REGISTRY["FAKE"] = _UncertainFakeProvider
        try:
            order = self.service.place_order(
                order.id, self.company.id, quote_id=quote.quote_id,
                shipping_address_reference="addr-1",
            )
        finally:
            provider_module._PROVIDER_REGISTRY.clear()
            provider_module._PROVIDER_REGISTRY.update(original_registry)

        return order

    def test_hold_indefinitely_never_auto_releases(self):

        order = self._make_uncertain_order("rp:uc:hold")
        # 기본 정책은 HOLD_INDEFINITELY — get_order()를 아무리 다시
        # 조회해도 예산이 반환되지 않는다.
        refetched = self.service.get_order(order.id, self.company.id)
        self.assertFalse(refetched.uncertain_budget_released)

        self.db.refresh(self.account)
        self.assertGreater(self.account.held_amount, 0.0)

    def test_auto_release_after_timeout_releases_budget_but_never_reorders(self):

        from app.domains.retail_purchase.constants import (
            RetailPurchaseUncertainHandlingPolicy,
        )

        setting = self.policy_svc.get_or_create_default_settings(self.company.id)
        setting.uncertain_handling_policy = (
            RetailPurchaseUncertainHandlingPolicy.AUTO_RELEASE_AFTER_TIMEOUT
        )
        setting.uncertain_auto_release_after_hours = 1
        self.db.commit()

        order = self._make_uncertain_order("rp:uc:auto")

        # 아직 시간이 지나지 않았으면 반환되지 않는다.
        not_yet = self.service.get_order(order.id, self.company.id)
        self.assertFalse(not_yet.uncertain_budget_released)

        # updated_at을 과거로 돌려 "1시간 경과"를 재현한다(실제
        # 스케줄러·sleep 없이 결정론적으로 검증).
        order.updated_at = datetime(2000, 1, 1)
        self.db.commit()

        released = self.service.get_order(order.id, self.company.id)
        self.assertTrue(released.uncertain_budget_released)
        self.assertEqual(
            released.status, RetailPurchaseOrderStatus.UNCERTAIN,
            "예산만 반환할 뿐 상태를 재시도 가능한 값으로 바꾸지 않는다.",
        )

        self.db.refresh(self.account)
        self.assertEqual(self.account.held_amount, 0.0)

        # 구매 자체는 여전히 재시도할 수 없다(절대 금지 사항 재확인).
        with self.assertRaises(ConflictException):
            self.service.place_order(
                order.id, self.company.id, quote_id="another-quote",
                shipping_address_reference="addr-1",
            )


class ProviderErrorContractTestCase(RetailPurchaseServiceTestCaseBase):
    """2026-08-22 14차 지시(작업 5) — Provider 실패가 표준
    ProviderErrorCode로 분류되는지 확인."""

    def test_price_changed_uses_standard_error_code(self):

        order = self._create_request(key="rp:price-changed")
        self.service.run_policy_check(
            order.id, self.company.id, self._good_policy_input(),
        )
        self.service.reserve_budget(order.id, self.company.id, Decimal("16000"))
        order, quote = self.service.request_quote(order.id, self.company.id)

        order.actual_amount = 1.0  # 재검증 시 max_total_amount로 쓰임
        self.db.commit()

        result_order = self.service.place_order(
            order.id, self.company.id, quote_id=quote.quote_id,
            shipping_address_reference="addr-1",
        )
        self.assertEqual(result_order.failure_code, "PRICE_CHANGED")


class CompanyIsolationTestCase(RetailPurchaseServiceTestCaseBase):

    def test_company_b_cannot_see_company_a_order(self):

        from app.core.exceptions import NotFoundException

        order = self._create_request(key="rp:isolation")

        other_company = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="서울",
        )
        self.db.add(other_company)
        self.db.commit()

        with self.assertRaises(NotFoundException):
            self.service.get_order(order.id, other_company.id)


class AuditLoggingTestCase(RetailPurchaseServiceTestCaseBase):
    """2026-08-24 Section 2 — RetailPurchaseService의 상태변경
    메서드가 실제로 audit_logs에 기록을 남기는지, 멱등 경로에서
    중복 기록되지 않는지, 회사 간 격리가 유지되는지 검증한다."""

    def _audit_rows(self, action: str | None = None):

        sql = "SELECT company_id, user_id, action, entity, entity_id, description FROM audit_logs"
        params = {}
        if action is not None:
            sql += " WHERE action = :action"
            params["action"] = action
        return self.db.execute(text(sql), params).fetchall()

    def test_create_purchase_request_writes_audit_log(self):

        order = self._create_request(key="rp:audit-1")

        rows = self._audit_rows("RETAIL_PURCHASE_ORDER_CREATED")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.company_id, self.company.id)
        self.assertEqual(row.entity, "retail_purchase_order")
        self.assertEqual(row.entity_id, str(order.id))

    def test_idempotent_create_does_not_duplicate_audit_log(self):

        self._create_request(key="rp:audit-dup")
        self._create_request(key="rp:audit-dup")  # 동일 idempotency_key

        rows = self._audit_rows("RETAIL_PURCHASE_ORDER_CREATED")
        self.assertEqual(
            len(rows), 1,
            "멱등 재호출(기존 반환)인데 감사로그가 중복 기록됨",
        )

    def test_audit_log_company_isolation(self):

        order_a = self._create_request(key="rp:audit-iso-a")

        other_company = Company(
            name="회사 B", business_number="333-33-33333",
            ceo="대표B", phone="02-000-0003",
            email="c@example.com", address="서울",
        )
        self.db.add(other_company)
        self.db.commit()

        order_b = self.service.create_purchase_request(
            other_company.id, source_order_id=2, provider_code="FAKE",
            product_url="https://fake.example/p2",
            external_product_id="FAKE-PRD-p2",
            selected_option=None, quantity=1,
            idempotency_key="rp:audit-iso-b", match_confidence=1.0,
            match_evidence_json="[]", expected_amount=5000.0,
        )

        rows = self._audit_rows("RETAIL_PURCHASE_ORDER_CREATED")
        by_entity = {r.entity_id: r.company_id for r in rows}
        self.assertEqual(by_entity[str(order_a.id)], self.company.id)
        self.assertEqual(by_entity[str(order_b.id)], other_company.id)
        self.assertNotEqual(by_entity[str(order_a.id)], by_entity[str(order_b.id)])

    def test_audit_log_description_has_no_sensitive_markers(self):

        self._create_request(key="rp:audit-sensitive")

        rows = self._audit_rows("RETAIL_PURCHASE_ORDER_CREATED")
        description = rows[0].description.lower()
        for forbidden in ("password", "card", "secret", "token", "api_key"):
            self.assertNotIn(forbidden, description)

    def test_policy_block_writes_audit_log(self):

        order = self._create_request(key="rp:audit-blocked")
        bad_input = self._good_policy_input(in_stock=False)

        self.service.run_policy_check(order.id, self.company.id, bad_input)

        rows = self._audit_rows("RETAIL_PURCHASE_POLICY_BLOCKED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].entity_id, str(order.id))


if __name__ == "__main__":
    unittest.main()
