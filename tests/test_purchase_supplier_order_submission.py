"""
=========================================================
Homez OS

File : tests/test_purchase_supplier_order_submission.py

PurchaseService.submit_to_supplier() — SupplierOrderProvider 실제
연결 검증(2026-08-20 2차 지시). 승인/전송 분리, 외부 호출 게이트
(EStop/공급처 관계 승인/가격 일치), 멱등성, 회사 격리.
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
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.model import OrderStatusEvent
from app.domains.order.schema import OrderChannelCollectRequest
from app.domains.order.schema import OrderChannelItemPayload
from app.domains.order.service import OrderService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.purchase.constants import PurchaseSubmissionStatus
from app.domains.purchase.model import Purchase
from app.domains.purchase.model import PurchaseItem
from app.domains.purchase.schema import PurchaseCreate
from app.domains.purchase.schema import PurchaseItemCreate
from app.domains.purchase.service import PurchaseService
from app.domains.source.model import CompanySupplierRelation
from app.domains.source.model import SupplierProductLink
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


class PurchaseSupplierOrderSubmissionTestCase(unittest.TestCase):

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

        self.order_service = OrderService(self.db)
        self.purchase_service = PurchaseService(self.db)
        self.inventory_service = InventoryService(self.db)
        self.funding_service = FundingService(self.db)
        self.source_service = SourceService(self.db)

        self.company_a = self._seed_company("A", "111-11-11111")
        self.company_b = self._seed_company("B", "222-22-22222")

        self.channel = MarketplaceChannel(
            code="FAKE", name="가짜채널", doc_verification_status="VERIFIED",
        )
        self.db.add(self.channel)
        self.db.commit()

        self.account = MarketplaceAccount(
            company_id=self.company_a.id, channel_id=self.channel.id,
            account_code="acc1", account_name="계정1",
        )
        self.db.add(self.account)
        self.db.commit()

        self.funding_service.create_account(
            FundingAccountCreate(total_funding=100_000.0), self.company_a.id,
        )

        self.supplier = Supplier(name="테스트 공급처", is_active=True)
        self.db.add(self.supplier)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_company(self, label, business_number):

        company = Company(
            name=f"회사 {label}", business_number=business_number,
            ceo="테스트", phone="02-000-0000",
            email=f"{label.lower()}@test.com", address="서울",
        )
        self.db.add(company)
        self.db.commit()

        return company

    def _make_confirmed_purchase(self, unit_cost=3000.0):

        candidate = ProductCandidate(
            candidate_key="submit-test:1", source_type="TREND",
            source_reference="submit-test", market="FAKE",
            product_name="발주 전송 테스트 상품", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        sku = self.inventory_service.create_sku(
            InventorySkuCreate(
                product_candidate_id=candidate.id,
                sku_code="SUBMIT-TEST", option_label="SUBMIT-TEST",
                initial_qty=0, safety_stock=0,
            ),
            self.company_a.id,
        )

        listing = MarketplaceListing(
            company_id=self.company_a.id,
            product_candidate_id=candidate.id,
            marketplace_account_id=self.account.id, status="DRAFT",
        )
        self.db.add(listing)
        self.db.commit()

        self.inventory_service.create_channel_mapping(
            sku.id, self.company_a.id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="FAKE", channel_sku="CH-SUBMIT-TEST",
            ),
        )

        collect_result = self.order_service.collect_channel_order(
            self.company_a.id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id="SUBMIT-TEST-ORDER",
                buyer_name="김구매", receiver_name="김수취",
                receiver_phone="010-0000-0000",
                receiver_address="서울시", receiver_zipcode="00000",
                ordered_at=datetime.utcnow(),
                items=[
                    OrderChannelItemPayload(
                        channel_sku="CH-SUBMIT-TEST", quantity=2,
                        unit_price=9000, product_name="발주 전송 테스트 상품",
                    ),
                ],
            ),
            triggered_by=1,
        )
        item = collect_result["items"][0]

        self.source_service.create_link(
            SupplierProductLinkCreate(
                product_candidate_id=candidate.id,
                supplier_id=self.supplier.id,
                supplier_sku="SUP-SUBMIT-TEST",
                unit_cost=unit_cost, moq=1,
            ),
            self.company_a.id, created_by=1,
        )

        purchase = self.purchase_service.create_purchase(
            self.company_a.id,
            PurchaseCreate(
                order_id=collect_result["order"].id,
                supplier_id=self.supplier.id,
                items=[
                    PurchaseItemCreate(
                        order_item_id=item.id, unit_cost=unit_cost,
                    ),
                ],
                idempotency_key=f"submit-test-{unit_cost}",
            ),
            triggered_by=1,
        )
        self.purchase_service.confirm_purchase(
            purchase.id, self.company_a.id, triggered_by=1,
        )

        return self.purchase_service.get_purchase(purchase.id, self.company_a.id)

    def _approve_relation(self):

        relation = self.source_service.create_relation(
            CompanySupplierRelationCreate(supplier_id=self.supplier.id),
            self.company_a.id, created_by=1,
        )
        self.source_service.set_relation_approval(
            relation.id, self.company_a.id, approve=True,
        )

    def test_submit_blocked_without_confirmation(self):

        candidate = ProductCandidate(
            candidate_key="unconfirmed:1", source_type="TREND",
            source_reference="unconfirmed", market="FAKE",
            product_name="미승인 테스트", status="APPROVED",
        )
        self.db.add(candidate)
        self.db.commit()

        sku = self.inventory_service.create_sku(
            InventorySkuCreate(
                product_candidate_id=candidate.id,
                sku_code="UNCONFIRMED", option_label="UNCONFIRMED",
                initial_qty=0, safety_stock=0,
            ),
            self.company_a.id,
        )
        listing = MarketplaceListing(
            company_id=self.company_a.id, product_candidate_id=candidate.id,
            marketplace_account_id=self.account.id, status="DRAFT",
        )
        self.db.add(listing)
        self.db.commit()
        self.inventory_service.create_channel_mapping(
            sku.id, self.company_a.id,
            InventoryChannelMappingCreate(
                marketplace_listing_id=listing.id,
                channel_code="FAKE", channel_sku="CH-UNCONFIRMED",
            ),
        )
        collect_result = self.order_service.collect_channel_order(
            self.company_a.id,
            OrderChannelCollectRequest(
                channel_code="FAKE", channel_order_id="UNCONFIRMED-ORDER",
                buyer_name="김구매", receiver_name="김수취",
                receiver_phone="010-0000-0000", receiver_address="서울시",
                receiver_zipcode="00000", ordered_at=datetime.utcnow(),
                items=[OrderChannelItemPayload(
                    channel_sku="CH-UNCONFIRMED", quantity=1,
                    unit_price=1000, product_name="미승인 테스트",
                )],
            ),
            triggered_by=1,
        )
        item = collect_result["items"][0]
        self.source_service.create_link(
            SupplierProductLinkCreate(
                product_candidate_id=candidate.id, supplier_id=self.supplier.id,
                supplier_sku="SUP-UNCONFIRMED", unit_cost=500.0, moq=1,
            ),
            self.company_a.id, created_by=1,
        )
        purchase = self.purchase_service.create_purchase(
            self.company_a.id,
            PurchaseCreate(
                order_id=collect_result["order"].id, supplier_id=self.supplier.id,
                items=[PurchaseItemCreate(order_item_id=item.id, unit_cost=500.0)],
                idempotency_key="unconfirmed-purchase",
            ),
            triggered_by=1,
        )
        # 승인(confirm)하지 않은 채 바로 전송 시도.

        self._approve_relation()

        with self.assertRaises(BadRequestException):
            self.purchase_service.submit_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )

    def test_submit_blocked_without_approved_relation(self):

        purchase = self._make_confirmed_purchase()
        # 공급처 거래관계를 승인하지 않음.

        with self.assertRaises(BadRequestException):
            self.purchase_service.submit_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )

    def test_submit_blocked_when_estop_active(self):

        purchase = self._make_confirmed_purchase()
        self._approve_relation()

        self.db.add(EmergencyStop(
            is_active=True, reason="테스트 비상정지", set_by=1,
        ))
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.purchase_service.submit_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )

    def test_submit_blocked_on_price_mismatch(self):

        purchase = self._make_confirmed_purchase(unit_cost=3000.0)
        self._approve_relation()

        # 연결 가격을 바꿔 발주 당시 단가와 어긋나게 만든다.
        link = self.db.query(SupplierProductLink).filter(
            SupplierProductLink.company_id == self.company_a.id,
        ).first()
        link.unit_cost = 9999.0
        self.db.commit()

        with self.assertRaises(BadRequestException):
            self.purchase_service.submit_to_supplier(
                purchase.id, self.company_a.id, "FAKE", triggered_by=1,
            )

    def test_submit_succeeds_with_fake_provider(self):

        purchase = self._make_confirmed_purchase()
        self._approve_relation()

        result = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )

        self.assertEqual(result.submission_status, PurchaseSubmissionStatus.SUBMITTED)
        self.assertIsNotNone(result.supplier_order_id)
        self.assertEqual(result.submission_provider_code, "FAKE")

    def test_submit_is_idempotent(self):

        purchase = self._make_confirmed_purchase()
        self._approve_relation()

        first = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )
        second = self.purchase_service.submit_to_supplier(
            purchase.id, self.company_a.id, "FAKE", triggered_by=1,
        )

        self.assertEqual(first.supplier_order_id, second.supplier_order_id)

    def test_submit_blocks_cross_company(self):

        purchase = self._make_confirmed_purchase()
        self._approve_relation()

        from app.core.exceptions import NotFoundException

        with self.assertRaises(NotFoundException):
            self.purchase_service.submit_to_supplier(
                purchase.id, self.company_b.id, "FAKE", triggered_by=1,
            )


if __name__ == "__main__":
    unittest.main()
