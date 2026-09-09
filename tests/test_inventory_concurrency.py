"""
=========================================================
Homez OS

File : tests/test_inventory_concurrency.py

V7 Gate 3(2026-08-15) — Inventory 원자적 증감의 동시성 검증. 실스레드 +
스레드별 독립 DB 연결/Session으로 경쟁시킨다(tests/
test_marketplace_listing_concurrency.py와 동일 패턴 — 워커 스레드가
메인 스레드 Session을 절대 공유하지 않는다, V7 Gate 1에서 발견된
크로스스레드 Session 결함과 같은 실수를 반복하지 않는다).
=========================================================
"""

import os
import tempfile
import threading
import unittest

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.brand.model import Brand  # noqa: F401 (mapper 해석용)
from app.domains.category.model import Category  # noqa: F401 (mapper 해석용)
from app.domains.company.model import Company
from app.domains.inventory.constants import InventoryReservationStatus
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku
from app.domains.inventory.schema import InventoryAdjustRequest
from app.domains.inventory.schema import InventoryReserveRequest
from app.domains.inventory.schema import InventorySkuCreate
from app.domains.inventory.service import InventoryService
from app.domains.product.model import Product  # noqa: F401 (mapper 해석용)
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.supplier.model import Supplier  # noqa: F401 (mapper 해석용)
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


class InventoryConcurrencyTestCase(unittest.TestCase):

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
                InventorySku.__table__,
                InventoryReservation.__table__,
                InventoryLedgerEvent.__table__,
                InventoryChannelMapping.__table__,
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
                name="동시성 테스트 회사", business_number="999-99-99999",
                ceo="테스트", phone="02-000-0000",
                email="concurrency-inv@example.com", address="서울",
            )
            seed_db.add(company)
            seed_db.commit()
            self.company_id = company.id

            candidate = ProductCandidate(
                candidate_key="test:COUPANG:INV-RACE-1", source_type="TREND",
                source_reference="INV-RACE-1", market="COUPANG",
                product_name="경쟁 테스트 상품", status="APPROVED",
            )
            seed_db.add(candidate)
            seed_db.commit()
            self.candidate_id = candidate.id

            service = InventoryService(seed_db)
            sku = service.create_sku(
                InventorySkuCreate(
                    product_candidate_id=self.candidate_id,
                    sku_code="RACE-SKU", option_label="기본",
                    initial_qty=10, safety_stock=0,
                ),
                self.company_id,
            )
            self.sku_id = sku.id
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

    def test_concurrent_reserve_exceeding_stock_exactly_one_succeeds(self):
        """
        가용재고 10에 대해 두 스레드가 각각 서로 다른 idempotency_key로
        7개씩(합 14, 재고 초과) 동시 예약을 시도한다 — 정확히 하나만
        성공해야 하고, 최종 available_qty는 절대 음수가 되면 안 된다.
        """

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name, idempotency_key):

                thread_db = SessionLocal2()
                service = InventoryService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    reservation = service.reserve(
                        self.sku_id, self.company_id,
                        InventoryReserveRequest(
                            quantity=7, idempotency_key=idempotency_key,
                        ),
                        triggered_by=1,
                    )
                    results[name] = ("ok", reservation.id)
                except Exception as e:  # noqa: BLE001
                    results[name] = ("error", type(e).__name__)
                finally:
                    thread_db.close()

            t1 = threading.Thread(
                target=worker, args=("thread-a", "race-key-a"),
            )
            t2 = threading.Thread(
                target=worker, args=("thread-b", "race-key-b"),
            )
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            outcomes = list(results.values())
            successes = [o for o in outcomes if o[0] == "ok"]
            failures = [o for o in outcomes if o[0] == "error"]

            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0][1], "BadRequestException")

            verify_db = SessionLocal2()
            try:
                sku = (
                    verify_db.query(InventorySku)
                    .filter(InventorySku.id == self.sku_id)
                    .first()
                )
                self.assertGreaterEqual(sku.available_qty, 0)
                self.assertEqual(sku.available_qty, 3)  # 10 - 7 = 3
                self.assertEqual(sku.reserved_qty, 7)

                reservation_count = (
                    verify_db.query(InventoryReservation)
                    .filter(
                        InventoryReservation.inventory_sku_id == self.sku_id,
                    )
                    .count()
                )
                self.assertEqual(reservation_count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    def test_concurrent_reserve_same_idempotency_key_creates_one_reservation(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = InventoryService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    reservation = service.reserve(
                        self.sku_id, self.company_id,
                        InventoryReserveRequest(
                            quantity=4, idempotency_key="shared-key",
                        ),
                        triggered_by=1,
                    )
                    results[name] = reservation.id
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            reservation_ids = list(results.values())
            self.assertEqual(len(set(reservation_ids)), 1)

            verify_db = SessionLocal2()
            try:
                count = (
                    verify_db.query(InventoryReservation)
                    .filter(
                        InventoryReservation.idempotency_key == "shared-key",
                    )
                    .count()
                )
                self.assertEqual(count, 1)

                # 딱 한 번만 차감됐어야 한다(중복 예약 방지, 요구사항 6).
                sku = (
                    verify_db.query(InventorySku)
                    .filter(InventorySku.id == self.sku_id)
                    .first()
                )
                self.assertEqual(sku.available_qty, 6)  # 10 - 4
                self.assertEqual(sku.reserved_qty, 4)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    def test_concurrent_release_of_same_reservation_exactly_one_succeeds(self):

        setup_db = self.SessionLocal()
        try:
            service = InventoryService(setup_db)
            reservation = service.reserve(
                self.sku_id, self.company_id,
                InventoryReserveRequest(
                    quantity=5, idempotency_key="release-race",
                ),
                triggered_by=1,
            )
            reservation_id = reservation.id
        finally:
            setup_db.close()

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = InventoryService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    released = service.release(
                        reservation_id, self.company_id, triggered_by=1,
                    )
                    results[name] = ("ok", released.status)
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
            successes = [o for o in outcomes if o[0] == "ok"]
            failures = [o for o in outcomes if o[0] == "error"]

            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0][1], "BadRequestException")

            verify_db = SessionLocal2()
            try:
                sku = (
                    verify_db.query(InventorySku)
                    .filter(InventorySku.id == self.sku_id)
                    .first()
                )
                # 이중 해제가 발생하면 available_qty가 두 번 늘어나
                # 15가 되었을 것이다 — 정확히 한 번만 반영돼야 한다.
                self.assertEqual(sku.available_qty, 10)
                self.assertEqual(sku.reserved_qty, 0)

                reservation = (
                    verify_db.query(InventoryReservation)
                    .filter(InventoryReservation.id == reservation_id)
                    .first()
                )
                self.assertEqual(
                    reservation.status, InventoryReservationStatus.RELEASED,
                )
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    def test_concurrent_adjust_same_idempotency_key_applies_once(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = InventoryService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    ledger = service.adjust_stock(
                        self.sku_id, self.company_id,
                        InventoryAdjustRequest(
                            quantity_delta=-3, reason="동시성 테스트 조정",
                            idempotency_key="adjust-race",
                        ),
                        triggered_by=1,
                    )
                    results[name] = ledger.id
                except Exception as e:  # noqa: BLE001
                    results[name] = f"error:{type(e).__name__}"
                finally:
                    thread_db.close()

            t1 = threading.Thread(target=worker, args=("thread-a",))
            t2 = threading.Thread(target=worker, args=("thread-b",))
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

            ledger_ids = list(results.values())
            self.assertEqual(len(set(ledger_ids)), 1)

            verify_db = SessionLocal2()
            try:
                sku = (
                    verify_db.query(InventorySku)
                    .filter(InventorySku.id == self.sku_id)
                    .first()
                )
                self.assertEqual(sku.available_qty, 7)  # 10 - 3, 한 번만 반영

                count = (
                    verify_db.query(InventoryLedgerEvent)
                    .filter(
                        InventoryLedgerEvent.idempotency_key
                        == "adjust-race",
                    )
                    .count()
                )
                self.assertEqual(count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()


if __name__ == "__main__":
    unittest.main()
