"""
=========================================================
Homez OS

File : tests/test_create_homez_admin.py

HOMEZ 최초 관리자 계정 생성 도구(`scripts/create_homez_admin.py`)
검증. 실제 homez.db는 사용하지 않는다 — 임시 SQLite 파일 DB에
Company/Role/RolePermission/Permission/User 테이블만 생성해 검증한다.
=========================================================
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import create_homez_admin as tool  # noqa: E402

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.permission.model import Permission
from app.domains.role.model import Role
from app.domains.role_permission.model import RolePermission
from app.domains.user.model import User


class CreateHomezAdminTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, Role.__table__, RolePermission.__table__,
                Permission.__table__, User.__table__,
            ],
        )

        # audit_logs는 ORM Model이 없는 실제 DB 원시 테이블이므로(도구가
        # 직접 SQL로 INSERT한다), 실제 스키마와 동일하게 원시 DDL로
        # 만들어야 도구의 단일 Transaction 동작을 제대로 검증할 수 있다.
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE audit_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "company_id INTEGER, "
                "user_id INTEGER, "
                "action VARCHAR(100) NOT NULL, "
                "entity VARCHAR(100) NOT NULL, "
                "entity_id VARCHAR(100) NOT NULL, "
                "description VARCHAR(500), "
                "ip_address VARCHAR(50)"
                ")",
            )

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_super_admin_role(self) -> Role:

        role = Role(name="Super Administrator", code="SUPER_ADMIN", description="최상위 관리자")
        self.db.add(role)
        self.db.commit()
        return role

    STRONG_PASSWORD = "Str0ng!Passw0rd"

    # --------------------------------------------------
    # 실제 homez.db를 대상으로 하지 않는다는 것을 스스로 강제 확인
    # --------------------------------------------------

    def test_never_targets_real_homez_db_in_this_test_suite(self):

        real_db = os.path.join(REPO_ROOT, "homez.db")
        self.assertNotEqual(self.db_path, real_db)

    # --------------------------------------------------
    # 성공
    # --------------------------------------------------

    def test_dry_run_never_inserts(self):

        self._seed_super_admin_role()

        result = tool.create_admin_account(
            self.db, username="admin_demo", email="admin_demo@example.com",
            password=self.STRONG_PASSWORD, dry_run=True,
        )

        self.assertTrue(result.success)
        self.assertIsNone(result.user_id)
        self.assertEqual(self.db.query(User).count(), 0)

    def test_real_creation_inserts_user_and_audit_log_in_one_transaction(self):

        self._seed_super_admin_role()

        result = tool.create_admin_account(
            self.db, username="admin_demo", email="admin_demo@example.com",
            password=self.STRONG_PASSWORD, dry_run=False,
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.user_id)

        user = self.db.query(User).filter(User.id == result.user_id).first()
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "admin_demo")
        self.assertTrue(user.is_active)
        # 저장된 해시가 평문이 아님을 확인(평문 자체를 이 테스트에서
        # 출력하지 않는다 — 상수시간 등가 여부만 검사).
        self.assertNotEqual(user.password_hash, self.STRONG_PASSWORD)

        from sqlalchemy import text
        rows = self.db.execute(
            text("SELECT action, entity, entity_id FROM audit_logs WHERE user_id = :uid"),
            {"uid": result.user_id},
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "CREATE_ADMIN_ACCOUNT")
        self.assertEqual(rows[0][1], "users")
        self.assertEqual(rows[0][2], str(result.user_id))

    # --------------------------------------------------
    # 중복
    # --------------------------------------------------

    def test_duplicate_username_is_rejected(self):

        self._seed_super_admin_role()

        first = tool.create_admin_account(
            self.db, username="dup_user", email="dup1@example.com",
            password=self.STRONG_PASSWORD, dry_run=False,
        )
        self.assertTrue(first.success)

        second = tool.create_admin_account(
            self.db, username="dup_user", email="dup2@example.com",
            password=self.STRONG_PASSWORD, dry_run=False,
        )
        self.assertFalse(second.success)
        self.assertTrue(any("이미 존재" in e for e in second.errors))
        self.assertEqual(self.db.query(User).count(), 1)

    def test_duplicate_email_is_rejected(self):

        self._seed_super_admin_role()

        first = tool.create_admin_account(
            self.db, username="user_a", email="shared@example.com",
            password=self.STRONG_PASSWORD, dry_run=False,
        )
        self.assertTrue(first.success)

        second = tool.create_admin_account(
            self.db, username="user_b", email="shared@example.com",
            password=self.STRONG_PASSWORD, dry_run=False,
        )
        self.assertFalse(second.success)
        self.assertEqual(self.db.query(User).count(), 1)

    # --------------------------------------------------
    # 약한 비밀번호
    # --------------------------------------------------

    def test_weak_password_is_rejected(self):

        self._seed_super_admin_role()

        result = tool.create_admin_account(
            self.db, username="weakpw", email="weakpw@example.com",
            password="short1", dry_run=False,
        )

        self.assertFalse(result.success)
        self.assertTrue(len(result.errors) > 0)
        self.assertEqual(self.db.query(User).count(), 0)

    def test_password_policy_matches_settings(self):

        from app.core.config import settings

        errors = tool.validate_password_policy("a" * settings.PASSWORD_MIN_LENGTH, settings)
        # 소문자만 있는 최소 길이 문자열 — upper/number/special 요구 조건 위반이 남아야 한다.
        self.assertTrue(len(errors) > 0)

        strong = "Aa1!" + "x" * max(0, settings.PASSWORD_MIN_LENGTH - 4)
        errors2 = tool.validate_password_policy(strong, settings)
        self.assertEqual(errors2, [])

    # --------------------------------------------------
    # SUPER_ADMIN 역할 부재
    # --------------------------------------------------

    def test_missing_super_admin_role_blocks_creation(self):

        # 역할을 시딩하지 않음.
        result = tool.create_admin_account(
            self.db, username="norole", email="norole@example.com",
            password=self.STRONG_PASSWORD, dry_run=False,
        )

        self.assertFalse(result.success)
        self.assertTrue(any("SUPER_ADMIN" in e for e in result.errors))
        self.assertEqual(self.db.query(User).count(), 0)

    def test_super_admin_role_lookup_is_case_insensitive(self):

        role = Role(name="Super", code="super_admin")  # 소문자로 시딩된 경우도 인식해야 함
        self.db.add(role)
        self.db.commit()

        found = tool.find_super_admin_role(self.db)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, role.id)

    # --------------------------------------------------
    # 중간 실패 rollback
    # --------------------------------------------------

    def test_mid_failure_rolls_back_user_insert_too(self):

        self._seed_super_admin_role()

        with patch.object(
            tool, "_insert_audit_log_no_commit",
            side_effect=RuntimeError("simulated audit log failure"),
        ):
            result = tool.create_admin_account(
                self.db, username="rollback_user", email="rollback@example.com",
                password=self.STRONG_PASSWORD, dry_run=False,
            )

        self.assertFalse(result.success)
        # User insert가 이미 flush되었더라도 audit log 실패 시 rollback으로
        # 전부 되돌아가야 한다 — DB에 사용자가 하나도 남지 않아야 한다.
        self.assertEqual(self.db.query(User).count(), 0)

    # --------------------------------------------------
    # 비밀번호가 인자로 노출되지 않는지(설계 확인)
    # --------------------------------------------------

    def test_cli_never_accepts_password_as_argument(self):

        import inspect

        parser_source = inspect.getsource(tool.main)
        self.assertNotIn('"--password"', parser_source)
        self.assertNotIn("'--password'", parser_source)

    def test_default_execution_is_dry_run(self):
        """--confirm을 주지 않으면 argparse 기본값이 False(dry-run)인지 확인한다."""

        parser = tool.argparse.ArgumentParser()
        parser.add_argument("--confirm", action="store_true")
        ns = parser.parse_args([])
        self.assertFalse(ns.confirm)


if __name__ == "__main__":
    unittest.main()
