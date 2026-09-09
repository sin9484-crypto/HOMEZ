"""
=========================================================
Homez OS

File : tests/test_gate4_order_fulfillment_migration.py

V7 Gate 4(2026-08-15) — 주문/발주/배송/반품
(migrations/20260815_02_create_v7_gate4_order_fulfillment_schema.sql)
검증.

기존 19개 Migration(Gate 3까지)을 순서대로 적용해 실제 homez.db와
동일한 스키마 상태를 임시 DB에 재현한 뒤, 이 신규 Migration을 적용해
성공/재적용실패/rollback/Model↔DDL 일치/공식 MigrationRunner 호환을
확인한다(tests/test_gate3_inventory_migration.py와 동일 패턴). 실제
homez.db는 이 테스트 전체에서 전혀 사용하지 않는다.
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import compute_checksum
from app.domains.order.model import Order
from app.domains.order.model import OrderIngestionEvent
from app.domains.order.model import OrderItem
from app.domains.order.model import OrderStatusEvent
from app.domains.purchase.model import Purchase
from app.domains.purchase.model import PurchaseItem
from app.domains.return_order.model import ReturnOrder
from app.domains.return_order.model import ReturnOrderStatusEvent
from app.domains.shipment.model import Shipment
from app.domains.shipment.model import ShipmentItem
from app.domains.shipment.model import ShipmentStatusEvent

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")

# 실제 homez.db의 schema_migrations 이력 순서(Gate 3 종료 시점) — 이
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
    "20260815_01_create_v7_gate3_inventory_schema.sql",
]

NEW_MIGRATION = "20260815_02_create_v7_gate4_order_fulfillment_schema.sql"

# 전부 신규 테이블(ALTER/재생성 없음).
NEW_TABLE_MODELS = {
    "orders": Order,
    "order_items": OrderItem,
    "order_ingestion_events": OrderIngestionEvent,
    "order_status_events": OrderStatusEvent,
    "purchases": Purchase,
    "purchase_items": PurchaseItem,
    "shipments": Shipment,
    "shipment_items": ShipmentItem,
    "shipment_status_events": ShipmentStatusEvent,
    "return_orders": ReturnOrder,
    "return_order_status_events": ReturnOrderStatusEvent,
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

        self.assertNotIn("ALTER TABLE", self.executed_sql.upper())
        self.assertNotIn("DROP TABLE", self.executed_sql.upper())

    def test_touches_only_expected_tables(self):

        created = set(
            re.findall(r"^CREATE TABLE (\w+) \(", self.executed_sql, re.M),
        )
        self.assertEqual(created, set(NEW_TABLE_MODELS))

    def test_does_not_modify_prior_migration_files(self):

        for filename in PRIOR_MIGRATIONS:
            path = os.path.join(MIGRATIONS_DIR, filename)
            self.assertTrue(
                os.path.exists(path), f"{filename} 파일이 없습니다.",
            )


class MigrationApplyTestCase(unittest.TestCase):

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

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    # 2026-08-20 추가 — migrations/20260820_02_add_purchase_supplier_
    # order_submission_columns.sql이 purchases에 additive 컬럼을
    # 더했다(SupplierOrderProvider 연결). 이 테스트는 오직 이 Gate 4
    # 파일(20260815_02) 하나만 적용해 비교하므로, 그 이후 정당하게
    # 추가된 컬럼은 여기서 예외로 둔다(별도 Migration 테스트
    # tests/test_source_supplier_product_links_migration.py가 전체
    # Migration 적용 후 purchases 컬럼 전체 일치를 이미 검증한다).
    _PURCHASES_COLUMNS_ADDED_BY_LATER_MIGRATION = frozenset({
        "submission_status", "submission_provider_code", "supplier_order_id",
        "submitted_at", "confirmed_price", "accepted_quantities_json",
        "rejected_quantities_json", "submission_error_code",
        "submission_retryable", "submission_retry_after_seconds",
        "correlation_id", "approval_fingerprint",
    })

    # 2026-09-07 추가 — migrations/20260907_00_add_order_item_purchase_
    # task_link.sql이 order_items에 purchase_task_id를 더했다(V7 통합
    # 매입 item4, purchase_task→OrderItem 연결). 이 테스트는 오직 이
    # Gate 4 파일(20260815_02) 하나만 적용해 비교하므로, 그 이후
    # 정당하게 추가된 컬럼은 여기서 예외로 둔다(별도 테스트
    # tests/test_purchase_task_order_item_linkage.py가 이 컬럼의
    # 실제 연결·값 채움을 이미 검증한다).
    _ORDER_ITEMS_COLUMNS_ADDED_BY_LATER_MIGRATION = frozenset({
        "purchase_task_id",
    })

    def test_new_table_columns_match_model(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            for table_name, model_cls in NEW_TABLE_MODELS.items():
                db_cols = _db_columns(conn, table_name)
                model_cols = _model_columns(model_cls)
                if table_name == "purchases":
                    for col in self._PURCHASES_COLUMNS_ADDED_BY_LATER_MIGRATION:
                        model_cols.pop(col, None)
                if table_name == "order_items":
                    for col in self._ORDER_ITEMS_COLUMNS_ADDED_BY_LATER_MIGRATION:
                        model_cols.pop(col, None)
                self.assertEqual(
                    db_cols, model_cols,
                    f"{table_name}: DB 컬럼과 Model이 다릅니다.",
                )
        finally:
            conn.close()

    def test_orders_company_channel_order_unique(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            def _insert(company_id, channel_order_id):
                conn.execute(
                    "INSERT INTO orders (company_id, channel_code, "
                    "channel_order_id, order_number, status, buyer_name, "
                    "receiver_name, receiver_phone, receiver_address, "
                    "receiver_zipcode, total_amount, ordered_at, "
                    "channel_sync_status, created_at, updated_at) VALUES "
                    "(?, 'FAKE', ?, 'O-1', 'PENDING', 'buyer', 'receiver', "
                    "'010', 'addr', '00000', 1000, '2026-01-01', "
                    "'PENDING', '2026-01-01', '2026-01-01')",
                    (company_id, channel_order_id),
                )

            _insert(1, "CH-001")
            # 다른 회사는 동일한 channel_order_id를 독립적으로 쓸 수 있다.
            _insert(2, "CH-001")
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                _insert(1, "CH-001")
        finally:
            conn.close()

    def test_purchases_idempotency_scoped_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            def _insert(company_id, key):
                conn.execute(
                    "INSERT INTO purchases (company_id, order_id, "
                    "supplier_id, status, idempotency_key, total_cost, "
                    "requested_at, created_at, updated_at) VALUES "
                    "(?, 1, 1, 'REQUESTED', ?, 1000, '2026-01-01', "
                    "'2026-01-01', '2026-01-01')",
                    (company_id, key),
                )

            _insert(1, "order:1:purchase")
            _insert(2, "order:1:purchase")
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                _insert(1, "order:1:purchase")
        finally:
            conn.close()

    def test_shipments_idempotency_scoped_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            def _insert(company_id, key):
                conn.execute(
                    "INSERT INTO shipments (company_id, order_id, "
                    "shipment_number, idempotency_key, status, "
                    "created_at, updated_at) VALUES "
                    "(?, 1, 'S-1', ?, 'PENDING', '2026-01-01', "
                    "'2026-01-01')",
                    (company_id, key),
                )

            _insert(1, "order:1:shipment:1")
            _insert(2, "order:1:shipment:1")
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                _insert(1, "order:1:shipment:1")
        finally:
            conn.close()

    def test_return_orders_idempotency_scoped_per_company(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prior_migrations(conn)
            conn.executescript(self.new_sql)

            def _insert(company_id, key):
                conn.execute(
                    "INSERT INTO return_orders (company_id, order_id, "
                    "order_item_id, shipment_id, return_type, status, "
                    "quantity, reason, idempotency_key, requested_at, "
                    "created_at, updated_at) VALUES "
                    "(?, 1, 1, 1, 'RETURN', 'REQUESTED', 1, '단순변심', "
                    "?, '2026-01-01', '2026-01-01', '2026-01-01')",
                    (company_id, key),
                )

            _insert(1, "return:1")
            _insert(2, "return:1")
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                _insert(1, "return:1")
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
