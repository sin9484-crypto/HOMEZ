"""
=========================================================
Homez OS

File : tests/test_settlement_difference_analysis.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — SettlementDifferenceAnalysisService.
읽기 전용 분석이 실제 Settlement 상태·금액을 바꾸지 않는지, 차이
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
from app.domains.funding.model import FundingAccount
from app.domains.order.model import Order
from app.domains.settlement.difference_analysis_service import (
    SettlementDifferenceAnalysisService,
)
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.settlement.service import SettlementService

from tests.ai_governance_test_helpers import deactivated_capability

NOW = datetime(2026, 8, 22, 12, 0, 0)


class SettlementDifferenceAnalysisTestCase(unittest.TestCase):

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
                Order.__table__,
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

        self.account = FundingAccount(
            company_id=self.company.id,
        )
        self.db.add(self.account)
        self.db.commit()

        self.service = SettlementDifferenceAnalysisService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _order(self, total_amount=10000.0, created_at=None):

        order = Order(
            company_id=self.company.id, channel_code="FAKE",
            channel_order_id=f"CH-{datetime.utcnow().timestamp()}",
            buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울",
            receiver_zipcode="00000", total_amount=total_amount,
            ordered_at=NOW, created_at=created_at or NOW,
        )
        self.db.add(order)
        self.db.commit()
        return order

    def _settlement(
        self, order_id=None, gross_amount=10000.0, fee_amount=0.0,
        status=SettlementService.STATUS_PENDING, created_at=None,
        updated_at=None, idempotency_key=None,
    ):

        settlement = MarketplaceSettlement(
            company_id=self.company.id, market="FAKE",
            market_order_id=f"MO-{datetime.utcnow().timestamp()}",
            order_id=order_id, account_id=self.account.id,
            gross_amount=gross_amount, fee_amount=fee_amount,
            net_amount=gross_amount - fee_amount, status=status,
            idempotency_key=(
                idempotency_key or f"settle-{datetime.utcnow().timestamp()}"
            ),
            created_at=created_at or NOW,
        )
        self.db.add(settlement)
        self.db.commit()
        if updated_at is not None:
            settlement.updated_at = updated_at
            self.db.commit()
        return settlement

    def test_no_data_yields_no_differences(self):

        differences, envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertEqual(differences, [])
        self.assertEqual(envelope.result_type, "CONFIRMED_DATA")
        self.assertEqual(envelope.decision, "NO_DIFFERENCES_FOUND")

    def test_amount_mismatch_detected(self):

        order = self._order(total_amount=10000.0)
        self._settlement(order_id=order.id, gross_amount=9500.0)

        differences, envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertEqual(len(differences), 1)
        self.assertEqual(
            differences[0].difference_type, "SETTLEMENT_AMOUNT_MISMATCH",
        )
        self.assertEqual(envelope.result_type, "HUMAN_REVIEW_REQUIRED")

    def test_amount_within_tolerance_not_flagged(self):

        order = self._order(total_amount=10000.0)
        self._settlement(order_id=order.id, gross_amount=10000.5)

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)
        self.assertEqual(differences, [])

    def test_already_mismatch_status_not_double_flagged_for_amount(self):

        order = self._order(total_amount=10000.0)
        self._settlement(
            order_id=order.id, gross_amount=9500.0,
            status=SettlementService.STATUS_MISMATCH,
            updated_at=NOW - timedelta(hours=1),
        )

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)
        self.assertFalse(
            any(d.difference_type == "SETTLEMENT_AMOUNT_MISMATCH" for d in differences),
        )

    def test_pending_stale_detected(self):

        self._settlement(created_at=NOW - timedelta(hours=400))

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(d.difference_type == "SETTLEMENT_PENDING_STALE" for d in differences),
        )

    def test_pending_fresh_not_flagged(self):

        self._settlement(created_at=NOW - timedelta(hours=1))

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)
        self.assertEqual(differences, [])

    def test_mismatch_unresolved_detected(self):

        self._settlement(
            status=SettlementService.STATUS_MISMATCH,
            created_at=NOW - timedelta(hours=100),
            updated_at=NOW - timedelta(hours=100),
        )

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(
                d.difference_type == "SETTLEMENT_MISMATCH_UNRESOLVED"
                for d in differences
            ),
        )

    def test_held_stale_detected(self):

        self._settlement(
            status=SettlementService.STATUS_HELD,
            created_at=NOW - timedelta(hours=100),
            updated_at=NOW - timedelta(hours=100),
        )

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertTrue(
            any(d.difference_type == "SETTLEMENT_HELD_STALE" for d in differences),
        )

    def test_estop_marks_differences_not_execution_allowed_but_still_detects(self):

        self._settlement(created_at=NOW - timedelta(hours=400))
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)

        self.assertEqual(len(differences), 1)
        self.assertFalse(differences[0].execution_allowed)

    def test_create_review_action_skipped_when_execution_not_allowed(self):

        self._settlement(created_at=NOW - timedelta(hours=400))
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)
        action = self.service.create_review_action(
            self.company.id, differences[0], idempotency_key="sda:1",
        )
        self.assertIsNone(action)

    def test_create_review_action_creates_proposed_action(self):

        self._settlement(created_at=NOW - timedelta(hours=400))

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)
        action = self.service.create_review_action(
            self.company.id, differences[0], idempotency_key="sda:2",
        )

        self.assertIsNotNone(action)
        self.assertEqual(action.status, "REVIEW_REQUIRED")
        self.assertEqual(action.action_type, "SETTLEMENT_DIFFERENCE_REVIEW")

    def test_blocks_when_settlement_capability_deactivated(self):

        with deactivated_capability("SETTLEMENT"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.analyze(self.company.id, now=NOW)

    def test_analyze_does_not_write_to_settlement_status(self):
        """분석은 읽기 전용이다 — 상태를 절대 바꾸지 않는다."""

        settlement = self._settlement(created_at=NOW - timedelta(hours=400))

        self.service.analyze(self.company.id, now=NOW)

        refreshed = self.db.get(MarketplaceSettlement, settlement.id)
        self.assertEqual(refreshed.status, SettlementService.STATUS_PENDING)

    def test_cross_company_settlement_not_included(self):

        other_company = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="서울",
        )
        self.db.add(other_company)
        self.db.commit()
        other_account = FundingAccount(company_id=other_company.id)
        self.db.add(other_account)
        self.db.commit()

        other_settlement = MarketplaceSettlement(
            company_id=other_company.id, market="FAKE",
            market_order_id="MO-OTHER", account_id=other_account.id,
            gross_amount=10000.0, fee_amount=0.0, net_amount=10000.0,
            status=SettlementService.STATUS_PENDING,
            idempotency_key="settle-other",
            created_at=NOW - timedelta(hours=400),
        )
        self.db.add(other_settlement)
        self.db.commit()

        differences, _envelope = self.service.analyze(self.company.id, now=NOW)
        self.assertEqual(differences, [])


if __name__ == "__main__":
    unittest.main()
