"""
=========================================================
Homez OS

File : tests/test_purchase_channel_connection_migration.py

Gate PT-3(2026-09-08, item 7) — migrations/20260908_00_create_
purchase_channel_connection_schema.sql 검증. 임시 SQLite 파일에
저장소의 실제 Migration 전체를(mine 포함) 순서대로 재생 시켜, 처음부터
끝까지 클린 적용이 가능한지·재적용이 명시적으로 실패하는지·Model과
DDL이 일치하는지 확인한다. 실제 homez.db는 전혀 열지 않는다.
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
from app.database.migration_runner import MigrationRunnerError
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

NEW_MIGRATION = "20260908_00_create_purchase_channel_connection_schema.sql"

# 2026-09-09 — 20260908_01_create_purchase_order_submission_attempts.sql이
# NEW_MIGRATION보다 늦게 추가되면서(사전순으로 NEW_MIGRATION 뒤에 위치),
# "NEW_MIGRATION 하나만 제외하면 나머지는 전부 이전 상태"라는 이 파일의
# 원래 가정이 깨졌다 — 제외 대상에 없는 이 파일이 "이전 상태" 임시 DB에
# 그대로 복사·적용되어, NEW_MIGRATION 없이 그보다 사전순으로 뒤인 파일만
# 적용된 상태가 되면서 MigrationRunner.diagnose()가 OrderInversionError를
# 정확하게 잡아냈다(전체 회귀 2026-09-09 실행에서 재현). 이 두 파일은
# 같은 작업 단위(item 7)이므로 "이전 상태"에서는 둘 다 제외해야 한다.
EXCLUDED_FROM_PRIOR_STATE = {
    NEW_MIGRATION,
    "20260908_01_create_purchase_order_submission_attempts.sql",
}


def _db_columns(conn: sqlite3.Connection, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


class FullChainReplayTestCase(unittest.TestCase):
    """저장소의 실제 migrations/ 디렉터리 전체를(이 신규 파일 포함)
    빈 파일 DB에 순서대로 적용한다 — 지금까지 쌓인 모든 Migration이
    여전히 처음부터 클린하게 재생 가능함을 이 신규 파일과 함께
    재확인하는 것이 목적이다."""

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
            applied = self.runner.apply_pending(conn)
            applied_names = {a.filename for a in applied} if applied and hasattr(applied[0], "filename") else set(applied or [])
        finally:
            conn.close()

        # apply_pending()의 반환 형태가 버전에 따라 달라질 수 있으므로
        # 형태에 의존하지 않고, 실제 DB에 두 신규 테이블이 생겼는지로
        # 최종 결과를 검증한다.
        conn = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("purchase_channel_connections", tables)
            self.assertIn("purchase_channel_connection_events", tables)
            self.assertIn("purchase_tasks", tables)
            self.assertIn("purchase_records", tables)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_purchase_tasks_and_purchase_records_gain_connection_column(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            task_cols = _db_columns(conn, "purchase_tasks")
            record_cols = _db_columns(conn, "purchase_records")
        finally:
            conn.close()

        self.assertIn("channel_connection_id", task_cols)
        self.assertTrue(task_cols["channel_connection_id"], "nullable이어야 합니다.")
        self.assertIn("channel_connection_id", record_cols)
        self.assertTrue(record_cols["channel_connection_id"], "nullable이어야 합니다.")

    def test_new_tables_match_model_exactly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            connection_cols = _db_columns(conn, "purchase_channel_connections")
            event_cols = _db_columns(conn, "purchase_channel_connection_events")
        finally:
            conn.close()

        self.assertEqual(
            connection_cols, _model_columns(PurchaseChannelConnection),
            "purchase_channel_connections: DB 컬럼과 Model이 다릅니다.",
        )
        self.assertEqual(
            event_cols, _model_columns(PurchaseChannelConnectionEvent),
            "purchase_channel_connection_events: DB 컬럼과 Model이 다릅니다.",
        )

    def test_reapplying_full_chain_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
        finally:
            conn.close()

        # 이미 전부 적용된 상태에서 다시 apply_pending()을 호출하면
        # pending이 없어 조용히 아무 것도 하지 않아야 정상이다(재적용
        # 실패는 "같은 파일을 두 번 직접 실행"할 때의 계약이다 — 아래에서
        # 그걸 직접 확인한다).
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

    def test_new_tables_start_empty_no_backfill_guessing(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            connection_count = conn.execute(
                "SELECT COUNT(*) FROM purchase_channel_connections",
            ).fetchone()[0]
            event_count = conn.execute(
                "SELECT COUNT(*) FROM purchase_channel_connection_events",
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(connection_count, 0)
        self.assertEqual(event_count, 0)

    def test_existing_purchase_task_rows_preserved_after_add_column(self):
        """기존 purchase_tasks/purchase_records 데이터가 있어도 이번
        ADD COLUMN이 그 데이터를 보존하는지 — 실제로 한 행을 미리
        넣어두고 Migration을 적용해도 사라지지 않는지 확인한다."""

        conn = sqlite3.connect(self.db_path)
        try:
            # 새 파일 적용 직전까지만 적용(마지막 파일 제외)하기 위해,
            # 신규 파일을 제외한 별도 러너로 먼저 적용한다.
            all_files = sorted(
                f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")
            )
            self.assertIn(NEW_MIGRATION, all_files)
            prior_only_dir = tempfile.mkdtemp()
            for name in all_files:
                if name in EXCLUDED_FROM_PRIOR_STATE:
                    continue
                # 바이트 그대로 복사한다(텍스트 모드 read/write는 개행
                # 문자를 바꿔 checksum을 어긋나게 만들 수 있다).
                shutil.copyfile(
                    os.path.join(MIGRATIONS_DIR, name),
                    os.path.join(prior_only_dir, name),
                )

            prior_runner = MigrationRunner(self.db_path, prior_only_dir)
            prior_runner.apply_pending(conn)

            conn.execute(
                "INSERT INTO purchase_tasks ("
                "company_id, source_order_id, source_order_item_id, "
                "creation_source, product_title, quantity, options_json, "
                "components_json, status, version, retryable, "
                "idempotency_key, created_at, updated_at"
                ") VALUES (1, 1, NULL, 'MANUAL', 'existing row', 1, '[]', "
                "'[]', 'SEARCH_REQUIRED', 1, 0, 'existing-key', "
                "'2026-09-08T00:00:00', '2026-09-08T00:00:00')",
            )
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(self.db_path)
        try:
            self.runner.apply_pending(conn)
            row = conn.execute(
                "SELECT product_title, channel_connection_id FROM purchase_tasks "
                "WHERE idempotency_key = 'existing-key'",
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "Migration 적용 후 기존 행이 사라졌습니다.")
        self.assertEqual(row[0], "existing row")
        self.assertIsNone(row[1], "기존 행의 새 컬럼은 추측으로 채우지 않고 NULL이어야 합니다.")


class ModelDdlCanonicalDiffTestCase(unittest.TestCase):
    """homez-migration-safety 원칙 — SQLAlchemy CreateTable을 sqlite
    dialect로 컴파일한 canonical DDL과 이 Migration 파일 안의 CREATE
    TABLE 문을 공백만 정규화해 비교한다."""

    @staticmethod
    def _normalize(sql: str) -> str:

        return " ".join(sql.split()).rstrip(";").strip()

    def test_connection_table_ddl_matches_model(self):

        with open(
            os.path.join(MIGRATIONS_DIR, NEW_MIGRATION), encoding="utf-8",
        ) as f:
            migration_sql = f.read()

        canonical = str(
            CreateTable(PurchaseChannelConnection.__table__).compile(
                dialect=sqlite_dialect.dialect(),
            ),
        )

        start = migration_sql.index("CREATE TABLE purchase_channel_connections (")
        end = migration_sql.index(");", start) + 1
        actual = migration_sql[start:end]

        self.assertEqual(self._normalize(actual), self._normalize(canonical))

    def test_event_table_ddl_matches_model(self):

        with open(
            os.path.join(MIGRATIONS_DIR, NEW_MIGRATION), encoding="utf-8",
        ) as f:
            migration_sql = f.read()

        canonical = str(
            CreateTable(PurchaseChannelConnectionEvent.__table__).compile(
                dialect=sqlite_dialect.dialect(),
            ),
        )

        start = migration_sql.index("CREATE TABLE purchase_channel_connection_events (")
        end = migration_sql.index(");", start) + 1
        actual = migration_sql[start:end]

        self.assertEqual(self._normalize(actual), self._normalize(canonical))


if __name__ == "__main__":
    unittest.main()
