"""
=========================================================
Homez OS

File : tests/test_order_exception_review_router.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — GET /orders/exception-analysis,
POST /orders/exception-analysis/review-actions. httpx 미설치로 라우터
함수를 직접 호출한다. 실제 homez.db는 전혀 접근하지 않는다.
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
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.router import analyze_order_exceptions
from app.domains.order.router import create_order_exception_review_action
from app.domains.order.schema import OrderExceptionReviewActionCreate
from app.domains.return_order.model import ReturnOrder
from app.domains.shipment.model import Shipment

NOW = datetime(2026, 8, 22, 12, 0, 0)


class _FakeUser:

    def __init__(self, user_id, company_id):
        self.id = user_id
        self.company_id = company_id


class OrderExceptionReviewRouterTestCase(unittest.TestCase):

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

        self.user_a = _FakeUser(1, self.company_a.id)
        self.user_b = _FakeUser(2, self.company_b.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _stale_pending_order(self, company_id):

        order = Order(
            company_id=company_id, channel_code="FAKE",
            channel_order_id=f"CH-{datetime.utcnow().timestamp()}",
            buyer_name="구매자", receiver_name="수취인",
            receiver_phone="010-0000-0000", receiver_address="서울",
            receiver_zipcode="00000", ordered_at=NOW,
            created_at=NOW - timedelta(hours=48),
        )
        self.db.add(order)
        self.db.commit()
        return order

    def test_analyze_returns_exceptions_for_own_company_only(self):

        self._stale_pending_order(self.company_a.id)
        self._stale_pending_order(self.company_b.id)

        result = analyze_order_exceptions(
            current_user=self.user_a, db=self.db,
        )

        self.assertEqual(len(result.exceptions), 1)

    def test_create_review_action_succeeds_for_matching_exception(self):

        self._stale_pending_order(self.company_a.id)
        analysis = analyze_order_exceptions(
            current_user=self.user_a, db=self.db,
        )
        target = analysis.exceptions[0]

        result = create_order_exception_review_action(
            OrderExceptionReviewActionCreate(
                exception_type=target.exception_type,
                target_entity=target.target_entity,
                idempotency_key="oer:1",
            ),
            current_user=self.user_a, db=self.db,
        )

        self.assertIsNotNone(result.proposed_action)
        self.assertEqual(result.proposed_action.company_id, self.company_a.id)

    def test_create_review_action_rejects_stale_nonexistent_exception(self):

        with self.assertRaises(NotFoundException):
            create_order_exception_review_action(
                OrderExceptionReviewActionCreate(
                    exception_type="PENDING_ORDER_STALE",
                    target_entity="order:999999",
                    idempotency_key="oer:2",
                ),
                current_user=self.user_a, db=self.db,
            )

    def test_create_review_action_blocked_during_emergency_stop(self):

        self._stale_pending_order(self.company_a.id)
        analysis = analyze_order_exceptions(
            current_user=self.user_a, db=self.db,
        )
        target = analysis.exceptions[0]

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        with self.assertRaises(BadRequestException):
            create_order_exception_review_action(
                OrderExceptionReviewActionCreate(
                    exception_type=target.exception_type,
                    target_entity=target.target_entity,
                    idempotency_key="oer:3",
                ),
                current_user=self.user_a, db=self.db,
            )

    def test_analyze_not_blocked_during_emergency_stop(self):
        """조회는 EStop과 무관해야 한다."""

        self._stale_pending_order(self.company_a.id)
        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )

        result = analyze_order_exceptions(
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(len(result.exceptions), 1)

    def test_company_b_cannot_create_action_for_company_a_exception_type(self):
        """company_b가 같은 exception_type/target_entity 문자열을
        보내도, company_b의 실제 analyze() 결과에는 그 예외가 없으므로
        404다(문자열만으로 타사 예외를 재사용할 수 없다)."""

        order_a = self._stale_pending_order(self.company_a.id)

        with self.assertRaises(NotFoundException):
            create_order_exception_review_action(
                OrderExceptionReviewActionCreate(
                    exception_type="PENDING_ORDER_STALE",
                    target_entity=f"order:{order_a.id}",
                    idempotency_key="oer:4",
                ),
                current_user=self.user_b, db=self.db,
            )


if __name__ == "__main__":
    unittest.main()
