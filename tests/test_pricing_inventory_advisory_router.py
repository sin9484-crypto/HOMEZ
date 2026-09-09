"""
=========================================================
Homez OS

File : tests/test_pricing_inventory_advisory_router.py

Gate AI-F(2026-08-22 CTO 지시) 검증 — 가격·재고 추천 Router. httpx
미설치로 TestClient를 쓸 수 없어(tests/test_sourcing_ui_backend_
gates.py와 동일 관례) 라우터 함수를 직접 호출한다. 실제 homez.db는
전혀 접근하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy import text as _sa_text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.ai_governance.model import ProposedAction
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.service import SafetyService
from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.company.model import Company
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku
from app.domains.inventory.router import propose_replenishment
from app.domains.inventory.router import suggest_replenishment
from app.domains.inventory.schema import ReplenishmentProposalCreate
from app.domains.inventory.schema import ReplenishmentSuggestionRequest
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.pricing.router import propose_price_change
from app.domains.pricing.router import suggest_price
from app.domains.pricing.schema import PriceChangeProposalCreate
from app.domains.pricing.schema import PriceSuggestionRequest
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.source.model import SupplierProductLink
from app.domains.user.model import User

from app.domains.brand.model import Brand  # noqa: F401
from app.domains.category.model import Category  # noqa: F401
from app.domains.product.model import Product  # noqa: F401
from app.domains.supplier.model import Supplier  # noqa: F401


class _FakeUser:
    """current_user Depends 자리에 직접 넘기는 최소 스텁 — 실제 User
    ORM row가 필요 없다(라우터 함수는 .id/.company_id만 읽는다)."""

    def __init__(self, user_id, company_id):
        self.id = user_id
        self.company_id = company_id


class AdvisoryRouterTestCaseBase(unittest.TestCase):

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

        self.candidate = ProductCandidate(
            candidate_key="test:COUPANG:ROUTER-1", source_type="TREND",
            source_reference="ROUTER-1", market="COUPANG",
            product_name="테스트 상품", status="APPROVED",
        )
        self.db.add(self.candidate)
        self.db.commit()

        self.user_a = _FakeUser(1, self.company_a.id)
        self.user_b = _FakeUser(2, self.company_b.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)


class PriceRouterTestCase(AdvisoryRouterTestCaseBase):

    def _request(self, **overrides):

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
        return PriceSuggestionRequest(**defaults)

    def test_suggest_price_returns_envelope_and_does_not_write(self):

        response = suggest_price(
            self._request(), current_user=self.user_a, db=self.db,
        )

        self.assertIsNotNone(response.recommended_sale_price)
        self.assertIn("result_type", response.ai_result)
        self.assertEqual(
            self.db.query(ProposedAction).count(), 0,
            "조회만으로 ProposedAction이 만들어지면 안 된다.",
        )

    def test_propose_price_change_creates_action_scoped_to_company(self):

        result = propose_price_change(
            PriceChangeProposalCreate(
                **self._request().model_dump(),
                listing_id=1, idempotency_key="router-pc:1",
            ),
            current_user=self.user_a, db=self.db,
        )

        self.assertIsNotNone(result.proposed_action)
        self.assertEqual(result.proposed_action.company_id, self.company_a.id)

    def test_propose_price_change_blocked_during_emergency_stop(self):

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            propose_price_change(
                PriceChangeProposalCreate(
                    **self._request().model_dump(),
                    listing_id=1, idempotency_key="router-pc:estop",
                ),
                current_user=self.user_a, db=self.db,
            )

    def test_suggest_price_not_blocked_by_emergency_stop(self):
        """조회는 EStop과 무관해야 한다 — 실제 쓰기가 없기 때문."""

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        response = suggest_price(
            self._request(), current_user=self.user_a, db=self.db,
        )
        self.assertIsNotNone(response.recommended_sale_price)

    def test_company_a_cannot_create_action_visible_to_company_b(self):

        result = propose_price_change(
            PriceChangeProposalCreate(
                **self._request().model_dump(),
                listing_id=1, idempotency_key="router-pc:iso",
            ),
            current_user=self.user_a, db=self.db,
        )

        from app.domains.ai_governance.proposed_action_service import (
            ProposedActionService,
        )
        b_rows = ProposedActionService(self.db).list_for_company(
            self.company_b.id,
        )
        self.assertEqual(b_rows, [])

        # 회사 B가 회사 A의 ProposedAction을 승인하려 하면 조회 자체가
        # 안 된다(NotFoundException).
        from app.core.exceptions import NotFoundException
        with self.assertRaises(NotFoundException):
            ProposedActionService(self.db).approve(
                result.proposed_action.id, self.company_b.id, approved_by=2,
            )


class ReplenishmentRouterTestCase(AdvisoryRouterTestCaseBase):

    def _create_sku(self, company, available_qty=100, safety_stock=10):

        sku = InventorySku(
            company_id=company.id,
            product_candidate_id=self.candidate.id,
            sku_code=f"SKU-ROUTER-{company.id}",
            option_label="기본",
            available_qty=available_qty,
            reserved_qty=0,
            safety_stock=safety_stock,
        )
        self.db.add(sku)
        self.db.commit()
        return sku

    def test_suggest_replenishment_does_not_write(self):

        sku = self._create_sku(self.company_a)

        response = suggest_replenishment(
            sku.id, ReplenishmentSuggestionRequest(lookback_days=30),
            current_user=self.user_a, db=self.db,
        )

        self.assertEqual(response.sku_id, sku.id)
        self.assertEqual(self.db.query(ProposedAction).count(), 0)

    def test_company_a_cannot_query_company_b_sku(self):

        sku_b = self._create_sku(self.company_b)

        with self.assertRaises(Exception):
            suggest_replenishment(
                sku_b.id, ReplenishmentSuggestionRequest(lookback_days=30),
                current_user=self.user_a, db=self.db,
            )

    def test_propose_replenishment_blocked_during_emergency_stop(self):

        sku = self._create_sku(self.company_a)
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            propose_replenishment(
                sku.id,
                ReplenishmentProposalCreate(
                    lookback_days=30, idempotency_key="router-repl:estop",
                ),
                current_user=self.user_a, db=self.db,
            )

    def test_company_a_cannot_create_proposal_for_company_b_sku(self):

        sku_b = self._create_sku(self.company_b)

        with self.assertRaises(Exception):
            propose_replenishment(
                sku_b.id,
                ReplenishmentProposalCreate(
                    lookback_days=30, idempotency_key="router-repl:iso",
                ),
                current_user=self.user_a, db=self.db,
            )


if __name__ == "__main__":
    unittest.main()
