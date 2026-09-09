"""
=========================================================
Homez OS

File : tests/test_purchase_task_order_item_linkage.py

2026-09-07 V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_LEDGER_AUDIT_
20260907.md §2/§8, 사용자 확정 "purchase_task 중심") 검증 —
PurchaseTaskService.record_tracking()이 실제 courier·tracking_number
확보 시점에 (1) OrderItem.purchase_task_id를 채우고, (2) 개별
조달용 가짜 예약(InventoryService.reserve_externally_procured())으로
공용 재고를 건드리지 않으면서 OrderItem을 RESERVED로 전이시키고,
(3) 기존 shipment 도메인의 실제 create_shipment()를 호출해 OrderItem을
SHIPPED로 마무리하는지 검증한다. 임시 SQLite 파일 DB만 쓴다 — 실제
homez.db·쇼핑몰·배송사 API를 전혀 접촉하지 않는다.
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
from app.domains.inventory.constants import InventoryReservationReferenceType
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
from app.domains.order.repository import OrderRepository
from app.domains.order.schema import OrderChannelCollectRequest
from app.domains.order.schema import OrderChannelItemPayload
from app.domains.order.service import OrderService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
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
from app.domains.purchase_task.service import PurchaseTaskService
from app.domains.role.model import Role
from app.domains.shipment.model import Shipment
from app.domains.shipment.model import ShipmentItem
from app.domains.shipment.model import ShipmentStatusEvent
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


class PurchaseTaskOrderItemLinkageTestCase(unittest.TestCase):

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
                Shipment.__table__,
                ShipmentItem.__table__,
                ShipmentStatusEvent.__table__,
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
            candidate_key="test:FAKE:LINK-1", source_type="TREND",
            source_reference="LINK-1", market="FAKE",
            product_name="테스트 무선청소기", brand_hint="브랜드A",
            status="APPROVED",
        )
        self.db.add(self.candidate)
        self.db.commit()

        self.order_service = OrderService(self.db)
        self.inventory_service = InventoryService(self.db)
        self.sync_service = PurchaseTaskOrderSyncService(self.db)
        self.pt_repository = PurchaseTaskRepository(self.db)
        self.order_repository = OrderRepository(self.db)
        self.service = PurchaseTaskService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_sku(self, sku_code="SKU-1", initial_qty=0):

        return self.inventory_service.create_sku(
            InventorySkuCreate(
                product_candidate_id=self.candidate.id,
                sku_code=sku_code, option_label=sku_code,
                initial_qty=initial_qty, safety_stock=0,
            ),
            self.company_id,
        )

    def _map_sku_to_channel(self, sku, channel_sku="CH-SKU-1"):

        channel = MarketplaceChannel(
            code="COUPANG", name="쿠팡", doc_verification_status="VERIFIED",
        )
        self.db.add(channel)
        self.db.commit()

        account = MarketplaceAccount(
            company_id=self.company_id, channel_id=channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(account)
        self.db.commit()

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

    def _collect_out_of_stock_order(self):
        """0재고 SKU로 채널 주문을 수집 — OUT_OF_STOCK OrderItem과
        그것을 참조하는 PurchaseTask가 자동 생성된다(order_sync_
        service의 기존 실제 동작, tests/test_purchase_task_order_
        sync.py::test_out_of_stock_item_creates_purchase_task와
        동일 패턴)."""

        sku = self._create_sku(initial_qty=0)
        self._map_sku_to_channel(sku)

        result = self.order_service.collect_channel_order(
            self.company_id,
            OrderChannelCollectRequest(
                channel_code="COUPANG", channel_order_id="CO-LINK-1",
                buyer_name="구매자", receiver_name="수취인",
                receiver_phone="010-0000-0000",
                receiver_address="서울시 어딘가 1-1", receiver_zipcode="00000",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-SKU-1", quantity=2,
                        unit_price=15000.0, product_name="테스트 무선청소기",
                    ),
                ],
            ),
            triggered_by=1,
        )
        item = result["items"][0]
        self.assertEqual(item.status, OrderItemStatus.OUT_OF_STOCK)

        task = self.pt_repository.get_task_by_source_order_item(
            self.company_id, item.id,
        )
        self.assertIsNotNone(task)

        return sku, item, task

    def _force_tracking_required(self, task):
        """이 테스트가 검증하려는 지점은 record_tracking() 그 자체이지
        match-check/evaluate/payment 등 앞선 워크플로 단계가 아니다 —
        그 단계들은 tests/test_purchase_task_service.py가 이미
        검증한다. 여기서는 도달 가능한 유효 상태(TRACKING_REQUIRED)로
        직접 전이시켜 record_tracking()만 격리해서 검증한다."""

        task.status = PurchaseTaskStatus.TRACKING_REQUIRED
        self.db.commit()

    def test_record_tracking_links_order_item_and_creates_real_shipment(self):

        sku, item, task = self._collect_out_of_stock_order()
        self._force_tracking_required(task)

        result_task = self.service.record_tracking(
            task.id, self.company_id, courier="CJ대한통운",
            courier_confirmed=True, tracking_number="TRACK-1",
            shipped_at=datetime.utcnow(), expected_arrival_at=None,
            is_partial_shipment=False, recorded_by=1,
        )
        self.assertEqual(result_task.status, PurchaseTaskStatus.SHIPPED)

        self.db.refresh(item)
        self.assertEqual(item.purchase_task_id, task.id)
        self.assertEqual(item.status, OrderItemStatus.SHIPPED)
        self.assertIsNotNone(item.reservation_id)

        reservation = (
            self.db.query(InventoryReservation)
            .filter(InventoryReservation.id == item.reservation_id)
            .one()
        )
        self.assertEqual(
            reservation.reference_type,
            InventoryReservationReferenceType.PURCHASE_TASK_EXTERNAL_PROCUREMENT,
        )
        self.assertEqual(reservation.reference_id, task.id)

        # 개별 조달 예약이므로 공용 재고는 여전히 0이어야 한다.
        self.db.refresh(sku)
        self.assertEqual(sku.available_qty, 0)
        self.assertEqual(sku.reserved_qty, 0)

        shipments = (
            self.db.query(Shipment)
            .filter(Shipment.order_id == item.order_id)
            .all()
        )
        self.assertEqual(len(shipments), 1)
        self.assertEqual(shipments[0].courier, "CJ대한통운")

        shipment_items = (
            self.db.query(ShipmentItem)
            .filter(ShipmentItem.order_item_id == item.id)
            .all()
        )
        self.assertEqual(len(shipment_items), 1)

    def test_record_tracking_without_tracking_number_skips_linkage(self):
        """courier/tracking_number가 없으면(미확인 상태) write-back을
        건너뛴다 — 실제 발송 확증이 없는데 OrderItem을 미리 SHIPPED로
        만들면 안 된다."""

        sku, item, task = self._collect_out_of_stock_order()
        self._force_tracking_required(task)

        self.service.record_tracking(
            task.id, self.company_id, courier=None,
            courier_confirmed=False, tracking_number=None,
            shipped_at=None, expected_arrival_at=None,
            is_partial_shipment=False, recorded_by=1,
        )

        self.db.refresh(item)
        self.assertIsNone(item.purchase_task_id)
        self.assertEqual(item.status, OrderItemStatus.OUT_OF_STOCK)

    def test_record_tracking_is_idempotent_via_status_guard(self):
        """record_tracking()은 TRACKING_REQUIRED 상태에서만 허용되고
        성공 즉시 SHIPPED로 전이하므로, 재호출은 상태 가드에서
        자연스럽게 막혀 write-back이 두 번 일어날 수 없다."""

        from app.core.exceptions import BadRequestException

        sku, item, task = self._collect_out_of_stock_order()
        self._force_tracking_required(task)

        self.service.record_tracking(
            task.id, self.company_id, courier="CJ대한통운",
            courier_confirmed=True, tracking_number="TRACK-1",
            shipped_at=datetime.utcnow(), expected_arrival_at=None,
            is_partial_shipment=False, recorded_by=1,
        )

        with self.assertRaises(BadRequestException):
            self.service.record_tracking(
                task.id, self.company_id, courier="CJ대한통운",
                courier_confirmed=True, tracking_number="TRACK-1",
                shipped_at=datetime.utcnow(), expected_arrival_at=None,
                is_partial_shipment=False, recorded_by=1,
            )

    def test_record_tracking_skips_linkage_when_no_source_order_item(self):
        """source_order_item_id가 없는 작업(직접 등록 등)은 연결할
        판매 주문이 없으므로 write-back을 조용히 건너뛴다."""

        task = PurchaseTask(
            company_id=self.company_id, source_order_id=999,
            source_order_item_id=None, product_title="수동 등록 상품",
            quantity=1, idempotency_key="manual-1",
            status=PurchaseTaskStatus.TRACKING_REQUIRED,
        )
        self.db.add(task)
        self.db.commit()

        result_task = self.service.record_tracking(
            task.id, self.company_id, courier="CJ대한통운",
            courier_confirmed=True, tracking_number="TRACK-2",
            shipped_at=datetime.utcnow(), expected_arrival_at=None,
            is_partial_shipment=False, recorded_by=1,
        )
        self.assertEqual(result_task.status, PurchaseTaskStatus.SHIPPED)


class DuplicateTaskGuardTestCase(PurchaseTaskOrderItemLinkageTestCase):
    """2026-09-07 V7 통합 매입 감사 후속(§3/§8) — 같은
    source_order_item_id에 대해 서로 다른 idempotency_key로 두 번
    작업을 만들 수 없어야 한다(감사가 지적한 정확히 그 결함)."""

    def test_second_task_for_same_item_with_different_key_is_blocked(self):

        from app.core.exceptions import BadRequestException

        _sku, item, _task = self._collect_out_of_stock_order()

        with self.assertRaises(BadRequestException):
            self.service.create_task(
                self.company_id, source_order_id=item.order_id,
                source_order_item_id=item.id, product_title="같은 품목 재시도",
                brand=None, manufacturer=None, model_name=None, gtin=None,
                capacity=None, quantity=1, color_or_scent=None,
                idempotency_key="manual-different-key",
                created_by=1,
            )

    def test_new_task_allowed_after_prior_task_failed(self):

        _sku, item, task = self._collect_out_of_stock_order()

        task.status = PurchaseTaskStatus.FAILED
        self.db.commit()

        new_task = self.service.create_task(
            self.company_id, source_order_id=item.order_id,
            source_order_item_id=item.id, product_title="재시도 작업",
            brand=None, manufacturer=None, model_name=None, gtin=None,
            capacity=None, quantity=1, color_or_scent=None,
            idempotency_key="manual-retry-key",
            created_by=1,
        )

        self.assertNotEqual(new_task.id, task.id)

    def test_new_task_blocked_after_prior_task_completed(self):

        from app.core.exceptions import BadRequestException

        _sku, item, task = self._collect_out_of_stock_order()

        task.status = PurchaseTaskStatus.COMPLETED
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.service.create_task(
                self.company_id, source_order_id=item.order_id,
                source_order_item_id=item.id, product_title="이미 완료된 품목",
                brand=None, manufacturer=None, model_name=None, gtin=None,
                capacity=None, quantity=1, color_or_scent=None,
                idempotency_key="manual-after-completed",
                created_by=1,
            )


if __name__ == "__main__":
    unittest.main()
