"""
=========================================================
Homez OS

File : tests/test_audit_logs_created_at_migration.py

작업 6(2026-08-21) — migrations/20260821_00_add_audit_logs_created_at.sql
검증. audit_logs 자체를 만드는 20260810_00_create_audit_logs_schema.sql
(Gate U-2) 위에 이 Migration 파일 하나만 추가 적용해, 컬럼 추가 결과와
app/core/audit_db.py::write_audit_log()의 Migration 적용 전/후 양쪽
동작을 함께 검증한다. 실제 homez.db는 전혀 열지 않는다 — 전부 신규
SQLite 임시 파일에서만 동작한다.
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.audit_db import write_audit_log
from app.database.migration_runner import MigrationExecutionError
from app.database.migration_runner import MigrationRunner

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"
_BASE_MIGRATION_FILENAME = "20260810_00_create_audit_logs_schema.sql"
_TARGET_MIGRATION_FILENAME = "20260821_00_add_audit_logs_created_at.sql"


def _seed_base_audit_logs_table(db_path: Path) -> None:

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            (_MIGRATIONS_DIR / _BASE_MIGRATION_FILENAME).read_text(
                encoding="utf-8",
            ),
        )
    finally:
        conn.close()


class AuditLogsCreatedAtMigrationRunnerTestCase(unittest.TestCase):
    """audit_logs 테이블이 이미 있는(U-2 적용된) 임시 DB에 이 Migration
    파일 하나만 격리해서 적용한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        _seed_base_audit_logs_table(self.db_path)

        self.isolated_migrations_dir = Path(tempfile.mkdtemp())
        shutil.copy(
            _MIGRATIONS_DIR / _TARGET_MIGRATION_FILENAME,
            self.isolated_migrations_dir / _TARGET_MIGRATION_FILENAME,
        )
        self.runner = MigrationRunner(self.db_path, self.isolated_migrations_dir)

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)
        shutil.rmtree(self.isolated_migrations_dir, ignore_errors=True)

    def test_pending_before_apply(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            diagnosis = self.runner.diagnose(conn)
        finally:
            conn.close()

        self.assertEqual(diagnosis["pending"], [_TARGET_MIGRATION_FILENAME])

    def test_apply_adds_nullable_created_at_column_without_default(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            applied = self.runner.apply_pending(conn)
            self.assertEqual(applied, [_TARGET_MIGRATION_FILENAME])

            columns = conn.execute("PRAGMA table_info(audit_logs)").fetchall()
            column_names = [c[1] for c in columns]
            self.assertIn("created_at", column_names)

            created_at_col = next(c for c in columns if c[1] == "created_at")
            # PRAGMA table_info 컬럼 순서: cid, name, type, notnull,
            # dflt_value, pk.
            self.assertEqual(created_at_col[2].upper(), "TEXT")
            self.assertEqual(created_at_col[3], 0)  # notnull == 0 (nullable)
            self.assertIsNone(created_at_col[4])  # DEFAULT 없음
        finally:
            conn.close()

    def test_existing_rows_are_not_backfilled(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, "
                "description, ip_address) VALUES "
                "(NULL, 1, 'PRE_MIGRATION_ACTION', 'TestEntity', '1', "
                "'existing row', NULL)",
            )
            conn.commit()

            self.runner.apply_pending(conn)

            row = conn.execute(
                "SELECT created_at FROM audit_logs WHERE action = "
                "'PRE_MIGRATION_ACTION'",
            ).fetchone()
            self.assertIsNone(row[0])
        finally:
            conn.close()

    def test_reapply_fails_fast_rather_than_silently_skipping(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.apply_pending(conn)

            # 이력을 지워 부기 없이 컬럼만 존재하는 상태를 재현한 뒤
            # 다시 적용을 시도하면, ALTER TABLE ADD COLUMN이 SQLite
            # 자체에서 "duplicate column name"으로 실패해야 한다 —
            # homez-migration-safety 스킬에 기록된 기존 정책(재적용은
            # 조용히 건너뛰지 않고 즉시 실패한다)과 동일하다.
            conn.execute("DELETE FROM schema_migrations")
            conn.commit()

            with self.assertRaises(MigrationExecutionError):
                self.runner.apply_pending(conn)
        finally:
            conn.close()


class WriteAuditLogForwardCompatibilityTestCase(unittest.TestCase):
    """app/core/audit_db.py::write_audit_log()이 이 Migration 적용 전/후
    양쪽 스키마에서 모두 안전하게 동작하는지 검증한다(핵심 설계
    계약 — 이번 라운드는 이 Migration을 실제 DB에 적용하지 않으므로,
    적용 전 동작이 전혀 깨지지 않는 것이 특히 중요하다)."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        _seed_base_audit_logs_table(self.db_path)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.SessionLocal = sessionmaker(bind=self.engine)

    def tearDown(self):

        self.engine.dispose()
        if self.db_path.exists():
            os.remove(self.db_path)

    def _apply_target_migration(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript(
                (_MIGRATIONS_DIR / _TARGET_MIGRATION_FILENAME).read_text(
                    encoding="utf-8",
                ),
            )
        finally:
            conn.close()

    def test_write_before_migration_succeeds_without_created_at(self):

        session = self.SessionLocal()
        try:
            write_audit_log(
                session, user_id=1, action="BEFORE_MIGRATION",
                entity="TestEntity", entity_id="1", description="test",
                company_id=1,
            )
            session.commit()

            row = session.execute(
                text(
                    "SELECT action FROM audit_logs WHERE action = "
                    "'BEFORE_MIGRATION'",
                ),
            ).fetchone()
            self.assertEqual(row[0], "BEFORE_MIGRATION")
        finally:
            session.close()

    def test_write_after_migration_records_utc_iso8601_timestamp(self):

        self._apply_target_migration()

        session = self.SessionLocal()
        try:
            write_audit_log(
                session, user_id=1, action="AFTER_MIGRATION",
                entity="TestEntity", entity_id="1", description="test",
                company_id=1,
            )
            session.commit()

            row = session.execute(
                text(
                    "SELECT created_at FROM audit_logs WHERE action = "
                    "'AFTER_MIGRATION'",
                ),
            ).fetchone()
            self.assertIsNotNone(row[0])

            parsed = datetime.fromisoformat(row[0])
            self.assertIsNotNone(parsed.tzinfo)
        finally:
            session.close()

    def test_write_after_migration_does_not_alter_existing_row_content(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, "
                "description, ip_address) VALUES "
                "(1, 1, 'PRE_EXISTING', 'TestEntity', '1', 'unchanged', "
                "NULL)",
            )
            conn.commit()
        finally:
            conn.close()

        self._apply_target_migration()

        session = self.SessionLocal()
        try:
            write_audit_log(
                session, user_id=2, action="NEW_AFTER",
                entity="TestEntity", entity_id="2", description="new",
                company_id=1,
            )
            session.commit()

            row = session.execute(
                text(
                    "SELECT description, created_at FROM audit_logs "
                    "WHERE action = 'PRE_EXISTING'",
                ),
            ).fetchone()
            self.assertEqual(row[0], "unchanged")
            self.assertIsNone(row[1])
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
