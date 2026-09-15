"""
=========================================================
Homez OS

File : tests/test_product_attribute_match_migration.py

2026-09-15 전면 감사 후속(Phase 9G, HOMEZ_USER_OPERATION_SETTINGS.md
10-4) —
migrations/20260915_08_create_product_attribute_match_schema.sql
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

NEW_MIGRATION = "20260915_08_create_product_attribute_match_schema.sql"


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

    def test_new_tables_exist_with_expected_columns(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            run_cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(product_attribute_comparison_runs)",
                ).fetchall()
            }
            item_cols = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(product_attribute_comparison_items)",
                ).fetchall()
            }
        finally:
            conn.close()

        for name in (
            "id", "company_id", "product_identifier", "connection_id",
            "overall_status", "triggered_by", "created_at", "resolved_by",
            "resolved_at", "resolution_note",
        ):
            self.assertIn(name, run_cols)

        for name in (
            "id", "run_id", "field_name",
            "supplier_value", "supplier_source", "supplier_confirmed_at",
            "sales_channel_value", "sales_channel_source",
            "sales_channel_confirmed_at",
            "homez_current_value", "homez_current_source",
            "homez_current_confirmed_at",
            "match_status", "selected_value",
        ):
            self.assertIn(name, item_cols)

    def test_item_foreign_key_to_run_is_enforced(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO product_attribute_comparison_runs ("
                "company_id, product_identifier, overall_status, created_at"
                ") VALUES (1, 'CH1', 'PASSED', '2026-09-15T00:00:00')",
            )
            conn.commit()
            run_id = conn.execute(
                "SELECT id FROM product_attribute_comparison_runs",
            ).fetchone()[0]

            conn.execute(
                "INSERT INTO product_attribute_comparison_items ("
                "run_id, field_name, match_status"
                ") VALUES (?, 'NAME', 'MATCHED')",
                (run_id,),
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO product_attribute_comparison_items ("
                    "run_id, field_name, match_status"
                    ") VALUES (99999, 'NAME', 'MATCHED')",
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
