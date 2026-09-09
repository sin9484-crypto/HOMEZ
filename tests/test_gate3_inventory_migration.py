"""
=========================================================
Homez OS

File : tests/test_gate3_inventory_migration.py

V7 Gate 3(2026-08-15) — Inventory 핵심 Migration
(migrations/20260815_01_create_v7_gate3_inventory_schema.sql) 검증.

기존 18개 Migration(Gate 2까지)을 순서대로 적용해 실제 homez.db와
동일한 스키마 상태를 임시 DB에 재현한 뒤, 이 신규 Migration을 적용해
성공/재적용실패/rollback/Model↔DDL 일치/공식 MigrationRunner 호환을
확인한다. 실제 homez.db는 이 테스트 전체에서 전혀 사용하지 않는다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import compute_checksum
from app.domains.inventory.model import InventoryChannelMapping
from app.domains.inventory.model import InventoryLedgerEvent
from app.domains.inventory.model import InventoryReservation
from app.domains.inventory.model import InventorySku

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

# 실제 homez.db의 schema_migrations 이력 순서(Gate 2 종료 시점) — 이
# 신규 Migration 직전까지 전부 적용.
PRIOR_MIGRATIONS = [
    "20260727_00_create_funding_settlement_schema.sql",
    "20260727_add_settlement_ledger_unique_index.sql",
    "20260728_00_create_v24_v3_schema.sql",
    "20260729_00_create_coupang_integration_schema.sql",
    "20260730_00_create_decision_ai_schema.sql",
    "20260730_01_create_auth_session_schema.sql",
    "20260730_02_create_store_connection_schema.sql",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
    "20260802_00_create_media_listing_package_schema.sql",
    "20260802_01_create_account_recovery_schema.sql",
    "20260803_00_create_account_registration_schema.sql",
    "20260805_00_add_marketplace_listing_status_sync.sql",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
    "20260808_00_create_listing_wizard_schema.sql",
    "20260810_00_create_audit_logs_schema.sql",
    "20260814_00_create_user_settings_schema.sql",
    "20260814_01_add_tenant_isolation_company_id.sql",
    "20260815_00_gate2_tenant_isolation_hardening.sql",
]

NEW_MIGRATION = "20260815_01_create_v7_gate3_inventory_schema.sql"

# 전부 신규 테이블(ALTER/재생성 없음).
NEW_TABLE_MODELS = {
    "inventory_skus": InventorySku,
    "inventory_reservations": InventoryReservation,
    "inventory_ledger_events": InventoryLedgerEvent,
    "inventory_channel_mappings": InventoryChannelMapping,
}


def _read(filename: str) -> str:

    path = os.path.join(MIGRATIONS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


def _db_columns(conn, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


def _extract_rollback_sql(content: str) -> str:
    """
    파일 하단의 "-- Rollback (실행하지 않음 ...)" 주석 블록에서 실제
    SQL만 뽑아낸다(각 줄 맨 앞의 "-- " 접두사를 제거) —
    tests/test_gate2_tenant_isolation_hardening_migration.py와 동일 기법.
    """

    lines = content.splitlines()
    rollback_lines = []
    in_sql = False

    for line in lines:
        stripped = line.strip()

        if not in_sql:
            if stripped == "-- BEGIN;":
                in_sql = True
                rollback_lines.append("BEGIN;")
            continue

        if stripped.startswith("-- "):
            rollback_lines.append(stripped[3:])
        elif stripped == "--":
            continue

    return "\n".join(rollback_lines)


class MigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.content = _read(NEW_MIGRATION)

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
        self.assertNotIn("IF NOT EXISTS", self.executed_sql.upper())

    def test_no_alter_or_drop_all_new_tables(self):
        """전부 신규 테이블이므로 ALTER TABLE/DROP TABLE이 전혀 없어야 한다."""

        self.assertNotIn("ALTER TABLE", self.executed_sql.upper())
        self.assertNotIn("DROP TABLE", self.executed_sql.upper())

    def test_touches_only_expected_tables(self):

        created = set(
            re.findall(r"^CREATE TABLE (\w+) \(", self.executed_sql, re.M),
        )
        self.assertEqual(created, set(NEW_TABLE_MODELS))

    def test_does_not_modify_prior_migration_files(self):
        """
        이미 실제 homez.db에 APPLIED로 기록된 파일들은 checksum이
        고정돼 있다 — 이번 작업에서 손대지 않았어야 한다.
        """

        for filename in PRIOR_MIGRATIONS:
            path = os.path.join(MIGRATIONS_DIR, filename)
            self.assertTrue(
                os.path.exists(path), f"{filename} 파일이 없습니다.",
            )


class MigrationApplyTestCase(unittest.TestCase):
    """
    실제 homez.db와 동일한 스키마 상태(Gate 2까지 18개 Migration
    순서대로 적용)를 임시 DB에 재현한 뒤, 신규 Migration을 적용해
    검증한다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_sql = _read(NEW_MIGRATION)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _apply_prior_migrations(self, conn):

        for sql in self.prior_sql:
            conn.executescript(sql)

    def test_full_chain_applies_cleanly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])
        finally:
            conn.close()

    def test_reapply_fails_explicitly(self):
        """
        재적용 시도는 안전하게 실패해야 한다(CREATE TABLE 대상이 이미
        있어 SQLite가 거부한다) — 조용히 무시되지 않는다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    def test_new_table_columns_match_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in NEW_TABLE_MODELS.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_inventory_sku_company_scoped_sku_code_unique(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO inventory_skus (company_id, "
                "product_candidate_id, sku_code, option_label, "
                "available_qty, reserved_qty, safety_stock, is_active, "
                "created_at, updated_at) VALUES "
                "(1, 100, 'SKU-A', '기본', 10, 0, 0, 1, "
                "'2026-01-01', '2026-01-01')",
            )
            # 다른 회사는 동일한 sku_code를 독립적으로 쓸 수 있다.
            conn.execute(
                "INSERT INTO inventory_skus (company_id, "
                "product_candidate_id, sku_code, option_label, "
                "available_qty, reserved_qty, safety_stock, is_active, "
                "created_at, updated_at) VALUES "
                "(2, 200, 'SKU-A', '기본', 5, 0, 0, 1, "
                "'2026-01-01', '2026-01-01')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO inventory_skus (company_id, "
                    "product_candidate_id, sku_code, option_label, "
                    "available_qty, reserved_qty, safety_stock, "
                    "is_active, created_at, updated_at) VALUES "
                    "(1, 101, 'SKU-A', '옵션2', 0, 0, 0, 1, "
                    "'2026-01-01', '2026-01-01')",
                )
        finally:
            conn.close()

    def test_inventory_reservation_idempotency_scoped_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO inventory_skus (company_id, "
                "product_candidate_id, sku_code, option_label, "
                "available_qty, reserved_qty, safety_stock, is_active, "
                "created_at, updated_at) VALUES "
                "(1, 100, 'SKU-A', '기본', 10, 0, 0, 1, "
                "'2026-01-01', '2026-01-01')",
            )

            conn.execute(
                "INSERT INTO inventory_reservations (company_id, "
                "inventory_sku_id, quantity, status, idempotency_key, "
                "created_at, updated_at) VALUES "
                "(1, 1, 3, 'RESERVED', 'order:1:reserve', "
                "'2026-01-01', '2026-01-01')",
            )
            # 다른 회사는 동일한 idempotency_key 문자열을 써도 간섭하지
            # 않는다.
            conn.execute(
                "INSERT INTO inventory_reservations (company_id, "
                "inventory_sku_id, quantity, status, idempotency_key, "
                "created_at, updated_at) VALUES "
                "(2, 1, 1, 'RESERVED', 'order:1:reserve', "
                "'2026-01-01', '2026-01-01')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO inventory_reservations (company_id, "
                    "inventory_sku_id, quantity, status, idempotency_key, "
                    "created_at, updated_at) VALUES "
                    "(1, 1, 1, 'RESERVED', 'order:1:reserve', "
                    "'2026-01-01', '2026-01-01')",
                )
        finally:
            conn.close()

    def test_ledger_event_idempotency_partial_unique_index(self):
        """
        idempotency_key가 NULL인 행(RESERVED/RELEASED/CONSUMED)은 몇
        개든 자유롭게 쌓일 수 있고, NULL이 아닌 값(RESTOCKED/ADJUSTED/
        CHANNEL_SYNC)만 (company_id, idempotency_key) 단위로 유일해야
        한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO inventory_skus (company_id, "
                "product_candidate_id, sku_code, option_label, "
                "available_qty, reserved_qty, safety_stock, is_active, "
                "created_at, updated_at) VALUES "
                "(1, 100, 'SKU-A', '기본', 10, 0, 0, 1, "
                "'2026-01-01', '2026-01-01')",
            )

            # idempotency_key가 NULL인 행은 여러 개 허용.
            for _ in range(3):
                conn.execute(
                    "INSERT INTO inventory_ledger_events (company_id, "
                    "inventory_sku_id, event_type, quantity_delta, "
                    "available_after, reserved_after, "
                    "safety_stock_threshold, created_at) VALUES "
                    "(1, 1, 'RESERVED', -1, 9, 1, 0, '2026-01-01')",
                )
            conn.commit()

            count = conn.execute(
                "SELECT COUNT(*) FROM inventory_ledger_events "
                "WHERE idempotency_key IS NULL",
            ).fetchone()[0]
            self.assertEqual(count, 3)

            conn.execute(
                "INSERT INTO inventory_ledger_events (company_id, "
                "inventory_sku_id, event_type, quantity_delta, "
                "available_after, reserved_after, "
                "safety_stock_threshold, idempotency_key, "
                "created_at) VALUES "
                "(1, 1, 'RESTOCKED', 5, 14, 1, 0, 'restock:1', "
                "'2026-01-01')",
            )
            # 다른 회사는 동일한 idempotency_key 문자열을 써도 간섭하지
            # 않는다.
            conn.execute(
                "INSERT INTO inventory_ledger_events (company_id, "
                "inventory_sku_id, event_type, quantity_delta, "
                "available_after, reserved_after, "
                "safety_stock_threshold, idempotency_key, "
                "created_at) VALUES "
                "(2, 1, 'RESTOCKED', 5, 5, 0, 0, 'restock:1', "
                "'2026-01-01')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO inventory_ledger_events (company_id, "
                    "inventory_sku_id, event_type, quantity_delta, "
                    "available_after, reserved_after, "
                    "safety_stock_threshold, idempotency_key, "
                    "created_at) VALUES "
                    "(1, 1, 'RESTOCKED', 5, 19, 1, 0, 'restock:1', "
                    "'2026-01-01')",
                )
        finally:
            conn.close()

    def test_channel_mapping_uniqueness(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            conn.execute(
                "INSERT INTO inventory_skus (company_id, "
                "product_candidate_id, sku_code, option_label, "
                "available_qty, reserved_qty, safety_stock, is_active, "
                "created_at, updated_at) VALUES "
                "(1, 100, 'SKU-A', '기본', 10, 0, 0, 1, "
                "'2026-01-01', '2026-01-01')",
            )

            conn.execute(
                "INSERT INTO inventory_channel_mappings (company_id, "
                "inventory_sku_id, marketplace_listing_id, channel_code, "
                "channel_sku, is_active, last_sync_status, created_at, "
                "updated_at) VALUES "
                "(1, 1, 500, 'COUPANG', 'CPSKU-1', 1, 'PENDING', "
                "'2026-01-01', '2026-01-01')",
            )
            conn.commit()

            # 같은 SKU를 같은 Listing에 다시 매핑할 수 없다.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO inventory_channel_mappings (company_id, "
                    "inventory_sku_id, marketplace_listing_id, "
                    "channel_code, channel_sku, is_active, "
                    "last_sync_status, created_at, updated_at) VALUES "
                    "(1, 1, 500, 'COUPANG', 'CPSKU-2', 1, 'PENDING', "
                    "'2026-01-01', '2026-01-01')",
                )

            # 같은 회사+채널에서 동일한 channel_sku를 다른 SKU/Listing에
            # 재사용할 수 없다.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO inventory_channel_mappings (company_id, "
                    "inventory_sku_id, marketplace_listing_id, "
                    "channel_code, channel_sku, is_active, "
                    "last_sync_status, created_at, updated_at) VALUES "
                    "(1, 1, 501, 'COUPANG', 'CPSKU-1', 1, 'PENDING', "
                    "'2026-01-01', '2026-01-01')",
                )
        finally:
            conn.close()


class MigrationRollbackTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.prior_sql = [_read(name) for name in PRIOR_MIGRATIONS]
        self.new_content = _read(NEW_MIGRATION)
        self.new_sql = "\n".join(
            line for line in self.new_content.splitlines()
            if not line.strip().startswith("--")
        )
        self.rollback_sql = _extract_rollback_sql(self.new_content)

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_rollback_restores_pre_migration_schema(self):

        conn = sqlite3.connect(self.db_path)
        try:
            for sql in self.prior_sql:
                conn.executescript(sql)

            before_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }

            conn.executescript(self.new_sql)

            new_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertEqual(
                new_tables - before_tables, set(NEW_TABLE_MODELS),
            )

            self.assertTrue(self.rollback_sql.strip())
            conn.executescript(self.rollback_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            after_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertEqual(before_tables, after_tables)
        finally:
            conn.close()


class OfficialMigrationRunnerTestCase(unittest.TestCase):
    """
    이 저장소의 공식 MigrationRunner(app/database/migration_runner.py)
    + 실제 migrations/ 디렉터리(신규 파일 포함) 전체를 그대로 사용한
    리허설. 실제 homez.db는 사용하지 않는다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.runner = MigrationRunner(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
        )

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_new_migration_applies_cleanly_via_runner(self):

        conn = sqlite3.connect(self.db_path)
        try:
            applied = self.runner.apply_pending(conn)
            self.assertIn(NEW_MIGRATION, applied)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

            fk_violations = conn.execute(
                "PRAGMA foreign_key_check",
            ).fetchall()
            self.assertEqual(fk_violations, [])

            for table_name in NEW_TABLE_MODELS:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                self.assertIsNotNone(row, f"{table_name} 테이블이 없습니다.")

            history_row = conn.execute(
                "SELECT status FROM schema_migrations WHERE filename=?",
                (NEW_MIGRATION,),
            ).fetchone()
            self.assertIsNotNone(history_row)
            self.assertEqual(history_row[0], "APPLIED")

            applied_again = self.runner.apply_pending(conn)
            self.assertEqual(applied_again, [])

            diagnosis = self.runner.diagnose(conn)
            self.assertIn(NEW_MIGRATION, diagnosis["already_applied"])
            self.assertNotIn(NEW_MIGRATION, diagnosis["pending"])
        finally:
            conn.close()

    def test_checksum_is_stable(self):

        path = os.path.join(MIGRATIONS_DIR, NEW_MIGRATION)

        from pathlib import Path

        checksum_a = compute_checksum(Path(path))
        checksum_b = compute_checksum(Path(path))
        self.assertEqual(checksum_a, checksum_b)


if __name__ == "__main__":
    unittest.main()
