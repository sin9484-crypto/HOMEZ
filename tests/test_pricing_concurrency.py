"""
=========================================================
Homez OS

File : tests/test_pricing_concurrency.py

V7 Gate 5(2026-08-15) — Pricing & Margin Reconciliation 동시성 검증.
실스레드 + 스레드별 독립 DB 연결/Session으로 경쟁시킨다
(tests/test_order_fulfillment_concurrency.py와 동일 패턴 — 워커
스레드가 메인 스레드 Session을 절대 공유하지 않는다).

특히 `record_actual_margin()`의 2단계 commit 경계(MarginSnapshot을
먼저 확정 commit한 뒤 SettlementReconciliation을 upsert)가 실제
동시성 상황에서도 "뒤 단계의 rollback이 앞 단계의 확정된 커밋을
지우지 않는지"를 실측으로 검증한다 — V7 Gate 4가 겪은 결함과 같은
클래스를 이번에는 미리 설계로 피했는지 실제로 확인한다.
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.service import FundingService
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventorySku
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.order.model import Order
from app.domains.order.model import OrderItem
from app.domains.pricing.constants import PriceChangeStatus
from app.domains.pricing.model import MarginSnapshot
from app.domains.pricing.model import PriceChangeRequest
from app.domains.pricing.model import PriceChangeStatusEvent
from app.domains.pricing.model import ProductPricing
from app.domains.pricing.model import SettlementReconciliation
from app.domains.pricing.schema import PriceChangeCreate
from app.domains.pricing.schema import ProductPricingInitCreate
from app.domains.pricing.service import PricingService
from app.domains.purchase.model import PurchaseItem
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.user.model import User  # noqa: F401 (Company relationship 해석용)


class PricingConcurrencyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceListing.__table__,
                InventorySku.__table__,
                InventoryChannelMapping.__table__,
                FundingAccount.__table__,
                FundingHold.__table__,
                SupplierPayment.__table__,
                FundingLedger.__table__,
                MarketplaceSettlement.__table__,
                Order.__table__,
                OrderItem.__table__,
                PurchaseItem.__table__,
                ProductPricing.__table__,
                PriceChangeRequest.__table__,
                PriceChangeStatusEvent.__table__,
                MarginSnapshot.__table__,
                SettlementReconciliation.__table__,
            ],
        )

        # 2026-08-15 V7 Gate 8 — approve_price_change()가 이제
        # write_audit_log()를 쓴다. audit_logs는 ORM Model이 없는
        # raw-SQL 전용 테이블이라 Base.metadata.create_all()로는
        # 생기지 않는다 — tests/test_pricing_core.py와 동일한 DDL을
        # 재사용해 수동으로 만든다.
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )

        seed_db = self.SessionLocal()
        try:
            company = Company(
                name="동시성 테스트 회사", business_number="999-99-99999",
                ceo="테스트", phone="02-000-0000",
                email="concurrency-pricing@example.com", address="서울",
            )
            seed_db.add(company)
            seed_db.commit()
            self.company_id = company.id

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
                company_id=self.company_id, product_candidate_id=1,
                marketplace_account_id=account.id, status="DRAFT",
            )
            seed_db.add(listing)
            seed_db.commit()
            self.listing_id = listing.id

            sku = InventorySku(
                company_id=self.company_id, product_candidate_id=1,
                sku_code="RACE-SKU", option_label="기본",
                available_qty=100, reserved_qty=0,
            )
            seed_db.add(sku)
            seed_db.commit()
            self.sku_id = sku.id

            mapping = InventoryChannelMapping(
                company_id=self.company_id, inventory_sku_id=self.sku_id,
                marketplace_listing_id=self.listing_id, channel_code="FAKE",
                channel_sku="CH-SKU-1",
            )
            seed_db.add(mapping)
            seed_db.commit()

            pricing_service = PricingService(seed_db)
            self.pricing = pricing_service.initialize_pricing(
                self.company_id,
                ProductPricingInitCreate(
                    listing_id=self.listing_id,
                    initial_sale_price=Decimal("10000"),
                    cost_of_goods=Decimal("5000"),
                    shipping_cost=Decimal("500"),
                    packaging_cost=Decimal("100"),
                    ad_cost=Decimal("200"),
                    channel_fee_rate=Decimal("0.1"),
                    payment_fee_rate=Decimal("0.03"),
                    return_reserve_rate=Decimal("0.01"),
                    tax_basis_rate=Decimal("0.0"),
                ),
                1,
            )

            request = pricing_service.request_price_change(
                self.company_id, self.listing_id,
                PriceChangeCreate(
                    requested_sale_price=Decimal("12000"),
                    idempotency_key="pc:race",
                ),
                1,
            )
            # 아래에서 seed_db를 닫으므로, 이후 사용할 값은 여기서
            # 미리 순수 파이썬 값으로 캡처한다(DetachedInstanceError
            # 방지 — ORM 객체를 세션 종료 후 재사용하지 않는다).
            self.request_id = request.id
            self.pricing_id = self.pricing.id
            self.pricing_version = self.pricing.version

            order = Order(
                company_id=self.company_id, channel_code="FAKE",
                channel_order_id="RACE-ORDER-1", order_number="O-RACE-1",
                status="DELIVERED", buyer_name="구매자",
                receiver_name="수취인", receiver_phone="010-0000-0000",
                receiver_address="서울시 1-1", receiver_zipcode="00000",
                total_amount=10000.0, ordered_at=datetime.utcnow(),
            )
            seed_db.add(order)
            seed_db.commit()
            self.order_id = order.id

            item = OrderItem(
                company_id=self.company_id, order_id=order.id,
                inventory_sku_id=self.sku_id, channel_sku="CH-SKU-1",
                sku_code_snapshot="RACE-SKU",
                product_name_snapshot="경쟁 테스트 상품",
                quantity=1, unit_price=10000.0, status="SHIPPED",
            )
            seed_db.add(item)
            seed_db.commit()

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
    # 요구사항 3 — 동시 가격 변경 승인 경쟁: 정확히 1회만 반영
    # --------------------------------------------------

    def test_concurrent_approve_applies_price_exactly_once(self):

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = PricingService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    approved = service.approve_price_change(
                        self.request_id, self.company_id, 1, None,
                    )
                    results[name] = ("ok", approved.status)
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
            self.assertEqual(len(outcomes), 2)
            # 두 스레드 모두 예외 없이 APPROVED를 봐야 한다(경쟁 패자도
            # 재조회로 승자의 결과를 그대로 반환받는다 — Settlement
            # DEPOSITED 재호출과 동일 철학).
            self.assertTrue(
                all(o == ("ok", "APPROVED") for o in outcomes), outcomes,
            )

            verify_db = SessionLocal2()
            try:
                pricing = (
                    verify_db.query(ProductPricing)
                    .filter(ProductPricing.id == self.pricing_id)
                    .first()
                )
                self.assertEqual(
                    Decimal(pricing.current_sale_price), Decimal("12000"),
                )
                self.assertIsNone(pricing.pending_price_change_id)
                # setUp이 캡처한 pricing_version은 이미 요청연결(+1)까지
                # 반영된 값이다 — 승인 반영이 정확히 1회만 일어났다면
                # 여기서 다시 +1만 늘어야 한다(두 스레드가 모두 반영
                # 했다면 +2 이상이 된다).
                self.assertEqual(pricing.version, self.pricing_version + 1)

                snapshot_count = (
                    verify_db.query(MarginSnapshot)
                    .filter(MarginSnapshot.listing_id == self.listing_id)
                    .filter(MarginSnapshot.reason == "PRICE_CHANGE_APPROVED")
                    .count()
                )
                # 승인 반영(및 그에 따른 EXPECTED 스냅샷 기록)은 정확히
                # 1회만 일어나야 한다 — 두 스레드가 각각 스냅샷을 만들면
                # 가격이 중복 반영된 것이다.
                self.assertEqual(snapshot_count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()

    # --------------------------------------------------
    # 요구사항 2/5 — 동시 실제 마진 기록: 2단계 commit 안전성
    # --------------------------------------------------

    def test_concurrent_record_actual_margin_does_not_lose_snapshots(self):
        """
        같은 Order에 대해 두 스레드가 동시에 record_actual_margin()을
        호출하면, SettlementReconciliation upsert 단계에서 한쪽은
        INSERT IntegrityError → rollback → UPDATE 경로를 타게 된다.
        이 rollback이 먼저 이미 확정 commit된 MarginSnapshot(양쪽
        스레드 모두)을 지우지 않아야 한다(V7 Gate 4가 겪은 결함과
        같은 클래스 재발 방지 실측).
        """

        engine2, SessionLocal2 = self._threaded_engine()

        try:
            results = {}
            barrier = threading.Barrier(2)

            def worker(name):

                thread_db = SessionLocal2()
                service = PricingService(thread_db)

                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass

                try:
                    result = service.record_actual_margin(
                        self.order_id, self.company_id, 1,
                    )
                    results[name] = (
                        "ok", len(result["snapshots"]),
                        result["reconciliation"].status,
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
            self.assertTrue(
                all(o[0] == "ok" for o in outcomes), outcomes,
            )

            verify_db = SessionLocal2()
            try:
                # 두 스레드가 각각 만든 ACTUAL 스냅샷(품목 1개 × 2회
                # 호출 = 2개)이 전부 살아 있어야 한다 — 하나도 rollback
                # 으로 유실되면 안 된다.
                snapshot_count = (
                    verify_db.query(MarginSnapshot)
                    .filter(MarginSnapshot.order_id == self.order_id)
                    .filter(MarginSnapshot.margin_type == "ACTUAL")
                    .count()
                )
                self.assertEqual(snapshot_count, 2)

                # 대사 기록은 정확히 1행이어야 한다(UNIQUE(company_id,
                # order_id) — 경쟁 패자는 재조회 경로로 수렴).
                reconciliation_count = (
                    verify_db.query(SettlementReconciliation)
                    .filter(SettlementReconciliation.order_id == self.order_id)
                    .count()
                )
                self.assertEqual(reconciliation_count, 1)
            finally:
                verify_db.close()
        finally:
            engine2.dispose()


if __name__ == "__main__":
    unittest.main()
