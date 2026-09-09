"""
=========================================================
Homez OS

File : tests/test_order_fulfillment_core.py

V7 Gate 4(2026-08-15) — 주문 수집/재고예약/발주·입고/배송/반품·교환
핵심 기능 검증. 임시 SQLite 파일 DB만 사용한다 — 실제 homez.db는 이
테스트 전체에서 전혀 접근하지 않는다. 실제 채널 API는 호출하지 않는다
(Fake Provider 전용).
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.exceptions import TooManyRequestsException
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
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
from app.domains.order.adapters.fake_provider import CHANNEL_CANCELLED_SUFFIX
from app.domains.order.adapters.fake_provider import TRIGGER_429
from app.domains.order.constants import OrderChannelSyncStatus
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


class OrderFulfillmentTestCaseBase(unittest.TestCase):

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
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()
        self.company_id = self.company.id

        self.candidate = ProductCandidate(
            candidate_key="test:FAKE:ORD-1", source_type="TREND",
            source_reference="ORD-1", market="FAKE",
            product_name="테스트 상품", status="APPROVED",
        )
        self.db.add(self.candidate)
        self.db.commit()

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

    # --------------------------------------------------
    # 픽스처 헬퍼
    # --------------------------------------------------

    def _create_sku(
        self, sku_code="SKU-1", initial_qty=10, safety_stock=0,
        option_label=None,
    ):

        return self.inventory_service.create_sku(
            InventorySkuCreate(
                product_candidate_id=self.candidate.id,
                sku_code=sku_code,
                option_label=option_label or sku_code,
                initial_qty=initial_qty,
                safety_stock=safety_stock,
            ),
            self.company_id,
        )

    def _create_listing(self):

        channel = (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.code == "FAKE")
            .first()
        )
        if channel is None:
            channel = MarketplaceChannel(
                code="FAKE", name="가짜채널",
                doc_verification_status="VERIFIED",
            )
            self.db.add(channel)
            self.db.commit()

        account = (
            self.db.query(MarketplaceAccount)
            .filter(MarketplaceAccount.company_id == self.company_id)
            .filter(MarketplaceAccount.channel_id == channel.id)
            .first()
        )
        if account is None:
            account = MarketplaceAccount(
                company_id=self.company_id, channel_id=channel.id,
                account_code="acc1", account_name="계정1",
            )
            self.db.add(account)
            self.db.commit()

        listing = (
            self.db.query(MarketplaceListing)
            .filter(MarketplaceListing.company_id == self.company_id)
            .filter(
                MarketplaceListing.product_candidate_id == self.candidate.id,
            )
            .filter(MarketplaceListing.marketplace_account_id == account.id)
            .first()
        )
        if listing is None:
            listing = MarketplaceListing(
                company_id=self.company_id,
                product_candidate_id=self.candidate.id,
                marketplace_account_id=account.id,
                status="DRAFT",
            )
            self.db.add(listing)
            self.db.commit()

        return listing

    def _map_sku_to_channel(self, sku, channel_sku="CH-SKU-1"):

        listing = self._create_listing()

        return self.inventory_service.create_channel_mapping(
            sku.id, self.company_id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="FAKE",
                channel_sku=channel_sku,
            ),
        )

    def _fund_account(self, total_funding=1_000_000.0):

        self.funding_service.create_account(
            FundingAccountCreate(total_funding=total_funding), self.company_id,
        )

    def _collect(
        self, channel_order_id="CH-ORDER-1", channel_sku="CH-SKU-1",
        quantity=2, unit_price=1000.0,
    ) -> dict:

        return self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="FAKE",
                channel_order_id=channel_order_id,
                buyer_name="구매자",
                receiver_name="수취인",
                receiver_phone="010-0000-0000",
                receiver_address="서울시 어딘가 1-1",
                receiver_zipcode="00000",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku=channel_sku, quantity=quantity,
                        unit_price=unit_price, product_name="테스트 상품",
                    ),
                ],
            ),
            triggered_by=1,
        )

    def _full_happy_path_to_reserved_item(
        self, initial_qty=10, quantity=2, channel_order_id="CH-ORDER-1",
    ):
        """재고 매핑 + 충분한 재고로 수집해 RESERVED 품목까지 만든다."""

        sku = self._create_sku(initial_qty=initial_qty)
        self._map_sku_to_channel(sku, channel_sku="CH-SKU-1")

        result = self._collect(
            channel_order_id=channel_order_id, channel_sku="CH-SKU-1",
            quantity=quantity,
        )

        return sku, result


# ====================================================
# 채널 주문 수집(요구사항 1/2/3/6/7)
# ====================================================

class OrderCollectionTestCase(OrderFulfillmentTestCaseBase):

    def test_collect_creates_order_and_reserves_items(self):

        sku, result = self._full_happy_path_to_reserved_item()

        self.assertEqual(
            result["ingestion_status"], OrderIngestionStatus.COLLECTED,
        )
        order = result["order"]
        self.assertEqual(order.status, OrderStatus.RESERVED)
        self.assertEqual(order.company_id, self.company_id)
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.RESERVED)
        self.assertIsNotNone(item.reservation_id)

        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 8)
        self.assertEqual(updated_sku.reserved_qty, 2)

        # 원본/정규화 스냅샷이 분리 저장돼야 한다(요구사항 7).
        events = self.order_service.list_ingestion_events(
            order.id, self.company_id,
        )
        self.assertEqual(len(events), 1)
        self.assertIn("channel_order_id", events[0].raw_payload)
        self.assertIn("order_id", events[0].normalized_snapshot)

    def test_collect_still_works_when_order_shipment_return_capability_deactivated(
        self,
    ):
        """Audit(2026-08-21, AG-0) — 채널 주문 수집은 시스템/운영자가
        직접 수행하는 핵심 업무다. ORDER_SHIPMENT_RETURN AI Capability가
        비활성이어도 정상 동작해야 한다(이전 라운드의 잘못된 게이트를
        되돌린 회귀 방지 테스트)."""

        from tests.ai_governance_test_helpers import deactivated_capability

        sku = self._create_sku(initial_qty=10)
        self._map_sku_to_channel(sku, channel_sku="CH-SKU-1")

        with deactivated_capability("ORDER_SHIPMENT_RETURN"):
            result = self._collect(channel_sku="CH-SKU-1")

        self.assertEqual(
            result["ingestion_status"], OrderIngestionStatus.COLLECTED,
        )

    def test_duplicate_channel_order_id_ignored(self):

        sku, first = self._full_happy_path_to_reserved_item()

        second = self._collect(
            channel_order_id="CH-ORDER-1", channel_sku="CH-SKU-1", quantity=2,
        )

        self.assertEqual(
            second["ingestion_status"], OrderIngestionStatus.DUPLICATE_IGNORED,
        )
        self.assertEqual(second["order"].id, first["order"].id)

        orders = self.order_service.list_orders(self.company_id)
        self.assertEqual(len(orders), 1)

        # 재고가 두 번 차감되지 않아야 한다.
        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 8)

    def test_unmapped_channel_sku_skipped_but_order_still_created(self):

        result = self._collect(
            channel_order_id="CH-ORDER-2", channel_sku="UNMAPPED-SKU",
            quantity=1,
        )

        self.assertEqual(
            result["ingestion_status"], OrderIngestionStatus.COLLECTED,
        )
        self.assertEqual(result["order"].status, OrderStatus.PENDING)
        self.assertEqual(len(result["items"]), 0)
        self.assertIn("UNMAPPED-SKU", result["unresolved_channel_skus"])

    def test_insufficient_stock_marks_item_out_of_stock_but_creates_order(self):

        sku = self._create_sku(initial_qty=1)
        self._map_sku_to_channel(sku, channel_sku="CH-SKU-1")

        result = self._collect(
            channel_order_id="CH-ORDER-3", channel_sku="CH-SKU-1", quantity=5,
        )

        self.assertEqual(
            result["ingestion_status"], OrderIngestionStatus.COLLECTED,
        )
        self.assertEqual(result["order"].status, OrderStatus.PENDING)
        item = result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.OUT_OF_STOCK)
        self.assertIsNone(item.reservation_id)

        # 재고 상태는 손상되지 않아야 한다(fail-closed).
        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 1)

    def test_emergency_stop_skips_auto_reserve_but_still_collects(self):

        sku = self._create_sku(initial_qty=10)
        self._map_sku_to_channel(sku, channel_sku="CH-SKU-1")

        SafetyService(self.db).activate_emergency_stop(
            reason="테스트 비상정지", set_by=1, is_admin=True,
        )

        result = self._collect(
            channel_order_id="CH-ORDER-4", channel_sku="CH-SKU-1", quantity=2,
        )

        self.assertEqual(
            result["ingestion_status"], OrderIngestionStatus.COLLECTED,
        )
        self.assertEqual(result["order"].status, OrderStatus.PENDING)
        item = result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.PENDING)
        self.assertIsNone(item.reservation_id)

        # 재고는 전혀 건드리지 않았어야 한다.
        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 10)


# ====================================================
# 취소(요구사항 3)
# ====================================================

class OrderCancelTestCase(OrderFulfillmentTestCaseBase):

    def test_cancel_releases_reservation_and_funding_hold(self):

        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        self._fund_account()
        self.purchase_service.create_purchase(
            self.company_id,
            PurchaseCreate(
                order_id=order.id, supplier_id=1,
                items=[
                    PurchaseItemCreate(
                        order_item_id=item.id, unit_cost=100.0,
                    ),
                ],
                idempotency_key="purchase:1",
            ),
            triggered_by=1,
        )

        hold = self.funding_service.repository.get_active_hold_by_order(
            order.id,
        )
        self.assertIsNotNone(hold)

        cancelled = self.order_service.cancel_order(
            order.id, self.company_id, reason="고객 변심", triggered_by=1,
        )
        self.assertEqual(cancelled.status, OrderStatus.CANCELLED)

        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 10)
        self.assertEqual(updated_sku.reserved_qty, 0)

        released_hold = (
            self.db.query(FundingHold).filter(FundingHold.id == hold.id).first()
        )
        self.assertEqual(released_hold.status, self.funding_service.STATUS_RELEASED)

        items = self.order_service.list_items(order.id, self.company_id)
        self.assertTrue(
            all(i.status == OrderItemStatus.CANCELLED for i in items),
        )

    def test_cancel_blocked_after_shipped(self):

        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item.id], courier="CJ대한통운",
                invoice_number="INV-1", idempotency_key="ship:1",
            ),
            triggered_by=1,
        )

        with self.assertRaises(BadRequestException):
            self.order_service.cancel_order(
                order.id, self.company_id, reason="너무 늦음", triggered_by=1,
            )


# ====================================================
# 채널 상태 동기화(요구사항 1/6)
# ====================================================

class OrderChannelSyncTestCase(OrderFulfillmentTestCaseBase):

    def test_sync_normal_marks_synced(self):

        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]

        synced = self.order_service.sync_channel_status(
            order.id, self.company_id, triggered_by=1,
        )
        self.assertTrue(synced["success"])
        self.assertEqual(
            synced["order"].channel_sync_status, OrderChannelSyncStatus.SYNCED,
        )
        self.assertFalse(synced["cancelled"])

    def test_sync_429_sets_retry_after_and_blocks_immediate_retry(self):

        sku = self._create_sku(initial_qty=10)
        self._map_sku_to_channel(sku, channel_sku="CH-SKU-1")

        result = self._collect(
            channel_order_id=f"{TRIGGER_429}-A", channel_sku="CH-SKU-1",
            quantity=1,
        )
        order = result["order"]

        synced = self.order_service.sync_channel_status(
            order.id, self.company_id, triggered_by=1,
        )
        self.assertFalse(synced["success"])
        self.assertEqual(
            synced["order"].channel_sync_status, OrderChannelSyncStatus.FAILED,
        )

        with self.assertRaises(TooManyRequestsException):
            self.order_service.sync_channel_status(
                order.id, self.company_id, triggered_by=1,
            )

    def test_channel_observed_cancellation_triggers_internal_cancel(self):

        sku, result = self._full_happy_path_to_reserved_item(
            channel_order_id=f"CH-CANCEL{CHANNEL_CANCELLED_SUFFIX}",
        )
        order = result["order"]

        synced = self.order_service.sync_channel_status(
            order.id, self.company_id, triggered_by=1,
        )
        self.assertTrue(synced["success"])
        self.assertTrue(synced["cancelled"])
        self.assertEqual(synced["order"].status, OrderStatus.CANCELLED)

        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 10)

    def test_emergency_stop_blocks_channel_sync(self):

        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]

        SafetyService(self.db).activate_emergency_stop(
            reason="테스트", set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            self.order_service.sync_channel_status(
                order.id, self.company_id, triggered_by=1,
            )


# ====================================================
# 발주/입고(요구사항 4)
# ====================================================

class PurchaseTestCase(OrderFulfillmentTestCaseBase):

    def _create_purchase(self, order, item, unit_cost=100.0, key="purchase:1"):

        return self.purchase_service.create_purchase(
            self.company_id,
            PurchaseCreate(
                order_id=order.id, supplier_id=1,
                items=[
                    PurchaseItemCreate(
                        order_item_id=item.id, unit_cost=unit_cost,
                    ),
                ],
                idempotency_key=key,
            ),
            triggered_by=1,
        )

    def test_create_purchase_holds_funds(self):

        self._fund_account(total_funding=10000.0)
        sku, result = self._full_happy_path_to_reserved_item(quantity=2)
        order = result["order"]
        item = result["items"][0]

        purchase = self._create_purchase(order, item, unit_cost=100.0)

        self.assertEqual(purchase.status, PurchaseStatus.REQUESTED)
        self.assertEqual(purchase.total_cost, 200.0)

        account = self.funding_service.repository.get_account_by_company(
            self.company_id,
        )
        self.assertEqual(account.held_amount, 200.0)

        updated_item = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertEqual(updated_item.purchase_id, purchase.id)

    def test_create_purchase_idempotent_replay(self):

        self._fund_account()
        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        first = self._create_purchase(order, item)
        second = self._create_purchase(order, item)

        self.assertEqual(first.id, second.id)

    def test_create_purchase_insufficient_funds_rolls_back(self):

        self._fund_account(total_funding=10.0)
        sku, result = self._full_happy_path_to_reserved_item(quantity=2)
        order = result["order"]
        item = result["items"][0]

        with self.assertRaises(BadRequestException):
            self._create_purchase(order, item, unit_cost=100.0)

        self.assertEqual(
            self.purchase_service.repository.list_purchases_for_order(
                order.id, self.company_id,
            ),
            [],
        )
        updated_item = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertIsNone(updated_item.purchase_id)

    def test_receive_purchase_restocks_pays_and_retries_out_of_stock_item(self):

        self._fund_account()
        sku = self._create_sku(initial_qty=1)
        self._map_sku_to_channel(sku, channel_sku="CH-SKU-1")

        result = self._collect(
            channel_order_id="CH-ORDER-OOS", channel_sku="CH-SKU-1",
            quantity=3,
        )
        order = result["order"]
        item = result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.OUT_OF_STOCK)

        purchase = self._create_purchase(order, item, unit_cost=50.0)
        self.purchase_service.confirm_purchase(
            purchase.id, self.company_id, triggered_by=1,
        )
        received = self.purchase_service.receive_purchase(
            purchase.id, self.company_id, triggered_by=1,
        )
        self.assertEqual(received.status, PurchaseStatus.RECEIVED)

        # restock() 이후 재고가 반영되고, OUT_OF_STOCK이던 품목이
        # 자동으로 RESERVED로 전환돼야 한다(요구사항 3↔4 연결 고리).
        updated_item = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertEqual(updated_item.status, OrderItemStatus.RESERVED)
        self.assertIsNotNone(updated_item.reservation_id)

        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 1)  # 4입고 - 3예약
        self.assertEqual(updated_sku.reserved_qty, 3)

        # SupplierPayment가 실제로 생성됐어야 한다(요구사항 4).
        payment = self.funding_service.repository.get_payment_by_purchase(
            purchase.id,
        )
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, 150.0)

    def test_receive_requires_confirm_first(self):

        self._fund_account()
        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        purchase = self._create_purchase(order, item)

        with self.assertRaises(BadRequestException):
            self.purchase_service.receive_purchase(
                purchase.id, self.company_id, triggered_by=1,
            )

    def test_cancel_purchase_clears_order_item_link(self):

        self._fund_account()
        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        purchase = self._create_purchase(order, item)
        self.purchase_service.cancel_purchase(
            purchase.id, self.company_id, reason="공급처 재고 없음",
            triggered_by=1,
        )

        updated_item = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertIsNone(updated_item.purchase_id)


# ====================================================
# 배송/부분출고(요구사항 3/5)
# ====================================================

class ShipmentTestCase(OrderFulfillmentTestCaseBase):

    def test_create_shipment_consumes_reservation_and_marks_shipped(self):

        sku, result = self._full_happy_path_to_reserved_item(quantity=3)
        order = result["order"]
        item = result["items"][0]

        shipment = self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item.id], courier="CJ대한통운",
                invoice_number="INV-1", idempotency_key="ship:1",
            ),
            triggered_by=1,
        )

        self.assertEqual(shipment.status, ShipmentStatus.SHIPPED)

        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.reserved_qty, 0)  # 소모 완료

        updated_item = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertEqual(updated_item.status, OrderItemStatus.SHIPPED)
        self.assertEqual(updated_item.shipped_quantity, 3)

        updated_order = self.order_service.get_order(order.id, self.company_id)
        self.assertEqual(updated_order.status, OrderStatus.SHIPPED)

    def test_partial_shipment_across_multiple_order_items(self):
        """
        한 주문에 품목이 2개면 서로 다른 Shipment로 나눠 발송할 수
        있다(요구사항 5 — 품목 단위 부분출고).
        """

        sku_a = self._create_sku(sku_code="SKU-A", initial_qty=10)
        self._map_sku_to_channel(sku_a, channel_sku="CH-SKU-A")
        sku_b = self._create_sku(sku_code="SKU-B", initial_qty=10)
        self._map_sku_to_channel(sku_b, channel_sku="CH-SKU-B")

        result = self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id="CH-MULTI",
                buyer_name="구매자", receiver_name="수취인",
                receiver_phone="010-0000-0000", receiver_address="서울",
                receiver_zipcode="00000", ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-SKU-A", quantity=1, unit_price=1000,
                        product_name="A",
                    ),
                    OrderChannelItemPayload(
                        channel_sku="CH-SKU-B", quantity=1, unit_price=2000,
                        product_name="B",
                    ),
                ],
            ),
            triggered_by=1,
        )
        order = result["order"]
        item_a, item_b = result["items"]

        shipment1 = self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item_a.id], courier="CJ",
                invoice_number="INV-A", idempotency_key="ship:a",
            ),
            triggered_by=1,
        )

        mid_order = self.order_service.get_order(order.id, self.company_id)
        self.assertEqual(mid_order.status, OrderStatus.PARTIALLY_SHIPPED)

        shipment2 = self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item_b.id], courier="CJ",
                invoice_number="INV-B", idempotency_key="ship:b",
            ),
            triggered_by=1,
        )

        final_order = self.order_service.get_order(order.id, self.company_id)
        self.assertEqual(final_order.status, OrderStatus.SHIPPED)
        self.assertNotEqual(shipment1.id, shipment2.id)

    def test_status_transitions_to_delivered_updates_order(self):

        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        shipment = self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item.id], courier="CJ",
                invoice_number="INV-1", idempotency_key="ship:1",
            ),
            triggered_by=1,
        )

        self.shipment_service.update_status(
            shipment.id, self.company_id, ShipmentStatus.IN_TRANSIT,
            triggered_by=1,
        )
        delivered = self.shipment_service.update_status(
            shipment.id, self.company_id, ShipmentStatus.DELIVERED,
            triggered_by=1,
        )
        self.assertEqual(delivered.status, ShipmentStatus.DELIVERED)

        final_order = self.order_service.get_order(order.id, self.company_id)
        self.assertEqual(final_order.status, OrderStatus.DELIVERED)

    def test_invalid_status_transition_blocked(self):

        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        shipment = self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item.id], courier="CJ",
                invoice_number="INV-1", idempotency_key="ship:1",
            ),
            triggered_by=1,
        )

        with self.assertRaises(BadRequestException):
            self.shipment_service.update_status(
                shipment.id, self.company_id, ShipmentStatus.READY,
                triggered_by=1,
            )


# ====================================================
# 반품/교환(요구사항 5)
# ====================================================

class ReturnOrderTestCase(OrderFulfillmentTestCaseBase):

    def _ship(self, order, item, key="ship:1"):

        return self.shipment_service.create_shipment(
            self.company_id, order.id,
            ShipmentCreate(
                order_item_ids=[item.id], courier="CJ",
                invoice_number=f"INV-{key}", idempotency_key=key,
            ),
            triggered_by=1,
        )

    def test_return_flow_restocks_inventory(self):

        sku, result = self._full_happy_path_to_reserved_item(quantity=2)
        order = result["order"]
        item = result["items"][0]

        shipment = self._ship(order, item)

        return_order = self.return_service.create_return_order(
            self.company_id,
            ReturnOrderCreate(
                order_item_id=item.id, shipment_id=shipment.id,
                return_type=ReturnOrderType.RETURN, quantity=2,
                reason="단순변심", idempotency_key="ret:1",
            ),
            triggered_by=1,
        )
        self.assertEqual(return_order.status, ReturnOrderStatus.REQUESTED)

        self.return_service.approve(
            return_order.id, self.company_id, triggered_by=1,
        )
        self.return_service.mark_received(
            return_order.id, self.company_id, triggered_by=1,
        )
        completed = self.return_service.complete(
            return_order.id, self.company_id, triggered_by=1,
        )
        self.assertEqual(completed.status, ReturnOrderStatus.COMPLETED)

        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 10)  # 반품 입고로 복원

        updated_item = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertEqual(updated_item.status, OrderItemStatus.RETURNED)
        self.assertEqual(updated_item.returned_quantity, 2)

        final_order = self.order_service.get_order(order.id, self.company_id)
        self.assertEqual(final_order.status, OrderStatus.RETURNED)

    def test_exchange_flow_restocks_and_re_reserves(self):

        sku, result = self._full_happy_path_to_reserved_item(quantity=2)
        order = result["order"]
        item = result["items"][0]

        shipment = self._ship(order, item)
        original_reservation_id = item.reservation_id

        return_order = self.return_service.create_return_order(
            self.company_id,
            ReturnOrderCreate(
                order_item_id=item.id, shipment_id=shipment.id,
                return_type=ReturnOrderType.EXCHANGE, quantity=2,
                reason="사이즈 교환", idempotency_key="ret:2",
            ),
            triggered_by=1,
        )

        self.return_service.approve(
            return_order.id, self.company_id, triggered_by=1,
        )
        self.return_service.mark_received(
            return_order.id, self.company_id, triggered_by=1,
        )
        self.return_service.complete(
            return_order.id, self.company_id, triggered_by=1,
        )

        updated_item = self.order_service._get_item_required(
            item.id, self.company_id,
        )
        self.assertEqual(updated_item.status, OrderItemStatus.RESERVED)
        self.assertNotEqual(updated_item.reservation_id, original_reservation_id)
        self.assertEqual(updated_item.shipped_quantity, 0)

        updated_sku = self.inventory_service.get_sku(sku.id, self.company_id)
        self.assertEqual(updated_sku.available_qty, 8)  # 복원 10 - 재예약 2
        self.assertEqual(updated_sku.reserved_qty, 2)

    def test_reject_flow(self):

        sku, result = self._full_happy_path_to_reserved_item()
        order = result["order"]
        item = result["items"][0]

        shipment = self._ship(order, item)

        return_order = self.return_service.create_return_order(
            self.company_id,
            ReturnOrderCreate(
                order_item_id=item.id, shipment_id=shipment.id,
                return_type=ReturnOrderType.RETURN, quantity=2,
                reason="테스트", idempotency_key="ret:3",
            ),
            triggered_by=1,
        )

        rejected = self.return_service.reject(
            return_order.id, self.company_id, reason="증빙 부족",
            triggered_by=1,
        )
        self.assertEqual(rejected.status, ReturnOrderStatus.REJECTED)

    def test_quantity_mismatch_rejected(self):

        sku, result = self._full_happy_path_to_reserved_item(quantity=2)
        order = result["order"]
        item = result["items"][0]

        shipment = self._ship(order, item)

        with self.assertRaises(BadRequestException):
            self.return_service.create_return_order(
                self.company_id,
                ReturnOrderCreate(
                    order_item_id=item.id, shipment_id=shipment.id,
                    return_type=ReturnOrderType.RETURN, quantity=1,
                    reason="부분반품시도", idempotency_key="ret:4",
                ),
                triggered_by=1,
            )


if __name__ == "__main__":
    unittest.main()
