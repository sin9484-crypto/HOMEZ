"""
=========================================================
Homez OS

File : tests/test_source_supplier_product_links_migration.py

V7 Section F(2026-08-20) —
migrations/20260820_00_create_source_supplier_product_links_schema.sql
검증. 저장소의 migrations/ 디렉터리 전체를 MigrationRunner로 임시
SQLite 파일에 순서대로 적용해(공식 실행 경로와 동일) 성공/재적용
안전/Model↔DDL 일치를 확인한다. 실제 homez.db는 사용하지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database.migration_runner import MigrationRunner
from app.domains.media_asset.model import MediaAsset
from app.domains.purchase.model import Purchase
from app.domains.source.model import CompanySupplierRelation
from app.domains.source.model import SupplierProductLink

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS_DIR = Path(REPO_ROOT) / "migrations"
NEW_MIGRATION_NAME = "20260820_00_create_source_supplier_product_links_schema.sql"
RELATION_MIGRATION_NAME = "20260820_01_create_company_supplier_relations_schema.sql"
SUBMISSION_MIGRATION_NAME = (
    "20260820_02_add_purchase_supplier_order_submission_columns.sql"
)
RIGHTS_STATUS_MIGRATION_NAME = "20260820_03_add_media_asset_rights_status.sql"


def _model_columns(model_cls) -> dict[str, bool]:

    return {col.name: col.nullable for col in model_cls.__table__.columns}


def _db_columns(conn: sqlite3.Connection, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


class SourceMigrationTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        self.runner = MigrationRunner(self.db_path, MIGRATIONS_DIR)

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)

    def test_new_migration_file_is_discovered(self):

        names = [p.name for p in self.runner.list_migration_files()]
        self.assertIn(NEW_MIGRATION_NAME, names)

    def test_apply_all_migrations_creates_table_matching_model(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            applied = self.runner.apply_pending(conn)
            self.assertTrue(
                any(NEW_MIGRATION_NAME in str(p) for p in applied)
                or self.runner.get_history(conn).get(NEW_MIGRATION_NAME) is not None,
            )

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("supplier_product_links", tables)

            model_cols = _model_columns(SupplierProductLink)
            db_cols = _db_columns(conn, "supplier_product_links")
            self.assertEqual(set(model_cols.keys()), set(db_cols.keys()))
            for name, model_nullable in model_cols.items():
                self.assertEqual(
                    model_nullable, db_cols[name],
                    f"nullable mismatch on column {name}",
                )

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

        finally:
            conn.close()

    def test_reapplying_all_migrations_is_safe(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)
            second_pass = self.runner.apply_pending(conn)
            self.assertEqual(second_pass, [])

        finally:
            conn.close()

    def test_relation_migration_discovered_and_matches_model(self):

        names = [p.name for p in self.runner.list_migration_files()]
        self.assertIn(RELATION_MIGRATION_NAME, names)

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("company_supplier_relations", tables)

            model_cols = _model_columns(CompanySupplierRelation)
            db_cols = _db_columns(conn, "company_supplier_relations")
            self.assertEqual(set(model_cols.keys()), set(db_cols.keys()))

        finally:
            conn.close()

    def test_purchase_submission_columns_discovered_and_match_model(self):

        names = [p.name for p in self.runner.list_migration_files()]
        self.assertIn(SUBMISSION_MIGRATION_NAME, names)

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            model_cols = _model_columns(Purchase)
            db_cols = _db_columns(conn, "purchases")
            self.assertEqual(set(model_cols.keys()), set(db_cols.keys()))

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

        finally:
            conn.close()

    def test_existing_purchase_rows_get_null_submission_columns(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            conn.execute(
                "INSERT INTO purchases (company_id, order_id, supplier_id, "
                "status, idempotency_key, total_cost, requested_at, "
                "created_at, updated_at) VALUES (1, 1, 1, 'REQUESTED', "
                "'pre-existing-key', 1000.0, '2026-08-20 00:00:00', "
                "'2026-08-20 00:00:00', '2026-08-20 00:00:00')",
            )
            conn.commit()

            row = conn.execute(
                "SELECT submission_status, supplier_order_id FROM purchases "
                "WHERE idempotency_key = 'pre-existing-key'",
            ).fetchone()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])

        finally:
            conn.close()

    def test_media_asset_rights_status_column_discovered_and_matches_model(self):

        names = [p.name for p in self.runner.list_migration_files()]
        self.assertIn(RIGHTS_STATUS_MIGRATION_NAME, names)

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            model_cols = _model_columns(MediaAsset)
            db_cols = _db_columns(conn, "media_assets")
            self.assertEqual(set(model_cols.keys()), set(db_cols.keys()))

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")

        finally:
            conn.close()

    def test_existing_media_asset_rows_default_to_unverified(self):
        """4차 지시 — 3차 라운드의 "기존 행은 근거 없이 VERIFIED로
        backfill" 결정을 되돌린다. 신규/기존 구분 없이 컬럼 기본값은
        RIGHTS_UNVERIFIED다(애플리케이션 기본값과 통일)."""

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            conn.execute(
                "INSERT INTO media_assets (company_id, owner_type, owner_id, "
                "asset_role, purpose, display_order, storage_path, mime_type, "
                "file_size_bytes, sha256_hex, status, created_at) VALUES "
                "(1, 'PRODUCT_CANDIDATE', 1, 'ORIGINAL', 'MAIN', 0, "
                "'media/1/ab/test.png', 'image/png', 100, "
                "'ab0000000000000000000000000000000000000000000000000000000000', "
                "'ACTIVE', '2026-08-20 00:00:00')",
            )
            conn.commit()

            row = conn.execute(
                "SELECT rights_status FROM media_assets "
                "WHERE storage_path = 'media/1/ab/test.png'",
            ).fetchone()
            self.assertEqual(row[0], "RIGHTS_UNVERIFIED")
        finally:
            conn.close()

    def test_backfill_update_copies_real_source_value_not_a_hardcoded_literal(self):
        """4차 지시 — 파생물(GENERATED) 행의 backfill이 실제
        migrations/20260820_03 파일에 적힌 그 UPDATE 문을 그대로
        추출해서, "리터럴을 박아 넣은 게 아니라 source_asset_id 관계를
        진짜 SELECT로 따라간다"는 것을 독립적으로 증명한다. 원본을
        인위적으로 다른 값(VERIFIED)으로 미리 심어 둔 뒤 UPDATE를
        돌려도 파생물이 정확히 그 값을 그대로 따라가야 하고, source_
        asset_id가 끊어진(고아) 행은 기본값(RIGHTS_UNVERIFIED)에
        그대로 남아야 한다."""

        migration_path = MIGRATIONS_DIR / RIGHTS_STATUS_MIGRATION_NAME
        sql_text = migration_path.read_text(encoding="utf-8")

        match_start = sql_text.index("UPDATE media_assets")
        match_end = sql_text.index(";", match_start) + 1
        update_statement = sql_text[match_start:match_end]

        self.assertIn("source_asset_id", update_statement)
        self.assertIn("SELECT", update_statement)
        self.assertNotIn("'VERIFIED'", update_statement)

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE media_assets (id INTEGER PRIMARY KEY, "
                "asset_role TEXT, source_asset_id INTEGER, rights_status TEXT)",
            )
            # 원본(이미 다른 메커니즘으로 VERIFIED가 된 것을 인위적으로
            # 시뮬레이션) — 이 값이 하드코딩이 아니라 실제로 복사되는지
            # 확인하는 것이 이 테스트의 핵심이다.
            conn.execute(
                "INSERT INTO media_assets VALUES (1, 'ORIGINAL', NULL, 'VERIFIED')",
            )
            # 정상 파생물 — 원본(#1)의 값을 그대로 따라가야 한다.
            conn.execute(
                "INSERT INTO media_assets VALUES "
                "(2, 'GENERATED', 1, 'RIGHTS_UNVERIFIED')",
            )
            # source_asset_id가 끊어진(고아) 파생물 — 기본값 그대로여야 한다.
            conn.execute(
                "INSERT INTO media_assets VALUES "
                "(3, 'GENERATED', 999, 'RIGHTS_UNVERIFIED')",
            )
            # source_asset_id가 NULL인 원본 — 대상에서 아예 제외되어야 한다.
            conn.execute(
                "INSERT INTO media_assets VALUES "
                "(4, 'ORIGINAL', NULL, 'RIGHTS_UNVERIFIED')",
            )
            conn.commit()

            conn.executescript(update_statement)

            rows = dict(
                conn.execute(
                    "SELECT id, rights_status FROM media_assets ORDER BY id",
                ).fetchall(),
            )
            self.assertEqual(rows[1], "VERIFIED")  # 원본은 UPDATE 대상이 아님
            self.assertEqual(rows[2], "VERIFIED")  # 원본 값을 그대로 복사
            self.assertEqual(rows[3], "RIGHTS_UNVERIFIED")  # 고아는 그대로
            self.assertEqual(rows[4], "RIGHTS_UNVERIFIED")  # ORIGINAL은 대상 아님
        finally:
            conn.close()

    def test_unique_constraint_enforced(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.ensure_history_table(conn)
            self.runner.apply_pending(conn)

            conn.execute(
                "INSERT INTO supplier_product_links ("
                "company_id, product_candidate_id, supplier_id, supplier_sku, "
                "unit_cost, moq, status, created_by, created_at, updated_at"
                ") VALUES (1, 1, 1, 'SKU-1', 1000.0, 1, 'ACTIVE', 1, "
                "'2026-08-20 00:00:00', '2026-08-20 00:00:00')",
            )
            conn.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO supplier_product_links ("
                    "company_id, product_candidate_id, supplier_id, supplier_sku, "
                    "unit_cost, moq, status, created_by, created_at, updated_at"
                    ") VALUES (1, 1, 1, 'SKU-1', 2000.0, 1, 'ACTIVE', 1, "
                    "'2026-08-20 00:00:00', '2026-08-20 00:00:00')",
                )

        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
