"""
=========================================================
Homez OS

File : tests/test_purchase_order_submission_migration.py

Gate PT-3(2026-09-08 후속, "확인된 발주 계약 구현") — migrations/
20260908_01_create_purchase_order_submission_attempts.sql 검증.
임시 SQLite 파일에 저장소의 실제 Migration 전체를(이 신규 파일
포함) 순서대로 재생시켜, 처음부터 끝까지 클린 적용이 가능한지·
재적용이 명시적으로 실패하는지·Model과 DDL이 일치하는지·중복 실행
잠금(UNIQUE 제약)이 실제로 동작하는지 확인한다. 실제 homez.db는
전혀 열지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from app.database.migration_runner import MigrationRunner
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = "20260908_01_create_purchase_order_submission_attempts.sql"


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
            self.assertIn("purchase_order_submission_attempts", tables)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_new_table_matches_model_exactly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_order_submission_attempts")
        finally:
            conn.close()

        self.assertEqual(
            cols, _model_columns(PurchaseOrderSubmissionAttempt),
            "purchase_order_submission_attempts: DB 컬럼과 Model이 다릅니다.",
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
                "SELECT COUNT(*) FROM purchase_order_submission_attempts",
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 0)

    def test_duplicate_idempotency_key_within_company_rejected_by_db(self):
        """중복 실행 잠금의 핵심 — UNIQUE 제약 자체가 실제로 동작하는지
        Migration이 만든 실제 테이블에서 직접 확인한다(애플리케이션
        코드가 아니라 스키마 레벨 보증)."""

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            conn.execute(
                "INSERT INTO purchase_order_submission_attempts ("
                "company_id, connection_id, idempotency_key, mall_code, "
                "product_code, options_json, status, started_at, "
                "created_at, updated_at"
                ") VALUES (1, 4, 'order-key-1', 'ONCHANNEL', 'CH1', "
                "'[]', 'PENDING', '2026-09-08T00:00:00', "
                "'2026-09-08T00:00:00', '2026-09-08T00:00:00')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO purchase_order_submission_attempts ("
                    "company_id, connection_id, idempotency_key, mall_code, "
                    "product_code, options_json, status, started_at, "
                    "created_at, updated_at"
                    ") VALUES (1, 4, 'order-key-1', 'ONCHANNEL', 'CH1', "
                    "'[]', 'PENDING', '2026-09-08T00:00:01', "
                    "'2026-09-08T00:00:01', '2026-09-08T00:00:01')",
                )
        finally:
            conn.close()

    def test_same_idempotency_key_different_company_allowed(self):
        """잠금 범위는 회사 단위다 — 다른 회사는 같은 키를 써도
        막히지 않는다(회사 간 격리)."""

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            for company_id in (1, 2):
                conn.execute(
                    "INSERT INTO purchase_order_submission_attempts ("
                    "company_id, connection_id, idempotency_key, mall_code, "
                    "product_code, options_json, status, started_at, "
                    "created_at, updated_at"
                    ") VALUES (?, 4, 'shared-key', 'ONCHANNEL', 'CH1', "
                    "'[]', 'PENDING', '2026-09-08T00:00:00', "
                    "'2026-09-08T00:00:00', '2026-09-08T00:00:00')",
                    (company_id,),
                )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM purchase_order_submission_attempts",
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
            CreateTable(PurchaseOrderSubmissionAttempt.__table__).compile(
                dialect=sqlite_dialect.dialect(),
            ),
        )

        start = migration_sql.index(
            "CREATE TABLE purchase_order_submission_attempts (",
        )
        end = migration_sql.index(");", start) + 1
        actual = migration_sql[start:end]

        self.assertEqual(self._normalize(actual), self._normalize(canonical))


if __name__ == "__main__":
    unittest.main()
