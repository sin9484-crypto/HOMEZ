"""
=========================================================
Homez OS

File : tests/test_settlement_difference_review_router.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — GET /settlements/difference-
analysis, POST /settlements/difference-analysis/review-actions.
httpx 미설치로 라우터 함수를 직접 호출한다. 실제 homez.db는 전혀
접근하지 않는다.
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

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.ai_governance.model import ProposedAction
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company
from app.domains.funding.model import FundingAccount
from app.domains.order.model import Order
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.settlement.router import analyze_settlement_differences
from app.domains.settlement.router import (
    create_settlement_difference_review_action,
)
from app.domains.settlement.schema import (
    SettlementDifferenceReviewActionCreate,
)
from app.domains.settlement.service import SettlementService

NOW = datetime(2026, 8, 22, 12, 0, 0)


class _FakeUser:

    def __init__(self, user_id, company_id):
        self.id = user_id
        self.company_id = company_id


class SettlementDifferenceReviewRouterTestCase(unittest.TestCase):

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

        self.company_a = Company(
            name="회사 A", business_number="111-11-11111",
            ceo="대표A", phone="02-000-0001",
            email="a@example.com", address="서울",
        )
        self.company_b = Company(
            name="회사 B", business_number="222-22-22222",
            ceo="대표B", phone="02-000-0002",
            email="b@example.com", address="서울",
        )
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

        self.account_a = FundingAccount(company_id=self.company_a.id)
        self.account_b = FundingAccount(company_id=self.company_b.id)
        self.db.add_all([self.account_a, self.account_b])
        self.db.commit()

        self.user_a = _FakeUser(1, self.company_a.id)
        self.user_b = _FakeUser(2, self.company_b.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _stale_pending_settlement(self, company_id, account_id, key):

        settlement = MarketplaceSettlement(
            company_id=company_id, market="FAKE",
            market_order_id=f"MO-{key}", account_id=account_id,
            gross_amount=10000.0, fee_amount=0.0, net_amount=10000.0,
            status=SettlementService.STATUS_PENDING,
            idempotency_key=key,
            created_at=datetime.utcnow() - timedelta(hours=400),
        )
        self.db.add(settlement)
        self.db.commit()
        return settlement

    def test_analyze_returns_differences_for_own_company_only(self):

        self._stale_pending_settlement(
            self.company_a.id, self.account_a.id, "settle:a",
        )
        self._stale_pending_settlement(
            self.company_b.id, self.account_b.id, "settle:b",
        )

        result = analyze_settlement_differences(
            current_user=self.user_a, db=self.db,
        )

        self.assertEqual(len(result.differences), 1)

    def test_create_review_action_succeeds_for_matching_difference(self):

        self._stale_pending_settlement(
            self.company_a.id, self.account_a.id, "settle:c",
        )
        analysis = analyze_settlement_differences(
            current_user=self.user_a, db=self.db,
        )
        target = analysis.differences[0]

        result = create_settlement_difference_review_action(
            SettlementDifferenceReviewActionCreate(
                difference_type=target.difference_type,
                target_entity=target.target_entity,
                idempotency_key="sdr:1",
            ),
            current_user=self.user_a, db=self.db,
        )

        self.assertIsNotNone(result.proposed_action)
        self.assertEqual(result.proposed_action.company_id, self.company_a.id)

    def test_create_review_action_rejects_stale_nonexistent_difference(self):

        with self.assertRaises(NotFoundException):
            create_settlement_difference_review_action(
                SettlementDifferenceReviewActionCreate(
                    difference_type="SETTLEMENT_PENDING_STALE",
                    target_entity="settlement:999999",
                    idempotency_key="sdr:2",
                ),
                current_user=self.user_a, db=self.db,
            )

    def test_create_review_action_blocked_during_emergency_stop(self):

        self._stale_pending_settlement(
            self.company_a.id, self.account_a.id, "settle:d",
        )
        analysis = analyze_settlement_differences(
            current_user=self.user_a, db=self.db,
        )
        target = analysis.differences[0]

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            create_settlement_difference_review_action(
                SettlementDifferenceReviewActionCreate(
                    difference_type=target.difference_type,
                    target_entity=target.target_entity,
                    idempotency_key="sdr:3",
                ),
                current_user=self.user_a, db=self.db,
            )

    def test_analyze_not_blocked_during_emergency_stop(self):
        """조회는 EStop과 무관해야 한다."""

        self._stale_pending_settlement(
            self.company_a.id, self.account_a.id, "settle:e",
        )
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        result = analyze_settlement_differences(
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(len(result.differences), 1)


if __name__ == "__main__":
    unittest.main()
