"""
=========================================================
Homez OS

File : tests/test_account_recovery_migration.py

계정 복구(recovery_codes, password_reset_tokens) Migration 정적/적용
검증 + Model 자동 드리프트 감지 + 중간 실패 rollback 검증. 표준
라이브러리 unittest + sqlite3만 사용. homez.db는 사용하지 않고 임시
SQLite 파일 DB만 사용한다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from app.domains.account_recovery.model import PasswordResetToken
from app.domains.account_recovery.model import RecoveryCode

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT,
    "migrations",
    "20260802_01_create_account_recovery_schema.sql",
)

EXPECTED_TABLES = {"recovery_codes", "password_reset_tokens"}
MODEL_TABLE_ORDER = [RecoveryCode, PasswordResetToken]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class AccountRecoveryMigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            cls.content = f.read()

        cls.non_comment_lines = [
            line for line in cls.content.splitlines()
            if not line.strip().startswith("--")
        ]
        cls.executed_sql = "\n".join(cls.non_comment_lines)

    def test_exactly_two_create_table_statements(self):

        creates = re.findall(r"^CREATE TABLE (\w+)", self.executed_sql, re.M)
        self.assertEqual(set(creates), EXPECTED_TABLES)

    def test_no_create_table_if_not_exists(self):

        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_foreign_keys(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertNotIn("REFERENCES", self.executed_sql.upper())

    def test_exactly_one_executed_begin_and_commit(self):

        self.assertEqual(len(re.findall(r"^BEGIN;", self.executed_sql, re.M)), 1)
        self.assertEqual(len(re.findall(r"^COMMIT;", self.executed_sql, re.M)), 1)

    def test_no_executed_mutation_or_ddl_drop_statements(self):

        forbidden = re.findall(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            self.executed_sql, re.M | re.I,
        )
        self.assertEqual(forbidden, [])

    def test_no_default_check_trigger_cascade(self):

        for keyword in ("DEFAULT", "CHECK", "TRIGGER", "CASCADE"):
            self.assertNotRegex(self.executed_sql.upper(), rf"\b{keyword}\b")

    def test_migration_matches_sqlalchemy_model_ddl(self):

        dialect = sqlite_dialect.dialect()

        canonical_statements = []

        for model in MODEL_TABLE_ORDER:
            table = model.__table__
            canonical_statements.append(
                _normalize_sql(str(CreateTable(table).compile(dialect=dialect))),
            )
            for index in sorted(table.indexes, key=lambda ix: ix.name):
                canonical_statements.append(
                    _normalize_sql(str(CreateIndex(index).compile(dialect=dialect))),
                )

        executable_body = self.executed_sql.replace("BEGIN;", "").replace("COMMIT;", "")
        migration_statements = [
            _normalize_sql(stmt) for stmt in executable_body.split(";") if stmt.strip()
        ]

        missing = [s for s in canonical_statements if s not in migration_statements]
        extra = [s for s in migration_statements if s not in canonical_statements]

        self.assertEqual(
            (missing, extra), ([], []),
            f"Model ↔ Migration DDL 불일치.\nmissing: {missing}\nextra: {extra}",
        )


class AccountRecoveryMigrationApplyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            self.migration_sql = f.read()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_migration_applies_and_creates_tables(self):

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.migration_sql)
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_migration_reapply_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.migration_sql)
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.migration_sql)
        finally:
            conn.close()

    def test_rollback_script_drops_all_objects_cleanly(self):

        rollback_statements = []
        in_rollback = False

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("-- BEGIN;"):
                    in_rollback = True
                    continue
                if stripped.startswith("-- COMMIT;"):
                    break
                if in_rollback and stripped.startswith("-- DROP"):
                    rollback_statements.append(stripped[3:])

        self.assertTrue(len(rollback_statements) > 0)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.migration_sql)
            for stmt in rollback_statements:
                conn.execute(stmt)
            conn.commit()

            remaining = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index') "
                "AND name NOT LIKE 'sqlite_%'",
            ).fetchall()
            self.assertEqual(remaining, [])
        finally:
            conn.close()

    def test_mid_migration_failure_rolls_back(self):
        """
        recovery_codes와 이름이 겹치는 충돌용 테이블을 미리 만들어두면
        Migration은 CREATE TABLE에서 즉시 실패해야 하고, 그 실패가
        rollback되어도 충돌 테이블 자체와 아직 실행되지 않은
        password_reset_tokens 생성 시도는 반영되지 않아야 한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("CREATE TABLE recovery_codes (conflict_marker INTEGER)")
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            self.assertIn("recovery_codes", str(ctx.exception))
            self.assertIn("already exists", str(ctx.exception))

            conn.rollback()

            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(recovery_codes)")
            }
            self.assertEqual(columns, {"conflict_marker"})

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertNotIn("password_reset_tokens", tables)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
