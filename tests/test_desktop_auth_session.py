"""
=========================================================
Homez OS

File : tests/test_desktop_auth_session.py

HOMEZ Desktop 로그인 흐름 — 서버 측 세션/로그아웃/접근 차단 검증
(2026-07-30, "Desktop 로그인부터 V5 진입 Gate 직전까지" 작업).

httpx가 설치되어 있지 않아(패키지 설치 금지 범위) FastAPI TestClient를
쓸 수 없다 — 기존 tests/test_v24_v3_schema_migration.py와 동일하게
라우터/의존성 함수를 ASGI 계층 없이 직접 호출한다. 실제 homez.db는
사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.audit_log import log_auth_event
from app.core.auth import get_current_user as core_get_current_user
from app.core.authorization import UserRole
from app.core.authorization import require_admin
from app.core.security import hash_password
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
    """auth_login()이 필요로 하는 최소 Request 표면."""

    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None


class DesktopAuthSessionTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                Role.__table__,
                RolePermission.__table__,
                Permission.__table__,
                User.__table__,
                AuthSession.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
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
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password(password),
            name=username,
            role_id=role_id,
            is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    # --------------------------------------------------
    # 2. 정상 로그인 (실제 라우터 함수 경로)
    # --------------------------------------------------

    def test_login_endpoint_accepts_request_body_not_query_string(self):

        self._create_user("admin1", "Test1234!", self.admin_role.id)

        result = auth_login(
            data=LoginRequest(username="admin1", password="Test1234!"),
            request=_FakeRequest(),
            db=self.db,
        )

        self.assertTrue(result["access_token"])
        self.assertEqual(result["user"].username, "admin1")

    def test_login_openapi_schema_has_no_query_parameters(self):
        """
        /auth/login이 더 이상 username/password를 쿼리 파라미터로 받지
        않는지 실제 OpenAPI 스키마로 확인한다(비밀번호가 URL에 노출되는
        경로가 완전히 제거되었는지에 대한 구조적 증거).
        """

        import app.main as main_module

        schema = main_module.app.openapi()
        login_op = schema["paths"]["/auth/login"]["post"]

        query_params = [
            p for p in login_op.get("parameters", [])
            if p.get("in") == "query"
        ]
        self.assertEqual(query_params, [])
        self.assertIn("requestBody", login_op)

    # --------------------------------------------------
    # 3/4. 실패 로그인
    # --------------------------------------------------

    def test_wrong_password_returns_401_via_router(self):

        self._create_user("admin2", "Test1234!", self.admin_role.id)

        with self.assertRaises(HTTPException) as ctx:
            auth_login(
                data=LoginRequest(username="admin2", password="Wrong!"),
                request=_FakeRequest(),
                db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 401)

    def test_inactive_account_returns_401_via_router(self):
        """
        2026-08-03: 계정 존재/부재를 응답에서 구분하지 않기 위해
        비활성 계정도 존재하지 않는 계정/틀린 비밀번호와 동일하게
        401(일반 오류)을 반환하도록 통일했다 — 이전에는 403("Inactive
        user")으로 따로 구분돼 그 자체가 계정 열거 신호였다. 승인
        대기/거절/정지 계정(app/domains/account_registration)도 전부
        is_active=False로 표현되므로 이 경로가 함께 담당한다.
        """

        self._create_user(
            "inactive1", "Test1234!", self.admin_role.id, is_active=False,
        )

        with self.assertRaises(HTTPException) as ctx:
            auth_login(
                data=LoginRequest(username="inactive1", password="Test1234!"),
                request=_FakeRequest(),
                db=self.db,
            )

        self.assertEqual(ctx.exception.status_code, 401)

    # --------------------------------------------------
    # 5/6. 미인증 접근 거부
    # --------------------------------------------------

    def test_missing_or_garbage_token_is_rejected(self):

        with self.assertRaises(HTTPException) as ctx:
            core_get_current_user(token="not-a-real-token", db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)

    # --------------------------------------------------
    # 7. 역할별 권한
    # --------------------------------------------------

    def test_viewer_role_is_rejected_by_require_admin(self):

        user = self._create_user("viewer1", "Test1234!", self.viewer_role.id)

        with self.assertRaises(HTTPException) as ctx:
            require_admin(user)

        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_role_passes_require_admin(self):

        user = self._create_user("admin3", "Test1234!", self.admin_role.id)

        self.assertIs(require_admin(user), user)

    # --------------------------------------------------
    # 8/9. 로그아웃 + 재사용 차단 (두 get_current_user 구현 모두)
    # --------------------------------------------------

    def test_logout_revokes_session_and_blocks_reuse_core_auth(self):

        user = self._create_user("admin4", "Test1234!", self.admin_role.id)

        login_result = auth_login(
            data=LoginRequest(username="admin4", password="Test1234!"),
            request=_FakeRequest(),
            db=self.db,
        )
        token = login_result["access_token"]

        # 로그아웃 전: 정상적으로 인증된다.
        fetched = core_get_current_user(token=token, db=self.db)
        self.assertEqual(fetched.username, "admin4")

        auth_logout(current_user=user, token=token, db=self.db)

        # 로그아웃 후: 서명은 여전히 유효하지만 서버가 세션을 취소했으므로
        # 재사용이 차단되어야 한다.
        with self.assertRaises(HTTPException) as ctx:
            core_get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)

    def test_logout_blocks_reuse_domain_auth_dependency(self):
        """app/domains/auth/dependencies.py의 get_current_user도 동일하게 차단해야 한다."""

        user = self._create_user("admin5", "Test1234!", self.admin_role.id)

        login_result = auth_login(
            data=LoginRequest(username="admin5", password="Test1234!"),
            request=_FakeRequest(),
            db=self.db,
        )
        token = login_result["access_token"]

        self.assertEqual(
            domain_get_current_user(token=token, db=self.db).username, "admin5",
        )

        auth_logout(current_user=user, token=token, db=self.db)

        with self.assertRaises(HTTPException) as ctx:
            domain_get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)

    def test_logout_is_idempotent(self):

        user = self._create_user("admin6", "Test1234!", self.admin_role.id)

        login_result = auth_login(
            data=LoginRequest(username="admin6", password="Test1234!"),
            request=_FakeRequest(),
            db=self.db,
        )
        token = login_result["access_token"]

        auth_logout(current_user=user, token=token, db=self.db)
        # 두 번째 로그아웃 호출도 예외 없이 완료되어야 한다(멱등).
        auth_logout(current_user=user, token=token, db=self.db)

    # --------------------------------------------------
    # 10. 세션 만료 (서버 측 expires_at)
    # --------------------------------------------------

    def test_server_side_session_expiry_blocks_access(self):

        from datetime import datetime, timedelta

        self._create_user("admin7", "Test1234!", self.admin_role.id)

        login_result = auth_login(
            data=LoginRequest(username="admin7", password="Test1234!"),
            request=_FakeRequest(),
            db=self.db,
        )
        token = login_result["access_token"]

        # JWT 자체는 아직 만료되지 않았지만, 서버 세션의 만료 시각만
        # 과거로 앞당겨 "세션 만료"를 독립적으로 강제할 수 있는지 검증한다.
        repo = SessionRepository(self.db)
        from app.core.security import decode_access_token
        payload = decode_access_token(token)
        session_row = repo.get_by_jti(payload["jti"])
        session_row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            core_get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, 401)

    # --------------------------------------------------
    # 감사 로그 — 예외 없이 호출되는지만 확인(파일 내용 자체는 로거 검증)
    # --------------------------------------------------

    def test_log_auth_event_redacts_sensitive_kwargs(self):

        import logging

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("homez.auth.audit")
        handler = _Capture()
        logger.addHandler(handler)
        try:
            log_auth_event("login_success", username="x", password="SHOULD_NOT_APPEAR")
        finally:
            logger.removeHandler(handler)

        joined = " ".join(records)
        self.assertNotIn("SHOULD_NOT_APPEAR", joined)
        self.assertIn("[REDACTED]", joined)


if __name__ == "__main__":
    unittest.main()
