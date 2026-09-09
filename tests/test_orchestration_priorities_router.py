"""
=========================================================
Homez OS

File : tests/test_orchestration_priorities_router.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — GET /orchestration/priorities.
httpx 미설치로 라우터 함수를 직접 호출한다. 실제 homez.db는 전혀
접근하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy import text as _sa_text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.ai_governance.model import ProposedAction
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.company.model import Company
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.orchestration.router import get_operations_priorities
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.purchase.model import Purchase
from app.domains.return_order.model import ReturnOrder
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.shipment.model import Shipment

NOW = datetime(2026, 8, 22, 12, 0, 0)


class _FakeUser:

    def __init__(self, user_id, company_id):
        self.id = user_id
        self.company_id = company_id


class OrchestrationPrioritiesRouterTestCase(unittest.TestCase):

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

        self.user = _FakeUser(1, self.company.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_returns_empty_items_when_no_data(self):

        result = get_operations_priorities(
            current_user=self.user, db=self.db,
        )

        self.assertEqual(result["items"], [])
        self.assertEqual(result["ai_result"]["decision"], "NO_PRIORITIES_FOUND")

    def test_returns_priority_item_for_product_input_required(self):

        candidate = ProductCandidate(
            candidate_key="opr:1", source_type="TREND",
            source_reference="ref-1", market="FAKE",
            product_name="상품1", status=CandidateStatus.DISCOVERED,
            owner_company_id=self.company.id, visibility="COMPANY",
        )
        self.db.add(candidate)
        self.db.commit()

        result = get_operations_priorities(
            current_user=self.user, db=self.db,
        )

        self.assertTrue(
            any(i["category"] == "PRODUCT_INPUT_REQUIRED" for i in result["items"]),
        )


if __name__ == "__main__":
    unittest.main()
