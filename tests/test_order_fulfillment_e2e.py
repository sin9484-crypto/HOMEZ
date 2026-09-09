"""
=========================================================
Homez OS

File : tests/test_order_fulfillment_e2e.py

V7 Gate 4(2026-08-15) — 요구사항 8: Fake Provider로 "채널 주문 수집
→ 재고 예약 → 공급처 발주 → 입고 → 출고 → 배송 완료"까지 전체 흐름이
끝까지 동작하는 것을 증명한다. 실제 외부 API 호출은 전혀 없다(전부
Fake Provider/픽스처). 실제 homez.db는 이 테스트 전체에서 전혀
접근하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.service import FundingService
from app.domains.inventory.constants import InventoryLedgerEventType
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
from app.domains.order.constants import OrderIngestionStatus
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus
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
from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.schema import PurchaseItemCreate
from app.domains.purchase.service import PurchaseService
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.constants import ReturnOrderType
from app.domains.return_order.model import ReturnOrder
from app.domains.return_order.model import ReturnOrderStatusEvent
from app.domains.return_order.schema import ReturnOrderCreate
from app.domains.return_order.service import ReturnOrderService
from app.domains.shipment.constants import ShipmentStatus
from app.domains.shipment.model import Shipment
from app.domains.shipment.model import ShipmentItem
from app.domains.shipment.model import ShipmentStatusEvent
from app.domains.shipment.schema import ShipmentCreate
from app.domains.shipment.service import ShipmentService
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class FullOrderFulfillmentE2ETestCase(unittest.TestCase):
    """
    한 회사(무재고 중개 판매자)가 쿠팡류 채널(Fake Provider로 시뮬레이션)
    에서 주문을 받아 재고를 예약하고, 재고가 부족한 품목은 공급처에
    발주해 입고시켜 재예약하고, 두 품목을 서로 다른 송장으로 나눠
    출고(부분출고)한 뒤 배송 완료까지, 그리고 그중 하나는 고객이
    반품하는 전체 여정을 검증한다.
    """

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
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )

        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.company = Company(
            name="에브리홈즈 E2E 테스트 회사", business_number="333-33-33333",
            ceo="대표", phone="02-000-0003", email="e2e@example.com",
            address="서울",
        )
        self.db.add(self.company)
        self.db.commit()
        self.company_id = self.company.id

        self.order_service = OrderService(self.db)
        self.purchase_service = PurchaseService(self.db)
        self.shipment_service = ShipmentService(self.db)
        self.return_service = ReturnOrderService(self.db)
        self.inventory_service = InventoryService(self.db)
        self.funding_service = FundingService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_candidate_and_sku(
        self, sku_code, channel_sku, initial_qty, listing_account,
    ):

        candidate = ProductCandidate(
            candidate_key=f"test:FAKE:{sku_code}", source_type="TREND",
            source_reference=sku_code, market="FAKE",
            product_name=f"무재고 상품 {sku_code}", status="APPROVED",
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
            marketplace_account_id=listing_account.id,
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

        return sku

    def test_full_channel_to_delivery_and_return_journey(self):

        # --------------------------------------------------
        # 0) 사전 준비 — 채널 계정, SKU 2개(하나는 재고 충분, 하나는
        #    재고 0으로 시작해 공급처 발주가 실제로 필요하게 만든다).
        # --------------------------------------------------

        channel = MarketplaceChannel(
            code="FAKE", name="가짜채널", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(account)
        self.db.commit()

        sku_instock = self._create_candidate_and_sku(
            "E2E-A", "CH-E2E-A", initial_qty=5, listing_account=account,
        )
        sku_needs_purchase = self._create_candidate_and_sku(
            "E2E-B", "CH-E2E-B", initial_qty=0, listing_account=account,
        )

        self.funding_service.create_account(
            FundingAccountCreate(total_funding=100_000.0), self.company_id,
        )

        # --------------------------------------------------
        # 1) 채널 주문 수집(요구사항 1/2/3/7) — 두 품목 중 하나는
        #    즉시 재고 예약, 하나는 재고 부족(OUT_OF_STOCK).
        # --------------------------------------------------

        collect_result = self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id="E2E-CHANNEL-ORDER-1",
                buyer_name="김구매", receiver_name="김수취",
                receiver_phone="010-1234-5678",
                receiver_address="서울시 강남구 테헤란로 1",
                receiver_zipcode="06120", ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-E2E-A", quantity=2,
                        unit_price=15000, product_name="무재고 상품 E2E-A",
                    ),
                    OrderChannelItemPayload(
                        channel_sku="CH-E2E-B", quantity=3,
                        unit_price=8000, product_name="무재고 상품 E2E-B",
                    ),
                ],
            ),
            triggered_by=1,
        )

        self.assertEqual(
            collect_result["ingestion_status"], OrderIngestionStatus.COLLECTED,
        )
        order = collect_result["order"]
        self.assertEqual(order.status, OrderStatus.PENDING)  # B가 아직 부족

        item_a, item_b = collect_result["items"]
        self.assertEqual(item_a.status, OrderItemStatus.RESERVED)
        self.assertEqual(item_b.status, OrderItemStatus.OUT_OF_STOCK)

        # 중복 수집 방지(요구사항 2) — 같은 channel_order_id 재수집은
        # 무시된다.
        duplicate_result = self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id="E2E-CHANNEL-ORDER-1",
                buyer_name="김구매", receiver_name="김수취",
                receiver_phone="010-1234-5678",
                receiver_address="서울시 강남구 테헤란로 1",
                receiver_zipcode="06120", ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-E2E-A", quantity=2,
                        unit_price=15000, product_name="무재고 상품 E2E-A",
                    ),
                ],
            ),
            triggered_by=1,
        )
        self.assertEqual(
            duplicate_result["ingestion_status"],
            OrderIngestionStatus.DUPLICATE_IGNORED,
        )
        self.assertEqual(
            len(self.order_service.list_orders(self.company_id)), 1,
        )

        # --------------------------------------------------
        # 2) 공급처 발주(요구사항 4) — B 품목에 대해서만 발주하고,
        #    Funding Hold가 실제로 걸린다.
        # --------------------------------------------------

        purchase = self.purchase_service.create_purchase(
            self.company_id,
            PurchaseCreate(
                order_id=order.id, supplier_id=42,
                items=[
                    PurchaseItemCreate(
                        order_item_id=item_b.id, unit_cost=5000.0,
                    ),
                ],
                idempotency_key=f"order:{order.id}:purchase",
                supplier_order_number="SUP-PO-0001",
            ),
            triggered_by=1,
        )
        self.assertEqual(purchase.status, PurchaseStatus.REQUESTED)
        self.assertEqual(purchase.total_cost, 15000.0)  # 3개 * 5000원

        account_after_hold = (
            self.funding_service.repository.get_account_by_company(
                self.company_id,
            )
        )
        self.assertEqual(account_after_hold.held_amount, 15000.0)

        # --------------------------------------------------
        # 3) 입고 확정(요구사항 4) — restock() + 공급처 지급 확정 +
        #    OUT_OF_STOCK 품목 자동 재예약(요구사항 3↔4 연결).
        # --------------------------------------------------

        self.purchase_service.confirm_purchase(
            purchase.id, self.company_id, triggered_by=1,
        )
        received_purchase = self.purchase_service.receive_purchase(
            purchase.id, self.company_id, triggered_by=1,
        )
        self.assertEqual(received_purchase.status, PurchaseStatus.RECEIVED)

        item_b_after = self.order_service._get_item_required(
            item_b.id, self.company_id,
        )
        self.assertEqual(item_b_after.status, OrderItemStatus.RESERVED)
        self.assertIsNotNone(item_b_after.reservation_id)

        supplier_payment = (
            self.funding_service.repository.get_payment_by_purchase(
                purchase.id,
            )
        )
        self.assertIsNotNone(supplier_payment)
        self.assertEqual(supplier_payment.amount, 15000.0)

        account_after_payment = (
            self.funding_service.repository.get_account_by_company(
                self.company_id,
            )
        )
        self.assertEqual(account_after_payment.held_amount, 0.0)
        self.assertEqual(account_after_payment.total_funding, 85000.0)

        # 이제 주문 전체가 재예약 완료 상태여야 한다(요구사항 3).
        order_after_receive = self.order_service.get_order(
            order.id, self.company_id,
        )
        self.assertEqual(order_after_receive.status, OrderStatus.RESERVED)

        # --------------------------------------------------
        # 4) 출고(요구사항 3/5) — 두 품목을 각각 다른 송장으로 나눠
        #    발송한다(부분출고).
        # --------------------------------------------------

        shipment_a = self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item_a.id], courier="CJ대한통운",
                invoice_number="INV-E2E-A",
                idempotency_key=f"order:{order.id}:shipment:a",
            ),
            triggered_by=1,
        )
        self.assertEqual(shipment_a.status, ShipmentStatus.SHIPPED)

        order_after_first_ship = self.order_service.get_order(
            order.id, self.company_id,
        )
        self.assertEqual(
            order_after_first_ship.status, OrderStatus.PARTIALLY_SHIPPED,
        )

        shipment_b = self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item_b.id], courier="CJ대한통운",
                invoice_number="INV-E2E-B",
                idempotency_key=f"order:{order.id}:shipment:b",
            ),
            triggered_by=1,
        )
        self.assertEqual(shipment_b.status, ShipmentStatus.SHIPPED)

        order_after_both_ship = self.order_service.get_order(
            order.id, self.company_id,
        )
        self.assertEqual(order_after_both_ship.status, OrderStatus.SHIPPED)

        # --------------------------------------------------
        # 5) 배송중 → 배송완료(요구사항 5).
        # --------------------------------------------------

        self.shipment_service.update_status(
            shipment_a.id, self.company_id, ShipmentStatus.IN_TRANSIT,
            triggered_by=1,
        )
        self.shipment_service.update_status(
            shipment_a.id, self.company_id, ShipmentStatus.DELIVERED,
            triggered_by=1,
        )
        order_mid_delivery = self.order_service.get_order(
            order.id, self.company_id,
        )
        # 아직 shipment_b가 배송완료가 아니므로 주문은 DELIVERED가
        # 아니어야 한다.
        self.assertNotEqual(order_mid_delivery.status, OrderStatus.DELIVERED)

        self.shipment_service.update_status(
            shipment_b.id, self.company_id, ShipmentStatus.IN_TRANSIT,
            triggered_by=1,
        )
        delivered_b = self.shipment_service.update_status(
            shipment_b.id, self.company_id, ShipmentStatus.DELIVERED,
            triggered_by=1,
        )
        self.assertEqual(delivered_b.status, ShipmentStatus.DELIVERED)

        order_final = self.order_service.get_order(order.id, self.company_id)
        self.assertEqual(order_final.status, OrderStatus.DELIVERED)

        # --------------------------------------------------
        # 6) 채널 상태 동기화(요구사항 1/6, Fake Provider) — 배송
        #    완료 후에도 정상적으로 폴링 가능해야 한다.
        # --------------------------------------------------

        sync_result = self.order_service.sync_channel_status(
            order.id, self.company_id, triggered_by=1,
        )
        self.assertTrue(sync_result["success"])

        # --------------------------------------------------
        # 7) 반품(요구사항 5) — A 품목을 반품, 재고가 실제로 복원된다.
        # --------------------------------------------------

        return_order = self.return_service.create_return_order(
            self.company_id,
            ReturnOrderCreate(
                order_item_id=item_a.id, shipment_id=shipment_a.id,
                return_type=ReturnOrderType.RETURN, quantity=2,
                reason="단순변심", idempotency_key=f"order:{order.id}:return:a",
            ),
            triggered_by=1,
        )
        self.return_service.approve(
            return_order.id, self.company_id, triggered_by=1,
        )
        self.return_service.mark_received(
            return_order.id, self.company_id, triggered_by=1,
        )
        completed_return = self.return_service.complete(
            return_order.id, self.company_id, triggered_by=1,
        )
        self.assertEqual(completed_return.status, ReturnOrderStatus.COMPLETED)

        sku_a_final = self.inventory_service.get_sku(
            sku_instock.id, self.company_id,
        )
        self.assertEqual(sku_a_final.available_qty, 5)  # 전량 복원

        item_a_final = self.order_service._get_item_required(
            item_a.id, self.company_id,
        )
        self.assertEqual(item_a_final.status, OrderItemStatus.RETURNED)

        # --------------------------------------------------
        # 8) 감사 가능성(요구사항 7) — 전체 여정이 append-only 이력에
        #    남아 있어야 한다.
        # --------------------------------------------------

        status_events = self.order_service.list_status_events(
            order.id, self.company_id,
        )
        sources = [e.source for e in status_events]
        self.assertIn("INGESTION", sources)
        self.assertIn("SHIPMENT", sources)
        self.assertIn("RETURN", sources)

        ingestion_events = self.order_service.list_ingestion_events(
            order.id, self.company_id,
        )
        self.assertEqual(len(ingestion_events), 2)  # 최초수집 + 중복무시
        collected = [
            e for e in ingestion_events if e.status == "COLLECTED"
        ][0]
        self.assertIn("E2E-CHANNEL-ORDER-1", collected.raw_payload)
        self.assertIsNotNone(collected.normalized_snapshot)


if __name__ == "__main__":
    unittest.main()
