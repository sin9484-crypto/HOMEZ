"""
=========================================================
Homez OS

File : tests/test_price_stock_quote_cache_migration.py

2026-09-15 전면 감사 후속(Phase 9A/9D/9E, HOMEZ_USER_OPERATION_
SETTINGS.md 7-8/8-5/8-6) —
migrations/20260915_06_create_price_stock_quote_cache_schema.sql
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

NEW_MIGRATION = "20260915_06_create_price_stock_quote_cache_schema.sql"


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

        self.assertIn("price_cache_ttl_settings", tables)
        self.assertIn("stock_cache_ttl_settings", tables)
        self.assertIn("price_stock_quote_caches", tables)

    def test_quote_cache_enforces_unique_key(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            conn.execute(
                "INSERT INTO price_stock_quote_caches ("
                "company_id, connection_id, product_code, option_id, "
                "source, created_at, updated_at"
                ") VALUES (1, 4, 'CH1', 'opt-1', 'real_api', "
                "'2026-09-15T00:00:00', '2026-09-15T00:00:00')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO price_stock_quote_caches ("
                    "company_id, connection_id, product_code, option_id, "
                    "source, created_at, updated_at"
                    ") VALUES (1, 4, 'CH1', 'opt-1', 'real_api', "
                    "'2026-09-15T00:00:01', '2026-09-15T00:00:01')",
                )
        finally:
            conn.close()

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
