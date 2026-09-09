"""
=========================================================
Homez OS

File : tests/test_migration_restricted_mode_schema_error_handling.py

2026-09-08 사고 재발 방지 — item 7 작업 중 실제로 겪은 장애를 그대로
재현한다: 대기 중인 Migration이 있는(제한 모드) 상태에서, 그 Migration
이 추가하는 컬럼을 이미 참조하는 Model로 기존 GET 조회를 실행하면
`OperationalError: no such column`이 그대로 500으로 터졌다.
`app/main.py`의 `_enforce_migration_restricted_mode` 미들웨어는
GET/HEAD/OPTIONS를 항상 통과시키므로(쓰기만 차단) 이 실패를 막지
못한다.

이 테스트는 (1) 이 정확한 실패를 임시 SQLite 파일 DB로 재현하고,
(2) app/main.py에 새로 추가한 `_handle_operational_error` 전역
핸들러가 제한 모드일 때만 이 실패를 명확한 "갱신 필요" 응답(503,
SCHEMA_UPDATE_REQUIRED)으로 바꾸는지, (3) 제한 모드가 아니거나
스키마 불일치로 보이지 않는 다른 OperationalError는 그대로 다시
던져 기존 처리(진짜 DB 오류를 숨기지 않음)를 유지하는지 확인한다.
실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import asyncio
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core import migration_restricted_mode as rm
from app.core.config import settings
from app.domains.company.model import Company
from app.domains.purchase_task.repository import PurchaseTaskRepository
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"
NEW_MIGRATION = "20260908_00_create_purchase_channel_connection_schema.sql"

# 2026-09-09 — 20260908_01_create_purchase_order_submission_attempts.sql이
# NEW_MIGRATION보다 늦게 추가되면서(사전순으로 NEW_MIGRATION 뒤에 위치),
# "NEW_MIGRATION 하나만 빼면 나머지는 전부 이전 상태"라는 이 파일의 원래
# 가정이 깨졌다 — 제외 대상에 없는 이 파일이 prior_only_dir에 그대로
# 복사·적용되어 NEW_MIGRATION 없이 그보다 사전순으로 뒤인 파일만 적용된
# 상태가 되면서 MigrationRunner.diagnose()가 OrderInversionError를
# 정확히 잡아냈다(전체 회귀 2026-09-09 실행에서 재현). 같은 작업 단위
# (item 7)이므로 "이전 상태"에서는 둘 다 제외해야 한다.
EXCLUDED_FROM_PRIOR_STATE = {
    NEW_MIGRATION,
    "20260908_01_create_purchase_order_submission_attempts.sql",
}


def _fake_request(path: str = "/purchase-tasks"):

    return SimpleNamespace(url=SimpleNamespace(path=path))


class SchemaMismatchReproductionTestCase(unittest.TestCase):
    """실제 실패를 임시 DB로 재현 — 신규 Migration 파일만 빼고 전부
    적용한 뒤 현재 Model(그 파일이 추가하는 컬럼을 포함)로 조회한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.addCleanup(lambda: self.db_path.exists() and self.db_path.unlink())

        # 신규 파일만 제외한 "이전 상태" migrations 디렉터리를 만든다
        # (바이트 그대로 복사 — checksum 불일치를 피하기 위함).
        self.prior_only_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.prior_only_dir, ignore_errors=True))
        for name in sorted(os.listdir(MIGRATIONS_DIR)):
            if not name.endswith(".sql") or name in EXCLUDED_FROM_PRIOR_STATE:
                continue
            shutil.copyfile(MIGRATIONS_DIR / name, self.prior_only_dir / name)

        from app.database.migration_runner import MigrationRunner

        prior_runner = MigrationRunner(self.db_path, self.prior_only_dir)
        conn = sqlite3.connect(str(self.db_path))
        try:
            prior_runner.apply_pending(conn)
        finally:
            conn.close()

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        company = Company(
            name="재현 회사", business_number="888-88-88888",
            ceo="재현", phone="02-000-0000",
            email="repro@example.com", address="재현",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

        self._original_database_url = settings.DATABASE_URL
        self.addCleanup(self._restore_settings)

    def _restore_settings(self):

        settings.DATABASE_URL = self._original_database_url
        rm.reset_restricted_mode_state_for_tests()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

    def test_existing_query_fails_with_no_such_column_on_unmigrated_schema(self):
        """재현: 신규 컬럼을 아직 갖지 않은 스키마에서 기존 조회가
        정확히 어떤 예외로 실패하는지."""

        repository = PurchaseTaskRepository(self.db)
        with self.assertRaises(OperationalError) as ctx:
            repository.list_tasks(self.company_id)

        message = str(ctx.exception.orig).lower()
        self.assertIn("no such column", message)
        self.assertIn("channel_connection_id", message)

    def test_handler_converts_to_clear_update_required_response_when_restricted(self):

        from app.main import SCHEMA_UPDATE_REQUIRED_ERROR_CODE
        from app.main import _handle_operational_error

        settings.DATABASE_URL = f"sqlite:///{self.db_path}"
        rm.reset_restricted_mode_state_for_tests()
        state = rm.refresh_restricted_mode_state()
        self.assertTrue(state.restricted)
        self.assertIn(NEW_MIGRATION, state.pending_files)

        repository = PurchaseTaskRepository(self.db)
        try:
            repository.list_tasks(self.company_id)
            self.fail("이 시점에는 여전히 OperationalError가 나야 합니다.")
        except OperationalError as exc:
            response = asyncio.run(
                _handle_operational_error(_fake_request(), exc),
            )

        self.assertEqual(response.status_code, 503)
        body = response.body.decode("utf-8")
        self.assertIn(SCHEMA_UPDATE_REQUIRED_ERROR_CODE, body)
        self.assertIn(
            SCHEMA_UPDATE_REQUIRED_ERROR_CODE,
            response.headers.get("x-migration-restricted-code", ""),
        )

    def test_handler_reraises_when_not_restricted(self):
        """제한 모드가 아니면(예: 이 DB에 신규 파일까지 전부 적용된
        뒤) 같은 종류의 에러라도 그대로 다시 던진다 — 이 핸들러가
        기존 500 처리를 대체하지 않는다는 것을 확인."""

        from app.main import _handle_operational_error

        settings.DATABASE_URL = f"sqlite:///{self.db_path}"
        rm.reset_restricted_mode_state_for_tests()
        # 신규 파일까지 마저 적용해 더 이상 pending이 없게 만든다.
        from app.database.migration_runner import MigrationRunner

        full_runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)
        conn = sqlite3.connect(str(self.db_path))
        try:
            full_runner.apply_pending(conn)
        finally:
            conn.close()
        state = rm.refresh_restricted_mode_state()
        self.assertFalse(state.restricted)

        # 이미 잡아둔 예외 객체(재현 케이스에서 재사용)로 핸들러만 다시
        # 검증한다 — 제한 모드가 아니므로 그대로 다시 던져져야 한다.
        fake_exc = OperationalError(
            "SELECT 1", {}, sqlite3.OperationalError("no such column: x.y"),
        )
        with self.assertRaises(OperationalError):
            asyncio.run(_handle_operational_error(_fake_request(), fake_exc))

    def test_handler_reraises_unrelated_operational_errors_even_when_restricted(self):
        """스키마 불일치로 보이지 않는 다른 OperationalError(예: DB
        잠김)는 제한 모드여도 그대로 다시 던진다 — 진짜 DB 결함을 이
        메시지로 가리지 않는다."""

        from app.main import _handle_operational_error

        settings.DATABASE_URL = f"sqlite:///{self.db_path}"
        rm.reset_restricted_mode_state_for_tests()
        state = rm.refresh_restricted_mode_state()
        self.assertTrue(state.restricted)

        unrelated_exc = OperationalError(
            "INSERT INTO x", {}, sqlite3.OperationalError("database is locked"),
        )
        with self.assertRaises(OperationalError):
            asyncio.run(_handle_operational_error(_fake_request(), unrelated_exc))


if __name__ == "__main__":
    unittest.main()
