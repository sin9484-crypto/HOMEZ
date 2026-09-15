"""
=========================================================
Homez OS

File : tests/test_backup_records_is_encrypted_migration.py

2026-09-15 전면 감사 후속(Phase 5) —
migrations/20260915_03_add_backup_records_is_encrypted.sql 검증.
임시 SQLite 파일에 저장소의 실제 Migration 전체를(이 신규 파일
포함) 순서대로 재생시켜, 처음부터 끝까지 클린 적용이 가능한지·
재적용이 명시적으로 실패하는지·backup_records 테이블이 NOT NULL
DEFAULT 0인 is_encrypted 컬럼을 얻는지·기존 backup_records 행이
보존되고 기본값 0(=과거 실제로 평문이었다는 사실)으로 백필되는지
확인한다. 실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = "20260915_03_add_backup_records_is_encrypted.sql"


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

    def test_backup_records_gains_not_null_is_encrypted_column_default_false(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "backup_records")
        finally:
            conn.close()

        self.assertIn("is_encrypted", cols)
        self.assertFalse(cols["is_encrypted"], "NOT NULL이어야 한다.")

    def test_existing_backup_records_preserved_and_backfilled_false(self):
        """기존 backup_records 행이 있어도 이번 ALTER TABLE ADD
        COLUMN이 데이터를 보존하고, 신규 컬럼은 DEFAULT 0(false)으로
        채워지는지 확인한다 — 이 값은 사실과 다르지 않다: 이 컬럼
        추가 이전에 만들어진 백업은 실제로 평문이었다."""

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
                "INSERT INTO backup_records ("
                "file_path, file_size_bytes, sha256, integrity_check_result, "
                "trigger_source, triggered_by_user_id, label, created_at"
                ") VALUES ('C:/tmp/test_backup.db', 1024, "
                "'" + ("0" * 64) + "', 'ok', 'manual', 1, '테스트', "
                "'2026-09-15T00:00:00')",
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            row = conn.execute(
                "SELECT file_path, is_encrypted FROM backup_records "
                "WHERE file_path = 'C:/tmp/test_backup.db'",
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "기존 행이 사라지면 안 된다.")
        self.assertEqual(row[1], 0)

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
