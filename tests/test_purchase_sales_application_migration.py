"""
=========================================================
Homez OS

File : tests/test_purchase_sales_application_migration.py

2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수" 확정) —
migrations/20260910_07_create_purchase_sales_application_schema.sql
검증. 임시 SQLite 파일에 저장소의 실제 Migration 전체를(이 신규
파일 포함) 순서대로 재생시켜, 처음부터 끝까지 클린 적용이 가능한지·
재적용이 명시적으로 실패하는지·Model과 DDL이 일치하는지·
(company_id, connection_id, product_code) UNIQUE 제약이 실제로
동작하는지 확인한다. 실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from app.database.migration_runner import MigrationRunner
from app.domains.purchase_task.model import PurchaseSalesApplicationAttempt

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = "20260910_07_create_purchase_sales_application_schema.sql"


def _db_columns(conn: sqlite3.Connection, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


class FullChainReplayTestCase(unittest.TestCase):
    """저장소의 실제 migrations/ 디렉터리 전체를(이 신규 파일 포함)
    빈 파일 DB에 순서대로 적용한다."""

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
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("purchase_sales_application_attempts", tables)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_new_table_matches_model_exactly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_sales_application_attempts")
        finally:
            conn.close()

        self.assertEqual(
            cols, _model_columns(PurchaseSalesApplicationAttempt),
            "purchase_sales_application_attempts: DB 컬럼과 Model이 다릅니다.",
        )

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

    def test_table_starts_empty(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM purchase_sales_application_attempts",
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 0)

    def test_duplicate_product_within_same_connection_rejected_by_db(self):
        """이 테이블의 핵심 — (company_id, connection_id, product_code)
        UNIQUE 제약이 "같은 연결로 같은 상품을 두 번 신청 시도해도
        행은 하나만 존재한다"를 스키마 레벨에서 보증한다(애플리케이션
        코드가 아니라)."""

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            conn.execute(
                "INSERT INTO purchase_sales_application_attempts ("
                "company_id, connection_id, mall_code, product_code, "
                "status, started_at, created_at, updated_at"
                ") VALUES (1, 4, 'ONCHANNEL', 'CH1', "
                "'PENDING', '2026-09-10T00:00:00', "
                "'2026-09-10T00:00:00', '2026-09-10T00:00:00')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO purchase_sales_application_attempts ("
                    "company_id, connection_id, mall_code, product_code, "
                    "status, started_at, created_at, updated_at"
                    ") VALUES (1, 4, 'ONCHANNEL', 'CH1', "
                    "'PENDING', '2026-09-10T00:00:01', "
                    "'2026-09-10T00:00:01', '2026-09-10T00:00:01')",
                )
        finally:
            conn.close()

    def test_same_product_different_connection_allowed(self):
        """같은 회사라도 다른 연결(다른 온채널 계정)로는 같은 상품을
        독립적으로 신청 이력을 남길 수 있다."""

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            for connection_id in (4, 5):
                conn.execute(
                    "INSERT INTO purchase_sales_application_attempts ("
                    "company_id, connection_id, mall_code, product_code, "
                    "status, started_at, created_at, updated_at"
                    ") VALUES (1, ?, 'ONCHANNEL', 'CH1', "
                    "'PENDING', '2026-09-10T00:00:00', "
                    "'2026-09-10T00:00:00', '2026-09-10T00:00:00')",
                    (connection_id,),
                )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM purchase_sales_application_attempts",
            ).fetchone()[0]
            self.assertEqual(count, 2)
        finally:
            conn.close()

    def test_same_product_different_company_allowed(self):
        """회사 간 격리 — 다른 회사는 같은 connection_id·product_code
        조합이 와도(실무에서는 발생하지 않지만 스키마 차원에서) 막히지
        않는다."""

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            for company_id in (1, 2):
                conn.execute(
                    "INSERT INTO purchase_sales_application_attempts ("
                    "company_id, connection_id, mall_code, product_code, "
                    "status, started_at, created_at, updated_at"
                    ") VALUES (?, 4, 'ONCHANNEL', 'CH1', "
                    "'PENDING', '2026-09-10T00:00:00', "
                    "'2026-09-10T00:00:00', '2026-09-10T00:00:00')",
                    (company_id,),
                )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM purchase_sales_application_attempts",
            ).fetchone()[0]
            self.assertEqual(count, 2)
        finally:
            conn.close()


class ModelDdlCanonicalDiffTestCase(unittest.TestCase):
    """homez-migration-safety 원칙 — SQLAlchemy CreateTable을 sqlite
    dialect로 컴파일한 canonical DDL과 이 Migration 파일 안의 CREATE
    TABLE 문을 공백만 정규화해 비교한다."""

    @staticmethod
    def _normalize(sql: str) -> str:

        return " ".join(sql.split()).rstrip(";").strip()

    def test_table_ddl_matches_model(self):

        with open(
            os.path.join(MIGRATIONS_DIR, NEW_MIGRATION), encoding="utf-8",
        ) as f:
            migration_sql = f.read()

        canonical = str(
            CreateTable(PurchaseSalesApplicationAttempt.__table__).compile(
                dialect=sqlite_dialect.dialect(),
            ),
        )

        start = migration_sql.index(
            "CREATE TABLE purchase_sales_application_attempts (",
        )
        end = migration_sql.index(");", start) + 1
        actual = migration_sql[start:end]

        self.assertEqual(self._normalize(actual), self._normalize(canonical))


if __name__ == "__main__":
    unittest.main()
