"""
=========================================================
Homez OS

File : tests/test_account_admin.py

HOMEZ Desktop "설정 → 계정 및 보안" 화면 API 검증(2026-07-30).
실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import account_admin
from app.core.security import hash_password
from app.core.security import verify_password
from app.database.base import Base
from app.domains.auth.router import change_password
from app.domains.auth.router import revoke_all_own_sessions
from app.domains.auth.schema import ChangePasswordRequest
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


class AccountAdminTestCase(unittest.TestCase):

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
        with self.engine.begin() as conn:
            conn.exec_driver_sql(AUDIT_LOGS_DDL)

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.super_admin_role = Role(name="Super Administrator", code="SUPER_ADMIN")
        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.db.add_all([self.super_admin_role, self.viewer_role])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, username, role_id, is_active=True, password=STRONG_PASSWORD):

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(password), role_id=role_id, is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    # --------------------------------------------------
    # SUPER_ADMIN 신규 사용자 생성
    # --------------------------------------------------

    def test_super_admin_can_create_new_user(self):

        admin = self._create_user("admin1", self.super_admin_role.id)

        result = account_admin.create_user(
            account_admin.AdminUserCreateRequest(
                username="newbie", email="newbie@example.com",
                password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
                role_code="VIEWER",
            ),
            current_user=admin, db=self.db,
        )

        self.assertEqual(result.username, "newbie")
        self.assertEqual(result.role, "VIEWER")
        self.assertTrue(result.is_active)

        # 감사 로그 기록 확인
        from sqlalchemy import text
        rows = self.db.execute(
            text("SELECT action FROM audit_logs WHERE entity_id = :eid"),
            {"eid": str(result.id)},
        ).fetchall()
        self.assertTrue(any(r[0] == "CREATE_USER" for r in rows))

    def test_create_user_rejects_unknown_role(self):

        admin = self._create_user("admin2", self.super_admin_role.id)

        with self.assertRaises(HTTPException) as ctx:
            account_admin.create_user(
                account_admin.AdminUserCreateRequest(
                    username="x", email="x@example.com",
                    password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
                    role_code="NOT_A_ROLE",
                ),
                current_user=admin, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_user_rejects_weak_password(self):

        admin = self._create_user("admin3", self.super_admin_role.id)

        with self.assertRaises(HTTPException) as ctx:
            account_admin.create_user(
                account_admin.AdminUserCreateRequest(
                    username="weak", email="weak@example.com",
                    password="short", password_confirmation="short",
                    role_code="VIEWER",
                ),
                current_user=admin, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_user_response_never_contains_password_or_hash(self):

        admin = self._create_user("admin4", self.super_admin_role.id)

        result = account_admin.create_user(
            account_admin.AdminUserCreateRequest(
                username="checkme", email="checkme@example.com",
                password=STRONG_PASSWORD, password_confirmation=STRONG_PASSWORD,
                role_code="VIEWER",
            ),
            current_user=admin, db=self.db,
        )
        dumped = result.model_dump()
        self.assertNotIn("password", dumped)
        self.assertNotIn("password_hash", dumped)

    # --------------------------------------------------
    # 활성화/비활성화
    # --------------------------------------------------

    def test_deactivate_user_succeeds_and_revokes_sessions(self):

        admin = self._create_user("admin5", self.super_admin_role.id)
        victim = self._create_user("victim", self.viewer_role.id)

        from datetime import datetime, timedelta
        create_session(
            self.db, user_id=victim.id, jti="victim-jti",
            issued_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        self.db.commit()

        result = account_admin.update_user_active_status(
            victim.id, account_admin.AdminUserActiveUpdateRequest(active=False),
            current_user=admin, db=self.db,
        )

        self.assertFalse(result.is_active)

        session_row = SessionRepository(self.db).get_by_jti("victim-jti")
        self.assertIsNotNone(session_row.revoked_at)

    def test_self_deactivation_is_blocked(self):

        admin = self._create_user("admin6", self.super_admin_role.id)

        with self.assertRaises(HTTPException) as ctx:
            account_admin.update_user_active_status(
                admin.id, account_admin.AdminUserActiveUpdateRequest(active=False),
                current_user=admin, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_last_active_super_admin_cannot_be_deactivated(self):

        admin1 = self._create_user("only_admin", self.super_admin_role.id)
        other_viewer = self._create_user("someone_else", self.viewer_role.id)

        # someone_else(SUPER_ADMIN 아님)가 admin1을 비활성화하려는 시도라도
        # 마지막 활성 SUPER_ADMIN 보호는 role 자체 기준으로 걸려야 한다.
        with self.assertRaises(HTTPException) as ctx:
            account_admin.update_user_active_status(
                admin1.id, account_admin.AdminUserActiveUpdateRequest(active=False),
                current_user=other_viewer, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_second_super_admin_can_be_deactivated(self):

        admin1 = self._create_user("admin_a", self.super_admin_role.id)
        admin2 = self._create_user("admin_b", self.super_admin_role.id)

        result = account_admin.update_user_active_status(
            admin2.id, account_admin.AdminUserActiveUpdateRequest(active=False),
            current_user=admin1, db=self.db,
        )
        self.assertFalse(result.is_active)

    # --------------------------------------------------
    # 본인 비밀번호 변경 → 기존 세션 폐기
    # --------------------------------------------------

    def test_change_password_revokes_all_sessions(self):

        from datetime import datetime, timedelta

        user = self._create_user("selfchange", self.viewer_role.id)
        create_session(
            self.db, user_id=user.id, jti="self-jti-1",
            issued_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        self.db.commit()

        response = change_password(
            ChangePasswordRequest(
                current_password=STRONG_PASSWORD,
                new_password="NewStr0ng!Pass",
                new_password_confirmation="NewStr0ng!Pass",
            ),
            current_user=user, db=self.db,
        )

        self.assertIn("message", response)

        self.db.refresh(user)
        self.assertTrue(verify_password("NewStr0ng!Pass", user.password_hash))

        session_row = SessionRepository(self.db).get_by_jti("self-jti-1")
        self.assertIsNotNone(session_row.revoked_at)

    def test_change_password_rejects_wrong_current_password(self):

        user = self._create_user("wrongcur", self.viewer_role.id)

        with self.assertRaises(HTTPException) as ctx:
            change_password(
                ChangePasswordRequest(
                    current_password="NotTheRealOne1!",
                    new_password="NewStr0ng!Pass",
                    new_password_confirmation="NewStr0ng!Pass",
                ),
                current_user=user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_change_password_rejects_confirmation_mismatch(self):

        user = self._create_user("mismatch", self.viewer_role.id)

        with self.assertRaises(HTTPException) as ctx:
            change_password(
                ChangePasswordRequest(
                    current_password=STRONG_PASSWORD,
                    new_password="NewStr0ng!Pass",
                    new_password_confirmation="Different1!",
                ),
                current_user=user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_revoke_all_own_sessions_endpoint(self):

        from datetime import datetime, timedelta

        user = self._create_user("revokeall", self.viewer_role.id)
        create_session(
            self.db, user_id=user.id, jti="revoke-jti-1",
            issued_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        self.db.commit()

        revoke_all_own_sessions(current_user=user, db=self.db)

        session_row = SessionRepository(self.db).get_by_jti("revoke-jti-1")
        self.assertIsNotNone(session_row.revoked_at)


if __name__ == "__main__":
    unittest.main()
