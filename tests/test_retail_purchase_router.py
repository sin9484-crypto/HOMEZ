"""
=========================================================
Homez OS

File : tests/test_retail_purchase_router.py

Gate RP-1(2026-08-22) 검증 — retail_purchase 라우터. httpx 미설치로
라우터 함수를 직접 호출한다. 실제 homez.db는 전혀 접근하지 않는다.
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.exceptions import UnauthorizedException
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingLedger
from app.domains.retail_purchase.model import PaymentAccountReference
from app.domains.retail_purchase.model import RetailPurchaseOrder
from app.domains.retail_purchase.model import RetailPurchasePolicySetting
from app.domains.retail_purchase.provider import FakeRetailPurchaseProvider
from app.domains.retail_purchase.router import create_purchase_request
from app.domains.retail_purchase.router import get_order
from app.domains.retail_purchase.router import get_policy
from app.domains.retail_purchase.router import get_product_detail
from app.domains.retail_purchase.router import list_orders
from app.domains.retail_purchase.router import list_providers
from app.domains.retail_purchase.router import preview_checkout
from app.domains.retail_purchase.router import run_policy_check
from app.domains.retail_purchase.router import search_products
from app.domains.retail_purchase.router import update_policy
from app.domains.retail_purchase.schema import CheckoutPreviewRequest
from app.domains.retail_purchase.schema import ProductSearchQueryRequest
from app.domains.retail_purchase.schema import RetailPurchasePolicyCheckRequest
from app.domains.retail_purchase.schema import RetailPurchasePolicySettingUpdate
from app.domains.retail_purchase.schema import RetailPurchaseRequestCreate

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


class _FakeUser:

    def __init__(self, user_id, company_id):
        self.id = user_id
        self.company_id = company_id


class RetailPurchaseRouterTestCase(unittest.TestCase):

    def setUp(self):

        FakeRetailPurchaseProvider.reset_state()
        reset_recent_auth_state_for_tests()
        self.addCleanup(reset_recent_auth_state_for_tests)
        self.addCleanup(FakeRetailPurchaseProvider.reset_state)

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

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="서울",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.account_a = FundingAccount(
            company_id=self.company_a.id, total_funding=1000000.0,
        )
        self.db.add(self.account_a)
        self.db.commit()

        self.user_a = _FakeUser(1, self.company_a.id)
        self.user_b = _FakeUser(2, self.company_b.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_request(self, user, key="rp:1"):

        return create_purchase_request(
            RetailPurchaseRequestCreate(
                source_order_id=1, provider_code="FAKE",
                product_url="https://fake.example/p1",
                external_product_id="FAKE-PRD-p1", selected_option=None,
                quantity=1, idempotency_key=key, match_confidence=1.0,
                match_evidence=[], expected_amount=10000.0,
            ),
            current_user=user, db=self.db,
        )

    def test_list_providers_reports_fake_as_test_only_and_others_as_contract_required(self):

        result = list_providers(current_user=self.user_a, db=self.db)
        by_code = {r.provider_code: r for r in result}

        self.assertEqual(by_code["FAKE"].connection_status, "TEST_ONLY")
        self.assertEqual(
            by_code["OFFICIAL_MARKETPLACE"].connection_status,
            "CONTRACT_REQUIRED",
        )
        self.assertIn("ORDER_CREATE", by_code["FAKE"].capabilities)
        self.assertIn(
            "ORDER_CREATE", by_code["OFFICIAL_MARKETPLACE"].not_supported,
        )

    def test_create_and_get_order_round_trip(self):

        created = self._create_request(self.user_a)
        fetched = get_order(created.id, current_user=self.user_a, db=self.db)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.status, "PROPOSED")

    def test_company_b_cannot_get_company_a_order(self):

        created = self._create_request(self.user_a)

        with self.assertRaises(NotFoundException):
            get_order(created.id, current_user=self.user_b, db=self.db)

    def test_list_orders_only_returns_own_company(self):

        self._create_request(self.user_a, key="rp:list:a")

        result_a = list_orders(
            status_filter=None, current_user=self.user_a, db=self.db,
        )
        result_b = list_orders(
            status_filter=None, current_user=self.user_b, db=self.db,
        )
        self.assertEqual(len(result_a), 1)
        self.assertEqual(len(result_b), 0)

    def test_policy_update_requires_recent_auth(self):

        with self.assertRaises(UnauthorizedException):
            update_policy(
                RetailPurchasePolicySettingUpdate(min_net_profit=1000),
                current_user=self.user_a, db=self.db,
                recent_auth_token=None,
            )

    def test_policy_update_succeeds_with_recent_auth_and_persists(self):

        token, _expires = issue_recent_auth_token(self.user_a.id)

        result = update_policy(
            RetailPurchasePolicySettingUpdate(
                min_net_profit=1500, allowed_provider_codes=["FAKE"],
            ),
            current_user=self.user_a, db=self.db,
            recent_auth_token=token,
        )
        self.assertEqual(result.min_net_profit, 1500)
        self.assertEqual(result.allowed_provider_codes, ["FAKE"])

        refetched = get_policy(current_user=self.user_a, db=self.db)
        self.assertEqual(refetched.min_net_profit, 1500)

    def test_policy_settings_are_isolated_per_company(self):

        token_a, _ = issue_recent_auth_token(self.user_a.id)
        update_policy(
            RetailPurchasePolicySettingUpdate(min_net_profit=9999),
            current_user=self.user_a, db=self.db, recent_auth_token=token_a,
        )

        policy_b = get_policy(current_user=self.user_b, db=self.db)
        self.assertNotEqual(policy_b.min_net_profit, 9999)

    def test_run_policy_check_via_router_allows_when_within_policy(self):

        token, _ = issue_recent_auth_token(self.user_a.id)
        update_policy(
            RetailPurchasePolicySettingUpdate(
                allowed_provider_codes=["FAKE"], min_net_profit=0,
                min_margin_rate=0, min_seller_trust_score=0.5,
                # 이 테스트는 _create_request()의 expected_amount
                # (10000)와 실제 결제금액(13000) 차이 자체를 검증
                # 대상으로 삼지 않으므로, 가격 상승률 한도를 넉넉히
                # 풀어 다른 조건만 순수하게 확인한다(가격 상승률
                # 차단 자체는 별도 테스트가 다룬다).
                max_price_increase_rate=1.0,
            ),
            current_user=self.user_a, db=self.db, recent_auth_token=token,
        )

        order = self._create_request(self.user_a, key="rp:policy:1")

        response = run_policy_check(
            order.id,
            RetailPurchasePolicyCheckRequest(
                in_stock=True, estimated_delivery_days=2,
                return_allowed=True, seller_trust_score=0.9,
                coupang_sale_amount=30000, retail_actual_amount=13000,
                shipping_fee=3000, coupang_fee_amount=3000,
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(response.decision, "ALLOW")
        self.assertEqual(response.order.status, "POLICY_CHECKED")

    def test_run_policy_check_blocks_when_retailer_not_allowed(self):

        # 기본 정책은 allowed_provider_codes가 빈 배열이라 전부 차단.
        order = self._create_request(self.user_a, key="rp:policy:2")

        response = run_policy_check(
            order.id,
            RetailPurchasePolicyCheckRequest(
                in_stock=True, coupang_sale_amount=30000,
                retail_actual_amount=13000, shipping_fee=3000,
                coupang_fee_amount=3000,
            ),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(response.decision, "BLOCK")
        self.assertIn("RETAILER_NOT_ALLOWED", response.reasons)

    def test_search_products_returns_fake_result(self):

        result = search_products(
            ProductSearchQueryRequest(
                provider_code="FAKE", keyword="테스트상품",
            ),
            current_user=self.user_a,
        )
        self.assertEqual(result.provider_code, "FAKE")
        self.assertEqual(len(result.items), 1)
        self.assertTrue(result.items[0].external_product_id.startswith("FAKE-PRD-"))

    def test_search_products_uncontracted_provider_blocked(self):

        with self.assertRaises(BadRequestException) as ctx:
            search_products(
                ProductSearchQueryRequest(
                    provider_code="OFFICIAL_MARKETPLACE", keyword="x",
                ),
                current_user=self.user_a,
            )
        self.assertIn("NOT_SUPPORTED", str(ctx.exception))

    def test_get_product_detail_returns_fake_detail(self):

        result = get_product_detail(
            "FAKE", "FAKE-PRD-1", current_user=self.user_a,
        )
        self.assertEqual(result.external_product_id, "FAKE-PRD-1")
        self.assertEqual(len(result.options), 1)

    def test_preview_checkout_computes_totals_without_creating_order(self):

        result = preview_checkout(
            CheckoutPreviewRequest(
                provider_code="FAKE", external_product_id="FAKE-PRD-1",
                quantity=2, expected_sale_amount=30000,
                expected_sale_fee_amount=3000,
            ),
            current_user=self.user_a,
        )
        self.assertEqual(result.quantity, 2)
        self.assertEqual(result.item_total, 20000)
        self.assertEqual(result.shipping_fee, 3000)
        self.assertEqual(result.total_purchase_amount, 23000)
        self.assertEqual(result.expected_net_profit, 30000 - 23000 - 3000)

        count = (
            self.db.query(RetailPurchaseOrder)
            .count()
        )
        self.assertEqual(count, 0, "미리보기는 주문 행을 만들지 않아야 한다.")


if __name__ == "__main__":
    unittest.main()
