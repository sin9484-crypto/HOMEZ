"""
=========================================================
Homez OS

File : tests/test_order_fulfillment_concurrency.py

V7 Gate 4(2026-08-15) — 주문 수집/발주/배송 멱등성의 동시성 검증.
실스레드 + 스레드별 독립 DB 연결/Session으로 경쟁시킨다
(tests/test_inventory_concurrency.py와 동일 패턴 — 워커 스레드가 메인
스레드 Session을 절대 공유하지 않는다, V7 Gate 1에서 발견된 크로스
스레드 Session 결함과 같은 실수를 반복하지 않는다).
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy import event
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
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.model import OrderStatusEvent
from app.domains.order.schema import OrderChannelCollectRequest
from app.domains.order.schema import OrderChannelItemPayload
from app.domains.order.service import OrderService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.purchase.model import Purchase
from app.domains.purchase.model import PurchaseItem
from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.schema import PurchaseItemCreate
from app.domains.purchase.service import PurchaseService
from app.domains.shipment.model import Shipment
from app.domains.shipment.model import ShipmentItem
from app.domains.shipment.model import ShipmentStatusEvent
from app.domains.shipment.schema import ShipmentCreate
from app.domains.shipment.service import ShipmentService
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class OrderFulfillmentConcurrencyTestCase(unittest.TestCase):

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

        seed_db = self.SessionLocal()
        try:
            company = Company(
                name="동시성 테스트 회사", business_number="999-99-99998",
                ceo="테스트", phone="02-000-0000",
                email="concurrency-order@example.com", address="서울",
            )
            seed_db.add(company)
            seed_db.commit()
            self.company_id = company.id

            candidate = ProductCandidate(
                candidate_key="test:FAKE:ORD-RACE-1", source_type="TREND",
                source_reference="ORD-RACE-1", market="FAKE",
                product_name="경쟁 테스트 상품", status="APPROVED",
            )
            seed_db.add(candidate)
            seed_db.commit()
            self.candidate_id = candidate.id

            inventory_service = InventoryService(seed_db)
            sku = inventory_service.create_sku(
                InventorySkuCreate(
                    product_candidate_id=self.candidate_id,
                    sku_code="RACE-SKU", option_label="기본",
                    initial_qty=100, safety_stock=0,
                ),
                self.company_id,
            )
            self.sku_id = sku.id

            channel = MarketplaceChannel(
                code="FAKE", name="가짜채널",
                doc_verification_status="VERIFIED",
            )
            seed_db.add(channel)
            seed_db.commit()

            account = MarketplaceAccount(
                company_id=self.company_id, channel_id=channel.id,
                account_code="acc1", account_name="계정1",
            )
            seed_db.add(account)
            seed_db.commit()

            listing = MarketplaceListing(
                company_id=self.company_id,
                product_candidate_id=self.candidate_id,
                marketplace_account_id=account.id,
                status="DRAFT",
            )
            seed_db.add(listing)
            seed_db.commit()
            self.listing_id = listing.id

            inventory_service.create_channel_mapping(
                self.sku_id, self.company_id,
                InventoryChannelMappingCreate(
                    marketplace_listing_id=self.listing_id,
                    channel_code="FAKE", channel_sku="CH-SKU-1",
                ),
            )

            funding_service = FundingService(seed_db)
            funding_service.create_account(
                FundingAccountCreate(total_funding=1_000_000.0),
                self.company_id,
            )
        finally:
            seed_db.close()

    def tearDown(self):

        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _threaded_engine(self):

        engine2 = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine2, "connect")
        def _set_busy_timeout(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        return engine2, sessionmaker(
            autocommit=False, autoflush=False, bind=engine2,
        )

    # --------------------------------------------------
    # 요구사항 2 — 동일 채널 주문 동시 중복 수집 방지
    # --------------------------------------------------

    def test_concurrent_duplicate_collection_creates_exactly_one_order(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = OrderService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    result = service.collect_channel_order(
                        self.company_id,
                        OrderChannelCollectRequest(
                            channel_code="FAKE",
                            channel_order_id="RACE-ORDER-1",
                            buyer_name="구매자", receiver_name="수취인",
                            receiver_phone="010-0000-0000",
                            receiver_address="서울", receiver_zipcode="00000",
                            ordered_at=datetime.utcnow(),
                            items=[
                                OrderChannelItemPayload(
                                    channel_sku="CH-SKU-1", quantity=1,
                                    unit_price=1000, product_name="상품",
                                ),
                            ],
                        ),
                        triggered_by=1,
                    )
                    results[name] = (
                        "ok", result["ingestion_status"], result["order"].id,
                    )
                except Exception as e:  # noqa: BLE001
                    results[name] = ("error", type(e).__name__, None)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())
            self.assertEqual(len(outcomes), 2)
            self.assertTrue(all(o[0] == "ok" for o in outcomes))

            order_ids = {o[2] for o in outcomes}
            self.assertEqual(len(order_ids), 1)  # 정확히 하나의 Order

            verify_db = SessionLocal2()
            try:
                order_count = (
                    verify_db.query(Order)
                    .filter(Order.company_id == self.company_id)
                    .filter(Order.channel_order_id == "RACE-ORDER-1")
                    .count()
                )
                self.assertEqual(order_count, 1)

                # 재고는 정확히 1회만 차감됐어야 한다.
                sku = (
                    verify_db.query(InventorySku)
                    .filter(InventorySku.id == self.sku_id)
                    .first()
                )
                self.assertEqual(sku.available_qty, 99)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 요구사항 4 — 동일 idempotency_key 발주 동시 요청
    # --------------------------------------------------

    def test_concurrent_purchase_creation_same_idempotency_key(self):

        seed_db = self.SessionLocal()
        try:
            order_service = OrderService(seed_db)
            result = order_service.collect_channel_order(
                self.company_id,
                OrderChannelCollectRequest(
                    channel_code="FAKE", channel_order_id="PO-RACE-1",
                    buyer_name="구매자", receiver_name="수취인",
                    receiver_phone="010-0000-0000", receiver_address="서울",
                    receiver_zipcode="00000", ordered_at=datetime.utcnow(),
                    items=[
                        OrderChannelItemPayload(
                            channel_sku="CH-SKU-1", quantity=2,
                            unit_price=1000, product_name="상품",
                        ),
                    ],
                ),
                triggered_by=1,
            )
            order_id = result["order"].id
            item_id = result["items"][0].id
        finally:
            seed_db.close()

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = PurchaseService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    purchase = service.create_purchase(
                        self.company_id,
                        PurchaseCreate(
                            order_id=order_id, supplier_id=1,
                            items=[
                                PurchaseItemCreate(
                                    order_item_id=item_id, unit_cost=100.0,
                                ),
                            ],
                            idempotency_key="race-purchase-key",
                        ),
                        triggered_by=1,
                    )
                    results[name] = ("ok", purchase.id)
                except Exception as e:  # noqa: BLE001
                    results[name] = ("error", type(e).__name__)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())
            self.assertTrue(all(o[0] == "ok" for o in outcomes))
            purchase_ids = {o[1] for o in outcomes}
            self.assertEqual(len(purchase_ids), 1)  # 정확히 하나의 Purchase

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(Purchase)
                    .filter(Purchase.company_id == self.company_id)
                    .filter(Purchase.idempotency_key == "race-purchase-key")
                    .count()
                )
                self.assertEqual(count, 1)

                # Funding Hold도 정확히 1회만 걸렸어야 한다(200원, 1건).
                account = (
                    verify_db.query(FundingAccount)
                    .filter(FundingAccount.company_id == self.company_id)
                    .first()
                )
                self.assertEqual(account.held_amount, 200.0)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 요구사항 5 — 동일 idempotency_key 송장 동시 생성
    # --------------------------------------------------

    def test_concurrent_shipment_creation_same_idempotency_key(self):

        seed_db = self.SessionLocal()
        try:
            order_service = OrderService(seed_db)
            result = order_service.collect_channel_order(
                self.company_id,
                OrderChannelCollectRequest(
                    channel_code="FAKE", channel_order_id="SHIP-RACE-1",
                    buyer_name="구매자", receiver_name="수취인",
                    receiver_phone="010-0000-0000", receiver_address="서울",
                    receiver_zipcode="00000", ordered_at=datetime.utcnow(),
                    items=[
                        OrderChannelItemPayload(
                            channel_sku="CH-SKU-1", quantity=3,
                            unit_price=1000, product_name="상품",
                        ),
                    ],
                ),
                triggered_by=1,
            )
            order_id = result["order"].id
            item_id = result["items"][0].id
        finally:
            seed_db.close()

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = ShipmentService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    shipment = service.create_shipment(
                        self.company_id, order_id,
                        ShipmentCreate(
                            order_item_ids=[item_id], courier="CJ",
                            invoice_number="INV-RACE-1",
                            idempotency_key="race-shipment-key",
                        ),
                        triggered_by=1,
                    )
                    results[name] = ("ok", shipment.id)
                except Exception as e:  # noqa: BLE001
                    results[name] = ("error", type(e).__name__)
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())

            # 한쪽은 성공(consume), 다른 쪽은 idempotent 재생 성공 또는
            # "이미 SHIPPED 상태" 오류 중 하나여야 한다 — 어느 쪽이든
            # 예약이 두 번 소모되면 안 된다.
            successes = [o for o in outcomes if o[0] == "ok"]
            self.assertGreaterEqual(len(successes), 1)

            verify_db = SessionLocal2()
            try:
                shipment_count = (
                    verify_db.query(Shipment)
                    .filter(Shipment.company_id == self.company_id)
                    .filter(Shipment.idempotency_key == "race-shipment-key")
                    .count()
                )
                self.assertEqual(shipment_count, 1)

                sku = (
                    verify_db.query(InventorySku)
                    .filter(InventorySku.id == self.sku_id)
                    .first()
                )
                self.assertGreaterEqual(sku.reserved_qty, 0)
                self.assertEqual(sku.reserved_qty, 0)  # 정확히 1회만 소모
            finally:
                verify_db.close()
        finally:
            engine2.dispose()


if __name__ == "__main__":
    unittest.main()
