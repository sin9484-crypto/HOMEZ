"""
=========================================================
Homez OS

File : tests/test_supplier_incident_and_connection_health_migration.py

2026-09-15 전면 감사 후속(Phase 9B/9C, HOMEZ_USER_OPERATION_SETTINGS.md
7-11/7-16) —
migrations/20260915_05_add_supplier_incident_and_connection_health_fields.sql
검증. 실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = (
    "20260915_05_add_supplier_incident_and_connection_health_fields.sql"
)


def _db_columns(conn: sqlite3.Connection, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


class FullChainReplayTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = path
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        self.assertTrue(
            os.path.exists(os.path.join(MIGRATIONS_DIR, NEW_MIGRATION)),
            f"{NEW_MIGRATION} 파일이 없습니다.",
        )
        self.runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)

    def test_full_migration_history_applies_cleanly_including_new_file(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_connection_gains_four_new_nullable_columns(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_channel_connections")
        finally:
            conn.close()

        for name in (
            "rate_limited_until", "order_paused_at",
            "order_paused_reason", "last_successful_order_at",
        ):
            self.assertIn(name, cols)
            self.assertTrue(cols[name], f"{name}는 nullable이어야 한다.")

    def test_new_tables_exist(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("purchase_channel_connection_incidents", tables)
        self.assertIn("supplier_incident_auto_pause_settings", tables)

    def test_existing_connection_rows_preserved_and_backfilled_null(self):

        import shutil

        conn = sqlite3.connect(self.db_path)
        try:
            all_files = sorted(
                f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")
            )
            self.assertIn(NEW_MIGRATION, all_files)
            prior_only_dir = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, prior_only_dir, True)
            for name in all_files:
                if name >= NEW_MIGRATION:
                    continue
                shutil.copyfile(
                    os.path.join(MIGRATIONS_DIR, name),
                    os.path.join(prior_only_dir, name),
                )
            prior_runner = MigrationRunner(self.db_path, prior_only_dir)
            prior_runner.apply_pending(conn)

            conn.execute(
                "INSERT INTO purchase_channel_connections ("
                "company_id, mall_code, connection_method, account_label, "
                "status, consecutive_failure_count, is_active, created_at, "
                "updated_at"
                ") VALUES (1, 'ONCHANNEL', 'CREDENTIAL', '기존 연결', "
                "'CONNECTED', 0, 1, '2026-09-15T00:00:00', "
                "'2026-09-15T00:00:00')",
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            row = conn.execute(
                "SELECT account_label, rate_limited_until, order_paused_at, "
                "last_successful_order_at FROM purchase_channel_connections "
                "WHERE account_label = '기존 연결'",
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "기존 행이 사라지면 안 된다.")
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])
        self.assertIsNone(row[3])

    def test_reapplying_full_chain_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            second_run = self.runner.apply_pending(conn)
            self.assertEqual(len(second_run or []), 0)
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            with open(
                os.path.join(MIGRATIONS_DIR, NEW_MIGRATION), encoding="utf-8",
            ) as f:
                sql_text = f.read()
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(sql_text)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
