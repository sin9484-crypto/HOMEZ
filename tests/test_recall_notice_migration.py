"""
=========================================================
Homez OS

File : tests/test_recall_notice_migration.py

2026-09-15 전면 감사 후속(Phase 9I/9J, HOMEZ_USER_OPERATION_SETTINGS.md
10-17/10-18) —
migrations/20260915_09_create_recall_notice_schema.sql 검증. 실제
homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = "20260915_09_create_recall_notice_schema.sql"


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

        for name in (
            "recall_notices", "recall_check_runs",
            "recall_check_job_states", "recall_product_blocks",
        ):
            self.assertIn(name, tables)

    def test_recall_notice_dedupe_key_is_unique(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            conn.execute(
                "INSERT INTO recall_notices ("
                "product_identifier, reason, source, dedupe_key, "
                "discovered_at"
                ") VALUES ('P1', '위해성 확인', 'FAKE', 'k1', "
                "'2026-09-15T00:00:00')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO recall_notices ("
                    "product_identifier, reason, source, dedupe_key, "
                    "discovered_at"
                    ") VALUES ('P1', '다른 사유', 'FAKE', 'k1', "
                    "'2026-09-15T00:00:01')",
                )
        finally:
            conn.close()

    def test_product_block_foreign_key_to_notice_is_enforced(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            conn.execute("PRAGMA foreign_keys = ON")

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO recall_product_blocks ("
                    "company_id, product_identifier, recall_notice_id, "
                    "status, reason, blocked_at"
                    ") VALUES (1, 'P1', 99999, 'BLOCKED', '차단', "
                    "'2026-09-15T00:00:00')",
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
