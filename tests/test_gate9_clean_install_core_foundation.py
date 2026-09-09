"""
=========================================================
Homez OS

File : tests/test_gate9_clean_install_core_foundation.py

V7 Gate 9(2026-08-16) — 완전히 새 PC와 동등한 클린 디렉터리(DB 파일이
아예 존재하지 않는 상태)에서 공식 부트스트랩 경로만으로 신규 설치가
실제로 성공하는지 고정한다.

배경(실측으로 발견한 Critical 결함): 기존 22개 Migration 파일을
전부 순서대로 적용해도 companies/users/roles/permissions 등 10개
핵심 테이블이 전혀 생성되지 않았다 — 그 상태에서 공식 최초 관리자
생성 흐름(`app/core/first_admin_setup.py::atomic_create_first_admin()`)
을 호출하면 `sqlite3.OperationalError: no such table: users`로 즉시
실패했다. `app/database/init_db.py::initialize_database()`가 이
테이블들을 `Base.metadata.create_all()`로 선언하지만 저장소 어디에서도
호출되지 않는 죽은 코드였고, 공식 부트스트랩 경로
(`app/desktop/main.py` → `app/database/bootstrap.py::
bootstrap_environment()`)는 `MigrationRunner.apply_pending()`만
호출한다. 실제 운영 homez.db에는 이 테이블들이 Migration 시스템
도입 이전부터 이미 존재해 지금까지 아무도 이 결함을 알아채지
못했다 — 전체 회귀 테스트도 전부 `Base.metadata.create_all()`로 만든
임시 DB에서 실행돼 파일 기반 Migration 경로를 거치지 않기 때문이다.

수정: `migrations/20260816_00_create_v7_gate9_core_foundation_schema.sql`
신규 작성(실제 운영 homez.db의 현재 스키마를 그대로 옮김 — 이유는 그
파일 상단 주석 참고). 이 테스트는 그 수정이 실제로 "DB 파일이 아예
없는 상태"에서 신규 설치를 성립시키는지 고정한다. 실제 homez.db는
이 테스트 전체에서 전혀 사용하지 않는다(전부 임시 디렉터리).
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.first_admin_setup import (
    FirstAdminSetupStatus,
    atomic_create_first_admin,
)
from app.database.bootstrap import bootstrap_environment

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATIONS_DIR = REPO_ROOT / "migrations"

CORE_TABLES = (
    "companies", "users", "roles", "permissions", "role_permissions",
    "categories", "brands", "suppliers", "products", "marketplaces",
)


class CleanInstallCoreFoundationTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate9_test_"))
        self.db_path = self.tmp_dir / "data" / "homez.db"
        self.backups_dir = self.tmp_dir / "backups"

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_bootstrap_from_no_db_creates_all_core_tables(self):

        self.assertFalse(self.db_path.exists())

        result = bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )

        self.assertTrue(result.is_new_install)
        self.assertFalse(result.migration_approval_required)
        self.assertTrue(self.db_path.exists())

        conn = sqlite3.connect(str(self.db_path))
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
        finally:
            conn.close()

        for table in CORE_TABLES:
            self.assertIn(
                table, tables,
                f"클린 설치 후 핵심 테이블 '{table}'이 생성되지 않았다 "
                "— fresh-install 회귀.",
            )

    def test_first_admin_setup_succeeds_after_clean_bootstrap(self):

        bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )

        outcome = atomic_create_first_admin(
            str(self.db_path),
            username="gate9_admin",
            email="gate9_admin@example.com",
            password_hash="dummy_hash_for_test",
            role_id=1,
            company_name="Gate9 Test Company",
            name="Gate 9 Admin",
        )

        self.assertEqual(outcome.status, FirstAdminSetupStatus.SUCCESS)
        self.assertIsNotNone(outcome.user_id)
        self.assertIsNotNone(outcome.company_id)

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
                1,
            )
        finally:
            conn.close()

    def test_second_bootstrap_run_does_not_re_treat_as_new_install(self):

        first = bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(first.is_new_install)

        second = bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertFalse(second.is_new_install)


if __name__ == "__main__":
    unittest.main()
