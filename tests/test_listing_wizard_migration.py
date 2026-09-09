"""
=========================================================
Homez OS

File : tests/test_listing_wizard_migration.py

Gate I(2026-08-08) — 상품등록 통합 마법사 `listing_wizards` 신규 테이블
Migration 정적/적용 검증 + Model 자동 드리프트 감지. 표준 라이브러리
unittest + sqlite3만 사용. homez.db는 사용하지 않고, 테스트 전용 임시
SQLite 파일 DB만 사용한다.

이 테이블은 다른 테이블을 FK로 참조하지 않으므로(이 코드베이스 전역
컨벤션), 다른 Migration 체인 없이 독립적으로 적용 가능하다 — 그래도
"실제 DB가 겪을 상황"을 그대로 재현하기 위해, 기존 marketplace_listing
전체 체인(base → status_sync → rate_limit) 위에 적용하는 시나리오도
함께 검증한다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from app.domains.marketplace_listing.model import ListingWizard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations", "20260808_00_create_listing_wizard_schema.sql",
)
# 2026-08-28 "대기 상품 정리" — soft delete 컬럼 7개는 원본 CREATE TABLE
# 파일을 고치지 않고 별도 additive ALTER TABLE Migration으로 추가했다
# (이미 적용된 적 없는 파일이라도 "한 번 만든 CREATE TABLE Migration은
# 사후에 고치지 않는다"는 이 저장소 전역 관례를 그대로 따른 것). 그래서
# 이 테이블의 "최종 스키마"는 이제 두 파일을 순서대로 적용한 결과다.
SOFT_DELETE_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260828_02_add_listing_wizard_soft_delete.sql",
)
BASE_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
)
STATUS_SYNC_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260805_00_add_marketplace_listing_status_sync.sql",
)
RATE_LIMIT_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
)


def _normalize_sql(statement: str) -> str:

    return re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()


def _db_columns(conn, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _model_columns(model_cls) -> dict[str, bool]:

    return {
        col.name: col.nullable for col in model_cls.__table__.columns
    }


class ListingWizardMigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            cls.content = f.read()

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
        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)

    def test_no_executed_mutation_or_drop_or_alter(self):

        forbidden = re.findall(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER)\b",
            self.executed_sql, re.M | re.I,
        )
        self.assertEqual(forbidden, [])

    def test_no_default_check_trigger_cascade(self):

        for keyword in ("DEFAULT", "CHECK", "TRIGGER", "CASCADE"):
            self.assertNotRegex(
                self.executed_sql.upper(), rf"\b{keyword}\b",
            )

    def test_creates_exactly_one_table(self):

        creates = re.findall(r"^CREATE TABLE (\w+)", self.executed_sql, re.M)
        self.assertEqual(creates, ["listing_wizards"])

    def test_rollback_drops_only_in_comments_and_count_matches(self):

        comment_lines = [
            line for line in self.content.splitlines()
            if line.strip().startswith("--")
        ]
        comment_text = "\n".join(comment_lines)
        drop_count_in_comments = len(
            re.findall(r"DROP (TABLE|INDEX)", comment_text),
        )
        table_count = len(
            re.findall(r"^CREATE TABLE", self.executed_sql, re.M),
        )
        index_count = len(
            re.findall(r"^CREATE( UNIQUE)? INDEX", self.executed_sql, re.M),
        )
        self.assertEqual(drop_count_in_comments, table_count + index_count)

    def test_migration_matches_sqlalchemy_model_ddl_exactly(self):
        """
        이 테이블은 이제 두 Migration 파일(원본 CREATE TABLE + 2026-08-28
        soft delete ALTER TABLE)의 합으로 정의된다 — 단일 CreateTable()
        문자열과 원본 파일 하나만 비교하면 항상 어긋난다. 두 파일을 실제
        임시 SQLite에 순서대로 적용한 결과와, SQLAlchemy가 생성한 canonical
        DDL을 별도의 임시 SQLite에 적용한 결과를, 둘 다 PRAGMA table_info로
        읽어 컬럼(이름/타입/NOT NULL)을 비교한다 — 문자열 포맷 차이
        (CREATE TABLE 컬럼절 vs ALTER TABLE ADD COLUMN절)에 흔들리지
        않는 방식.
        """

        dialect = sqlite_dialect.dialect()
        table = ListingWizard.__table__

        canonical_conn = sqlite3.connect(":memory:")
        try:
            canonical_conn.executescript(
                str(CreateTable(table).compile(dialect=dialect)) + ";",
            )
            canonical_cols = _db_columns(canonical_conn, "listing_wizards")
        finally:
            canonical_conn.close()

        with open(SOFT_DELETE_MIGRATION_PATH, encoding="utf-8") as f:
            soft_delete_sql = f.read()

        migrated_conn = sqlite3.connect(":memory:")
        try:
            migrated_conn.executescript(self.content)
            migrated_conn.executescript(soft_delete_sql)
            migrated_cols = _db_columns(migrated_conn, "listing_wizards")
        finally:
            migrated_conn.close()

        self.assertEqual(
            migrated_cols, canonical_cols,
            "Model ↔ Migration(원본 CREATE TABLE + soft delete ALTER TABLE "
            "합계) 컬럼 불일치.",
        )


class ListingWizardMigrationApplyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(MIGRATION_PATH, encoding="utf-8") as f:
            self.new_sql = f.read()
        with open(SOFT_DELETE_MIGRATION_PATH, encoding="utf-8") as f:
            self.soft_delete_sql = f.read()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_applies_cleanly_on_empty_db(self):

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.new_sql)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertIn("listing_wizards", tables)
        finally:
            conn.close()

    def test_reapply_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.new_sql)
            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    def test_applies_cleanly_on_top_of_full_existing_chain(self):
        """
        실제 DB가 겪을 상황(기존 marketplace_listing 전체 체인이 이미
        적용된 위에 이 Migration을 얹는 것)을 재현한다. listing_wizards는
        다른 테이블을 FK로 참조하지 않으므로 순서 자체는 문제되지 않지만,
        테이블/인덱스 이름 충돌이 없는지는 반드시 확인해야 한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            with open(BASE_MIGRATION_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())
            with open(STATUS_SYNC_MIGRATION_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())
            with open(RATE_LIMIT_MIGRATION_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())

            conn.executescript(self.new_sql)
            conn.executescript(self.soft_delete_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_final_schema_matches_model_exactly(self):
        """
        2026-08-28 — 원본 CREATE TABLE만으로는 더 이상 모델과 일치하지
        않는다(soft delete 컬럼 7개는 별도 ALTER TABLE 파일). "최종
        스키마"는 두 파일을 순서대로 적용한 결과여야 한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.new_sql)
            conn.executescript(self.soft_delete_sql)

            db_cols = _db_columns(conn, "listing_wizards")
            model_cols = _model_columns(ListingWizard)
            self.assertEqual(db_cols, model_cols)
        finally:
            conn.close()

    def test_new_table_starts_empty_no_orphan_seed_rows(self):

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.new_sql)
            count = conn.execute(
                "SELECT COUNT(*) FROM listing_wizards",
            ).fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
