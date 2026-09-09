"""
=========================================================
Homez OS

File : tests/test_order_exception_analysis.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — OrderExceptionAnalysisService.
읽기 전용 분석이 실제 주문·배송·반품 상태를 바꾸지 않는지, 예외
탐지 로직이 정확한지, ProposedAction 연결이 EStop을 존중하는지
검증한다. 임시 SQLite 파일 DB만 사용한다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy import text as _sa_text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.ai_governance.model import ProposedAction
from app.domains.ai_governance.service import InactiveCapabilityError
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.order.constants import OrderIngestionStatus
from app.domains.order.constants import OrderItemStatus
from app.domains.order.constants import OrderStatus
from app.domains.order.exception_analysis_service import (
    OrderExceptionAnalysisService,
)
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.model import ReturnOrder
from app.domains.shipment.constants import ShipmentStatus
from app.domains.shipment.model import Shipment

from tests.ai_governance_test_helpers import deactivated_capability

NOW = datetime(2026, 8, 22, 12, 0, 0)


class OrderExceptionAnalysisTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                Order.__table__,
                OrderItem.__table__,
                OrderIngestionEvent.__table__,
                Shipment.__table__,
                ReturnOrder.__table__,
                ProposedAction.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )

        with self.engine.begin() as _conn:
            _conn.execute(_sa_text(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            ))


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

        self.service = OrderExceptionAnalysisService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _order(self, status=OrderStatus.PENDING, created_at=None):

        order = Order(
            company_id=self.company.id, channel_code="FAKE",
            channel_order_id=f"CH-{datetime.utcnow().timestamp()}",
            status=status, buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울",
            receiver_zipcode="00000", ordered_at=NOW,
            created_at=created_at or NOW,
        )
        self.db.add(order)
        self.db.commit()
        return order

    def test_no_data_yields_no_exceptions(self):

        exceptions, envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertEqual(exceptions, [])
        self.assertEqual(envelope.result_type, "CONFIRMED_DATA")
        self.assertEqual(envelope.decision, "NO_EXCEPTIONS_FOUND")

    def test_stale_pending_order_detected(self):

        self._order(
            status=OrderStatus.PENDING, created_at=NOW - timedelta(hours=48),
        )

        exceptions, envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0].exception_type, "PENDING_ORDER_STALE")
        self.assertEqual(envelope.result_type, "HUMAN_REVIEW_REQUIRED")

    def test_fresh_pending_order_not_flagged(self):

        self._order(
            status=OrderStatus.PENDING, created_at=NOW - timedelta(hours=1),
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)
        self.assertEqual(exceptions, [])

    def test_out_of_stock_item_detected(self):

        order = self._order()
        item = OrderItem(
            company_id=self.company.id, order_id=order.id,
            inventory_sku_id=1, channel_sku="CH-SKU", sku_code_snapshot="SKU-1",
            product_name_snapshot="테스트 상품", quantity=1, unit_price=1000.0,
            status=OrderItemStatus.OUT_OF_STOCK,
        )
        self.db.add(item)
        self.db.commit()

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(e.exception_type == "OUT_OF_STOCK" for e in exceptions),
        )

    def test_ingestion_failure_detected(self):

        event = OrderIngestionEvent(
            company_id=self.company.id, channel_code="FAKE",
            channel_order_id="CH-FAIL-1", status=OrderIngestionStatus.FAILED,
            raw_payload="{}", created_at=NOW - timedelta(hours=1),
        )
        self.db.add(event)
        self.db.commit()

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(e.exception_type == "ORDER_INGESTION_FAILED" for e in exceptions),
        )

    def _shipment(self, order, status, created_at, invoice_number=None):

        shipment = Shipment(
            company_id=self.company.id, order_id=order.id,
            idempotency_key=f"ship-{datetime.utcnow().timestamp()}",
            status=status, invoice_number=invoice_number,
            created_at=created_at,
        )
        self.db.add(shipment)
        self.db.commit()
        return shipment

    def test_shipment_preparation_delay_detected(self):

        order = self._order()
        self._shipment(
            order, ShipmentStatus.READY, NOW - timedelta(hours=72),
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(
                e.exception_type == "SHIPMENT_PREPARATION_DELAYED"
                for e in exceptions
            ),
        )

    def test_shipment_missing_invoice_detected(self):

        order = self._order()
        self._shipment(
            order, ShipmentStatus.SHIPPED, NOW - timedelta(hours=1),
            invoice_number=None,
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(
                e.exception_type == "SHIPMENT_INVOICE_MISSING"
                for e in exceptions
            ),
        )

    def test_shipment_with_invoice_not_flagged_for_missing_invoice(self):

        order = self._order()
        self._shipment(
            order, ShipmentStatus.SHIPPED, NOW - timedelta(hours=1),
            invoice_number="INV-1",
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertFalse(
            any(
                e.exception_type == "SHIPMENT_INVOICE_MISSING"
                for e in exceptions
            ),
        )

    def test_shipment_in_transit_delay_detected(self):

        order = self._order()
        self._shipment(
            order, ShipmentStatus.IN_TRANSIT, NOW - timedelta(hours=200),
            invoice_number="INV-1",
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(
                e.exception_type == "SHIPMENT_IN_TRANSIT_DELAYED"
                for e in exceptions
            ),
        )

    def _return_order(self, order, status, created_at):

        ret = ReturnOrder(
            company_id=self.company.id, order_id=order.id,
            order_item_id=1, shipment_id=1, return_type="RETURN",
            status=status, quantity=1, reason="단순 변심",
            idempotency_key=f"ret-{datetime.utcnow().timestamp()}",
            created_at=created_at,
        )
        self.db.add(ret)
        self.db.commit()
        return ret

    def test_return_requested_detected(self):

        order = self._order()
        self._return_order(
            order, ReturnOrderStatus.REQUESTED, NOW - timedelta(hours=1),
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(e.exception_type == "RETURN_REQUESTED" for e in exceptions),
        )

    def test_return_review_delay_detected(self):

        order = self._order()
        self._return_order(
            order, ReturnOrderStatus.REQUESTED, NOW - timedelta(hours=96),
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(e.exception_type == "RETURN_REVIEW_DELAYED" for e in exceptions),
        )

    def test_return_received_needs_refund_reshipment_review(self):

        order = self._order()
        self._return_order(
            order, ReturnOrderStatus.RECEIVED, NOW - timedelta(hours=1),
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        matches = [
            e for e in exceptions
            if e.exception_type == "REFUND_OR_RESHIPMENT_REVIEW_NEEDED"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].recommended_action, "REFUND_REVIEW")

    def test_estop_marks_exceptions_not_execution_allowed_but_still_detects(self):

        self._order(
            status=OrderStatus.PENDING, created_at=NOW - timedelta(hours=48),
        )
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertEqual(len(exceptions), 1)
        self.assertFalse(exceptions[0].execution_allowed)

    def test_create_review_action_skipped_when_execution_not_allowed(self):

        self._order(
            status=OrderStatus.PENDING, created_at=NOW - timedelta(hours=48),
        )
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)
        action = self.service.create_review_action(
            self.company.id, exceptions[0], idempotency_key="oea:1",
        )
        self.assertIsNone(action)

    def test_create_review_action_creates_proposed_action(self):

        self._order(
            status=OrderStatus.PENDING, created_at=NOW - timedelta(hours=48),
        )

        exceptions, _envelope = self.service.analyze(self.company.id, now=NOW)
        action = self.service.create_review_action(
            self.company.id, exceptions[0], idempotency_key="oea:2",
        )

        self.assertIsNotNone(action)
        self.assertEqual(action.status, "REVIEW_REQUIRED")
        self.assertEqual(action.action_type, "ORDER_EXCEPTION_REVIEW")

    def test_blocks_when_order_shipment_return_capability_deactivated(self):

        with deactivated_capability("ORDER_SHIPMENT_RETURN"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.analyze(self.company.id, now=NOW)

    def test_analyze_does_not_write_to_order_status(self):
        """분석은 읽기 전용이다 — 상태를 절대 바꾸지 않는다."""

        order = self._order(
            status=OrderStatus.PENDING, created_at=NOW - timedelta(hours=48),
        )

        self.service.analyze(self.company.id, now=NOW)

        refreshed = self.db.get(Order, order.id)
        self.assertEqual(refreshed.status, OrderStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
