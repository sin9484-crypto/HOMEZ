"""
=========================================================
Homez OS

File : tests/test_homez_auth_login.py

HOMEZ Desktop 로그인 흐름 — 실제 인증 검증(2026-07-30).

이 파일은 2026-07-30 재감사에서 발견된 두 가지 사전 존재 Critical/High
결함의 수정을 회귀로 고정한다:

1. `app/domains/user/model.py`가 실제 homez.db의 `users` 테이블
   스키마와 전혀 달라(예: password_hash vs 실제 컬럼 password) 단순
   조회조차 `OperationalError`로 실패하던 문제.
2. `passlib` 1.7.4가 설치된 `bcrypt` 5.0.0과 비호환이라
   `hash_password`/`verify_password` 호출 자체가 항상 실패하던 문제
   (`app/core/security.py`를 bcrypt 직접 호출로 전환해 해결).

또한 `app/domains/company/model.py`(Company.users 등 mapper 오류),
`app/domains/role/model.py`/`app/domains/role_permission/model.py`/
`app/domains/permission/policy.py`(순환 import로 인한
configure_mappers() 실패)의 수정도 함께 검증한다.

실제 homez.db는 사용하지 않는다 — Base.metadata.create_all로 격리된
임시 SQLite 파일에 User/Company/Role/RolePermission/Permission
테이블만 생성하고 직접 시딩한다.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.authorization import UserRole
from app.core.authorization import has_role
from app.core.security import hash_password
from app.core.security import verify_password
from app.database.base import Base
from app.domains.auth.service import AuthService
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User


class MapperConfigurationTestCase(unittest.TestCase):
    """
    Company.users 등의 mapper 오류(NoForeignKeysError)와 role/
    role_permission/permission 사이의 순환 import가 모두 해소되어
    전체 app.main을 그대로 import해도 configure_mappers()가
    성공하는지 확인한다 — 이전에는 이 시점에서 500 계열 예외로
    Desktop 로그인 경로 전체가 막혀 있었다.
    """

    def test_full_app_import_configures_mappers_without_error(self):

        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable, "-c",
                "import app.main\n"
                "from sqlalchemy.orm import configure_mappers\n"
                "configure_mappers()\n"
                "print('OK', len(app.main.app.routes))\n",
            ],
            capture_output=True, text=True, timeout=60,
        )

        self.assertEqual(
            result.returncode, 0,
            f"app.main import 또는 configure_mappers()가 실패했습니다.\n"
            f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("OK", result.stdout)


class RealLoginFlowTestCase(unittest.TestCase):
    """
    Base.metadata.create_all로 만든 격리 DB에 실제 User/Role 행을 만들고
    AuthService.login()을 실제로 호출한다(mock 없음).
    """

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
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        self.admin_role = Role(
            name="Administrator", code="ADMIN", description="관리자",
        )
        self.viewer_role = Role(
            name="Viewer", code="VIEWER", description="조회 전용",
        )
        self.db.add_all([self.admin_role, self.viewer_role])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(
        self,
        username: str,
        password: str,
        role_id: int | None,
        is_active: bool = True,
    ) -> User:

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
    # bcrypt/passlib 재현 + 수정 확인
    # --------------------------------------------------

    def test_hash_and_verify_password_round_trip(self):

        hashed = hash_password("Test1234!")

        self.assertTrue(verify_password("Test1234!", hashed))
        self.assertFalse(verify_password("WrongPassword!", hashed))

    def test_password_hash_is_not_plaintext(self):

        hashed = hash_password("Test1234!")

        self.assertNotEqual(hashed, "Test1234!")
        self.assertTrue(hashed.startswith("$2b$") or hashed.startswith("$2a$"))

    # --------------------------------------------------
    # 성공/실패/비활성 로그인
    # --------------------------------------------------

    def test_successful_login_issues_access_token(self):

        self._create_user("admin1", "Test1234!", self.admin_role.id)

        auth = AuthService(self.db)
        result = auth.login("admin1", "Test1234!")

        self.assertTrue(result["access_token"])
        self.assertEqual(result["token_type"], "bearer")
        self.assertEqual(result["user"].username, "admin1")

    def test_wrong_password_is_rejected(self):

        self._create_user("admin2", "Test1234!", self.admin_role.id)

        auth = AuthService(self.db)

        with self.assertRaises(ValueError):
            auth.login("admin2", "WrongPassword!")

    def test_unknown_username_is_rejected(self):

        auth = AuthService(self.db)

        with self.assertRaises(ValueError):
            auth.login("does_not_exist", "whatever")

    def test_inactive_account_is_rejected(self):
        """
        2026-08-03: 계정 존재/부재를 응답에서 구분하지 않기 위해
        비활성 계정도 존재하지 않는 계정/틀린 비밀번호와 동일하게
        ValueError(일반 오류)를 던지도록 통일했다 — 이전에는
        PermissionError(403, "Inactive user")로 따로 구분돼 그 자체가
        계정 열거 신호였다.
        """

        self._create_user(
            "inactive1", "Test1234!", self.admin_role.id, is_active=False,
        )

        auth = AuthService(self.db)

        with self.assertRaises(ValueError):
            auth.login("inactive1", "Test1234!")

    # --------------------------------------------------
    # 역할 기반 권한 판정 (has_role 대소문자 수정 확인)
    # --------------------------------------------------

    def test_admin_role_code_grants_admin_guard(self):

        user = self._create_user("admin3", "Test1234!", self.admin_role.id)

        self.assertEqual(user.role, "ADMIN")
        self.assertTrue(
            has_role(user, UserRole.ADMIN, UserRole.SUPER_ADMIN),
        )

    def test_viewer_role_code_does_not_grant_admin_guard(self):

        user = self._create_user("viewer1", "Test1234!", self.viewer_role.id)

        self.assertEqual(user.role, "VIEWER")
        self.assertFalse(
            has_role(user, UserRole.ADMIN, UserRole.SUPER_ADMIN),
        )

    def test_user_without_role_has_no_permissions(self):

        user = self._create_user("norole1", "Test1234!", role_id=None)

        self.assertIsNone(user.role)
        self.assertFalse(
            has_role(user, UserRole.ADMIN, UserRole.SUPER_ADMIN),
        )


if __name__ == "__main__":
    unittest.main()
