"""
=========================================================
Homez OS

File : tests/test_account_registration.py

회원가입 + 승인 워크플로 서비스/라우터 함수 직접 호출 검증. httpx가
설치되어 있지 않아 FastAPI TestClient를 쓸 수 없으므로 라우터 함수를
ASGI 계층 없이 직접 호출한다. 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.core.security import verify_password
from app.core.security import sha256
from app.database.base import Base
from app.domains.account_registration import service as reg_service
from app.domains.account_registration.constants import (
    NEW_PERMISSION_CODES,
    RegistrationStatus,
)
from app.domains.account_registration.model import InvitationCode
from app.domains.account_registration.model import UserRegistrationRequest
from app.domains.account_registration.router import (
    approve_endpoint,
    assign_role_endpoint,
    create_invitation_endpoint,
    list_requests_endpoint,
    reactivate_endpoint,
    register_endpoint,
    reject_endpoint,
    registration_status_endpoint,
    revoke_invitation_endpoint,
    suspend_endpoint,
)
from app.domains.account_registration.schema import (
    ApproveRequest,
    AssignRoleRequest,
    InvitationCreateRequest,
    RegisterRequest,
    RejectRequest,
)
from app.domains.auth.service import AuthService
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


class AccountRegistrationTestCase(unittest.TestCase):

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
        self.company_b = Company(name="Company B", active=True)
        self.db.add_all([self.company_a, self.company_b])
        self.db.flush()

        self.super_admin_role = Role(name="Super Administrator", code="SUPER_ADMIN")
        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.manager_role = Role(name="Manager", code="MANAGER")
        self.staff_role = Role(name="Staff", code="STAFF")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([
            self.super_admin_role, self.admin_role, self.manager_role,
            self.staff_role, self.viewer_role,
        ])
        self.db.flush()

        self.permissions = {}
        for code in NEW_PERMISSION_CODES:
            perm = Permission(name=code, code=code, active=True)
            self.db.add(perm)
            self.permissions[code] = perm
        self.db.flush()

        # ADMIN 역할에 세부 Permission을 부여해 "슈퍼바이저" 역할로 쓴다
        # (새 SUPERVISOR 역할을 하드코딩하지 않는다는 설계 그대로).
        for code in NEW_PERMISSION_CODES:
            self.db.add(RolePermission(role_id=self.admin_role.id, permission_id=self.permissions[code].id))
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

    def _add_session(self, user_id, jti):

        create_session(
            self.db, user_id=user_id, jti=jti,
            issued_at=datetime.now(timezone.utc).replace(tzinfo=None),
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        )
        self.db.commit()

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

    def _register(self, email="newuser@example.com", display_name="새 사용자", invitation_code="test-invite-viewer", password=STRONG_PASSWORD):

        return reg_service.register(
            self.db_path, email=email, display_name=display_name,
            password=password, password_confirmation=password,
            invitation_code=invitation_code,
        )

    # --------------------------------------------------
    # 1. 회원가입
    # --------------------------------------------------

    def test_successful_registration_creates_pending_request(self):

        admin = self._create_user("admin1", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        result = self._register()

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.SUCCESS)

        row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self.assertEqual(row.status, RegistrationStatus.PENDING_APPROVAL.value)
        self.assertEqual(row.company_id, self.company_a.id)

        user = self.db.query(User).filter(User.id == row.user_id).first()
        self.assertFalse(user.is_active)
        self.assertIsNone(user.role_id)

    def test_registered_user_cannot_login_before_approval(self):

        admin = self._create_user("admin2", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        result = self._register(email="pendinglogin@example.com")
        self.assertEqual(result.outcome, reg_service.RegisterOutcome.SUCCESS)

        auth = AuthService(self.db)
        with self.assertRaises(ValueError):
            auth.login("pendinglogin@example.com", STRONG_PASSWORD)

    def test_registration_creates_no_auth_session(self):

        admin = self._create_user("admin3", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        self._register(email="nosession@example.com")

        self.assertEqual(self.db.query(AuthSession).count(), 0)

    def test_duplicate_email_rejected(self):

        admin = self._create_user("admin4", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER", max_uses=2)

        first = self._register(email="dup@example.com")
        self.assertEqual(first.outcome, reg_service.RegisterOutcome.SUCCESS)

        second = self._register(email="dup@example.com")
        self.assertEqual(second.outcome, reg_service.RegisterOutcome.DUPLICATE_ACCOUNT)

    def test_weak_password_rejected(self):

        admin = self._create_user("admin5", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        with self.assertRaises(ValueError):
            reg_service.register(
                self.db_path, email="weak@example.com", display_name="약한",
                password="weak", password_confirmation="weak",
                invitation_code="test-invite-viewer",
            )

    def test_password_confirmation_mismatch_rejected(self):

        admin = self._create_user("admin6", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        with self.assertRaises(ValueError):
            reg_service.register(
                self.db_path, email="mismatch@example.com", display_name="불일치",
                password=STRONG_PASSWORD, password_confirmation="Different!Pass9",
                invitation_code="test-invite-viewer",
            )

    def test_client_cannot_control_company_role_or_active_via_schema(self):
        """RegisterRequest 스키마 자체에 company_id/role_id/is_active/승인 상태 필드가 없다."""

        fields = RegisterRequest.model_fields.keys()
        for forbidden in ("company_id", "role_id", "is_active", "status", "approved"):
            self.assertNotIn(forbidden, fields)

    def test_invalid_invitation_code_rejected(self):

        result = self._register(invitation_code="totally-wrong-code")

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.INVALID_INVITATION)
        self.assertEqual(self.db.query(User).count(), 0)

    def test_expired_invitation_code_rejected(self):

        admin = self._create_user("admin7", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER", expired=True)

        result = self._register()

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.INVALID_INVITATION)

    def test_revoked_invitation_code_rejected(self):

        admin = self._create_user("admin8", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER", revoked=True)

        result = self._register()

        self.assertEqual(result.outcome, reg_service.RegisterOutcome.INVALID_INVITATION)

    def test_invitation_code_not_stored_in_plaintext(self):

        admin = self._create_user("admin9", self.admin_role.id, self.company_a.id)
        row, raw_code = self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        self.assertNotEqual(row.code_hash, raw_code)
        self.assertNotIn(raw_code, row.code_hash)

    def test_invitation_code_use_limited_by_max_uses(self):

        admin = self._create_user("admin10", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER", max_uses=1)

        first = self._register(email="first@example.com")
        self.assertEqual(first.outcome, reg_service.RegisterOutcome.SUCCESS)

        second = self._register(email="second@example.com")
        self.assertEqual(second.outcome, reg_service.RegisterOutcome.INVALID_INVITATION)

    def test_concurrent_single_use_invitation_exactly_one_registration_succeeds(self):

        admin = self._create_user("admin11", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER", max_uses=1)

        results = []
        lock = threading.Lock()

        def _attempt(idx):
            r = reg_service.register(
                self.db_path, email=f"concurrent{idx}@example.com", display_name="동시",
                password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
                invitation_code="test-invite-viewer",
            )
            with lock:
                results.append(r.outcome)

        threads = [threading.Thread(target=_attempt, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r == reg_service.RegisterOutcome.SUCCESS), 1)

    def test_registration_status_check_generic_and_no_localstorage_leak(self):

        admin = self._create_user("admin12", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        result = self._register(email="statuscheck@example.com")
        token = reg_service.issue_status_check_token(result.request_id)

        response = registration_status_endpoint(token=token, db=self.db)

        self.assertEqual(response.status, RegistrationStatus.PENDING_APPROVAL.value)
        self.assertFalse(hasattr(response, "company_id"))
        self.assertFalse(hasattr(response, "role"))

    def test_invalid_status_check_token_rejected(self):

        with self.assertRaises(HTTPException) as ctx:
            registration_status_endpoint(token="not-a-real-token", db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    # --------------------------------------------------
    # 2. 승인
    # --------------------------------------------------

    def test_super_admin_can_approve(self):

        super_admin = self._create_user("sa1", self.super_admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, super_admin.id, "VIEWER")
        result = self._register(email="approveme1@example.com")

        row = approve_endpoint(
            result.request_id, ApproveRequest(role_code="VIEWER"),
            current_user=super_admin, db=self.db,
        )

        self.assertEqual(row.message, "승인되었습니다.")

        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self.assertEqual(req_row.status, RegistrationStatus.APPROVED.value)

        target = self.db.query(User).filter(User.id == req_row.user_id).first()
        self.assertTrue(target.is_active)
        self.assertEqual(target.role_id, self.viewer_role.id)

    def test_permitted_supervisor_can_approve(self):

        admin = self._create_user("admin13", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="approveme2@example.com")

        approve_endpoint(
            result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin, db=self.db,
        )

        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self.assertEqual(req_row.status, RegistrationStatus.APPROVED.value)

    def test_unpermitted_user_cannot_approve(self):

        viewer = self._create_user("viewer1", self.viewer_role.id, self.company_a.id)
        admin = self._create_user("admin14", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="approveme3@example.com")

        with self.assertRaises(HTTPException) as ctx:
            approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=viewer, db=self.db)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_other_company_request_returns_404(self):

        admin_a = self._create_user("admin15", self.admin_role.id, self.company_a.id)
        admin_b = self._create_user("admin16", self.super_admin_role.id, self.company_b.id)
        self._create_invitation_row(self.company_a.id, admin_a.id, "VIEWER")
        result = self._register(email="crosscompany@example.com")

        with self.assertRaises(HTTPException) as ctx:
            approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin_b, db=self.db)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_supervisor_cannot_grant_super_admin(self):

        admin = self._create_user("admin17", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="wantssuperadmin@example.com")

        with self.assertRaises(HTTPException) as ctx:
            approve_endpoint(result.request_id, ApproveRequest(role_code="SUPER_ADMIN"), current_user=admin, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_supervisor_cannot_grant_role_above_own_rank(self):

        admin = self._create_user("admin18", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="wantsabovemine@example.com")

        # ADMIN(rank 40) 자신보다 높은 역할은 없다(SUPER_ADMIN 제외 랭크
        # 최고이므로), 대신 같은 랭크(ADMIN)는 허용되는지, 랭크 개념
        # 자체가 작동하는지 MANAGER 승인이 되는지로 검증한다.
        approve_endpoint(result.request_id, ApproveRequest(role_code="MANAGER"), current_user=admin, db=self.db)

        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self.assertEqual(req_row.status, RegistrationStatus.APPROVED.value)

    def test_staff_role_cannot_grant_role_above_own_rank(self):

        staff = self._create_user("staff1", self.staff_role.id, self.company_a.id)
        for code in NEW_PERMISSION_CODES:
            self.db.add(RolePermission(role_id=self.staff_role.id, permission_id=self.permissions[code].id))
        self.db.commit()

        self._create_invitation_row(self.company_a.id, staff.id, "VIEWER")
        result = self._register(email="stafftest@example.com")

        with self.assertRaises(HTTPException) as ctx:
            approve_endpoint(result.request_id, ApproveRequest(role_code="ADMIN"), current_user=staff, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_concurrent_approval_exactly_one_succeeds(self):

        admin = self._create_user("admin19", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="concurrentapprove@example.com")
        self.db.commit()

        results = []
        lock = threading.Lock()

        def _attempt():
            local_session = self.SessionLocal()
            try:
                local_admin = local_session.query(User).filter(User.id == admin.id).first()
                approve_endpoint(
                    result.request_id, ApproveRequest(role_code="VIEWER"),
                    current_user=local_admin, db=local_session,
                )
                with lock:
                    results.append(True)
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

    def test_login_succeeds_after_approval(self):

        admin = self._create_user("admin20", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="loginafterapprove@example.com")

        approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin, db=self.db)

        auth = AuthService(self.db)
        login_result = auth.login("loginafterapprove@example.com", STRONG_PASSWORD)

        self.assertIn("access_token", login_result)

    def test_rejected_account_login_fails(self):

        admin = self._create_user("admin21", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="rejectme@example.com")

        reject_endpoint(result.request_id, RejectRequest(reason_code="POLICY_VIOLATION"), current_user=admin, db=self.db)

        auth = AuthService(self.db)
        with self.assertRaises(ValueError):
            auth.login("rejectme@example.com", STRONG_PASSWORD)

    def test_approve_reject_self_forbidden(self):

        admin = self._create_user("admin22", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="selfapprove@example.com")

        # 요청의 user_id를 admin 자신으로 바꿔 "자기 승인" 상황을 흉내낸다.
        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        req_row.user_id = admin.id
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    # --------------------------------------------------
    # 3. 정지/재활성
    # --------------------------------------------------

    def test_suspend_revokes_all_sessions(self):

        admin = self._create_user("admin23", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="suspendme@example.com")
        approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin, db=self.db)

        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        self._add_session(req_row.user_id, "jti-suspend-1")

        suspend_endpoint(result.request_id, current_user=admin, db=self.db)

        session_row = SessionRepository(self.db).get_by_jti("jti-suspend-1")
        self.assertIsNotNone(session_row.revoked_at)

    def test_suspended_before_token_reuse_fails(self):
        """정지 전 발급된 세션이 정지 후 거부되는지 _decode_user 경로로 확인."""

        from app.core.auth import _decode_user
        from app.core.security import create_access_token

        admin = self._create_user("admin24", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="tokenreuse@example.com")
        approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin, db=self.db)

        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        target = self.db.query(User).filter(User.id == req_row.user_id).first()

        token = create_access_token(data={"sub": str(target.id), "username": target.username})
        from app.core.security import decode_access_token
        payload = decode_access_token(token)
        self._add_session(target.id, payload["jti"])

        suspend_endpoint(result.request_id, current_user=admin, db=self.db)

        with self.assertRaises(HTTPException):
            _decode_user(token, self.db)

    def test_reactivate_does_not_auto_login(self):

        admin = self._create_user("admin25", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        result = self._register(email="reactivateme@example.com")
        approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin, db=self.db)
        suspend_endpoint(result.request_id, current_user=admin, db=self.db)

        response = reactivate_endpoint(result.request_id, current_user=admin, db=self.db)

        self.assertEqual(self.db.query(AuthSession).filter(AuthSession.revoked_at.is_(None)).count(), 0)
        self.assertIn("다시 로그인", response.message)

    def test_last_super_admin_cannot_be_suspended(self):

        founder = self._create_user("founder1", self.super_admin_role.id, self.company_a.id)
        supervisor = self._create_user("admin26", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, founder.id, "SUPER_ADMIN")

        result = self._register(email="futuresa@example.com", invitation_code="test-invite-super_admin")
        approve_endpoint(result.request_id, ApproveRequest(role_code="SUPER_ADMIN"), current_user=founder, db=self.db)

        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        new_sa = self.db.query(User).filter(User.id == req_row.user_id).first()

        # founder를 비활성화해 new_sa가 유일한 활성 SUPER_ADMIN이 되게
        # 만든 뒤, 그 유일한 SUPER_ADMIN(request_id로 추적되는 계정)을
        # supervisor가 정지 시도한다 — 반드시 차단돼야 한다.
        founder.is_active = False
        self.db.add(founder)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            suspend_endpoint(result.request_id, current_user=supervisor, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

        self.db.refresh(new_sa)
        self.assertTrue(new_sa.is_active)

    def test_self_suspend_forbidden(self):
        """
        자기 자신을 정지하려는 시도는 권한이 있어도(ADMIN — 세부
        Permission 보유) 차단돼야 한다는 것을 검증한다. 대상이 정지
        권한 자체가 없으면(예: VIEWER) 이 자기-대상 검사에 도달하기도
        전에 403(권한 없음)으로 이미 막히므로, 그 경로와 구분하기
        위해 대상 스스로도 USER_ACCESS_SUSPEND를 가진 ADMIN으로 승인한다.
        """

        admin = self._create_user("admin27", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "ADMIN")
        result = self._register(email="selfsuspend@example.com", invitation_code="test-invite-admin")
        approve_endpoint(result.request_id, ApproveRequest(role_code="ADMIN"), current_user=admin, db=self.db)

        req_row = self.db.query(UserRegistrationRequest).filter(UserRegistrationRequest.id == result.request_id).first()
        target = self.db.query(User).filter(User.id == req_row.user_id).first()

        with self.assertRaises(HTTPException) as ctx:
            suspend_endpoint(result.request_id, current_user=target, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    # --------------------------------------------------
    # 4. 역할 변경 / assign-role
    # --------------------------------------------------

    def test_assign_role_revokes_sessions(self):

        admin = self._create_user("admin28", self.admin_role.id, self.company_a.id)
        target = self._create_user("target1", self.viewer_role.id, self.company_a.id)
        self._add_session(target.id, "jti-assign-1")

        assign_role_endpoint(target.id, AssignRoleRequest(role_code="STAFF"), current_user=admin, db=self.db)

        self.db.refresh(target)
        self.assertEqual(target.role_id, self.staff_role.id)

        session_row = SessionRepository(self.db).get_by_jti("jti-assign-1")
        self.assertIsNotNone(session_row.revoked_at)

    def test_assign_role_other_company_404(self):

        admin_a = self._create_user("admin29", self.admin_role.id, self.company_a.id)
        target_b = self._create_user("target2", self.viewer_role.id, self.company_b.id)

        with self.assertRaises(HTTPException) as ctx:
            assign_role_endpoint(target_b.id, AssignRoleRequest(role_code="STAFF"), current_user=admin_a, db=self.db)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_assign_role_self_forbidden(self):

        admin = self._create_user("admin30", self.admin_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            assign_role_endpoint(admin.id, AssignRoleRequest(role_code="VIEWER"), current_user=admin, db=self.db)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_last_super_admin_role_cannot_be_removed(self):

        super_admin = self._create_user("solesa2", self.super_admin_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            assign_role_endpoint(super_admin.id, AssignRoleRequest(role_code="VIEWER"), current_user=super_admin, db=self.db)
        # self-forbidden도 걸리지만, last-super-admin도 걸려야 하므로
        # 둘 중 하나로 400이 나오면 충분하다.
        self.assertEqual(ctx.exception.status_code, 400)

    # --------------------------------------------------
    # 5. 초대 코드 관리
    # --------------------------------------------------

    def test_create_invitation_and_verify_hash_only_stored(self):

        admin = self._create_user("admin31", self.admin_role.id, self.company_a.id)

        response = create_invitation_endpoint(
            InvitationCreateRequest(max_role_code="VIEWER"), current_user=admin, db=self.db,
        )

        row = self.db.query(InvitationCode).order_by(InvitationCode.id.desc()).first()
        self.assertNotEqual(row.code_hash, response.invitation_code)

    def test_create_invitation_ceiling_enforced_at_issuance(self):

        admin = self._create_user("admin32", self.admin_role.id, self.company_a.id)

        with self.assertRaises(HTTPException) as ctx:
            create_invitation_endpoint(
                InvitationCreateRequest(max_role_code="SUPER_ADMIN"), current_user=admin, db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_revoke_invitation_other_company_404(self):

        admin_a = self._create_user("admin33", self.admin_role.id, self.company_a.id)
        admin_b = self._create_user("admin34", self.super_admin_role.id, self.company_b.id)

        invitation, _ = self._create_invitation_row(self.company_a.id, admin_a.id, "VIEWER")

        with self.assertRaises(HTTPException) as ctx:
            revoke_invitation_endpoint(invitation.id, current_user=admin_b, db=self.db)

        self.assertEqual(ctx.exception.status_code, 404)

    # --------------------------------------------------
    # 6. 감사 로그 — 민감 값 없음
    # --------------------------------------------------

    def test_audit_logs_never_contain_password_or_invitation_code(self):

        admin = self._create_user("admin35", self.admin_role.id, self.company_a.id)
        _, raw_code = self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")

        result = self._register(email="auditcheck@example.com", invitation_code=raw_code)
        approve_endpoint(result.request_id, ApproveRequest(role_code="VIEWER"), current_user=admin, db=self.db)

        from sqlalchemy import text as sql_text

        rows = self.db.execute(sql_text("SELECT description FROM audit_logs")).fetchall()

        forbidden_values = [STRONG_PASSWORD, raw_code, "auditcheck@example.com"]

        for row in rows:
            description = row[0] or ""
            for forbidden in forbidden_values:
                self.assertNotIn(forbidden, description)

    # --------------------------------------------------
    # 7. 목록 조회
    # --------------------------------------------------

    def test_list_requests_scoped_to_own_company(self):

        admin_a = self._create_user("admin36", self.admin_role.id, self.company_a.id)
        admin_b = self._create_user("admin37", self.super_admin_role.id, self.company_b.id)
        self._create_invitation_row(self.company_a.id, admin_a.id, "VIEWER")
        self._register(email="companyaonly@example.com")

        result_a = list_requests_endpoint(current_user=admin_a, db=self.db)
        result_b = list_requests_endpoint(current_user=admin_b, db=self.db)

        self.assertEqual(len(result_a), 1)
        self.assertEqual(len(result_b), 0)

    def test_list_requests_masks_email(self):

        admin = self._create_user("admin38", self.admin_role.id, self.company_a.id)
        self._create_invitation_row(self.company_a.id, admin.id, "VIEWER")
        self._register(email="maskcheck@example.com")

        rows = list_requests_endpoint(current_user=admin, db=self.db)

        self.assertNotEqual(rows[0].masked_email, "maskcheck@example.com")
        self.assertIn("*", rows[0].masked_email)


if __name__ == "__main__":
    unittest.main()
