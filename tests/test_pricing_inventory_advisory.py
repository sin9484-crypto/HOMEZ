"""
=========================================================
Homez OS

File : tests/test_pricing_inventory_advisory.py

Gate AI-F1(2026-08-22 CTO 지시) 검증 — 가격 제안(PriceAdvisoryService)
+ 재고 보충 제안(ReplenishmentAdvisoryService). 임시 SQLite 파일
DB만 사용한다 — 실제 homez.db는 이 테스트 전체에서 전혀 접근하지
않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy import text as _sa_text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.ai_governance.model import ProposedAction
from app.domains.ai_governance.service import InactiveCapabilityError
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.channel_policy.constants import ChannelPolicyResult
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.company.model import Company
from app.domains.inventory.constants import InventoryLedgerEventType
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku
from app.domains.inventory.replenishment_advisory_service import (
    ReplenishmentAdvisoryService,
)
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.pricing.price_advisory_service import PriceAdvisoryService
from app.domains.pricing.price_advisory_service import PriceSuggestionInput
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.source.model import SupplierProductLink
from app.domains.user.model import User  # noqa: F401

from app.domains.brand.model import Brand  # noqa: F401
from app.domains.category.model import Category  # noqa: F401
from app.domains.product.model import Product  # noqa: F401
from app.domains.supplier.model import Supplier  # noqa: F401

from tests.ai_governance_test_helpers import deactivated_capability


class AdvisoryTestCaseBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceListing.__table__,
                InventorySku.__table__,
                InventoryReservation.__table__,
                InventoryLedgerEvent.__table__,
                InventoryChannelMapping.__table__,
                SupplierProductLink.__table__,
                ChannelPolicyRule.__table__,
                CompanyChannelPolicySettings.__table__,
                ChannelPolicyEvaluation.__table__,
                ProposedAction.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )

        with self.engine.begin() as _conn:
            _conn.execute(_sa_text(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            ))


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

        self.candidate = ProductCandidate(
            candidate_key="test:COUPANG:ADV-1", source_type="TREND",
            source_reference="ADV-1", market="COUPANG",
            product_name="테스트 상품", status="APPROVED",
        )
        self.db.add(self.candidate)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)


class PriceAdvisoryServiceTestCase(AdvisoryTestCaseBase):

    def setUp(self):

        super().setUp()
        self.service = PriceAdvisoryService(self.db)

    def _full_input(self, **overrides):

        defaults = dict(
            current_sale_price=Decimal("10000"),
            cost_of_goods=Decimal("5000"),
            channel_fee_rate=Decimal("0.10"),
            payment_fee_rate=Decimal("0.02"),
            shipping_cost=Decimal("1000"),
            packaging_cost=Decimal("200"),
            ad_cost=Decimal("300"),
            return_reserve_rate=Decimal("0.01"),
            tax_basis_rate=Decimal("0.00"),
            target_margin_rate=Decimal("0.30"),
        )
        defaults.update(overrides)
        return PriceSuggestionInput(**defaults)

    def test_recommends_price_reaching_target_margin(self):

        suggestion, envelope = self.service.suggest(
            self.company.id, self._full_input(),
        )

        self.assertIsNotNone(suggestion.recommended_sale_price)
        self.assertFalse(suggestion.is_provisional)
        self.assertEqual(envelope.result_type, "CALCULATED_RESULT")

        # 재검증 — 권장가로 실제 계산했을 때 목표 마진에 근접해야 한다.
        self.assertGreaterEqual(
            suggestion.expected_margin_rate, Decimal("0.29"),
        )

    def test_missing_cost_fields_produce_provisional_result(self):

        suggestion, envelope = self.service.suggest(
            self.company.id,
            self._full_input(cost_of_goods=None, shipping_cost=None),
        )

        self.assertTrue(suggestion.is_provisional)
        self.assertIn("cost_of_goods", suggestion.missing_cost_fields)
        self.assertIn("shipping_cost", suggestion.missing_cost_fields)
        self.assertEqual(envelope.result_type, "EVIDENCE_REQUIRED")

    def test_min_price_floor_is_applied(self):

        suggestion, _ = self.service.suggest(
            self.company.id,
            self._full_input(min_price=Decimal("50000")),
        )

        self.assertEqual(suggestion.recommended_sale_price, Decimal("50000.00"))
        self.assertTrue(
            any("최소 가격" in w for w in suggestion.risk_warnings),
        )

    def test_max_change_rate_caps_recommendation(self):

        suggestion, _ = self.service.suggest(
            self.company.id,
            self._full_input(max_change_rate=Decimal("0.05")),
        )

        upper = Decimal("10000") * Decimal("1.05")
        self.assertLessEqual(suggestion.recommended_sale_price, upper)
        self.assertTrue(
            any("변경률" in w for w in suggestion.risk_warnings),
        )

    def test_unreachable_target_margin_does_not_guess(self):

        suggestion, _ = self.service.suggest(
            self.company.id,
            self._full_input(
                channel_fee_rate=Decimal("0.5"),
                payment_fee_rate=Decimal("0.4"),
                target_margin_rate=Decimal("0.3"),
            ),
        )

        self.assertIsNone(suggestion.recommended_sale_price)
        self.assertTrue(any("불가능" in r for r in suggestion.reason))

    def test_policy_blocked_product_gets_no_price_suggestion(self):

        rule = ChannelPolicyRule(
            channel="COUPANG", rule_code="TEST_BLOCK",
            category_scope_json='["ALL"]', severity="BLOCKING",
            validation_type="CATEGORY_PROHIBITED",
            profile_version="v1", active=True,
        )
        self.db.add(rule)
        self.db.commit()

        evaluation = ChannelPolicyEvaluation(
            company_id=self.company.id,
            product_candidate_id=self.candidate.id,
            channel="COUPANG",
            result=ChannelPolicyResult.CHANNEL_POLICY_BLOCKED,
            policy_profile_version="v1",
            rule_results_json="[]",
            input_fingerprint="x" * 64,
        )
        self.db.add(evaluation)
        self.db.commit()

        suggestion, envelope = self.service.suggest(
            self.company.id,
            self._full_input(
                product_candidate_id=self.candidate.id, channel="COUPANG",
            ),
        )

        self.assertTrue(suggestion.policy_blocked)
        self.assertIsNone(suggestion.recommended_sale_price)
        self.assertEqual(envelope.result_type, "POLICY_BLOCKED")
        self.assertFalse(envelope.execution_allowed)

    def test_blocks_when_pricing_inventory_capability_deactivated(self):

        with deactivated_capability("PRICING_INVENTORY"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.suggest(self.company.id, self._full_input())

    def test_propose_price_change_creates_review_required_action(self):

        suggestion, _envelope, action = self.service.propose_price_change(
            self.company.id, listing_id=42, data=self._full_input(),
            idempotency_key="pc-adv:1",
        )

        self.assertIsNotNone(action)
        self.assertEqual(action.status, "REVIEW_REQUIRED")
        self.assertEqual(action.action_type, "PRICE_CHANGE_PROPOSAL")
        self.assertEqual(action.target_entity, "marketplace_listing:42")
        self.assertEqual(action.created_by, None)

    def test_propose_price_change_creates_no_action_when_unreachable(self):

        _s, _e, action = self.service.propose_price_change(
            self.company.id, listing_id=42,
            data=self._full_input(
                channel_fee_rate=Decimal("0.5"),
                payment_fee_rate=Decimal("0.4"),
            ),
            idempotency_key="pc-adv:2",
        )

        self.assertIsNone(action)


class ReplenishmentAdvisoryServiceTestCase(AdvisoryTestCaseBase):

    def setUp(self):

        super().setUp()
        self.service = ReplenishmentAdvisoryService(self.db)

    def _create_sku(self, available_qty=100, safety_stock=10):

        sku = InventorySku(
            company_id=self.company.id,
            product_candidate_id=self.candidate.id,
            sku_code="SKU-ADV-1",
            option_label="기본",
            available_qty=available_qty,
            reserved_qty=0,
            safety_stock=safety_stock,
        )
        self.db.add(sku)
        self.db.commit()
        return sku

    def _record_consumed(self, sku, qty, days_ago, now):

        event = InventoryLedgerEvent(
            company_id=self.company.id,
            inventory_sku_id=sku.id,
            event_type=InventoryLedgerEventType.CONSUMED,
            quantity_delta=-qty,
            available_after=sku.available_qty,
            reserved_after=0,
            created_at=now - timedelta(days=days_ago),
        )
        self.db.add(event)
        self.db.commit()

    def test_no_sales_history_returns_evidence_required(self):

        sku = self._create_sku()

        suggestion, envelope = self.service.suggest(sku.id, self.company.id)

        self.assertIsNone(suggestion.avg_daily_sales)
        self.assertIsNone(suggestion.recommended_order_qty)
        self.assertIn(
            "최근 판매(CONSUMED) 이력 없음", suggestion.missing_evidence,
        )
        self.assertEqual(envelope.result_type, "EVIDENCE_REQUIRED")

    def test_sales_history_produces_replenishment_suggestion(self):

        now = datetime(2026, 8, 22, 12, 0, 0)
        sku = self._create_sku(available_qty=20, safety_stock=5)
        for day in range(1, 11):
            self._record_consumed(sku, qty=3, days_ago=day, now=now)

        link = SupplierProductLink(
            company_id=self.company.id,
            product_candidate_id=self.candidate.id,
            supplier_id=1, supplier_sku="SUP-SKU-1",
            unit_cost=1000.0, moq=1, lead_time_days=7,
            created_by=1,
        )
        self.db.add(link)
        self.db.commit()

        suggestion, envelope = self.service.suggest(
            sku.id, self.company.id, lookback_days=10, now=now,
        )

        self.assertIsNotNone(suggestion.avg_daily_sales)
        self.assertEqual(suggestion.avg_daily_sales, Decimal("3.00"))
        self.assertIsNotNone(suggestion.recommended_order_qty)
        self.assertIsNotNone(suggestion.projected_stockout_date)
        self.assertEqual(envelope.result_type, "AI_ESTIMATE")

    def test_moq_raises_recommended_quantity(self):

        now = datetime(2026, 8, 22, 12, 0, 0)
        sku = self._create_sku(available_qty=5, safety_stock=2)
        for day in range(1, 4):
            self._record_consumed(sku, qty=1, days_ago=day, now=now)

        link = SupplierProductLink(
            company_id=self.company.id,
            product_candidate_id=self.candidate.id,
            supplier_id=1, supplier_sku="SUP-SKU-2",
            unit_cost=1000.0, moq=500, lead_time_days=3,
            created_by=1,
        )
        self.db.add(link)
        self.db.commit()

        suggestion, _ = self.service.suggest(
            sku.id, self.company.id, lookback_days=3, now=now,
        )

        self.assertTrue(suggestion.moq_applied)
        self.assertEqual(suggestion.recommended_order_qty, 500)

    def test_blocks_when_pricing_inventory_capability_deactivated(self):

        sku = self._create_sku()

        with deactivated_capability("PRICING_INVENTORY"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.suggest(sku.id, self.company.id)

    def test_propose_replenishment_creates_review_required_action(self):

        now = datetime(2026, 8, 22, 12, 0, 0)
        sku = self._create_sku(available_qty=20, safety_stock=5)
        for day in range(1, 11):
            self._record_consumed(sku, qty=3, days_ago=day, now=now)

        link = SupplierProductLink(
            company_id=self.company.id,
            product_candidate_id=self.candidate.id,
            supplier_id=1, supplier_sku="SUP-SKU-3",
            unit_cost=1000.0, moq=1, lead_time_days=7,
            created_by=1,
        )
        self.db.add(link)
        self.db.commit()

        _s, _e, action = self.service.propose_replenishment(
            sku.id, self.company.id, idempotency_key="repl-adv:1",
            lookback_days=10, now=now,
        )

        self.assertIsNotNone(action)
        self.assertEqual(action.status, "REVIEW_REQUIRED")
        self.assertEqual(
            action.action_type, "INVENTORY_REPLENISHMENT_PROPOSAL",
        )
        self.assertEqual(action.target_entity, f"inventory_sku:{sku.id}")

    def test_propose_replenishment_creates_no_action_without_sales_history(self):

        sku = self._create_sku()

        _s, _e, action = self.service.propose_replenishment(
            sku.id, self.company.id, idempotency_key="repl-adv:2",
        )

        self.assertIsNone(action)


if __name__ == "__main__":
    unittest.main()
