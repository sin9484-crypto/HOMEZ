"""
=========================================================
Homez OS

File : tests/test_virtual_stock_zero_proposal_migration.py

2026-09-15 전면 감사 후속(Phase 9F, HOMEZ_USER_OPERATION_SETTINGS.md
8-19) —
migrations/20260915_07_create_virtual_stock_zero_proposal_schema.sql
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

NEW_MIGRATION = "20260915_07_create_virtual_stock_zero_proposal_schema.sql"


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

    def test_new_table_exists_with_expected_columns(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(virtual_stock_zero_proposals)",
                ).fetchall()
            }
        finally:
            conn.close()

        for name in (
            "id", "company_id", "connection_id", "product_code", "status",
            "reason", "created_at", "resolved_by", "resolved_at",
            "resolution_note",
        ):
            self.assertIn(name, cols)

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
