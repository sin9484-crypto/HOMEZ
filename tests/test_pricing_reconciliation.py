"""
=========================================================
Homez OS

File : tests/test_pricing_reconciliation.py

V7 Gate 5(2026-08-15) — 실제 마진 반영(record_actual_margin) + 정산
대사(SettlementReconciliation) + Settlement HELD/MISMATCH 상태 검증
(요구사항 2/4/5). 임시 SQLite 파일 DB만 사용한다 — 실제 homez.db는
이 테스트 전체에서 전혀 접근하지 않는다.

Order/OrderItem/PurchaseItem/InventoryChannelMapping은 각 도메인의
Service를 거치지 않고 이 테스트가 직접 ORM으로 심는다(이 파일의
검증 대상은 order/purchase 자체가 아니라, pricing 도메인이 그
결과물을 올바르게 읽어 계산하는지이다 — Gate 4에서 이미 각 도메인
자체는 충분히 검증됨).
=========================================================
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
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
from app.domains.pricing.constants import EstimatedComponent
from app.domains.pricing.constants import MarginType
from app.domains.pricing.constants import ReconciliationStatus
from app.domains.pricing.model import MarginSnapshot
from app.domains.pricing.model import PriceChangeRequest
from app.domains.pricing.model import PriceChangeStatusEvent
from app.domains.pricing.model import ProductPricing
from app.domains.pricing.model import SettlementReconciliation
from app.domains.pricing.schema import ProductPricingInitCreate
from app.domains.pricing.service import PricingService
from app.domains.purchase.model import PurchaseItem
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.settlement.schema import SettlementCreate
from app.domains.settlement.schema import SettlementMemoUpdate
from app.domains.settlement.service import SettlementService
from app.domains.user.model import User  # noqa: F401 (Company.relationship("User") 해석용)


class ReconciliationTestCaseBase(unittest.TestCase):

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

        self.pricing_service = PricingService(self.db)
        self.settlement_service = SettlementService(self.db)
        self.funding_service = FundingService(self.db)

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

        self.listing = MarketplaceListing(
            company_id=self.company_id, product_candidate_id=1,
            marketplace_account_id=account.id, status="DRAFT",
        )
        self.db.add(self.listing)
        self.db.commit()

        self.sku = InventorySku(
            company_id=self.company_id, product_candidate_id=1,
            sku_code="SKU-1", option_label="기본",
            available_qty=100, reserved_qty=0,
        )
        self.db.add(self.sku)
        self.db.commit()

        self.mapping = InventoryChannelMapping(
            company_id=self.company_id, inventory_sku_id=self.sku.id,
            marketplace_listing_id=self.listing.id, channel_code="FAKE",
            channel_sku="CH-SKU-1",
        )
        self.db.add(self.mapping)
        self.db.commit()

        self.pricing = self.pricing_service.initialize_pricing(
            self.company_id,
            ProductPricingInitCreate(
                listing_id=self.listing.id,
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

        self.funding_service.create_account(
            FundingAccountCreate(total_funding=1_000_000.0),
            self.company_id,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_order(
        self, order_id_hint="ORDER-1", quantity=2, unit_price=10000.0,
        returned_quantity=0,
    ) -> tuple[Order, OrderItem]:

        order = Order(
            company_id=self.company_id, channel_code="FAKE",
            channel_order_id=order_id_hint, order_number=f"O-{order_id_hint}",
            status="DELIVERED", buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울시 1-1",
            receiver_zipcode="00000",
            total_amount=quantity * unit_price,
            ordered_at=datetime.utcnow(),
        )
        self.db.add(order)
        self.db.commit()

        item = OrderItem(
            company_id=self.company_id, order_id=order.id,
            inventory_sku_id=self.sku.id, channel_sku="CH-SKU-1",
            sku_code_snapshot="SKU-1", product_name_snapshot="테스트 상품",
            quantity=quantity, unit_price=unit_price, status="SHIPPED",
            returned_quantity=returned_quantity,
        )
        self.db.add(item)
        self.db.commit()

        return order, item

    def _create_purchase_item(
        self, order_item: OrderItem, quantity=2, unit_cost=4800.0,
    ) -> PurchaseItem:

        pi = PurchaseItem(
            company_id=self.company_id, purchase_id=1,
            order_item_id=order_item.id, inventory_sku_id=self.sku.id,
            quantity=quantity, unit_cost=unit_cost,
            subtotal_cost=quantity * unit_cost,
        )
        self.db.add(pi)
        self.db.commit()

        return pi

    def _create_deposited_settlement(
        self, order: Order, gross=20000.0, fee=2000.0,
    ) -> MarketplaceSettlement:

        account = self.funding_service.repository.get_account_by_company(
            self.company_id,
        )
        settlement = self.settlement_service.create(
            SettlementCreate(
                market="FAKE", market_order_id=order.channel_order_id,
                order_id=order.id, account_id=account.id,
                gross_amount=gross, fee_amount=fee, net_amount=gross - fee,
                idempotency_key=f"settlement:{order.id}",
            ),
            self.company_id,
        )
        return self.settlement_service.confirm_deposit(
            settlement.id, self.company_id,
        )


# ====================================================
# 요구사항 2/5 — 실제 마진 반영(정산/매입원가 실측 반영)
# ====================================================

class RecordActualMarginTestCase(ReconciliationTestCaseBase):

    def test_actual_margin_uses_real_purchase_cost_and_settlement_fee(self):

        order, item = self._create_order(
            order_id_hint="ORDER-1", quantity=2, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=2, unit_cost=4800.0)
        settlement = self._create_deposited_settlement(
            order, gross=20000.0, fee=2000.0,
        )

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )

        self.assertEqual(result["order_id"], order.id)
        self.assertEqual(len(result["snapshots"]), 1)
        self.assertEqual(result["skipped_items"], [])

        snapshot = result["snapshots"][0]
        self.assertEqual(snapshot.margin_type, MarginType.ACTUAL)
        self.assertEqual(snapshot.order_id, order.id)
        self.assertEqual(snapshot.settlement_id, settlement.id)
        self.assertEqual(Decimal(snapshot.revenue), Decimal("20000.00"))
        # 실제 매입원가(PurchaseItem)를 그대로 써야 한다(추정 아님).
        self.assertEqual(Decimal(snapshot.cost_of_goods), Decimal("9600.00"))
        # 정산 fee_amount 전액이 이 주문의 유일한 품목에 안분된다(품목
        # 1개라 안분=실측과 동일 — 추정 표시하지 않는다).
        self.assertEqual(Decimal(snapshot.channel_fee), Decimal("2000.00"))

        estimated = json.loads(snapshot.estimated_components_json)
        self.assertNotIn(EstimatedComponent.COST_OF_GOODS, estimated)
        self.assertNotIn(EstimatedComponent.CHANNEL_FEE, estimated)
        # shipping/packaging/ad/return_reserve/tax는 이 저장소에 실측
        # 소스가 없어 항상 추정으로 표시돼야 한다.
        self.assertIn(EstimatedComponent.SHIPPING_COST, estimated)
        self.assertIn(EstimatedComponent.AD_COST, estimated)

    def test_actual_margin_falls_back_to_expected_cost_when_no_purchase(
        self,
    ):

        order, item = self._create_order(
            order_id_hint="ORDER-2", quantity=2, unit_price=10000.0,
        )
        # PurchaseItem 없음 — 아직 발주/입고가 반영되지 않은 상태.

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        snapshot = result["snapshots"][0]

        # cost_of_goods(5000/unit) * 2 = 10000 추정치를 대신 사용.
        self.assertEqual(
            Decimal(snapshot.cost_of_goods), Decimal("10000.00"),
        )
        estimated = json.loads(snapshot.estimated_components_json)
        self.assertIn(EstimatedComponent.COST_OF_GOODS, estimated)

    def test_actual_margin_reflects_refund_from_returned_quantity(self):

        order, item = self._create_order(
            order_id_hint="ORDER-3", quantity=4, unit_price=10000.0,
            returned_quantity=1,
        )
        self._create_purchase_item(item, quantity=4, unit_cost=4800.0)

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        snapshot = result["snapshots"][0]

        self.assertEqual(
            Decimal(snapshot.refund_adjustment), Decimal("10000.00"),
        )

    def test_unmapped_channel_sku_is_skipped_not_crashed(self):

        order = Order(
            company_id=self.company_id, channel_code="FAKE",
            channel_order_id="ORDER-UNMAPPED", order_number="O-X",
            status="DELIVERED", buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울시 1-1",
            receiver_zipcode="00000", total_amount=5000.0,
            ordered_at=datetime.utcnow(),
        )
        self.db.add(order)
        self.db.commit()

        item = OrderItem(
            company_id=self.company_id, order_id=order.id,
            inventory_sku_id=self.sku.id, channel_sku="UNMAPPED-SKU",
            sku_code_snapshot="SKU-1", product_name_snapshot="테스트 상품",
            quantity=1, unit_price=5000.0, status="SHIPPED",
        )
        self.db.add(item)
        self.db.commit()

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        self.assertEqual(result["snapshots"], [])
        self.assertEqual(len(result["skipped_items"]), 1)
        self.assertIn("no_channel_mapping", result["skipped_items"][0])

    def test_unknown_order_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.pricing_service.record_actual_margin(999999, self.company_id)

    def test_margin_variance_compares_expected_vs_latest_actual(self):

        order, item = self._create_order(
            order_id_hint="ORDER-4", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        self._create_deposited_settlement(order, gross=10000.0, fee=1000.0)

        self.pricing_service.record_actual_margin(order.id, self.company_id)

        variance = self.pricing_service.get_margin_variance(
            self.listing.id, self.company_id,
        )
        self.assertIsNotNone(variance["expected"])
        self.assertIsNotNone(variance["latest_actual"])
        self.assertIsNotNone(variance["margin_amount_variance"])


# ====================================================
# 요구사항 4/5 — 정산 대사(MATCHED/MISMATCH/HELD)
# ====================================================

class ReconciliationStatusTestCase(ReconciliationTestCaseBase):

    def test_reconciliation_is_pending_before_settlement_deposited(self):

        order, item = self._create_order(
            order_id_hint="ORDER-5", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        reconciliation = result["reconciliation"]
        self.assertEqual(
            reconciliation.status, ReconciliationStatus.PENDING_SETTLEMENT,
        )
        self.assertIsNone(reconciliation.actual_net_amount)

    def test_reconciliation_matches_when_settlement_equals_expected(self):
        """
        expected_net = revenue - channel_fee - payment_fee(Settlement의
        Fixed Rule net==gross-fee와 동일 개념 — return_reserve/tax는
        HOMEZ 내부 마진 가정일 뿐 채널 정산액에는 반영되지 않는다).
        품목이 1개뿐인 주문은 안분=실측이라 channel_fee는 settlement.
        fee_amount(1400) 그대로, payment_fee=0 → 10000-1400=8600.
        """

        order, item = self._create_order(
            order_id_hint="ORDER-6", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        self._create_deposited_settlement(
            order, gross=10000.0, fee=1400.0,
        )

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        reconciliation = result["reconciliation"]

        self.assertEqual(
            Decimal(reconciliation.expected_net_amount), Decimal("8600.00"),
        )
        self.assertEqual(
            Decimal(reconciliation.actual_net_amount), Decimal("8600.00"),
        )
        self.assertEqual(reconciliation.status, ReconciliationStatus.MATCHED)
        self.assertEqual(
            Decimal(reconciliation.variance_amount), Decimal("0.00"),
        )

    def test_reconciliation_flags_mismatch_without_auto_correction(self):
        """
        채널이 실제로 정산한 gross_amount가 HOMEZ 주문 기록의 매출과
        다르면(예: 채널 측 할인/조정이 주문에 반영되지 않음) 대사가
        어긋난다 — 금액을 자동으로 고치지 않고 MISMATCH로만 플래그
        한다.
        """

        order, item = self._create_order(
            order_id_hint="ORDER-7", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        # 채널이 실제로 신고한 gross(9000)가 주문 매출(10000)과 다르다.
        self._create_deposited_settlement(order, gross=9000.0, fee=900.0)

        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        reconciliation = result["reconciliation"]

        # expected_net = 주문 매출(10000) - 안분된 채널수수료(900) = 9100
        self.assertEqual(
            Decimal(reconciliation.expected_net_amount), Decimal("9100.00"),
        )
        # actual_net = 실제 정산 net_amount(9000 - 900 = 8100)
        self.assertEqual(
            Decimal(reconciliation.actual_net_amount), Decimal("8100.00"),
        )
        self.assertEqual(
            reconciliation.status, ReconciliationStatus.MISMATCH,
        )
        # 8100 - 9100 = -1000 — 금액을 자동으로 고치지 않고 그대로 노출.
        self.assertEqual(
            Decimal(reconciliation.variance_amount), Decimal("-1000.00"),
        )

    def test_held_reconciliation_is_not_overwritten_by_recompute(self):

        order, item = self._create_order(
            order_id_hint="ORDER-8", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        self._create_deposited_settlement(order, gross=10000.0, fee=1400.0)

        self.pricing_service.record_actual_margin(order.id, self.company_id)

        held = self.pricing_service.hold_reconciliation(
            order.id, self.company_id, "수동 조사 필요", 1,
        )
        self.assertEqual(held.status, ReconciliationStatus.HELD)

        # 재계산을 다시 호출해도 HELD는 그대로 유지돼야 한다.
        result = self.pricing_service.record_actual_margin(
            order.id, self.company_id,
        )
        self.assertEqual(
            result["reconciliation"].status, ReconciliationStatus.HELD,
        )

        released = self.pricing_service.release_reconciliation_hold(
            order.id, self.company_id, 1,
        )
        self.assertEqual(released.status, ReconciliationStatus.MATCHED)

    def test_hold_unknown_order_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.pricing_service.hold_reconciliation(
                999999, self.company_id, "사유", 1,
            )

    def test_release_hold_requires_held_state(self):

        order, item = self._create_order(
            order_id_hint="ORDER-9", quantity=1, unit_price=10000.0,
        )
        self._create_purchase_item(item, quantity=1, unit_cost=4800.0)
        self.pricing_service.record_actual_margin(order.id, self.company_id)

        with self.assertRaises(BadRequestException):
            self.pricing_service.release_reconciliation_hold(
                order.id, self.company_id, 1,
            )


# ====================================================
# 요구사항 4 — Settlement HELD/MISMATCH (SettlementService 확장)
# ====================================================

class SettlementHeldMismatchTestCase(ReconciliationTestCaseBase):

    def _pending_settlement(self, order) -> MarketplaceSettlement:

        account = self.funding_service.repository.get_account_by_company(
            self.company_id,
        )
        return self.settlement_service.create(
            SettlementCreate(
                market="FAKE", market_order_id=order.channel_order_id,
                order_id=order.id, account_id=account.id,
                gross_amount=10000.0, fee_amount=1000.0, net_amount=9000.0,
                idempotency_key=f"settlement:{order.id}",
            ),
            self.company_id,
        )

    def test_hold_then_release_returns_to_pending(self):

        order, _ = self._create_order(order_id_hint="ORDER-H1")
        settlement = self._pending_settlement(order)

        held = self.settlement_service.hold(
            settlement.id, self.company_id,
            SettlementMemoUpdate(memo="확인 필요"),
        )
        self.assertEqual(held.status, "HELD")

        # HELD 상태에서는 입금 확인이 차단돼야 한다(기존 PENDING 전용
        # 규칙이 자동으로 적용됨, 별도 코드 변경 없음).
        with self.assertRaises(BadRequestException):
            self.settlement_service.confirm_deposit(
                settlement.id, self.company_id,
            )

        released = self.settlement_service.release_hold(
            settlement.id, self.company_id,
        )
        self.assertEqual(released.status, "PENDING")

        # 해제 후에는 정상적으로 입금 확인이 가능해야 한다.
        deposited = self.settlement_service.confirm_deposit(
            settlement.id, self.company_id,
        )
        self.assertEqual(deposited.status, "DEPOSITED")

    def test_flag_mismatch_requires_memo_and_blocks_deposit(self):

        order, _ = self._create_order(order_id_hint="ORDER-H2")
        settlement = self._pending_settlement(order)

        with self.assertRaises(BadRequestException):
            self.settlement_service.flag_mismatch(
                settlement.id, self.company_id, SettlementMemoUpdate(),
            )

        flagged = self.settlement_service.flag_mismatch(
            settlement.id, self.company_id,
            SettlementMemoUpdate(memo="금액 불일치 발견"),
        )
        self.assertEqual(flagged.status, "MISMATCH")

        with self.assertRaises(BadRequestException):
            self.settlement_service.confirm_deposit(
                settlement.id, self.company_id,
            )

    def test_resolve_mismatch_requires_memo_and_does_not_touch_amounts(self):

        order, _ = self._create_order(order_id_hint="ORDER-H3")
        settlement = self._pending_settlement(order)
        self.settlement_service.flag_mismatch(
            settlement.id, self.company_id,
            SettlementMemoUpdate(memo="불일치"),
        )

        with self.assertRaises(BadRequestException):
            self.settlement_service.resolve_mismatch(
                settlement.id, self.company_id, SettlementMemoUpdate(),
            )

        resolved = self.settlement_service.resolve_mismatch(
            settlement.id, self.company_id,
            SettlementMemoUpdate(memo="수동 조사 완료, 금액 정상 확인"),
        )
        self.assertEqual(resolved.status, "PENDING")
        # 금액은 그대로다 — 자동 보정하지 않는다.
        self.assertEqual(resolved.gross_amount, 10000.0)
        self.assertEqual(resolved.net_amount, 9000.0)

    def test_deposited_settlement_cannot_be_held(self):

        order, _ = self._create_order(order_id_hint="ORDER-H4")
        settlement = self._pending_settlement(order)
        self.settlement_service.confirm_deposit(settlement.id, self.company_id)

        with self.assertRaises(BadRequestException):
            self.settlement_service.hold(settlement.id, self.company_id, None)


if __name__ == "__main__":
    unittest.main()
