"""
=========================================================
Homez OS

File : tests/test_ai_governance_proposed_action.py

AG-4(2026-08-21 CTO 후속 지시) 검증 — ProposedAction 스키마·생성·
승인·거절·만료·fingerprint 무효화·실행 기록. 반드시 MigrationRunner로
실제 migrations/*.sql을 순서대로 적용해 DB를 만든다(이 코드베이스
전역 컨벤션, test_channel_policy_engine.py와 동일 원칙).
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.migration_runner import MigrationRunner
from app.domains.ai_governance.constants import ProposedActionStatus
from app.domains.ai_governance.model import ProposedAction
from app.domains.ai_governance.proposed_action_service import (
    ProposedActionService,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = Path(REPO_ROOT) / "migrations"


class ProposedActionTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)

        conn = sqlite3.connect(str(self.db_path))
        try:
            runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)
            runner.ensure_history_table(conn)
            runner.apply_pending(conn)
        finally:
            conn.close()

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()
        self.service = ProposedActionService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            os.remove(self.db_path)

    def _create(self, **overrides):

        defaults = dict(
            company_id=1,
            capability_code="PRICING_INVENTORY",
            action_type="PRICE_CHANGE_REQUEST",
            target_entity="marketplace_listing:1",
            proposed_payload={"requested_sale_price": "12000"},
            reason="목표 마진 미달로 인상 제안",
            evidence=["margin_calculator:2026-08-21"],
            risk_level="LOW",
            approval_required=True,
            idempotency_key="pa:1",
        )
        defaults.update(overrides)
        return self.service.create(**defaults)

    # --------------------------------------------------
    # 1. 생성 — AI는 DRAFT/REVIEW_REQUIRED까지만
    # --------------------------------------------------

    def test_create_defaults_to_review_required(self):

        action, dup = self._create()
        self.assertFalse(dup)
        self.assertEqual(action.status, ProposedActionStatus.REVIEW_REQUIRED)
        self.assertIsNone(action.decided_by)
        self.assertIsNone(action.created_by)

    def test_create_rejects_non_ai_creatable_initial_status(self):
        """AI가 스스로 APPROVED/EXECUTED 등을 자칭해 생성하려 하면
        구조적으로 차단된다."""

        with self.assertRaises(BadRequestException):
            self._create(
                status=ProposedActionStatus.APPROVED,
                idempotency_key="pa:bad-status",
            )

    def test_create_is_idempotent_by_key(self):

        first, first_dup = self._create()
        second, second_dup = self._create()

        self.assertFalse(first_dup)
        self.assertTrue(second_dup)
        self.assertEqual(first.id, second.id)

    def test_input_fingerprint_reflects_proposed_payload(self):

        a, _ = self._create(idempotency_key="pa:fp-a")
        b, _ = self._create(
            idempotency_key="pa:fp-b",
            proposed_payload={"requested_sale_price": "99999"},
        )
        self.assertNotEqual(a.input_fingerprint, b.input_fingerprint)

    # --------------------------------------------------
    # 2. 승인 — 사람만
    # --------------------------------------------------

    def test_approve_transitions_to_approved_with_decider_recorded(self):

        action, _ = self._create()
        approved = self.service.approve(
            action.id, action.company_id, approved_by=99,
        )
        self.assertEqual(approved.status, ProposedActionStatus.APPROVED)
        self.assertEqual(approved.decided_by, 99)
        self.assertIsNotNone(approved.decided_at)

    def test_approve_rejects_already_decided_action(self):

        action, _ = self._create()
        self.service.approve(action.id, action.company_id, approved_by=1)

        with self.assertRaises(BadRequestException):
            self.service.approve(action.id, action.company_id, approved_by=2)

    def test_approve_rejects_expired_action_and_marks_expired(self):

        action, _ = self._create(
            idempotency_key="pa:expiring",
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )

        with self.assertRaises(ConflictException):
            self.service.approve(action.id, action.company_id, approved_by=1)

        row = self.db.get(ProposedAction, action.id)
        self.assertEqual(row.status, ProposedActionStatus.EXPIRED)

    def test_approve_invalidates_when_target_data_changed_since_creation(self):
        """대상 데이터가 생성 시점 이후 바뀌면(fingerprint 불일치)
        승인 자체가 자동으로 거부되고 INVALIDATED로 전이된다."""

        action, _ = self._create(idempotency_key="pa:refingerprint")

        with self.assertRaises(ConflictException):
            self.service.approve(
                action.id, action.company_id, approved_by=1,
                current_payload_for_refingerprint={
                    "requested_sale_price": "999999",
                },
            )

        row = self.db.get(ProposedAction, action.id)
        self.assertEqual(row.status, ProposedActionStatus.INVALIDATED)

    def test_approve_high_risk_action_type_requires_recent_auth(self):
        """Gate AI-F(2026-08-22) — PRICE_CHANGE_PROPOSAL 등 고위험
        action_type은 recent_auth_token 없이는 승인 자체가 거부된다."""

        from app.core.exceptions import UnauthorizedException

        action, _ = self._create(
            idempotency_key="pa:high-risk",
            action_type="PRICE_CHANGE_PROPOSAL",
        )

        with self.assertRaises(UnauthorizedException):
            self.service.approve(action.id, action.company_id, approved_by=1)

        row = self.db.get(ProposedAction, action.id)
        self.assertEqual(row.status, ProposedActionStatus.REVIEW_REQUIRED)

    def test_approve_high_risk_action_type_succeeds_with_valid_recent_auth(self):

        from app.core.recent_auth import issue_recent_auth_token
        from app.core.recent_auth import reset_recent_auth_state_for_tests

        self.addCleanup(reset_recent_auth_state_for_tests)
        reset_recent_auth_state_for_tests()

        action, _ = self._create(
            idempotency_key="pa:high-risk-ok",
            action_type="PRICE_CHANGE_PROPOSAL",
        )
        token, _expires = issue_recent_auth_token(1)

        approved = self.service.approve(
            action.id, action.company_id, approved_by=1,
            recent_auth_token=token,
        )
        self.assertEqual(approved.status, ProposedActionStatus.APPROVED)

    def test_approve_low_risk_action_type_does_not_require_recent_auth(self):

        action, _ = self._create(
            idempotency_key="pa:low-risk",
            action_type="ORDER_EXCEPTION_REVIEW",
        )

        approved = self.service.approve(
            action.id, action.company_id, approved_by=1,
        )
        self.assertEqual(approved.status, ProposedActionStatus.APPROVED)

    def test_approve_succeeds_when_refingerprint_matches_unchanged_data(self):

        action, _ = self._create(idempotency_key="pa:refingerprint-match")

        approved = self.service.approve(
            action.id, action.company_id, approved_by=1,
            current_payload_for_refingerprint={
                "requested_sale_price": "12000",
            },
        )
        self.assertEqual(approved.status, ProposedActionStatus.APPROVED)

    # --------------------------------------------------
    # 3. 거절
    # --------------------------------------------------

    def test_reject_transitions_to_rejected_with_reason(self):

        action, _ = self._create()
        rejected = self.service.reject(
            action.id, action.company_id, rejected_by=5, reason="근거 부족",
        )
        self.assertEqual(rejected.status, ProposedActionStatus.REJECTED)
        self.assertEqual(rejected.decision_reason, "근거 부족")

    # --------------------------------------------------
    # 4. 실행 기록 — 승인된 것만, 사후 기록일 뿐 실행 자체는 아님
    # --------------------------------------------------

    def test_mark_executed_requires_prior_approval(self):

        action, _ = self._create()

        with self.assertRaises(ConflictException):
            self.service.mark_executed(
                action.id, action.company_id,
                executed_reference="price_change_request:1",
            )

    def test_mark_executed_after_approval_records_reference(self):

        action, _ = self._create()
        self.service.approve(action.id, action.company_id, approved_by=1)

        executed = self.service.mark_executed(
            action.id, action.company_id,
            executed_reference="price_change_request:1",
        )
        self.assertEqual(executed.status, ProposedActionStatus.EXECUTED)
        self.assertEqual(
            executed.executed_reference, "price_change_request:1",
        )
        self.assertIsNotNone(executed.executed_at)

    # --------------------------------------------------
    # 5. 회사 격리
    # --------------------------------------------------

    def test_actions_are_isolated_per_company(self):

        action_a, _ = self._create(company_id=1, idempotency_key="pa:iso")
        # 다른 회사가 같은 idempotency_key를 독립적으로 쓸 수 있다.
        action_b, dup = self._create(company_id=2, idempotency_key="pa:iso")

        self.assertFalse(dup)
        self.assertNotEqual(action_a.id, action_b.id)

        with self.assertRaises(Exception):
            # company_id가 다르면 조회되지 않아야 한다(approve가
            # NotFoundException을 던진다).
            self.service.approve(action_a.id, 2, approved_by=1)

    def test_list_for_company_only_returns_own_rows(self):

        self._create(company_id=1, idempotency_key="pa:list-a")
        self._create(company_id=2, idempotency_key="pa:list-b")

        rows = self.service.list_for_company(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].company_id, 1)


class AuditLoggingTestCase(ProposedActionTestCase):
    """2026-08-24 Section 2 — ProposedActionService의 생성·승인·거절·
    실행기록이 실제로 audit_logs에 남는지, 멱등 생성이 중복 기록을
    만들지 않는지, 회사 간 격리가 유지되는지 검증한다."""

    def _audit_rows(self, action: str | None = None):

        from sqlalchemy import text

        sql = (
            "SELECT company_id, user_id, action, entity, entity_id, "
            "description FROM audit_logs"
        )
        params = {}
        if action is not None:
            sql += " WHERE action = :action"
            params["action"] = action
        return self.db.execute(text(sql), params).fetchall()

    def test_create_writes_audit_log(self):

        action, _ = self._create(idempotency_key="pa:audit-1")

        rows = self._audit_rows("AI_PROPOSED_ACTION_CREATED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].company_id, action.company_id)
        self.assertEqual(rows[0].entity, "ai_proposed_action")
        self.assertEqual(rows[0].entity_id, str(action.id))

    def test_idempotent_create_does_not_duplicate_audit_log(self):

        self._create(idempotency_key="pa:audit-dup")
        self._create(idempotency_key="pa:audit-dup")

        rows = self._audit_rows("AI_PROPOSED_ACTION_CREATED")
        self.assertEqual(
            len(rows), 1,
            "멱등 재호출(기존 반환)인데 감사로그가 중복 기록됨",
        )

    def test_approve_writes_audit_log(self):

        action, _ = self._create(idempotency_key="pa:audit-approve")
        self.service.approve(action.id, action.company_id, approved_by=42)

        rows = self._audit_rows("AI_PROPOSED_ACTION_APPROVED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, 42)
        self.assertEqual(rows[0].entity_id, str(action.id))

    def test_reject_writes_audit_log(self):

        action, _ = self._create(idempotency_key="pa:audit-reject")
        self.service.reject(
            action.id, action.company_id, rejected_by=7, reason="근거 부족",
        )

        rows = self._audit_rows("AI_PROPOSED_ACTION_REJECTED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, 7)
        self.assertIn("근거 부족", rows[0].description)

    def test_mark_executed_writes_audit_log_with_approver_as_actor(self):

        action, _ = self._create(idempotency_key="pa:audit-exec")
        self.service.approve(action.id, action.company_id, approved_by=99)
        self.service.mark_executed(
            action.id, action.company_id,
            executed_reference="price_change_request:1",
        )

        rows = self._audit_rows("AI_PROPOSED_ACTION_EXECUTED")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, 99)

    def test_audit_log_company_isolation(self):

        action_a, _ = self._create(company_id=1, idempotency_key="pa:audit-iso")
        action_b, _ = self._create(company_id=2, idempotency_key="pa:audit-iso")

        rows = self._audit_rows("AI_PROPOSED_ACTION_CREATED")
        by_entity = {r.entity_id: r.company_id for r in rows}
        self.assertEqual(by_entity[str(action_a.id)], 1)
        self.assertEqual(by_entity[str(action_b.id)], 2)

    def test_audit_log_description_has_no_sensitive_markers(self):

        self._create(idempotency_key="pa:audit-sensitive")

        rows = self._audit_rows("AI_PROPOSED_ACTION_CREATED")
        description = rows[0].description.lower()
        for forbidden in ("password", "card", "secret", "token", "api_key"):
            self.assertNotIn(forbidden, description)


if __name__ == "__main__":
    unittest.main()
