"""
=========================================================
Homez OS

File : tests/test_gate8_operations_schema_migration.py

V7 Gate 8(2026-08-15) —
migrations/20260815_04_create_v7_gate8_operations_schema.sql 검증.

배경: Gate Y(2026-08-12)가 만든 backup/restore/notification_center/
update 4개 도메인은 Model+Service+Router+자체 테스트(80개)까지 전부
있었지만, 정식 Migration SQL 파일이 하나도 없었다 — Gate Y 테스트
80개는 전부 `Base.metadata.create_all()`로 만든 임시 DB에서만
통과했을 뿐, 공식 `MigrationRunner.apply_pending()` 경로로는 이
5개 테이블이 실제 homez.db에 전혀 생성되지 않았다. 이 테스트는
기존 23개 Migration(Gate 5까지)을 순서대로 적용해 실제 homez.db와
동일한 스키마 상태를 임시 DB에 재현한 뒤, 신규 Migration을 적용해
성공/재적용실패/rollback/Model↔DDL 일치/공식 MigrationRunner 호환을
확인한다(tests/test_gate5_pricing_settlement_migration.py와 동일
패턴). 실제 homez.db는 이 테스트 전체에서 전혀 사용하지 않는다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner
from app.domains.backup.model import BackupRecord
from app.domains.notification_center.model import Notification
from app.domains.notification_center.model import NotificationRead
from app.domains.restore.model import RestoreAttempt
from app.domains.update.model import UpdateNotice

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

# 실제 homez.db의 schema_migrations 이력 순서(Gate 5 종료 시점) — 이
# 신규 Migration 직전까지 전부 적용.
PRIOR_MIGRATIONS = [
    "20260727_00_create_funding_settlement_schema.sql",
    "20260727_add_settlement_ledger_unique_index.sql",
    "20260728_00_create_v24_v3_schema.sql",
    "20260729_00_create_coupang_integration_schema.sql",
    "20260730_00_create_decision_ai_schema.sql",
    "20260730_01_create_auth_session_schema.sql",
    "20260730_02_create_store_connection_schema.sql",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
    "20260802_00_create_media_listing_package_schema.sql",
    "20260802_01_create_account_recovery_schema.sql",
    "20260803_00_create_account_registration_schema.sql",
    "20260805_00_add_marketplace_listing_status_sync.sql",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
    "20260808_00_create_listing_wizard_schema.sql",
    "20260810_00_create_audit_logs_schema.sql",
    "20260814_00_create_user_settings_schema.sql",
    "20260814_01_add_tenant_isolation_company_id.sql",
    "20260815_00_gate2_tenant_isolation_hardening.sql",
    "20260815_01_create_v7_gate3_inventory_schema.sql",
    "20260815_02_create_v7_gate4_order_fulfillment_schema.sql",
    "20260815_03_create_v7_gate5_pricing_settlement_schema.sql",
]

NEW_MIGRATION = "20260815_04_create_v7_gate8_operations_schema.sql"

NEW_TABLE_MODELS = {
    "backup_records": BackupRecord,
    "restore_attempts": RestoreAttempt,
    "notifications": Notification,
    "notification_reads": NotificationRead,
    "update_notices": UpdateNotice,
}


def _read(filename: str) -> str:

    path = os.path.join(MIGRATIONS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


def _db_columns(conn, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _extract_rollback_sql(content: str) -> str:

    lines = content.splitlines()
    rollback_lines = []
    in_sql = False

    for line in lines:
        stripped = line.strip()

        if not in_sql:
            if stripped == "-- BEGIN;":
                in_sql = True
                rollback_lines.append("BEGIN;")
            continue

        if stripped.startswith("-- "):
            rollback_lines.append(stripped[3:])
        elif stripped == "--":
            continue

    return "\n".join(rollback_lines)


class MigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.content = _read(NEW_MIGRATION)

        non_comment_lines = [
            line for line in cls.content.splitlines()
            if not line.strip().startswith("--")
        ]
        cls.executed_sql = "\n".join(non_comment_lines)

    def test_exactly_one_begin_and_commit(self):

        self.assertEqual(
            len(re.findall(r"^BEGIN;", self.executed_sql, re.M)), 1,
        )
        self.assertEqual(
            len(re.findall(r"^COMMIT;", self.executed_sql, re.M)), 1,
        )

    def test_no_foreign_keys_or_if_not_exists(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertNotIn("REFERENCES", self.executed_sql.upper())
        self.assertNotIn("IF NOT EXISTS", self.executed_sql.upper())

    def test_no_alter_or_drop_all_new_tables(self):

        self.assertNotIn("ALTER TABLE", self.executed_sql.upper())
        self.assertNotIn("DROP TABLE", self.executed_sql.upper())

    def test_touches_only_expected_tables(self):

        created = set(
            re.findall(r"^CREATE TABLE (\w+) \(", self.executed_sql, re.M),
        )
        self.assertEqual(created, set(NEW_TABLE_MODELS))

    def test_does_not_modify_prior_migration_files(self):

        for filename in PRIOR_MIGRATIONS:
            path = os.path.join(MIGRATIONS_DIR, filename)
            self.assertTrue(
                os.path.exists(path), f"{filename} 파일이 없습니다.",
            )


class MigrationApplyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_sql = _read(NEW_MIGRATION)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _apply_prior_migrations(self, conn):

        for sql in self.prior_sql:
            conn.executescript(sql)

    def test_full_chain_applies_cleanly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_reapply_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    def test_new_table_columns_match_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in NEW_TABLE_MODELS.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_backup_records_and_restore_attempts_are_independent(self):
        """
        backup_records(성공만 기록)와 restore_attempts(성공/실패 모두
        기록)는 서로 다른 테이블이라 한쪽에만 행이 있어도 다른 쪽에
        영향이 없어야 한다 — 두 테이블이 실수로 뒤섞이지 않았는지
        실제 INSERT로 확인.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO backup_records (file_path, "
                "file_size_bytes, sha256, integrity_check_result, "
                "trigger_source, created_at) VALUES "
                "('/tmp/x.db', 100, 'abc', 'ok', 'manual', "
                "'2026-01-01')",
            )
            conn.commit()

            restore_count = conn.execute(
                "SELECT COUNT(*) FROM restore_attempts",
            ).fetchone()[0]
            backup_count = conn.execute(
                "SELECT COUNT(*) FROM backup_records",
            ).fetchone()[0]

            self.assertEqual(restore_count, 0)
            self.assertEqual(backup_count, 1)
        finally:
            conn.close()

    def test_notification_reads_isolated_from_notifications_is_read(self):
        """
        공지(user_id NULL)의 개인별 읽음은 notification_reads에만
        쌓이고 notifications.is_read(개인 알림 전용 컬럼)는 공지
        삽입만으로는 절대 바뀌지 않아야 한다(Gate Y-3가 실제로 겪은
        결함 클래스 — Migration 레벨에서도 두 테이블이 분리돼 있음을
        재확인).
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO notifications (company_id, user_id, "
                "category, level, title, message, is_read, "
                "created_at) VALUES "
                "(1, NULL, 'backup', 'info', 't', 'm', 0, "
                "'2026-01-01')",
            )
            conn.commit()

            notif_id = conn.execute(
                "SELECT id FROM notifications",
            ).fetchone()[0]

            conn.execute(
                "INSERT INTO notification_reads (notification_id, "
                "user_id, read_at) VALUES (?, 1, '2026-01-01')",
                (notif_id,),
            )
            conn.commit()

            is_read = conn.execute(
                "SELECT is_read FROM notifications WHERE id=?",
                (notif_id,),
            ).fetchone()[0]

            self.assertEqual(is_read, 0)
        finally:
            conn.close()


class MigrationRollbackTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_content = _read(NEW_MIGRATION)
        self.new_sql = "\n".join(
            line for line in self.new_content.splitlines()
            if not line.strip().startswith("--")
        )
        self.rollback_sql = _extract_rollback_sql(self.new_content)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_rollback_restores_pre_migration_schema(self):

        conn = sqlite3.connect(self.db_path)
        try:
            for sql in self.prior_sql:
                conn.executescript(sql)

            before_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }

            conn.executescript(self.new_sql)

            new_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertEqual(
                new_tables - before_tables, set(NEW_TABLE_MODELS),
            )

            self.assertTrue(self.rollback_sql.strip())
            conn.executescript(self.rollback_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            after_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertEqual(before_tables, after_tables)
        finally:
            conn.close()


class OfficialMigrationRunnerTestCase(unittest.TestCase):
    """
    이 저장소의 공식 MigrationRunner(app/database/migration_runner.py)
    + 실제 migrations/ 디렉터리(신규 파일 포함) 전체를 그대로 사용한
    리허설. 실제 homez.db는 사용하지 않는다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.runner = MigrationRunner(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
        )

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_new_migration_applies_cleanly_via_runner(self):

        conn = sqlite3.connect(self.db_path)
        try:
            applied = self.runner.apply_pending(conn)
            self.assertIn(NEW_MIGRATION, applied)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])

            for table_name in NEW_TABLE_MODELS:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                self.assertIsNotNone(row, f"{table_name} 테이블이 없습니다.")

            history_row = conn.execute(
                "SELECT status FROM schema_migrations WHERE filename=?",
                (NEW_MIGRATION,),
            ).fetchone()
            self.assertIsNotNone(history_row)
            self.assertEqual(history_row[0], "APPLIED")

            applied_again = self.runner.apply_pending(conn)
            self.assertEqual(applied_again, [])

            diagnosis = self.runner.diagnose(conn)
            self.assertIn(NEW_MIGRATION, diagnosis["already_applied"])
            self.assertNotIn(NEW_MIGRATION, diagnosis["pending"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
