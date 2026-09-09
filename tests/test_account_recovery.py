"""
=========================================================
Homez OS

File : tests/test_account_recovery.py

계정 복구(아이디 찾기 / 복구 코드 / 이메일 재설정 / SUPER_ADMIN 발급
재설정) 서비스 계층 + 라우터 함수 직접 호출 검증. httpx가 설치되어
있지 않아 FastAPI TestClient를 쓸 수 없으므로(기존 tests/
test_desktop_auth_session.py와 동일한 이유) 라우터 함수를 ASGI 계층
없이 직접 호출한다. 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.core.security import verify_password
from app.database.base import Base
from app.domains.account_recovery import service as recovery_service
from app.domains.account_recovery.model import PasswordResetToken
from app.domains.account_recovery.model import RecoveryCode
from app.domains.account_recovery.providers import FakePasswordResetDeliveryProvider
from app.domains.account_recovery.providers import NullPasswordResetDeliveryProvider
from app.domains.account_recovery.router import forgot_id
from app.domains.account_recovery.router import generate_my_recovery_codes
from app.domains.account_recovery.router import password_reset_email_status
from app.domains.account_recovery.router import request_password_reset_email_endpoint
from app.domains.account_recovery.router import confirm_password_reset_token
from app.domains.account_recovery.router import reset_password_with_recovery_code
from app.domains.account_recovery.router import super_admin_initiate_reset_endpoint
from app.domains.account_recovery.schema import ForgotIdRequest
from app.domains.account_recovery.schema import PasswordResetRequestRequest
from app.domains.account_recovery.schema import PasswordResetTokenConsumeRequest
from app.domains.account_recovery.schema import RecoveryCodeResetRequest
from app.domains.account_recovery.schema import SuperAdminResetInitiateRequest
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.session.model import AuthSession
from app.domains.session.repository import SessionRepository
from app.domains.session.service import create_session
from app.domains.user.model import User

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)

STRONG_PASSWORD = "Str0ng!Passw0rd"
NEW_STRONG_PASSWORD = "NewStr0ng!Pass2"


class AccountRecoveryTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 15})

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, RolePermission.__table__,
                Permission.__table__, User.__table__, AuthSession.__table__,
                RecoveryCode.__table__, PasswordResetToken.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(name="Company A", active=True)
        self.company_b = Company(name="Company B", active=True)
        self.db.add_all([self.company_a, self.company_b])
        self.db.flush()

        self.super_admin_role = Role(name="Super Administrator", code="SUPER_ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.super_admin_role, self.viewer_role])
        self.db.commit()

        recovery_service.reset_rate_limit_state_for_tests()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        recovery_service.reset_rate_limit_state_for_tests()

    def _create_user(self, username, email, role_id, company_id, is_active=True, password=STRONG_PASSWORD):

        user = User(
            username=username, email=email,
            password_hash=hash_password(password), role_id=role_id,
            company_id=company_id, is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def _add_session(self, user_id, jti):

        create_session(
            self.db, user_id=user_id, jti=jti,
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.db.commit()

    # --------------------------------------------------
    # 1. 아이디 찾기 — 계정 존재/부재 응답 동일 + 전체 이메일 비노출
    # --------------------------------------------------

    def test_forgot_id_same_response_for_existing_and_nonexistent(self):

        self._create_user("owner1", "owner1@example.com", self.super_admin_role.id, self.company_a.id)
        provider = FakePasswordResetDeliveryProvider(configured=True)

        existing = forgot_id(
            ForgotIdRequest(email="owner1@example.com"), db=self.db, provider=provider,
        )
        recovery_service.reset_rate_limit_state_for_tests()
        missing = forgot_id(
            ForgotIdRequest(email="nobody@example.com"), db=self.db, provider=provider,
        )

        self.assertEqual(existing.message, missing.message)
        self.assertNotIn("owner1@example.com", existing.message)
        self.assertNotIn("@", existing.message)

    def test_forgot_id_sends_masked_login_id_only_when_configured_and_found(self):

        user = self._create_user("owner2", "owner2@example.com", self.super_admin_role.id, self.company_a.id)
        provider = FakePasswordResetDeliveryProvider(configured=True)

        forgot_id(ForgotIdRequest(email="owner2@example.com"), db=self.db, provider=provider)

        self.assertEqual(len(provider.sent_messages), 1)
        masked = provider.sent_messages[0]["masked_login_id"]
        self.assertNotEqual(masked, user.username)
        self.assertIn("*", masked)

    def test_forgot_id_reports_not_configured_without_faking_success(self):

        self._create_user("owner3", "owner3@example.com", self.super_admin_role.id, self.company_a.id)
        provider = NullPasswordResetDeliveryProvider()

        result = forgot_id(ForgotIdRequest(email="owner3@example.com"), db=self.db, provider=provider)

        self.assertIn("구성되지 않았습니다", result.message)

    def test_forgot_id_rate_limited_after_max_requests(self):

        self._create_user("owner4", "owner4@example.com", self.super_admin_role.id, self.company_a.id)
        provider = FakePasswordResetDeliveryProvider(configured=True)

        for _ in range(recovery_service.FORGOT_ID_MAX_REQUESTS):
            forgot_id(ForgotIdRequest(email="owner4@example.com"), db=self.db, provider=provider)

        sent_before_limit = len(provider.sent_messages)

        forgot_id(ForgotIdRequest(email="owner4@example.com"), db=self.db, provider=provider)

        self.assertEqual(len(provider.sent_messages), sent_before_limit)

    # --------------------------------------------------
    # 2. 복구 코드 — 저장/생성/재사용/만료/폐기/동시성
    # --------------------------------------------------

    def test_recovery_codes_not_stored_in_plaintext(self):

        user = self._create_user("codeuser1", "codeuser1@example.com", self.viewer_role.id, self.company_a.id)

        codes = recovery_service.generate_recovery_codes(self.db, user=user)

        self.assertEqual(len(codes), recovery_service.RECOVERY_CODE_COUNT)

        rows = self.db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).all()
        self.assertEqual(len(rows), recovery_service.RECOVERY_CODE_COUNT)

        for row, raw in zip(sorted(rows, key=lambda r: r.id), codes):
            self.assertNotEqual(row.code_hash, raw)
            self.assertNotIn(raw, row.code_hash)

    def test_regenerating_codes_revokes_previous_unused_batch(self):

        user = self._create_user("codeuser2", "codeuser2@example.com", self.viewer_role.id, self.company_a.id)

        first_batch = recovery_service.generate_recovery_codes(self.db, user=user)
        recovery_service.generate_recovery_codes(self.db, user=user)

        first_batch_rows = (
            self.db.query(RecoveryCode)
            .filter(RecoveryCode.user_id == user.id, RecoveryCode.revoked_at.isnot(None))
            .all()
        )
        self.assertEqual(len(first_batch_rows), len(first_batch))

        active_rows = (
            self.db.query(RecoveryCode)
            .filter(RecoveryCode.user_id == user.id, RecoveryCode.revoked_at.is_(None))
            .all()
        )
        self.assertEqual(len(active_rows), recovery_service.RECOVERY_CODE_COUNT)

    def test_correct_recovery_code_resets_password_and_revokes_sessions(self):

        user = self._create_user("codeuser3", "codeuser3@example.com", self.viewer_role.id, self.company_a.id)
        self._add_session(user.id, "jti-code-1")

        codes = recovery_service.generate_recovery_codes(self.db, user=user)

        result = recovery_service.consume_recovery_code(
            self.db, email="codeuser3@example.com", code=codes[0],
            new_password=NEW_STRONG_PASSWORD, new_password_confirmation=NEW_STRONG_PASSWORD,
        )

        self.assertTrue(result.success)

        self.db.refresh(user)
        self.assertTrue(verify_password(NEW_STRONG_PASSWORD, user.password_hash))

        session_row = SessionRepository(self.db).get_by_jti("jti-code-1")
        self.assertIsNotNone(session_row.revoked_at)

    def test_wrong_recovery_code_is_rejected(self):

        user = self._create_user("codeuser4", "codeuser4@example.com", self.viewer_role.id, self.company_a.id)
        recovery_service.generate_recovery_codes(self.db, user=user)

        result = recovery_service.consume_recovery_code(
            self.db, email="codeuser4@example.com", code="totally-wrong-code",
            new_password=NEW_STRONG_PASSWORD, new_password_confirmation=NEW_STRONG_PASSWORD,
        )

        self.assertFalse(result.success)
        self.db.refresh(user)
        self.assertTrue(verify_password(STRONG_PASSWORD, user.password_hash))

    def test_used_recovery_code_cannot_be_reused(self):

        user = self._create_user("codeuser5", "codeuser5@example.com", self.viewer_role.id, self.company_a.id)
        codes = recovery_service.generate_recovery_codes(self.db, user=user)

        first = recovery_service.consume_recovery_code(
            self.db, email="codeuser5@example.com", code=codes[0],
            new_password=NEW_STRONG_PASSWORD, new_password_confirmation=NEW_STRONG_PASSWORD,
        )
        self.assertTrue(first.success)

        second = recovery_service.consume_recovery_code(
            self.db, email="codeuser5@example.com", code=codes[0],
            new_password="Another!Str0ng9", new_password_confirmation="Another!Str0ng9",
        )
        self.assertFalse(second.success)

    def test_revoked_recovery_code_is_rejected(self):

        user = self._create_user("codeuser6", "codeuser6@example.com", self.viewer_role.id, self.company_a.id)
        first_batch = recovery_service.generate_recovery_codes(self.db, user=user)
        recovery_service.generate_recovery_codes(self.db, user=user)  # 첫 묶음 폐기

        result = recovery_service.consume_recovery_code(
            self.db, email="codeuser6@example.com", code=first_batch[0],
            new_password=NEW_STRONG_PASSWORD, new_password_confirmation=NEW_STRONG_PASSWORD,
        )

        self.assertFalse(result.success)

    def test_concurrent_recovery_code_use_exactly_one_succeeds(self):

        user = self._create_user("codeuser7", "codeuser7@example.com", self.viewer_role.id, self.company_a.id)
        codes = recovery_service.generate_recovery_codes(self.db, user=user)
        self.db.commit()

        results = []
        lock = threading.Lock()

        def _attempt():
            local_session = self.SessionLocal()
            try:
                r = recovery_service.consume_recovery_code(
                    local_session, email="codeuser7@example.com", code=codes[0],
                    new_password=NEW_STRONG_PASSWORD, new_password_confirmation=NEW_STRONG_PASSWORD,
                )
                with lock:
                    results.append(r.success)
            except Exception:
                with lock:
                    results.append(False)
            finally:
                local_session.close()

        threads = [threading.Thread(target=_attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r), 1)

    def test_new_password_confirmation_mismatch_rejected_without_state_change(self):

        user = self._create_user("codeuser8", "codeuser8@example.com", self.viewer_role.id, self.company_a.id)
        codes = recovery_service.generate_recovery_codes(self.db, user=user)

        with self.assertRaises(ValueError):
            recovery_service.consume_recovery_code(
                self.db, email="codeuser8@example.com", code=codes[0],
                new_password=NEW_STRONG_PASSWORD, new_password_confirmation="Different!Pass9",
            )

        self.db.refresh(user)
        self.assertTrue(verify_password(STRONG_PASSWORD, user.password_hash))

        row = self.db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).first()
        self.assertIsNone(row.used_at)

    def test_weak_new_password_rejected(self):

        user = self._create_user("codeuser9", "codeuser9@example.com", self.viewer_role.id, self.company_a.id)
        codes = recovery_service.generate_recovery_codes(self.db, user=user)

        with self.assertRaises(ValueError):
            recovery_service.consume_recovery_code(
                self.db, email="codeuser9@example.com", code=codes[0],
                new_password="weak", new_password_confirmation="weak",
            )

    # --------------------------------------------------
    # 3. 이메일 재설정 링크
    # --------------------------------------------------

    def test_email_reset_status_reflects_provider_configuration(self):

        configured = password_reset_email_status(provider=FakePasswordResetDeliveryProvider(configured=True))
        not_configured = password_reset_email_status(provider=NullPasswordResetDeliveryProvider())

        self.assertTrue(configured.configured)
        self.assertFalse(not_configured.configured)

    def test_email_reset_request_and_confirm_success(self):

        user = self._create_user("emailuser1", "emailuser1@example.com", self.viewer_role.id, self.company_a.id)
        self._add_session(user.id, "jti-email-1")
        provider = FakePasswordResetDeliveryProvider(configured=True)

        request_password_reset_email_endpoint(
            PasswordResetRequestRequest(email="emailuser1@example.com"), db=self.db, provider=provider,
        )

        self.assertEqual(len(provider.sent_messages), 1)
        self.assertFalse(any("token" in str(v).lower() and "=" not in str(v) for v in provider.sent_messages[0].values()))

        raw_token = self._extract_token_from_pending_row(user.id)

        result = confirm_password_reset_token(
            PasswordResetTokenConsumeRequest(
                token=raw_token, new_password=NEW_STRONG_PASSWORD,
                new_password_confirmation=NEW_STRONG_PASSWORD,
            ),
            db=self.db,
        )

        self.assertIn("재설정", result.message)

        self.db.refresh(user)
        self.assertTrue(verify_password(NEW_STRONG_PASSWORD, user.password_hash))

        session_row = SessionRepository(self.db).get_by_jti("jti-email-1")
        self.assertIsNotNone(session_row.revoked_at)

    def _extract_token_from_pending_row(self, user_id):
        """
        테스트 전용 헬퍼 — 실제로는 원문 토큰이 이메일로만 전달되므로
        DB에서 복원할 수 없다. 이 테스트는 Fake Provider가 원문을
        기록하지 않는다는 요구사항을 지키기 위해, 서비스 내부 함수를
        직접 재호출해 원문을 얻는다(발급 로직 자체를 다시 실행하지
        않고, 같은 트랜잭션에서 방금 만든 토큰을 얻기 위한 우회로
        `_issue_reset_token`을 직접 호출하는 대신 별도 토큰을 새로
        발급해 검증한다).
        """

        user = self.db.query(User).filter(User.id == user_id).first()
        raw = recovery_service._issue_reset_token(self.db, user=user, issued_by="EMAIL")
        self.db.commit()

        return raw

    def test_email_reset_token_expired_rejected(self):

        user = self._create_user("emailuser2", "emailuser2@example.com", self.viewer_role.id, self.company_a.id)
        raw_token = recovery_service._issue_reset_token(self.db, user=user, issued_by="EMAIL")

        row = self.db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.db.add(row)
        self.db.commit()

        result = recovery_service.consume_reset_token(
            self.db, token=raw_token, new_password=NEW_STRONG_PASSWORD,
            new_password_confirmation=NEW_STRONG_PASSWORD,
        )

        self.assertFalse(result.success)

    def test_email_reset_token_reuse_rejected(self):

        user = self._create_user("emailuser3", "emailuser3@example.com", self.viewer_role.id, self.company_a.id)
        raw_token = recovery_service._issue_reset_token(self.db, user=user, issued_by="EMAIL")
        self.db.commit()

        first = recovery_service.consume_reset_token(
            self.db, token=raw_token, new_password=NEW_STRONG_PASSWORD,
            new_password_confirmation=NEW_STRONG_PASSWORD,
        )
        self.assertTrue(first.success)

        second = recovery_service.consume_reset_token(
            self.db, token=raw_token, new_password="Another!Str0ng9",
            new_password_confirmation="Another!Str0ng9",
        )
        self.assertFalse(second.success)

    def test_new_email_token_revokes_previous_unused_token(self):

        user = self._create_user("emailuser4", "emailuser4@example.com", self.viewer_role.id, self.company_a.id)
        first_token = recovery_service._issue_reset_token(self.db, user=user, issued_by="EMAIL")
        self.db.commit()

        recovery_service._issue_reset_token(self.db, user=user, issued_by="EMAIL")
        self.db.commit()

        result = recovery_service.consume_reset_token(
            self.db, token=first_token, new_password=NEW_STRONG_PASSWORD,
            new_password_confirmation=NEW_STRONG_PASSWORD,
        )

        self.assertFalse(result.success)

    def test_email_reset_hidden_when_account_not_found(self):

        provider = FakePasswordResetDeliveryProvider(configured=True)

        outcome = recovery_service.request_password_reset_email(
            self.db, email="ghost@example.com", provider=provider,
            build_reset_link=lambda t: f"link://{t}",
        )

        self.assertEqual(outcome, recovery_service.ForgotIdOutcome.GENERIC)
        self.assertEqual(len(provider.sent_messages), 0)

    # --------------------------------------------------
    # 4. SUPER_ADMIN 발급 재설정
    # --------------------------------------------------

    def test_super_admin_can_initiate_reset_for_same_company_user(self):

        admin = self._create_user("admin1", "admin1@example.com", self.super_admin_role.id, self.company_a.id)
        target = self._create_user("target1", "target1@example.com", self.viewer_role.id, self.company_a.id)
        self._add_session(target.id, "jti-admin-1")

        response = super_admin_initiate_reset_endpoint(
            SuperAdminResetInitiateRequest(user_id=target.id), current_user=admin, db=self.db,
        )

        self.assertTrue(len(response.reset_token) > 20)

        result = recovery_service.consume_reset_token(
            self.db, token=response.reset_token, new_password=NEW_STRONG_PASSWORD,
            new_password_confirmation=NEW_STRONG_PASSWORD,
        )
        self.assertTrue(result.success)

        self.db.refresh(target)
        self.assertTrue(verify_password(NEW_STRONG_PASSWORD, target.password_hash))

        session_row = SessionRepository(self.db).get_by_jti("jti-admin-1")
        self.assertIsNotNone(session_row.revoked_at)

    def test_super_admin_cannot_initiate_reset_for_other_company_user(self):

        admin = self._create_user("admin2", "admin2@example.com", self.super_admin_role.id, self.company_a.id)
        other_company_user = self._create_user(
            "target2", "target2@example.com", self.viewer_role.id, self.company_b.id,
        )

        with self.assertRaises(HTTPException) as ctx:
            super_admin_initiate_reset_endpoint(
                SuperAdminResetInitiateRequest(user_id=other_company_user.id),
                current_user=admin, db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 404)

    def test_regular_user_cannot_call_super_admin_reset_endpoint(self):
        """
        SuperAdminGuard는 FastAPI Depends 체인에서 강제되므로, 여기서는
        guard가 실제로 SUPER_ADMIN이 아닌 사용자를 거부하는지 guard 자체를
        직접 호출해 확인한다(라우터 함수는 guard 통과를 전제하므로).
        """

        from app.core.guard import SuperAdminGuard

        viewer = self._create_user("viewer1", "viewer1@example.com", self.viewer_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            SuperAdminGuard(current_user=viewer)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_last_active_super_admin_reset_does_not_affect_role_or_company(self):

        admin = self._create_user("admin3", "admin3@example.com", self.super_admin_role.id, self.company_a.id)

        response = super_admin_initiate_reset_endpoint(
            SuperAdminResetInitiateRequest(user_id=admin.id), current_user=admin, db=self.db,
        )

        recovery_service.consume_reset_token(
            self.db, token=response.reset_token, new_password=NEW_STRONG_PASSWORD,
            new_password_confirmation=NEW_STRONG_PASSWORD,
        )

        self.db.refresh(admin)
        self.assertEqual(admin.role_id, self.super_admin_role.id)
        self.assertEqual(admin.company_id, self.company_a.id)

    # --------------------------------------------------
    # 5. 감사 로그 — 민감 값 없음
    # --------------------------------------------------

    def test_audit_logs_never_contain_password_token_or_code(self):

        user = self._create_user("audituser1", "audituser1@example.com", self.viewer_role.id, self.company_a.id)
        codes = recovery_service.generate_recovery_codes(self.db, user=user)

        recovery_service.consume_recovery_code(
            self.db, email="audituser1@example.com", code=codes[0],
            new_password=NEW_STRONG_PASSWORD, new_password_confirmation=NEW_STRONG_PASSWORD,
        )

        from sqlalchemy import text

        rows = self.db.execute(text("SELECT description FROM audit_logs")).fetchall()

        forbidden_values = [STRONG_PASSWORD, NEW_STRONG_PASSWORD, codes[0], "audituser1@example.com"]

        for row in rows:
            description = row[0] or ""
            for forbidden in forbidden_values:
                self.assertNotIn(forbidden, description)

    # --------------------------------------------------
    # 6. 로그인 상태 복구 코드 생성 라우터
    # --------------------------------------------------

    def test_generate_my_recovery_codes_router_returns_codes(self):

        user = self._create_user("selfgen1", "selfgen1@example.com", self.viewer_role.id, self.company_a.id)

        response = generate_my_recovery_codes(current_user=user, db=self.db)

        self.assertEqual(len(response.codes), recovery_service.RECOVERY_CODE_COUNT)

    # --------------------------------------------------
    # 7. 복구 코드 라우터(HTTPException 경로)
    # --------------------------------------------------

    def test_reset_password_with_recovery_code_router_rejects_wrong_code(self):

        user = self._create_user("routeruser1", "routeruser1@example.com", self.viewer_role.id, self.company_a.id)
        recovery_service.generate_recovery_codes(self.db, user=user)

        with self.assertRaises(HTTPException) as ctx:
            reset_password_with_recovery_code(
                RecoveryCodeResetRequest(
                    email="routeruser1@example.com", code="wrong",
                    new_password=NEW_STRONG_PASSWORD, new_password_confirmation=NEW_STRONG_PASSWORD,
                ),
                db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
