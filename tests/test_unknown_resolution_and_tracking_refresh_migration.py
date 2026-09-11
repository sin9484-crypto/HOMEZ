"""
=========================================================
Homez OS

File : tests/test_unknown_resolution_and_tracking_refresh_migration.py

2026-09-11 후속(운영 전 최종 검증 라운드) — migrations/20260911_01_
add_unknown_resolution_and_tracking_refresh.sql 검증. 임시 SQLite
파일에 저장소의 실제 Migration 전체를(이 신규 파일 포함) 순서대로
재생시켜, 처음부터 끝까지 클린 적용이 가능한지·재적용이 명시적으로
실패하는지·Model과 DDL이 일치하는지·기존 purchase_order_submission_
attempts/purchase_task_tracking_infos 행이 보존되는지 확인한다.
실제 homez.db는 전혀 열지 않는다.
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from app.database.migration_runner import MigrationRunner
from app.domains.purchase_task.model import PurchaseOrderUnknownResolutionEvent

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = "20260911_01_add_unknown_resolution_and_tracking_refresh.sql"


def _db_columns(conn: sqlite3.Connection, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


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
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("purchase_order_unknown_resolution_events", tables)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_new_table_matches_model_exactly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_order_unknown_resolution_events")
        finally:
            conn.close()

        self.assertEqual(
            cols, _model_columns(PurchaseOrderUnknownResolutionEvent),
            "purchase_order_unknown_resolution_events: DB 컬럼과 Model이 다릅니다.",
        )

    def test_submission_attempts_gains_five_new_columns(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_order_submission_attempts")
        finally:
            conn.close()

        self.assertIn("unknown_resolution_status", cols)
        self.assertIn("unknown_resolved_order_code", cols)
        self.assertIn("unknown_resolution_basis", cols)
        self.assertIn("unknown_resolved_by", cols)
        self.assertIn("unknown_resolved_at", cols)
        self.assertFalse(
            cols["unknown_resolution_status"],
            "NOT NULL(기본값 UNRESOLVED)이어야 한다.",
        )
        self.assertTrue(cols["unknown_resolved_order_code"])
        self.assertTrue(cols["unknown_resolution_basis"])
        self.assertTrue(cols["unknown_resolved_by"])
        self.assertTrue(cols["unknown_resolved_at"])

    def test_tracking_infos_gains_two_new_nullable_columns(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            cols = _db_columns(conn, "purchase_task_tracking_infos")
        finally:
            conn.close()

        self.assertIn("last_live_refresh_at", cols)
        self.assertIn("last_live_refresh_result", cols)
        self.assertTrue(cols["last_live_refresh_at"])
        self.assertTrue(cols["last_live_refresh_result"])

    def test_existing_attempt_rows_preserved_and_default_backfilled(self):
        """기존 purchase_order_submission_attempts 행이 있어도 이번
        ALTER TABLE ADD COLUMN이 데이터를 보존하고, 신규 컬럼은
        DEFAULT('UNRESOLVED')로 채워지는지 확인한다 — 이 값은 사실과
        다르지 않다: 기존 RESULT_UNKNOWN 행은 실제로 아직 아무도
        확정한 적이 없었다."""

        conn = sqlite3.connect(self.db_path)
        try:
            all_files = sorted(
                f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")
            )
            self.assertIn(NEW_MIGRATION, all_files)
            prior_only_dir = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, prior_only_dir, True)
            for name in all_files:
                if name == NEW_MIGRATION:
                    continue
                shutil.copyfile(
                    os.path.join(MIGRATIONS_DIR, name),
                    os.path.join(prior_only_dir, name),
                )
            prior_runner = MigrationRunner(self.db_path, prior_only_dir)
            prior_runner.apply_pending(conn)

            conn.execute(
                "INSERT INTO purchase_order_submission_attempts ("
                "company_id, connection_id, purchase_task_id, idempotency_key, "
                "mall_code, product_code, options_json, status, "
                "started_at, created_at, updated_at"
                ") VALUES (1, 4, 100, 'pt-100-4-CH1-abc-a1', "
                "'ONCHANNEL', 'CH1', '[]', 'RESULT_UNKNOWN', "
                "'2026-09-01T00:00:00', '2026-09-01T00:00:00', "
                "'2026-09-01T00:00:00')",
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            row = conn.execute(
                "SELECT idempotency_key, status, unknown_resolution_status, "
                "unknown_resolved_order_code "
                "FROM purchase_order_submission_attempts "
                "WHERE idempotency_key = 'pt-100-4-CH1-abc-a1'",
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "기존 행이 사라지면 안 된다.")
        self.assertEqual(row[1], "RESULT_UNKNOWN")
        self.assertEqual(row[2], "UNRESOLVED")
        self.assertIsNone(row[3])

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


class ModelDdlCanonicalDiffTestCase(unittest.TestCase):
    """homez-migration-safety 원칙 — SQLAlchemy CreateTable을 sqlite
    dialect로 컴파일한 canonical DDL과 이 Migration 파일 안의 CREATE
    TABLE 문을 공백만 정규화해 비교한다(신규 테이블만 — ALTER TABLE은
    ADD COLUMN 컬럼 목록을 위 테스트들이 이미 대조한다)."""

    @staticmethod
    def _normalize(sql: str) -> str:

        return " ".join(sql.split()).rstrip(";").strip()

    def test_new_event_table_ddl_matches_model(self):

        with open(
            os.path.join(MIGRATIONS_DIR, NEW_MIGRATION), encoding="utf-8",
        ) as f:
            migration_sql = f.read()

        canonical = str(
            CreateTable(PurchaseOrderUnknownResolutionEvent.__table__).compile(
                dialect=sqlite_dialect.dialect(),
            ),
        )

        start = migration_sql.index(
            "CREATE TABLE purchase_order_unknown_resolution_events (",
        )
        end = migration_sql.index(");", start) + 1
        actual = migration_sql[start:end]

        self.assertEqual(self._normalize(actual), self._normalize(canonical))


if __name__ == "__main__":
    unittest.main()
