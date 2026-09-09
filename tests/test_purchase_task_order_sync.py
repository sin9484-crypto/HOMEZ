"""
=========================================================
Homez OS

File : tests/test_purchase_task_order_sync.py

Gate PT-2(2026-08-22 16차 지시) 검증 — 쿠팡 등 채널 주문 수집을
purchase_task 자동 생성에 연결하는 PurchaseTaskOrderSyncService.
임시 SQLite 파일 DB만 쓴다. 실제 채널 API를 호출하지 않는다(Fake
채널 코드만 사용).
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

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
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
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
from app.domains.purchase_task.constants import PurchaseTaskCreationSource
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
from app.domains.purchase_task.order_sync_service import (
    PurchaseTaskOrderSyncService,
)
from app.domains.purchase_task.repository import PurchaseTaskRepository
from app.domains.role.model import Role
from app.domains.user.model import User

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class OrderSyncTestCaseBase(unittest.TestCase):

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
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
                PurchaseTask.__table__,
                PurchaseTaskCandidate.__table__,
                PurchaseTaskBudgetReservation.__table__,
                PurchaseRecord.__table__,
                PurchaseTaskTrackingInfo.__table__,
                PurchaseTaskEmailPreference.__table__,
                PurchaseTaskEmailLog.__table__,
                PurchaseTaskPolicySetting.__table__,
                PurchaseTaskCsvImportLog.__table__,
                Role.__table__,
                User.__table__,
                Notification.__table__,
                NotificationRead.__table__,
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
        self.company_id = self.company.id

        self.candidate = ProductCandidate(
            candidate_key="test:FAKE:PT2-1", source_type="TREND",
            source_reference="PT2-1", market="FAKE",
            product_name="테스트 무선이어폰", brand_hint="브랜드A",
            status="APPROVED",
        )
        self.db.add(self.candidate)
        self.db.commit()

        self.order_service = OrderService(self.db)
        self.inventory_service = InventoryService(self.db)
        self.funding_service = FundingService(self.db)
        self.sync_service = PurchaseTaskOrderSyncService(self.db)
        self.pt_repository = PurchaseTaskRepository(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # ---------------- 픽스처 헬퍼(test_order_fulfillment_core.py와 동일) ----------------

    def _create_sku(self, sku_code="SKU-1", initial_qty=10, safety_stock=0):

        return self.inventory_service.create_sku(
            InventorySkuCreate(
                product_candidate_id=self.candidate.id,
                sku_code=sku_code, option_label=sku_code,
                initial_qty=initial_qty, safety_stock=safety_stock,
            ),
            self.company_id,
        )

    def _map_sku_to_channel(self, sku, channel_sku="CH-SKU-1"):

        channel = (
            self.db.query(MarketplaceChannel)
            .filter(MarketplaceChannel.code == "COUPANG")
            .first()
        )
        if channel is None:
            channel = MarketplaceChannel(
                code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
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
                marketplace_account_id=account.id, status="DRAFT",
            )
            self.db.add(listing)
            self.db.commit()

        return self.inventory_service.create_channel_mapping(
            sku.id, self.company_id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="COUPANG", channel_sku=channel_sku,
            ),
        )

    def _collect(
        self, channel_order_id="CO-1", channel_sku="CH-SKU-1",
        quantity=1, unit_price=10000.0, product_name="테스트 무선이어폰",
    ) -> dict:

        return self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="COUPANG", channel_order_id=channel_order_id,
                buyer_name="구매자", receiver_name="수취인",
                receiver_phone="010-0000-0000",
                receiver_address="서울시 어딘가 1-1", receiver_zipcode="00000",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku=channel_sku, quantity=quantity,
                        unit_price=unit_price, product_name=product_name,
                    ),
                ],
            ),
            triggered_by=1,
        )


class AutoCreationTestCase(OrderSyncTestCaseBase):

    def test_out_of_stock_item_creates_purchase_task(self):

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)

        result = self._collect(quantity=2, unit_price=15000.0)
        item = result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.OUT_OF_STOCK)

        task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        self.assertIsNotNone(task)
        self.assertEqual(task.creation_source, PurchaseTaskCreationSource.ORDER_AUTO)
        self.assertEqual(task.quantity, 2)
        self.assertEqual(task.coupang_sale_amount, 30000.0)
        self.assertEqual(task.brand, "브랜드A")
        self.assertIn(
            task.status,
            (PurchaseTaskStatus.SEARCH_REQUIRED, PurchaseTaskStatus.REVIEW_REQUIRED),
        )

    def test_in_stock_item_is_skipped_with_explicit_status(self):

        sku = self._create_sku(initial_qty=10)
        self._map_sku_to_channel(sku)

        result = self._collect(quantity=1)
        item = result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.RESERVED)

        task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        self.assertIsNotNone(task)
        self.assertEqual(
            task.status, PurchaseTaskStatus.SKIPPED_INVENTORY_AVAILABLE,
        )
        self.assertIn(task.status, PurchaseTaskStatus.TERMINAL)

    def test_re_collecting_same_order_does_not_duplicate_tasks(self):

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)

        result1 = self._collect(channel_order_id="CO-DUP")
        # 동일 channel_order_id 재수집 — DUPLICATE_IGNORED 경로.
        result2 = self._collect(channel_order_id="CO-DUP")

        self.assertEqual(result1["order"].id, result2["order"].id)

        item = result1["items"][0]
        tasks = (
            self.db.query(PurchaseTask)
            .filter(PurchaseTask.source_order_item_id == item.id)
            .all()
        )
        self.assertEqual(len(tasks), 1)

    def test_multi_item_order_creates_one_task_per_item(self):

        sku1 = self._create_sku(sku_code="SKU-A", initial_qty=0)
        self._map_sku_to_channel(sku1, channel_sku="CH-A")
        sku2 = self._create_sku(sku_code="SKU-B", initial_qty=0)
        self._map_sku_to_channel(sku2, channel_sku="CH-B")

        result = self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="COUPANG", channel_order_id="CO-MULTI",
                buyer_name="구매자", receiver_name="수취인",
                receiver_phone="010-0000-0000",
                receiver_address="서울시 어딘가 1-1", receiver_zipcode="00000",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-A", quantity=1, unit_price=10000.0,
                        product_name="상품A",
                    ),
                    OrderChannelItemPayload(
                        channel_sku="CH-B", quantity=3, unit_price=5000.0,
                        product_name="상품B",
                    ),
                ],
            ),
            triggered_by=1,
        )

        self.assertEqual(len(result["items"]), 2)
        tasks = (
            self.db.query(PurchaseTask)
            .filter(PurchaseTask.source_order_id == result["order"].id)
            .all()
        )
        self.assertEqual(len(tasks), 2)
        quantities = sorted(t.quantity for t in tasks)
        self.assertEqual(quantities, [1, 3])

    def test_missing_product_name_falls_back_to_review_required(self):
        """OrderChannelItemPayload 스키마 자체가 product_name 빈 문자열을
        차단하므로(min_length=1) 실 수집 경로로는 재현할 수 없다 — 이
        방어 코드는 sync_order_item()을 직접 호출하는 다른 호출자
        (예: 향후 다른 채널 어댑터)를 대비한 안전장치이므로, 그
        경로만 직접 호출해 검증한다."""

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)

        result = self._collect()
        order = result["order"]
        item = result["items"][0]
        item.product_name_snapshot = ""
        self.db.commit()

        # 이미 생성된 idempotency_key를 지워야 재판정된다(수동 검증
        # 목적의 직접 호출이므로 idempotency 재사용 경로를 우회한다).
        existing_task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        self.db.delete(existing_task)
        self.db.commit()

        task = self.sync_service.sync_order_item(item, order, triggered_by=1)

        self.assertEqual(task.status, PurchaseTaskStatus.REVIEW_REQUIRED)
        self.assertEqual(task.caution_reason, "PRODUCT_MAPPING_INSUFFICIENT")

    def test_reconcile_recent_orders_is_idempotent_backfill(self):
        """purchase_task 테이블이 나중에 생겨도(또는 훅이 일시적으로
        실패해도) reconcile로 같은 결과를 안전하게 재현할 수 있어야
        한다."""

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)
        self._collect(channel_order_id="CO-RECONCILE")

        before = self.db.query(PurchaseTask).count()
        result = self.sync_service.reconcile_recent_orders(self.company_id)
        after = self.db.query(PurchaseTask).count()

        self.assertEqual(before, after)
        self.assertEqual(result["orders_scanned"], 1)


class CancellationSyncTestCase(OrderSyncTestCaseBase):

    def test_cancel_before_purchase_voids_task(self):

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)
        result = self._collect(channel_order_id="CO-CANCEL-1")
        item = result["items"][0]

        task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        self.assertEqual(task.status, PurchaseTaskStatus.SEARCH_REQUIRED)

        self.order_service.cancel_order(
            result["order"].id, self.company_id, "고객 변심", triggered_by=1,
        )

        self.db.refresh(task)
        self.assertEqual(task.status, PurchaseTaskStatus.SOURCE_ORDER_CANCELLED)
        self.assertIn(task.status, PurchaseTaskStatus.TERMINAL)

    def test_cancel_while_payment_pending_marks_uncertain_not_auto_release(self):

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)
        self._fund_account()
        result = self._collect(channel_order_id="CO-CANCEL-2", unit_price=100.0)
        item = result["items"][0]

        task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        # 결제 대기 상태를 직접 구성한다(policy evaluate 전체 경로는
        # 이미 test_purchase_task_service.py가 커버 — 여기서는 취소
        # 동기화 규칙만 검증).
        task.status = PurchaseTaskStatus.USER_PAYMENT_PENDING
        self.db.commit()

        self.order_service.cancel_order(
            result["order"].id, self.company_id, "고객 변심", triggered_by=1,
        )

        self.db.refresh(task)
        self.assertEqual(task.status, PurchaseTaskStatus.UNCERTAIN)

    def test_cancel_after_purchase_flags_cancel_required(self):
        """이미 구매까지 진행된 작업은 자동으로 취소되지 않는다 —
        기존 request_cancel()의 TRACKING_REQUIRED 전이 규칙을 그대로
        재사용해 CANCEL_REQUIRED로만 표시한다."""

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)
        result = self._collect(channel_order_id="CO-CANCEL-3")
        item = result["items"][0]

        task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        task.status = PurchaseTaskStatus.TRACKING_REQUIRED
        self.db.commit()

        self.order_service.cancel_order(
            result["order"].id, self.company_id, "고객 변심", triggered_by=1,
        )

        self.db.refresh(task)
        self.assertEqual(task.status, PurchaseTaskStatus.CANCEL_REQUIRED)

    def test_already_terminal_task_is_noop_on_source_cancel(self):

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)
        result = self._collect(channel_order_id="CO-CANCEL-4")
        item = result["items"][0]

        task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        task.status = PurchaseTaskStatus.COMPLETED
        version_before = task.version
        self.db.commit()

        self.order_service.cancel_order(
            result["order"].id, self.company_id, "고객 변심", triggered_by=1,
        )

        self.db.refresh(task)
        self.assertEqual(task.status, PurchaseTaskStatus.COMPLETED)
        self.assertEqual(task.version, version_before)

    def _fund_account(self, total_funding=1_000_000.0):

        self.funding_service.create_account(
            FundingAccountCreate(total_funding=total_funding), self.company_id,
        )


if __name__ == "__main__":
    unittest.main()
