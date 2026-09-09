"""
=========================================================
Homez OS

File : tests/test_ai_governance_proposed_action_router.py

Gate AI-F(2026-08-22 CTO 지시) 검증 — ProposedAction Router(목록/
상세/승인/반려). httpx 미설치로 TestClient를 쓸 수 없어 라우터
함수를 직접 호출한다. 실제 homez.db는 전혀 접근하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.exceptions import UnauthorizedException
from app.core.recent_auth import issue_recent_auth_token
from app.core.recent_auth import reset_recent_auth_state_for_tests
from app.database.base import Base
from app.domains.ai_governance.model import ProposedAction
from app.domains.ai_governance.proposed_action_service import (
    ProposedActionService,
)
from app.domains.ai_governance.router import approve_proposed_action
from app.domains.ai_governance.router import get_proposed_action
from app.domains.ai_governance.router import list_proposed_actions
from app.domains.ai_governance.router import reject_proposed_action
from app.domains.ai_governance.schema import ApproveProposedActionRequest
from app.domains.ai_governance.schema import RejectProposedActionRequest
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.service import SafetyService
from app.domains.company.model import Company


_AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class _FakeUser:

    def __init__(self, user_id, company_id):
        self.id = user_id
        self.company_id = company_id


class ProposedActionRouterTestCase(unittest.TestCase):

    def setUp(self):

        reset_recent_auth_state_for_tests()
        self.addCleanup(reset_recent_auth_state_for_tests)

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProposedAction.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.execute(text(_AUDIT_LOGS_DDL))

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

        self.service = ProposedActionService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_action(
        self, company_id=None, action_type="ORDER_EXCEPTION_REVIEW",
        idempotency_key="pa-router:1",
    ):

        action, _dup = self.service.create(
            company_id=company_id or self.company_a.id,
            capability_code="ORDER_SHIPMENT_RETURN",
            action_type=action_type,
            target_entity="order:1",
            proposed_payload={"note": "테스트"},
            reason="테스트 사유",
            evidence=[],
            risk_level="LOW",
            approval_required=True,
            idempotency_key=idempotency_key,
        )
        return action

    def test_list_returns_only_own_company_actions(self):

        self._create_action(company_id=self.company_a.id, idempotency_key="pa-r:a")
        self._create_action(company_id=self.company_b.id, idempotency_key="pa-r:b")

        results = list_proposed_actions(
            status_filter=None, current_user=self.user_a, db=self.db,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].company_id, self.company_a.id)

    def test_list_filters_by_status(self):

        pending = self._create_action(idempotency_key="pa-r:pending")
        approved = self._create_action(idempotency_key="pa-r:approved")
        self.service.approve(
            approved.id, self.company_a.id, approved_by=self.user_a.id,
        )

        results = list_proposed_actions(
            status_filter="APPROVED", current_user=self.user_a, db=self.db,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, approved.id)

    def test_get_other_company_action_returns_not_found(self):

        action = self._create_action(company_id=self.company_b.id)

        with self.assertRaises(NotFoundException):
            get_proposed_action(
                action.id, current_user=self.user_a, db=self.db,
            )

    def test_approve_uses_current_user_not_client_supplied_id(self):

        action = self._create_action()

        result = approve_proposed_action(
            action.id, ApproveProposedActionRequest(),
            current_user=self.user_a, db=self.db,
            recent_auth_token=None,
        )
        self.assertEqual(result.decided_by, self.user_a.id)

    def test_company_b_cannot_approve_company_a_action(self):

        action = self._create_action(company_id=self.company_a.id)

        with self.assertRaises(NotFoundException):
            approve_proposed_action(
                action.id, ApproveProposedActionRequest(),
                current_user=self.user_b, db=self.db,
                recent_auth_token=None,
            )

    def test_company_b_cannot_reject_company_a_action(self):

        action = self._create_action(company_id=self.company_a.id)

        with self.assertRaises(NotFoundException):
            reject_proposed_action(
                action.id, RejectProposedActionRequest(reason="테스트"),
                current_user=self.user_b, db=self.db,
            )

    def test_reject_records_reason(self):

        action = self._create_action()

        result = reject_proposed_action(
            action.id, RejectProposedActionRequest(reason="근거 부족"),
            current_user=self.user_a, db=self.db,
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.decision_reason, "근거 부족")

    def test_approve_high_risk_action_without_recent_auth_is_rejected(self):

        action = self._create_action(
            action_type="PRICE_CHANGE_PROPOSAL",
            idempotency_key="pa-r:high-risk",
        )

        with self.assertRaises(UnauthorizedException):
            approve_proposed_action(
                action.id, ApproveProposedActionRequest(),
                current_user=self.user_a, db=self.db,
                recent_auth_token=None,
            )

    def test_approve_high_risk_action_with_recent_auth_succeeds(self):

        action = self._create_action(
            action_type="PRICE_CHANGE_PROPOSAL",
            idempotency_key="pa-r:high-risk-ok",
        )
        token, _expires = issue_recent_auth_token(self.user_a.id)

        result = approve_proposed_action(
            action.id,
            ApproveProposedActionRequest(),
            current_user=self.user_a, db=self.db,
            recent_auth_token=token,
        )
        self.assertEqual(result.status, "APPROVED")

    def test_approve_blocked_during_emergency_stop(self):

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )
        action = self._create_action(idempotency_key="pa-r:estop")

        with self.assertRaises(BadRequestException):
            approve_proposed_action(
                action.id, ApproveProposedActionRequest(),
                current_user=self.user_a, db=self.db,
                recent_auth_token=None,
            )

    def test_list_is_not_blocked_during_emergency_stop(self):
        """조회는 EStop과 무관해야 한다."""

        SafetyService(self.db).activate_emergency_stop(
            "테스트 정지", set_by=1, is_admin=True,
        )
        self._create_action(idempotency_key="pa-r:estop-list")

        results = list_proposed_actions(
            status_filter=None, current_user=self.user_a, db=self.db,
        )
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
