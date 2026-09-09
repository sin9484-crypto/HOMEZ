"""
=========================================================
Homez OS

File : tests/test_media_asset_rights_evidence_migration.py

2026-08-28 이미지 권리 증빙/사용자 진행-선택 감사기록 Migration
검증 — image_rights_evidence, image_rights_acknowledgements 정적
검증 + 임시 SQLite 파일 적용/재적용실패/rollback/중간실패rollback +
Model↔DDL 자동 드리프트 감지. 실제 homez.db는 전혀 열지 않는다.
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

from app.domains.media_asset.model import ImageRightsAcknowledgement
from app.domains.media_asset.model import ImageRightsEvidence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260828_00_create_image_rights_evidence_schema.sql",
)

EXPECTED_TABLES = {
    "image_rights_evidence", "image_rights_acknowledgements",
}

MODEL_TABLE_ORDER = [ImageRightsEvidence, ImageRightsAcknowledgement]


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


class SchemaMigrationStaticTestCase(unittest.TestCase):

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
        self.assertEqual(len(creates), 2)
        self.assertEqual(set(creates), EXPECTED_TABLES)

    def test_no_create_table_if_not_exists(self):

        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_foreign_keys(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertIsNone(re.search(r"\bREFERENCES\b", self.executed_sql.upper()))

    def test_exactly_one_executed_begin_and_commit(self):

        begins = re.findall(r"^BEGIN;", self.executed_sql, re.M)
        commits = re.findall(r"^COMMIT;", self.executed_sql, re.M)
        self.assertEqual(len(begins), 1)
        self.assertEqual(len(commits), 1)

    def test_no_executed_mutation_or_ddl_drop_statements(self):

        forbidden = re.findall(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            self.executed_sql, re.M | re.I,
        )
        self.assertEqual(forbidden, [])

    def test_rollback_drops_only_in_comments_and_count_matches(self):

        comment_lines = [
            line for line in self.content.splitlines()
            if line.strip().startswith("--")
        ]
        comment_text = "\n".join(comment_lines)
        drop_count_in_comments = len(
            re.findall(r"DROP (TABLE|INDEX)", comment_text),
        )
        table_count = len(re.findall(r"^CREATE TABLE", self.executed_sql, re.M))
        index_count = len(
            re.findall(r"^CREATE( UNIQUE)? INDEX", self.executed_sql, re.M),
        )
        self.assertEqual(drop_count_in_comments, table_count + index_count)

    def test_no_default_check_trigger_cascade(self):

        for keyword in ("CHECK", "TRIGGER", "CASCADE", "DEFAULT"):
            self.assertNotIn(keyword, self.executed_sql.upper())

    def test_migration_matches_sqlalchemy_model_ddl(self):

        dialect = sqlite_dialect.dialect()
        canonical_statements = []

        for model_cls in MODEL_TABLE_ORDER:
            table = model_cls.__table__
            canonical_statements.append(
                _normalize_sql(str(CreateTable(table).compile(dialect=dialect))),
            )
            for index in sorted(table.indexes, key=lambda ix: ix.name):
                canonical_statements.append(
                    _normalize_sql(str(CreateIndex(index).compile(dialect=dialect))),
                )

        table_names = {m.__table__.name for m in MODEL_TABLE_ORDER}
        executable_body = (
            self.executed_sql.replace("BEGIN;", "").replace("COMMIT;", "")
        )
        migration_statements = [
            _normalize_sql(stmt)
            for stmt in executable_body.split(";")
            if stmt.strip()
            and any(
                f" {name} " in f" {_normalize_sql(stmt)} "
                or f" {name} (" in f" {_normalize_sql(stmt)} "
                for name in table_names
            )
        ]

        missing = [s for s in canonical_statements if s not in migration_statements]
        extra = [s for s in migration_statements if s not in canonical_statements]

        self.assertEqual(
            (missing, extra), ([], []),
            "Model ↔ Migration DDL 불일치.\n"
            f"missing({len(missing)}개): {missing}\nextra({len(extra)}개): {extra}",
        )


class SchemaMigrationApplyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            self.migration_sql = f.read()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_migration_applies_and_creates_all_tables(self):

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

            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])
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
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'",
            ).fetchall()
            self.assertEqual(remaining, [])
        finally:
            conn.close()

    def test_mid_migration_failure_rolls_back_created_objects(self):
        """2번째 테이블(image_rights_acknowledgements)과 이름이
        겹치는 충돌용 테이블을 미리 만들어 두면, Migration은 첫 번째
        테이블까지 생성한 뒤 거기서 실패해야 한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE image_rights_acknowledgements "
                "(conflict_marker INTEGER)",
            )
            conn.commit()

            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(self.migration_sql)

            error_message = str(ctx.exception)
            self.assertIn("image_rights_acknowledgements", error_message)
            self.assertIn("already exists", error_message)

            conn.rollback()

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            for table_name in EXPECTED_TABLES - {"image_rights_acknowledgements"}:
                self.assertNotIn(
                    table_name, tables,
                    f"실패 전에 생성된 {table_name}가 rollback되지 않았다.",
                )
            self.assertIn("image_rights_acknowledgements", tables)

            conflict_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(image_rights_acknowledgements)",
                )
            }
            self.assertEqual(conflict_columns, {"conflict_marker"})
        finally:
            conn.close()


class RealHomezDbApprovalDriftTestCase(unittest.TestCase):
    """2026-08-30 V7 후속 안정화 — 운영 DB 감사 로그(id=5,6, 2026-08-29
    T05:07:09Z)로 이 Migration이 사용자 승인 절차를 거쳐 실제로
    적용된 사실을 확인했다("영원히 미적용"을 강제하던 이전 버전의
    assertFalse는 이제 거짓이다 — 실패 재현 확인됨). 이제 지켜야
    하는 불변식은 "새 테이블이 있다면 반드시 승인 감사로그가 함께
    있어야 한다"이다."""

    def test_new_tables_present_only_with_approved_audit_log(self):

        target_filename = os.path.basename(MIGRATION_PATH)
        candidates = [
            r"C:\Users\Daum pc\Homez-OS\homez.db",
            r"C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db",
        ]
        for path in candidates:
            if not os.path.exists(path):
                continue
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                conn.execute("PRAGMA query_only = ON")
                tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'",
                    )
                }
                if EXPECTED_TABLES & tables:
                    has_audit_table = "audit_logs" in tables
                    self.assertTrue(
                        has_audit_table,
                        f"{path}에 새 테이블은 있지만 audit_logs 테이블이 "
                        "없어 승인 경로를 확인할 수 없습니다.",
                    )
                    approved_count = conn.execute(
                        "SELECT COUNT(*) FROM audit_logs WHERE "
                        "action='MIGRATION_APPLIED' AND description LIKE ?",
                        (f"%{target_filename}%",),
                    ).fetchone()[0]
                    self.assertGreater(
                        approved_count, 0,
                        f"{path}에 새 테이블이 있지만 이 Migration의 "
                        "MIGRATION_APPLIED 승인 감사 로그가 없습니다 — "
                        "승인 없이 적용된 흔적일 수 있습니다.",
                    )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
