"""
=========================================================
Homez OS

File : tests/test_v7_integrated_e2e.py

V7 Section 9(2026-08-20) — 두 Fake E2E 시나리오:
A. 정상 판매·배송·정산(재고 충분)
B. 재고 부족 → 공급처 검색(SupplierProductLink) → 소싱 추천
   (SourcingRecommendationService) → 발주안 승인 → 입고 → 재예약 →
   출고, orchestration projection이 각 단계마다 올바른 stage를
   보고하는지 함께 검증한다.

기존 Order/Inventory/Purchase/Shipment/Funding Service를 그대로
재사용한다(복제 없음). 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.automation_safety.model import EmergencyStop
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.service import FundingService
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku
from app.domains.inventory.schema import InventoryChannelMappingCreate
from app.domains.inventory.schema import InventorySkuCreate
from app.domains.inventory.service import InventoryService
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.orchestration.constants import OrchestrationStage
from app.domains.orchestration.dashboard_service import DashboardService
from app.domains.orchestration.projection_service import (
    OrchestrationProjectionService,
)
from app.domains.order.constants import OrderIngestionStatus
from app.domains.order.constants import OrderItemStatus
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.model import OrderStatusEvent
from app.domains.order.schema import OrderChannelCollectRequest
from app.domains.order.schema import OrderChannelItemPayload
from app.domains.order.service import OrderService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.purchase.constants import PurchaseStatus
from app.domains.purchase.model import Purchase
from app.domains.purchase.model import PurchaseItem
from app.domains.purchase.service import PurchaseService
from app.domains.return_order.model import ReturnOrder
from app.domains.return_order.model import ReturnOrderStatusEvent
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.shipment.constants import ShipmentStatus
from app.domains.shipment.model import Shipment
from app.domains.shipment.model import ShipmentItem
from app.domains.shipment.model import ShipmentStatusEvent
from app.domains.shipment.schema import ShipmentCreate
from app.domains.shipment.service import ShipmentService
from app.domains.source.model import CompanySupplierRelation
from app.domains.source.model import SupplierProductLink
from app.domains.source.recommendation_service import (
    SourcingRecommendationService,
)
from app.domains.source.schema import CompanySupplierRelationCreate
from app.domains.source.schema import SupplierProductLinkCreate
from app.domains.source.service import SourceService
from app.domains.supplier.model import Supplier
from app.domains.user.model import User  # noqa: F401

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class V7IntegratedE2ETestCase(unittest.TestCase):

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
                FundingAccount.__table__,
                FundingHold.__table__,
                SupplierPayment.__table__,
                FundingLedger.__table__,
                Order.__table__,
                OrderItem.__table__,
                OrderIngestionEvent.__table__,
                OrderStatusEvent.__table__,
                Purchase.__table__,
                PurchaseItem.__table__,
                Shipment.__table__,
                ShipmentItem.__table__,
                ShipmentStatusEvent.__table__,
                ReturnOrder.__table__,
                ReturnOrderStatusEvent.__table__,
                MarketplaceSettlement.__table__,
                Supplier.__table__,
                SupplierProductLink.__table__,
                CompanySupplierRelation.__table__,
                EmergencyStop.__table__,
            ],
        )

        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="V7 통합 E2E 회사", business_number="444-44-44444",
            ceo="대표", phone="02-000-0004", email="v7e2e@example.com",
            address="서울",
        )
        self.db.add(self.company)
        self.db.commit()
        self.company_id = self.company.id

        self.order_service = OrderService(self.db)
        self.purchase_service = PurchaseService(self.db)
        self.shipment_service = ShipmentService(self.db)
        self.inventory_service = InventoryService(self.db)
        self.funding_service = FundingService(self.db)
        self.source_service = SourceService(self.db)
        self.recommendation_service = SourcingRecommendationService(self.db)
        self.projection_service = OrchestrationProjectionService(self.db)
        self.dashboard_service = DashboardService(self.db)

        self.channel = MarketplaceChannel(
            code="FAKE", name="가짜채널", doc_verification_status="VERIFIED",
        )
        self.db.add(self.channel)
        self.db.commit()

        self.account = MarketplaceAccount(
            company_id=self.company_id, channel_id=self.channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(self.account)
        self.db.commit()

        self.funding_service.create_account(
            FundingAccountCreate(total_funding=200_000.0), self.company_id,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_candidate_and_sku(self, sku_code, channel_sku, initial_qty):

        candidate = ProductCandidate(
            candidate_key=f"v7e2e:FAKE:{sku_code}", source_type="TREND",
            source_reference=sku_code, market="FAKE",
            product_name=f"V7 통합 테스트 상품 {sku_code}", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        sku = self.inventory_service.create_sku(
            InventorySkuCreate(
                product_candidate_id=candidate.id,
                sku_code=sku_code, option_label=sku_code,
                initial_qty=initial_qty, safety_stock=1,
            ),
            self.company_id,
        )

        listing = MarketplaceListing(
            company_id=self.company_id,
            product_candidate_id=candidate.id,
            marketplace_account_id=self.account.id,
            status="DRAFT",
        )
        self.db.add(listing)
        self.db.commit()

        self.inventory_service.create_channel_mapping(
            sku.id, self.company_id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="FAKE", channel_sku=channel_sku,
            ),
        )

        return candidate, sku

    # ------------------------------------------------
    # A. 정상 판매·배송·정산
    # ------------------------------------------------

    def test_scenario_a_normal_sale_ship_settle(self):

        _candidate, _sku = self._create_candidate_and_sku(
            "V7A-1", "CH-V7A-1", initial_qty=5,
        )

        collect_result = self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id="V7A-ORDER-1",
                buyer_name="김구매", receiver_name="김수취",
                receiver_phone="010-1111-1111",
                receiver_address="서울시 강남구", receiver_zipcode="06120",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-V7A-1", quantity=1,
                        unit_price=10000, product_name="V7 통합 테스트 상품",
                    ),
                ],
            ),
            triggered_by=1,
        )
        self.assertEqual(
            collect_result["ingestion_status"], OrderIngestionStatus.COLLECTED,
        )
        item = collect_result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.RESERVED)

        stage = self.projection_service.project_order_item_stage(
            item.id, self.company_id,
        )
        self.assertEqual(stage, OrchestrationStage.FULFILLMENT_READY)

        shipment = self.shipment_service.create_shipment(
            self.company_id, collect_result["order"].id,
            ShipmentCreate(
                order_item_ids=[item.id], idempotency_key="v7a-shipment-1",
                courier="FAKE_COURIER", invoice_number="INV-1",
            ),
            triggered_by=1,
        )
        self.assertEqual(shipment.status, ShipmentStatus.SHIPPED)

        stage = self.projection_service.project_order_item_stage(
            item.id, self.company_id,
        )
        self.assertEqual(stage, OrchestrationStage.SHIPPED)

        self.shipment_service.update_status(
            shipment.id, self.company_id, ShipmentStatus.DELIVERED,
            triggered_by=1,
        )

        stage = self.projection_service.project_order_item_stage(
            item.id, self.company_id,
        )
        self.assertEqual(stage, OrchestrationStage.SETTLEMENT_PENDING)

        summary = self.dashboard_service.get_summary(self.company_id)
        self.assertFalse(summary["estop_active"])

    def test_dashboard_summary_still_works_when_operations_coordination_capability_deactivated(
        self,
    ):
        """Audit(2026-08-21, AG-0) — Dashboard 요약은 단순 조회다.
        OPERATIONS_COORDINATION AI Capability가 비활성이어도 정상
        동작해야 한다(이전 라운드의 잘못된 게이트를 되돌린 회귀 방지
        테스트)."""

        from tests.ai_governance_test_helpers import deactivated_capability

        with deactivated_capability("OPERATIONS_COORDINATION"):
            summary = self.dashboard_service.get_summary(self.company_id)

        self.assertIn("estop_active", summary)

    # ------------------------------------------------
    # B. 재고 부족 → 소싱 → 발주 승인 → 입고 → 재예약 → 출고
    # ------------------------------------------------

    def test_scenario_b_shortage_sourcing_purchase_partial_retry(self):

        candidate, _sku = self._create_candidate_and_sku(
            "V7B-1", "CH-V7B-1", initial_qty=0,
        )

        supplier = Supplier(name="V7 통합 테스트 공급처", is_active=True)
        self.db.add(supplier)
        self.db.commit()

        collect_result = self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id="V7B-ORDER-1",
                buyer_name="김구매", receiver_name="김수취",
                receiver_phone="010-2222-2222",
                receiver_address="서울시 강남구", receiver_zipcode="06120",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-V7B-1", quantity=2,
                        unit_price=8000, product_name="V7 통합 테스트 상품",
                    ),
                ],
            ),
            triggered_by=1,
        )
        item = collect_result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.OUT_OF_STOCK)

        stage = self.projection_service.project_order_item_stage(
            item.id, self.company_id,
        )
        self.assertEqual(stage, OrchestrationStage.OUT_OF_STOCK)

        # 공급처 연결이 없는 상태에서 소싱 추천을 시도하면 명확히
        # 차단된다(임의 공급처를 지어내지 않는다).
        from app.core.exceptions import BadRequestException

        with self.assertRaises(BadRequestException):
            self.recommendation_service.create_purchase_proposal_for_order_item(
                item.id, self.company_id, triggered_by=1,
                idempotency_key="v7b-proposal-attempt-1",
            )

        self.source_service.create_link(
            SupplierProductLinkCreate(
                product_candidate_id=candidate.id,
                supplier_id=supplier.id,
                supplier_sku="SUP-SKU-V7B-1",
                unit_cost=3000.0, moq=1, lead_time_days=2,
            ),
            self.company_id, created_by=1,
        )

        purchase = (
            self.recommendation_service.create_purchase_proposal_for_order_item(
                item.id, self.company_id, triggered_by=1,
                idempotency_key="v7b-proposal-1",
            )
        )
        self.assertEqual(purchase.status, PurchaseStatus.REQUESTED)
        self.assertEqual(purchase.supplier_id, supplier.id)
        self.assertEqual(purchase.total_cost, 6000.0)  # 2개 * 3000원

        stage = self.projection_service.project_order_item_stage(
            item.id, self.company_id,
        )
        self.assertEqual(stage, OrchestrationStage.PURCHASE_APPROVAL_REQUIRED)

        # 사용자 승인(기존 PurchaseService 그대로 재사용).
        self.purchase_service.confirm_purchase(
            purchase.id, self.company_id, triggered_by=1,
        )

        stage = self.projection_service.project_order_item_stage(
            item.id, self.company_id,
        )
        self.assertEqual(stage, OrchestrationStage.SUPPLIER_CONFIRMED)

        # 승인 이후 실제 발주 전송(SupplierOrderProvider) — 공급처
        # 거래관계 승인이 선행돼야 한다.
        relation = self.source_service.create_relation(
            CompanySupplierRelationCreate(supplier_id=supplier.id),
            self.company_id, created_by=1,
        )
        self.source_service.set_relation_approval(
            relation.id, self.company_id, approve=True,
        )

        submitted = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_id, "FAKE", triggered_by=1,
        )
        self.assertEqual(submitted.submission_status, "SUBMITTED")
        self.assertIsNotNone(submitted.supplier_order_id)

        self.purchase_service.receive_purchase(
            purchase.id, self.company_id, triggered_by=1,
        )

        item_after = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertEqual(item_after.status, OrderItemStatus.RESERVED)

        stage = self.projection_service.project_order_item_stage(
            item.id, self.company_id,
        )
        self.assertEqual(stage, OrchestrationStage.FULFILLMENT_READY)

        shipment = self.shipment_service.create_shipment(
            self.company_id, collect_result["order"].id,
            ShipmentCreate(
                order_item_ids=[item.id], idempotency_key="v7b-shipment-1",
                courier="FAKE_COURIER", invoice_number="INV-2",
            ),
            triggered_by=1,
        )
        self.assertEqual(shipment.status, ShipmentStatus.SHIPPED)

        summary = self.dashboard_service.get_summary(self.company_id)
        self.assertEqual(summary["out_of_stock_items"], 0)


if __name__ == "__main__":
    unittest.main()
