"""
=========================================================
Homez OS

File : tests/test_auth_login_permissions.py

Gate T(2026-08-10) — 로그인 응답에 역할의 세부 Permission 코드 목록이
포함되는지 검증한다. 이전에는 LoginResponse에 이 값이 전혀 없어
클라이언트의 Permission 기반 nav 표시가 ADMIN/SUPER_ADMIN을 제외한
모든 역할에서 실질적으로 죽어 있었다(발견된 결함, 이번에 수정).
실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.auth.service import AuthService
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.session.model import AuthSession
from app.domains.user.model import User
from app.core.security import hash_password

STRONG_PASSWORD = "Str0ng!Passw0rd"


class LoginPermissionsTestCase(unittest.TestCase):

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
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.company = Company(name="Company A", active=True)
        self.db.add(self.company)
        self.db.flush()

        self.viewer_role = Role(name="Viewer", code="VIEWER")
        self.super_admin_role = Role(name="Super Administrator", code="SUPER_ADMIN")
        self.db.add_all([self.viewer_role, self.super_admin_role])
        self.db.flush()

        self.perm_view = Permission(name="Wizard View", code="listing_wizard.view", active=True)
        self.perm_economics = Permission(name="Economics View", code="listing_wizard.economics_view", active=True)
        self.db.add_all([self.perm_view, self.perm_economics])
        self.db.flush()

        self.db.add(RolePermission(role_id=self.viewer_role.id, permission_id=self.perm_view.id))
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, username, role_id):

        user = User(
            username=username, email=f"{username}@example.com",
            password_hash=hash_password(STRONG_PASSWORD), role_id=role_id,
            company_id=self.company.id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

        return user

    def test_login_returns_granted_permission_codes(self):

        self._create_user("viewer1", self.viewer_role.id)

        result = AuthService(self.db).login("viewer1", STRONG_PASSWORD)

        self.assertEqual(result["permissions"], ["listing_wizard.view"])
        self.assertNotIn("listing_wizard.economics_view", result["permissions"])

    def test_login_returns_empty_permissions_when_role_has_none(self):

        # 아무 것도 부여되지 않은 새 역할로 확인한다(위 setUp의 VIEWER는
        # 이미 1개 부여됨 — 별도의 빈 역할을 만든다).
        empty_role = Role(name="Staff", code="STAFF")
        self.db.add(empty_role)
        self.db.commit()

        self._create_user("staffer1", empty_role.id)

        result = AuthService(self.db).login("staffer1", STRONG_PASSWORD)

        self.assertEqual(result["permissions"], [])

    def test_super_admin_login_permissions_field_is_still_present(self):
        # SUPER_ADMIN은 role_permissions 내용과 무관하게 항상 모든 권한을
        # 통과하지만(app/core/authorization.py::is_super_admin), 로그인
        # 응답 자체는 role_permissions에 실제로 부여된 것만 그대로
        # 반환한다(빈 리스트일 수 있음) — 클라이언트가 role 자체로
        # SUPER_ADMIN을 이미 별도 취급하므로 문제 없다.
        self._create_user("super1", self.super_admin_role.id)

        result = AuthService(self.db).login("super1", STRONG_PASSWORD)

        self.assertIn("permissions", result)
        self.assertIsInstance(result["permissions"], list)


if __name__ == "__main__":
    unittest.main()
