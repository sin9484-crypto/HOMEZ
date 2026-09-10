"""
=========================================================
Homez OS

File : tests/test_sensitive_data_view_permission.py

2026-09-10 Phase 11(HOMEZ_USER_OPERATION_SETTINGS.md 11번 — "개인정보
조회 권한을 별도로 설정한다") — 신규 `VIEW_SENSITIVE_DATA` 권한
코드가 실제로 시딩되는지, `require_permission()`이 SUPER_ADMIN/
일반 역할/명시적으로 부여된 역할을 올바르게 구분하는지 검증한다.

`app/domains/order/router.py`/`app/domains/purchase_task/router.py`의
실제 라우터 함수는 tests/test_order_response_privacy.py(recent-auth
게이트, require_permission은 mock 처리)와 이 파일(권한 판정 자체)이
역할을 분담해서 검증한다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastapi import HTTPException

from app.core.permission_check import has_permission
from app.core.permission_check import require_permission
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class ViewSensitiveDataPermissionTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        # bootstrap_environment()는 Migration만 적용하고 permissions
        # 기준 데이터는 시딩하지 않는다 — 그건 별도로 seed_permissions()
        # (app/desktop/main.py가 실제 부팅 흐름에서 호출하는
        # initialize_seed()의 일부)가 담당한다. roles는 이 테스트가
        # 직접 만들 것이므로(코드 값을 자유롭게 고르기 위해)
        # seed_roles()까지 함께 부르는 initialize_seed()는 부르지
        # 않는다 — DEFAULT_ROLES에 이미 "SUPER_ADMIN" 코드가 있어
        # 아래에서 같은 코드로 새로 만들면 UNIQUE 충돌이 난다.
        from app.database.seed import seed_permissions

        seed_permissions(self.db)
        self.db.commit()

        self.company = Company(
            name="민감정보권한 테스트 회사", business_number="141-41-41414",
            ceo="테스트", phone="02-000-0000",
            email="sensitive-perm@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def test_permission_code_is_seeded(self):

        permission = (
            self.db.query(Permission)
            .filter(Permission.code == "VIEW_SENSITIVE_DATA")
            .first()
        )
        self.assertIsNotNone(
            permission,
            "bootstrap_environment()가 initialize_seed()를 실행했다면 "
            "VIEW_SENSITIVE_DATA 권한이 permissions 테이블에 있어야 "
            "한다.",
        )

    def test_super_admin_always_has_permission_without_explicit_grant(self):

        role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(role)
        self.db.commit()

        user = User(
            company_id=self.company.id, username="superadmin",
            email="superadmin@example.com", password_hash="x",
            name="최고관리자", role_id=role.id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()

        self.assertTrue(
            has_permission(self.db, user, "VIEW_SENSITIVE_DATA"),
        )
        # 예외 없이 통과해야 한다.
        require_permission(self.db, user, "VIEW_SENSITIVE_DATA")

    def test_plain_role_does_not_have_permission_by_default(self):

        role = Role(name="Staff", code="STAFF")
        self.db.add(role)
        self.db.commit()

        user = User(
            company_id=self.company.id, username="staffuser",
            email="staffuser@example.com", password_hash="x",
            name="직원", role_id=role.id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()

        self.assertFalse(
            has_permission(self.db, user, "VIEW_SENSITIVE_DATA"),
        )
        with self.assertRaises(HTTPException) as ctx:
            require_permission(self.db, user, "VIEW_SENSITIVE_DATA")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_role_with_explicit_grant_has_permission(self):

        role = Role(name="CS Staff", code="CS_STAFF")
        self.db.add(role)
        self.db.commit()

        permission = (
            self.db.query(Permission)
            .filter(Permission.code == "VIEW_SENSITIVE_DATA")
            .first()
        )
        self.assertIsNotNone(permission)

        self.db.add(
            RolePermission(role_id=role.id, permission_id=permission.id),
        )
        self.db.commit()

        user = User(
            company_id=self.company.id, username="csstaff",
            email="csstaff@example.com", password_hash="x",
            name="CS 담당자", role_id=role.id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()

        self.assertTrue(
            has_permission(self.db, user, "VIEW_SENSITIVE_DATA"),
        )
        require_permission(self.db, user, "VIEW_SENSITIVE_DATA")

    def test_granting_this_permission_does_not_grant_others(self):
        """VIEW_SENSITIVE_DATA를 개별 부여해도 다른 권한까지 함께
        열리면 안 된다 — 최소권한 원칙."""

        role = Role(name="CS Staff Only", code="CS_STAFF_ONLY")
        self.db.add(role)
        self.db.commit()

        permission = (
            self.db.query(Permission)
            .filter(Permission.code == "VIEW_SENSITIVE_DATA")
            .first()
        )
        self.db.add(
            RolePermission(role_id=role.id, permission_id=permission.id),
        )
        self.db.commit()

        user = User(
            company_id=self.company.id, username="csonly",
            email="csonly@example.com", password_hash="x",
            name="CS 전용", role_id=role.id, is_active=True,
        )
        self.db.add(user)
        self.db.commit()

        self.assertTrue(
            has_permission(self.db, user, "VIEW_SENSITIVE_DATA"),
        )
        self.assertFalse(has_permission(self.db, user, "USER_DELETE"))
        self.assertFalse(has_permission(self.db, user, "COMPANY_UPDATE"))


if __name__ == "__main__":
    unittest.main()
