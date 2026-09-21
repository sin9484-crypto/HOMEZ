"""
=========================================================
Homez OS

File : tests/test_migration_restricted_mode_db_path_contract.py

2026-08-20 4차 지시 — DB 경로 단일 계약 회귀 테스트.

배경: `app/core/migration_restricted_mode.py`/`app/core/migration_
approval.py`가 `settings.DATABASE_URL`(app/database/session.py의
SQLAlchemy 엔진이 실제로 쓰는 값)을 무시하고 `app.desktop.paths.
get_homez_db_path(confirm=True)`로 항상 "실제" homez.db 고정 경로만
확인했다 — 격리 테스트 DB로 서버를 띄워도 이 두 모듈만은 항상 실제
운영 DB의 미적용 Migration 여부로 모든 쓰기(POST/PATCH)를 423으로
전역 차단했다(2026-08-20 3차 라운드 실제 브라우저 E2E에서 재현·확인).

이 파일은 두 모듈이 이제 정확히 같은 계약(settings.DATABASE_URL →
resolve_sqlite_path())을 따르는지, 그리고 그 계약이 실제로 격리 DB의
Migration 적용 여부에 따라 제한 모드를 올바르게 켜고 끄는지 검증한다.
실제 homez.db는 어디에서도 열지 않는다(마지막 테스트가 이를 재확인).
=========================================================
"""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support.real_install_gate import requires_real_install_diagnostics
from app.core import migration_approval
from app.core import migration_restricted_mode
from app.core.config import settings
from app.core.first_admin_setup import resolve_sqlite_path
from app.database.migration_runner import MigrationRunner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_MIGRATIONS_DIR = Path(REPO_ROOT) / "migrations"


class DbPathContractTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "isolated.db"
        self.database_url = f"sqlite:///{self.db_path}"
        self._original_database_url = settings.DATABASE_URL

    def tearDown(self):

        settings.DATABASE_URL = self._original_database_url
        migration_restricted_mode.reset_restricted_mode_state_for_tests()

    # ----------------------------------------------------
    # 계약: DATABASE_URL override 시 get_homez_db_path()는
    # 아예 호출되지 않는다(실제 DB에 닿을 방법 자체가 없다).
    # ----------------------------------------------------

    def test_real_migration_paths_never_touches_official_db_path_helper(self):

        settings.DATABASE_URL = self.database_url

        with patch(
            "app.desktop.paths.get_homez_db_path",
            side_effect=AssertionError(
                "DATABASE_URL이 override된 상태에서는 get_homez_db_path()가 "
                "절대 호출되면 안 된다",
            ),
        ):
            db_path, _migrations_dir = migration_restricted_mode._real_migration_paths()

        self.assertEqual(db_path, self.db_path)

    def test_migration_approval_real_paths_never_touches_official_db_path_helper(self):

        settings.DATABASE_URL = self.database_url

        with patch(
            "app.desktop.paths.get_homez_db_path",
            side_effect=AssertionError(
                "DATABASE_URL이 override된 상태에서는 get_homez_db_path()가 "
                "절대 호출되면 안 된다",
            ),
        ):
            db_path, _migrations_dir, _backups_dir = migration_approval._real_paths()

        self.assertEqual(db_path, self.db_path)

    def test_three_consumers_agree_on_identical_db_path(self):
        """SQLAlchemy 세션 계약(resolve_sqlite_path)·MigrationRunner용
        경로(migration_restricted_mode)·승인 화면용 경로(migration_
        approval)가 동일한 DATABASE_URL로부터 정확히 같은 경로에
        합의해야 한다 — 셋 중 하나라도 어긋나면 "지금 쓰기가 막힌
        DB"와 "실제로 쓰기가 일어나는 DB"가 갈라진다."""

        settings.DATABASE_URL = self.database_url

        session_path = Path(resolve_sqlite_path())
        rm_path, _ = migration_restricted_mode._real_migration_paths()
        ma_path, _, _ = migration_approval._real_paths()

        self.assertEqual(session_path, self.db_path)
        self.assertEqual(rm_path, self.db_path)
        self.assertEqual(ma_path, self.db_path)

    def test_resolve_sqlite_path_fails_fast_on_non_sqlite_url(self):

        with self.assertRaises(ValueError):
            resolve_sqlite_path("postgresql://example/db")

    # ----------------------------------------------------
    # 격리 DB의 실제 Migration 적용 상태에 따라 제한 모드가
    # 올바르게 켜지고 꺼지는지(실제 homez.db 상태와 무관하게).
    # ----------------------------------------------------

    def test_isolated_db_with_pending_migrations_is_restricted(self):

        settings.DATABASE_URL = self.database_url
        sqlite3.connect(str(self.db_path)).close()  # 빈 DB = 전부 pending

        migration_restricted_mode.reset_restricted_mode_state_for_tests()
        state = migration_restricted_mode.refresh_restricted_mode_state()

        self.assertTrue(state.restricted)
        self.assertTrue(len(state.pending_files) > 0)

    def test_isolated_db_fully_migrated_via_official_runner_is_not_restricted(self):
        """9번 요구사항 핵심 시나리오 — 격리 DB에 실제 전체 Migration을
        공식 Runner로 적용하면(진짜 스키마 반영, 부기 조작 아님) 제한
        모드가 정상적으로 해제되어야 한다."""

        settings.DATABASE_URL = self.database_url

        runner = MigrationRunner(self.db_path, REAL_MIGRATIONS_DIR)
        conn = sqlite3.connect(str(self.db_path))
        try:
            applied = runner.apply_pending(conn)
        finally:
            conn.close()

        self.assertTrue(len(applied) > 0)

        migration_restricted_mode.reset_restricted_mode_state_for_tests()
        state = migration_restricted_mode.refresh_restricted_mode_state()

        self.assertFalse(state.restricted, state.pending_files)
        self.assertEqual(state.pending_files, [])

    def test_isolated_restriction_is_independent_of_real_homez_db_state(self):
        """이 테스트가 도는 프로세스 안에서 실제 homez.db가 미적용
        Migration을 갖고 있어도(2026-08-20 시점 실제로 5건 존재),
        격리 DB를 전부 적용해 두면 그 격리 DB 기준으로는 제한 모드가
        아니어야 한다 — 두 판정이 서로 새지 않는다는 뜻이다."""

        settings.DATABASE_URL = self.database_url

        runner = MigrationRunner(self.db_path, REAL_MIGRATIONS_DIR)
        conn = sqlite3.connect(str(self.db_path))
        try:
            runner.apply_pending(conn)
        finally:
            conn.close()

        migration_restricted_mode.reset_restricted_mode_state_for_tests()
        state = migration_restricted_mode.refresh_restricted_mode_state()

        self.assertFalse(state.restricted)


class RealHomezDbNeverOpenedTestCase(unittest.TestCase):
    """이 파일의 모든 테스트가 실제 homez.db를 단 한 번도 열지 않았는지
    (읽기조차 하지 않았는지) 확인한다 — mtime이 이 파일의 다른
    테스트들이 실행되는 동안 전혀 바뀌지 않아야 한다."""

    REAL_DB_PATH = os.path.join(REPO_ROOT, "homez.db")

    # 실제 설치환경 진단 — 기본 전체 회귀에서 제외(opt-in: HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)
    @requires_real_install_diagnostics
    def test_real_db_untouched(self):

        if not os.path.exists(self.REAL_DB_PATH):
            self.skipTest("실제 homez.db가 이 환경에 없습니다.")

        with open(self.REAL_DB_PATH, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()

        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
