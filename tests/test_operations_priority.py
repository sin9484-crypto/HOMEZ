"""
=========================================================
Homez OS

File : tests/test_operations_priority.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — OperationsPriorityService.
읽기 전용 집계가 실제 상태를 바꾸지 않는지, 하위 capability
(ORDER_SHIPMENT_RETURN/SETTLEMENT) 비활성 시에도 전체 조회가 막히지
않는지(AG-0 원칙), Dashboard의 기존 COUNT 요약과 무관하게 동작하는지
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
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.orchestration.priority_service import (
    OperationsPriorityService,
)
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.purchase.constants import PurchaseStatus
from app.domains.purchase.constants import PurchaseSubmissionStatus
from app.domains.purchase.model import Purchase
from app.domains.return_order.constants import ReturnOrderStatus
from app.domains.return_order.model import ReturnOrder
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.shipment.model import Shipment

from tests.ai_governance_test_helpers import deactivated_capability

NOW = datetime(2026, 8, 22, 12, 0, 0)


class OperationsPriorityTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                FundingAccount.__table__,
                ProductCandidate.__table__,
                Purchase.__table__,
                Order.__table__,
                OrderItem.__table__,
                OrderIngestionEvent.__table__,
                Shipment.__table__,
                ReturnOrder.__table__,
                MarketplaceSettlement.__table__,
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

        self.service = OperationsPriorityService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_no_data_yields_no_items(self):

        items, envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertEqual(items, [])
        self.assertEqual(envelope.result_type, "CONFIRMED_DATA")
        self.assertEqual(envelope.decision, "NO_PRIORITIES_FOUND")

    def test_product_input_required_detected(self):

        candidate = ProductCandidate(
            candidate_key="opc:1", source_type="TREND",
            source_reference="ref-1", market="FAKE",
            product_name="상품1", status=CandidateStatus.DISCOVERED,
            owner_company_id=self.company.id, visibility="COMPANY",
        )
        self.db.add(candidate)
        self.db.commit()

        items, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(i.category == "PRODUCT_INPUT_REQUIRED" for i in items),
        )

    def _purchase(self, status, submission_status=None):

        purchase = Purchase(
            company_id=self.company.id, order_id=1, supplier_id=1,
            status=status, idempotency_key=f"pur:{datetime.utcnow().timestamp()}",
            total_cost=1000.0, requested_at=NOW,
            submission_status=submission_status,
        )
        self.db.add(purchase)
        self.db.commit()
        return purchase

    def test_purchase_approval_pending_detected(self):

        self._purchase(status=PurchaseStatus.REQUESTED)

        items, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(i.category == "PURCHASE_APPROVAL_PENDING" for i in items),
        )

    def test_purchase_failed_detected_as_high_urgency(self):

        self._purchase(
            status=PurchaseStatus.CANCELLED,
            submission_status=PurchaseSubmissionStatus.FAILED,
        )

        items, _envelope = self.service.analyze(self.company.id, now=NOW)

        matches = [i for i in items if i.category == "PURCHASE_FAILED"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].urgency, "HIGH")

    def _return_order(self, status):

        ret = ReturnOrder(
            company_id=self.company.id, order_id=1, order_item_id=1,
            shipment_id=1, return_type="RETURN", status=status,
            quantity=1, reason="단순 변심",
            idempotency_key=f"ret:{datetime.utcnow().timestamp()}",
            created_at=NOW,
        )
        self.db.add(ret)
        self.db.commit()
        return ret

    def test_return_refund_pending_detected(self):

        self._return_order(status=ReturnOrderStatus.REQUESTED)

        items, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(i.category == "RETURN_REFUND_PENDING" for i in items),
        )

    def test_order_exceptions_included_via_subanalysis(self):

        order = Order(
            company_id=self.company.id, channel_code="FAKE",
            channel_order_id="CH-1", status="PENDING",
            buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울",
            receiver_zipcode="00000", ordered_at=NOW,
            created_at=NOW - timedelta(hours=48),
        )
        self.db.add(order)
        self.db.commit()

        items, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(
                i.category == "ORDER_SHIPMENT_RETURN_EXCEPTIONS"
                for i in items
            ),
        )

    def test_order_subcapability_deactivated_does_not_block_overall_analysis(self):

        order = Order(
            company_id=self.company.id, channel_code="FAKE",
            channel_order_id="CH-2", status="PENDING",
            buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울",
            receiver_zipcode="00000", ordered_at=NOW,
            created_at=NOW - timedelta(hours=48),
        )
        self.db.add(order)
        self.db.commit()

        with deactivated_capability("ORDER_SHIPMENT_RETURN"):
            items, envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertFalse(
            any(
                i.category == "ORDER_SHIPMENT_RETURN_EXCEPTIONS"
                for i in items
            ),
        )
        self.assertTrue(
            any(
                "ORDER_SHIPMENT_RETURN" in m
                for m in envelope.missing_evidence
            ),
        )

    def test_blocks_when_operations_coordination_capability_deactivated(self):

        with deactivated_capability("OPERATIONS_COORDINATION"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.analyze(self.company.id, now=NOW)

    def test_analyze_does_not_write_anything(self):
        """분석은 읽기 전용이다 — 어떤 테이블도 바꾸지 않는다."""

        self._purchase(status=PurchaseStatus.REQUESTED)

        self.service.analyze(self.company.id, now=NOW)

        refreshed = self.db.query(Purchase).all()
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(refreshed[0].status, PurchaseStatus.REQUESTED)

    def test_cross_company_data_not_included(self):

        other_company = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="서울",
        )
        self.db.add(other_company)
        self.db.commit()

        purchase = Purchase(
            company_id=other_company.id, order_id=1, supplier_id=1,
            status=PurchaseStatus.REQUESTED, idempotency_key="pur:other",
            total_cost=1000.0, requested_at=NOW,
        )
        self.db.add(purchase)
        self.db.commit()

        items, _envelope = self.service.analyze(self.company.id, now=NOW)
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
