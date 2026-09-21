"""
=========================================================
Homez OS

File : tests/test_store_connection_migration.py

판매채널 연결(StoreConnection) — Migration 정적/적용 검증 + Model
자동 드리프트 감지 + 중간 실패 rollback 검증. 표준 라이브러리
unittest + sqlite3만 사용. 실제 homez.db에는 적용하지 않는다(임시
SQLite 파일 DB에서만 적용/재적용/rollback을 검증).
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

from tests.support.real_install_gate import requires_real_install_diagnostics
from app.domains.store_connection.model import StoreConnection

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations", "20260730_02_create_store_connection_schema.sql",
)
REAL_DB_PATH = os.path.join(REPO_ROOT, "homez.db")

EXPECTED_TABLES = {"store_connections"}


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class StoreConnectionMigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            cls.content = f.read()

        cls.non_comment_lines = [
            line for line in cls.content.splitlines()
            if not line.strip().startswith("--")
        ]
        cls.executed_sql = "\n".join(cls.non_comment_lines)

    def test_exactly_one_create_table_statement(self):

        creates = re.findall(r"^CREATE TABLE (\w+)", self.executed_sql, re.M)
        self.assertEqual(set(creates), EXPECTED_TABLES)

    def test_no_create_table_if_not_exists(self):

        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_foreign_keys(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertNotIn("REFERENCES", self.executed_sql.upper())

    def test_exactly_one_executed_begin_and_commit(self):

        self.assertEqual(
            len(re.findall(r"^BEGIN;", self.executed_sql, re.M)), 1,
        )
        self.assertEqual(
            len(re.findall(r"^COMMIT;", self.executed_sql, re.M)), 1,
        )

    def test_no_executed_mutation_or_ddl_drop_statements(self):

        forbidden = re.findall(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            self.executed_sql, re.M | re.I,
        )
        self.assertEqual(forbidden, [])

    def test_no_default_check_trigger_cascade(self):

        for keyword in ("DEFAULT", "CHECK", "TRIGGER", "CASCADE"):
            self.assertNotRegex(self.executed_sql.upper(), rf"\b{keyword}\b")

    def test_no_secret_columns(self):
        """
        Secret 원문(access_key/secret_key/client_secret 등)이나
        Access Token 컬럼이 정의되어 있지 않은지 확인한다 —
        credential_reference만 존재해야 한다.
        """

        forbidden_terms = (
            "SECRET", "ACCESS_KEY", "CLIENT_SECRET", "ACCESS_TOKEN",
            "PASSWORD",
        )
        for term in forbidden_terms:
            self.assertNotIn(term, self.executed_sql.upper())

        self.assertIn("CREDENTIAL_REFERENCE", self.executed_sql.upper())

    def test_migration_matches_sqlalchemy_model_ddl(self):

        dialect = sqlite_dialect.dialect()
        table = StoreConnection.__table__

        canonical_statements = [
            _normalize_sql(str(CreateTable(table).compile(dialect=dialect))),
        ]
        for index in sorted(table.indexes, key=lambda ix: ix.name):
            canonical_statements.append(
                _normalize_sql(
                    str(CreateIndex(index).compile(dialect=dialect)),
                ),
            )

        executable_body = self.executed_sql.replace(
            "BEGIN;", "",
        ).replace("COMMIT;", "")
        migration_statements = [
            _normalize_sql(stmt)
            for stmt in executable_body.split(";") if stmt.strip()
        ]

        missing = [s for s in canonical_statements if s not in migration_statements]
        extra = [s for s in migration_statements if s not in canonical_statements]

        self.assertEqual(
            (missing, extra), ([], []),
            f"Model ↔ Migration DDL 불일치.\nmissing: {missing}\nextra: {extra}",
        )


class StoreConnectionMigrationApplyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            self.migration_sql = f.read()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_migration_applies_and_creates_table(self):

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
        store_connections와 이름이 겹치는 충돌용 테이블을 미리 만들어
        두면 Migration은 CREATE TABLE에서 즉시 실패해야 하고, 그 실패가
        rollback되어도 충돌 테이블 자체는 그대로 남아야 한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE store_connections (conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            self.assertIn("store_connections", str(ctx.exception))
            self.assertIn("already exists", str(ctx.exception))

            conn.rollback()

            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(store_connections)")
            }
            self.assertEqual(columns, {"conflict_marker"})
        finally:
            conn.close()

    # 실제 설치환경 진단 — 기본 전체 회귀에서 제외(opt-in: HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)
    @requires_real_install_diagnostics
    def test_real_homez_db_has_store_connections_table_applied(self):
        """
        2026-08-01 Gate 2에서 이 Migration을 실제 homez.db에 적용했다
        (사용자 명시적 요청 + Gate 1 리허설/백업 전부 통과 후). 이제는
        "아직 적용되지 않았다"가 아니라 "정확히 한 번, model과 일치하게
        적용되었다"를 읽기 전용으로 확인한다 — 이 테스트는 실제 DB에
        어떤 쓰기도 하지 않는다(PRAGMA query_only=ON).
        """

        if not os.path.exists(REAL_DB_PATH):
            self.skipTest("실제 homez.db가 이 환경에 없습니다.")

        conn = sqlite3.connect(f"file:{REAL_DB_PATH}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")

        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])

            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(store_connections)")
            }
            model_columns = {c.name for c in StoreConnection.__table__.columns}
            self.assertEqual(columns, model_columns)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
