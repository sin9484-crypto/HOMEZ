"""
=========================================================
Homez OS

File : tests/test_account_registration_optional_invite.py

2026-08-03: 초대 코드를 선택 입력으로 바꾼 변경(Gate 2)에 대한 서비스
계층 테스트. httpx가 설치되어 있지 않아 FastAPI TestClient를 쓸 수
없으므로 서비스 함수를 직접 호출한다. 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.core.security import sha256
from app.database.base import Base
from app.domains.account_registration import service as reg_service
from app.domains.account_registration.constants import (
    NEW_PERMISSION_CODES,
    RegistrationStatus,
)
from app.domains.account_registration.model import InvitationCode
from app.domains.account_registration.model import UserRegistrationRequest
from app.domains.account_registration.schema import RegisterRequest
from app.domains.auth.service import AuthService
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.session.model import AuthSession
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


class OptionalInvitationRegistrationTestCase(unittest.TestCase):

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
                UserRegistrationRequest.__table__, InvitationCode.__table__,
            ],
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(name="Company A", active=True)
        self.db.add(self.company_a)
        self.db.flush()

        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.db.add_all([self.viewer_role, self.admin_role])
        self.db.flush()

        for code in NEW_PERMISSION_CODES:
            self.db.add(Permission(name=code, code=code, active=True))
        self.db.commit()

        reg_service.reset_rate_limit_state_for_tests()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        reg_service.reset_rate_limit_state_for_tests()

    def _create_user(self, username, role_id, company_id, is_active=True, password=STRONG_PASSWORD):

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(password), role_id=role_id,
            company_id=company_id, is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def _create_invitation_row(self, company_id, created_by_user_id, max_role_code="VIEWER", max_uses=1, expired=False, revoked=False):

        raw_code = "test-invite-" + max_role_code.lower()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        row = InvitationCode(
            company_id=company_id, code_hash=sha256(raw_code), created_by_user_id=created_by_user_id,
            max_role_code=max_role_code, created_at=now,
            expires_at=now - timedelta(hours=1) if expired else now + timedelta(days=1),
            max_uses=max_uses, used_count=0,
            revoked_at=now if revoked else None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)

        return row, raw_code

    def _register(self, email="newuser@example.com", display_name="새 사용자", invitation_code=None, password=STRONG_PASSWORD):

        return reg_service.register(
            self.db_path, email=email, display_name=display_name,
            password=password, password_confirmation=password,
            invitation_code=invitation_code,
        )

    # --------------------------------------------------
    # 스키마 계층 — 선택 입력 정규화
    # --------------------------------------------------

    def test_schema_accepts_missing_invitation_code(self):

        req = RegisterRequest(
            email="a@example.com", display_name="A",
            password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
        )
        self.assertIsNone(req.invitation_code)

    def test_schema_normalizes_whitespace_only_code_to_none(self):

        req = RegisterRequest(
            email="a@example.com", display_name="A",
            password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
            invitation_code="   ",
        )
        self.assertIsNone(req.invitation_code)

    def test_schema_keeps_real_code_trimmed(self):

        req = RegisterRequest(
            email="a@example.com", display_name="A",
            password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
            invitation_code="  real-code  ",
        )
        self.assertEqual(req.invitation_code, "real-code")

    # --------------------------------------------------
    # 코드 없이 가입 — 단일 활성 회사 자동 배정
    # --------------------------------------------------

    def test_register_without_code_succeeds_with_single_active_company(self):

        result = self._register(email="nocode@example.com", invitation_code=None)

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.SUCCESS)

        row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self.assertEqual(row.status, RegistrationStatus.PENDING_APPROVAL.value)
        self.assertEqual(row.company_id, self.company_a.id)
        self.assertIsNone(row.invitation_code_id)

        user = self.db.query(User).filter(User.id == row.user_id).first()
        self.assertFalse(user.is_active)
        self.assertIsNone(user.role_id)
        self.assertEqual(user.company_id, self.company_a.id)

    def test_register_without_code_creates_no_auth_session_and_no_auto_login(self):

        self._register(email="nocode2@example.com", invitation_code=None)

        self.assertEqual(self.db.query(AuthSession).count(), 0)

        auth = AuthService(self.db)
        with self.assertRaises(ValueError):
            auth.login("nocode2@example.com", STRONG_PASSWORD)

    def test_register_with_whitespace_only_code_treated_as_no_code(self):

        result = self._register(email="whitespace@example.com", invitation_code="   ")

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.SUCCESS)
        row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self.assertEqual(row.company_id, self.company_a.id)
        self.assertIsNone(row.invitation_code_id)

    def test_register_without_code_then_approve_and_login_succeeds(self):

        admin = self._create_user("admin1", self.admin_role.id, self.company_a.id)

        result = self._register(email="approveme@example.com", invitation_code=None)
        self.assertEqual(result.outcome, reg_service.RegisterOutcome.SUCCESS)

        reg_service.approve_request(
            self.db, request_id=result.request_id, current_user=admin, role_code="VIEWER",
        )

        auth = AuthService(self.db)
        login_result = auth.login("approveme@example.com", STRONG_PASSWORD)
        self.assertIn("access_token", login_result)

    # --------------------------------------------------
    # 코드 없이 가입 — 활성 회사가 정확히 1개가 아닌 경우 안전 거부
    # --------------------------------------------------

    def test_register_without_code_rejected_when_zero_active_companies(self):

        self.company_a.active = False
        self.db.commit()

        result = self._register(email="zerocompanies@example.com", invitation_code=None)

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.COMPANY_UNAVAILABLE)
        self.assertIsNone(result.request_id)
        self.assertEqual(self.db.query(User).filter(User.email == "zerocompanies@example.com").count(), 0)

    def test_register_without_code_rejected_when_multiple_active_companies(self):

        company_b = Company(name="Company B", active=True)
        self.db.add(company_b)
        self.db.commit()

        result = self._register(email="twocompanies@example.com", invitation_code=None)

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.COMPANY_UNAVAILABLE)
        self.assertIsNone(result.request_id)
        self.assertEqual(self.db.query(User).filter(User.email == "twocompanies@example.com").count(), 0)

    def test_register_without_code_does_not_arbitrarily_pick_a_company(self):
        """
        회사가 2개 이상일 때, 재시도해도(여러 번 호출) 절대 임의의 회사로
        배정되지 않고 매번 동일하게 안전 거부되어야 한다.
        """

        company_b = Company(name="Company B", active=True)
        self.db.add(company_b)
        self.db.commit()

        for _ in range(3):
            result = self._register(email=f"retry{_}@example.com", invitation_code=None)
            self.assertEqual(result.outcome, reg_service.RegisterOutcome.COMPANY_UNAVAILABLE)

    # --------------------------------------------------
    # 코드가 주어지면 기존 검증 정책 그대로 — 없음으로 조용히 처리 금지
    # --------------------------------------------------

    def test_register_with_valid_code_still_succeeds_and_binds_invitation_company(self):

        admin = self._create_user("admin2", self.admin_role.id, self.company_a.id)
        _, raw_code = self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        result = self._register(email="withcode@example.com", invitation_code=raw_code)

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.SUCCESS)
        row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self.assertIsNotNone(row.invitation_code_id)
        self.assertEqual(row.company_id, self.company_a.id)

    def test_register_with_garbage_code_explicitly_rejected_not_treated_as_no_code(self):
        """
        존재하지 않는 코드가 "명시적으로" 주어지면, 활성 회사가 1개뿐이라
        코드 없이는 성공했을 상황이라도 그대로 성공시키면 안 된다 —
        잘못된 코드는 조용히 "코드 없음"으로 격하되지 않고 명시적으로
        거부되어야 한다.
        """

        result = self._register(email="garbagecode@example.com", invitation_code="this-code-does-not-exist")

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.INVALID_INVITATION)
        self.assertIsNone(result.request_id)
        self.assertEqual(self.db.query(User).filter(User.email == "garbagecode@example.com").count(), 0)

    def test_register_with_expired_code_explicitly_rejected(self):

        admin = self._create_user("admin3", self.admin_role.id, self.company_a.id)
        _, raw_code = self._create_invitation_row(self.company_a.id, admin.id, "VIEWER", expired=True)

        result = self._register(email="expiredcode@example.com", invitation_code=raw_code)

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.INVALID_INVITATION)

    def test_register_with_revoked_code_explicitly_rejected(self):

        admin = self._create_user("admin4", self.admin_role.id, self.company_a.id)
        _, raw_code = self._create_invitation_row(self.company_a.id, admin.id, "VIEWER", revoked=True)

        result = self._register(email="revokedcode@example.com", invitation_code=raw_code)

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.INVALID_INVITATION)

    # --------------------------------------------------
    # 클라이언트가 company_id/role_id를 지정할 방법이 없다(선택 입력이
    # 되어도 동일) — atomic_register_user의 파라미터 목록 자체로 확인.
    # --------------------------------------------------

    def test_atomic_register_user_has_no_company_id_or_role_id_parameter(self):

        import inspect

        sig = inspect.signature(reg_service.atomic_register_user)
        self.assertNotIn("company_id", sig.parameters)
        self.assertNotIn("role_id", sig.parameters)


if __name__ == "__main__":
    unittest.main()
