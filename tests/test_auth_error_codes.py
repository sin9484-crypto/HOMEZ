"""
=========================================================
Homez OS

File : tests/test_auth_error_codes.py

2026-08-03: 로그인 화면 임의 전환 결함 수정(Gate 3/4)의 서버측 계약을
검증한다 — 401/403 응답이 X-Auth-Error-Code 헤더로 원인을 안정적으로
구분하는지, 그리고 기존 detail 문자열/상태 코드 계약이 그대로
유지되는지(콘솔 JS가 이 헤더만 보고 로그아웃 여부를 판단한다).

httpx가 설치되어 있지 않아 FastAPI TestClient를 쓸 수 없으므로 의존성
함수를 ASGI 계층 없이 직접 호출한다. 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.audit_db import write_audit_log  # noqa: F401  (import 부작용 없음 확인용)
from app.core.auth import get_current_user as core_get_current_user
from app.core.auth import get_current_active_user as core_get_current_active_user
from app.core.authorization import UserRole, require_admin
from app.domains.auth.dependencies import get_current_active_user as domain_get_current_active_user
from app.core.security import decode_access_token, hash_password
from app.database.base import Base
from app.domains.auth.router import login as auth_login
from app.domains.auth.router import logout as auth_logout
from app.domains.auth.dependencies import get_current_user as domain_get_current_user
from app.domains.auth.schema import LoginRequest
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.session.model import AuthSession
from app.domains.session.repository import SessionRepository
from app.domains.user.model import User


class _FakeClient:

    def __init__(self, host="127.0.0.1"):
        self.host = host


class _FakeRequest:

    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None


class AuthErrorCodeTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, RolePermission.__table__,
                Permission.__table__, User.__table__, AuthSession.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        self.admin_role = Role(name="Administrator", code="ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.admin_role, self.viewer_role])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, username, password, role_id, is_active=True):

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(password), name=username,
            role_id=role_id, is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def _login(self, username, password):

        return auth_login(
            data=LoginRequest(username=username, password=password),
            request=_FakeRequest(), db=self.db,
        )

    # --------------------------------------------------
    # SESSION_REVOKED — 명시적 로그아웃 후 재사용
    # --------------------------------------------------

    def test_logout_then_reuse_yields_session_revoked_code_core_auth(self):

        user = self._create_user("u1", "Test1234!", self.admin_role.id)
        token = self._login("u1", "Test1234!")["access_token"]

        core_get_current_user(token=token, db=self.db)  # 로그아웃 전 정상 확인
        auth_logout(current_user=user, token=token, db=self.db)

        with self.assertRaises(HTTPException) as ctx:
            core_get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.headers.get("X-Auth-Error-Code"), "SESSION_REVOKED")
        # 기존 detail 계약은 그대로 유지된다(콘솔 외 다른 클라이언트 호환).
        self.assertEqual(ctx.exception.detail, "Invalid authentication credentials")

    def test_logout_then_reuse_yields_session_revoked_code_domain_auth(self):

        user = self._create_user("u2", "Test1234!", self.admin_role.id)
        token = self._login("u2", "Test1234!")["access_token"]

        domain_get_current_user(token=token, db=self.db)
        auth_logout(current_user=user, token=token, db=self.db)

        with self.assertRaises(HTTPException) as ctx:
            domain_get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.headers.get("X-Auth-Error-Code"), "SESSION_REVOKED")
        self.assertEqual(ctx.exception.detail, "Could not validate credentials.")

    # --------------------------------------------------
    # SESSION_EXPIRED — 서버측 세션 만료 시각 경과
    # --------------------------------------------------

    def test_server_side_session_expiry_yields_session_expired_code(self):

        self._create_user("u3", "Test1234!", self.admin_role.id)
        token = self._login("u3", "Test1234!")["access_token"]

        repo = SessionRepository(self.db)
        payload = decode_access_token(token)
        session_row = repo.get_by_jti(payload["jti"])
        session_row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            core_get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.headers.get("X-Auth-Error-Code"), "SESSION_EXPIRED")

    def test_garbage_token_yields_session_expired_code(self):
        """서명 자체가 무효한 토큰(예: JWT 파싱 실패)도 SESSION_EXPIRED로 분류된다."""

        with self.assertRaises(HTTPException) as ctx:
            core_get_current_user(token="not-a-real-token", db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.headers.get("X-Auth-Error-Code"), "SESSION_EXPIRED")

    # --------------------------------------------------
    # ACCOUNT_DISABLED — 403이지만 허용된 로그아웃 조건(비활성/정지 계정)
    # --------------------------------------------------

    def test_inactive_user_yields_account_disabled_code_core_auth(self):

        user = self._create_user("u5", "Test1234!", self.admin_role.id, is_active=False)

        with self.assertRaises(HTTPException) as ctx:
            core_get_current_active_user(current_user=user)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.headers.get("X-Auth-Error-Code"), "ACCOUNT_DISABLED")

    def test_inactive_user_yields_account_disabled_code_domain_auth(self):

        user = self._create_user("u6", "Test1234!", self.admin_role.id, is_active=False)

        with self.assertRaises(HTTPException) as ctx:
            domain_get_current_active_user(current_user=user)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.headers.get("X-Auth-Error-Code"), "ACCOUNT_DISABLED")

    # --------------------------------------------------
    # PERMISSION_DENIED — 403, 로그아웃 대상 아님
    # --------------------------------------------------

    def test_insufficient_role_yields_permission_denied_code(self):

        user = self._create_user("u4", "Test1234!", self.viewer_role.id)

        with self.assertRaises(HTTPException) as ctx:
            require_admin(user)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.headers.get("X-Auth-Error-Code"), "PERMISSION_DENIED")
        # 기존 detail 문자열 계약 유지(변경 없음).
        self.assertEqual(ctx.exception.detail, "Permission denied")

    def test_permission_denied_code_not_in_allowed_logout_set(self):
        """
        콘솔 JS의 ALLOWED_LOGOUT_CODES와 어긋나지 않는지 문자열 값 자체를
        고정한다 — 이 상수 값이 바뀌면 콘솔 JS도 함께 바뀌어야 한다.
        """

        user = User(username="dummy", email="dummy@example.com", password_hash="x", role_id=None)

        with self.assertRaises(HTTPException) as ctx:
            require_admin(user)

        code = ctx.exception.headers.get("X-Auth-Error-Code")
        self.assertEqual(code, "PERMISSION_DENIED")
        self.assertNotIn(code, {"SESSION_EXPIRED", "SESSION_REVOKED", "ACCOUNT_DISABLED"})


if __name__ == "__main__":
    unittest.main()
