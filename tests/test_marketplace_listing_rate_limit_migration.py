"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_rate_limit_migration.py

Gate H(2026-08-07) — marketplace_listings에 rate_limit_retry_after_
seconds/rate_limit_retry_available_at 컬럼 2개 추가 Migration 검증.

`20260805_00_add_marketplace_listing_status_sync.sql`은 이미 실제
homez.db에 APPLIED로 기록되어(schema_migrations, checksum
e8399dc8...c43e184 — 이 사실은 읽기 전용으로 재확인했다) checksum이
고정돼 있으므로, 그 파일을 수정하지 않고 이 신규 파일(순수 ALTER
TABLE ADD COLUMN)로만 추가한다. 리허설은 항상 "원본 9-테이블 스키마 +
status_sync 컬럼/테이블이 이미 적용된 임시 DB" 위에서 이 신규 파일을
적용하는 순서로 진행해, 실제 homez.db가 겪을 상황을 그대로 재현한다.
실제 homez.db는 사용하지 않는다(LIVE_GATE_QUEUE 대기).
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from app.domains.marketplace_listing.model import MarketplaceListing

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
)
STATUS_SYNC_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260805_00_add_marketplace_listing_status_sync.sql",
)
NEW_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
)


def _model_columns(model_cls) -> dict[str, bool]:

    return {
        col.name: col.nullable for col in model_cls.__table__.columns
    }


def _db_columns(conn, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: not bool(row[3]) for row in rows}


class RateLimitMigrationStaticTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(NEW_MIGRATION_PATH, encoding="utf-8") as f:
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

    def test_no_foreign_keys_or_drop_or_create_if_not_exists(self):

        self.assertNotIn("FOREIGN KEY", self.executed_sql.upper())
        self.assertNotIn("REFERENCES", self.executed_sql.upper())
        self.assertNotIn("CREATE TABLE IF NOT EXISTS", self.content)
        self.assertNotRegex(self.executed_sql.upper(), r"\bDROP\b")

    def test_no_default_needed_both_columns_nullable(self):
        """
        Model에서 둘 다 nullable=True이고 Python default도 없으므로,
        이 Migration에는 DEFAULT가 전혀 없어야 한다(기존
        20260805 파일의 platform_sync_status DEFAULT와 성격이
        다르다 — 그건 NOT NULL 컬럼의 backfill이 필수였다).
        """

        self.assertNotIn("DEFAULT", self.executed_sql.upper())

    def test_alters_only_marketplace_listings_two_columns(self):

        alters = re.findall(
            r"^ALTER TABLE (\w+) ADD COLUMN (\w+)", self.executed_sql, re.M,
        )
        self.assertEqual(
            alters,
            [
                ("marketplace_listings", "rate_limit_retry_after_seconds"),
                ("marketplace_listings", "rate_limit_retry_available_at"),
            ],
        )

        creates = re.findall(r"^CREATE TABLE (\w+)", self.executed_sql, re.M)
        self.assertEqual(creates, [])


class RateLimitMigrationApplyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(BASE_MIGRATION_PATH, encoding="utf-8") as f:
            self.base_sql = f.read()
        with open(STATUS_SYNC_MIGRATION_PATH, encoding="utf-8") as f:
            self.status_sync_sql = f.read()
        with open(NEW_MIGRATION_PATH, encoding="utf-8") as f:
            self.new_sql = f.read()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _apply_prerequisites(self, conn):

        conn.executescript(self.base_sql)
        conn.executescript(self.status_sync_sql)

    def test_applies_cleanly_on_top_of_already_applied_prerequisites(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prerequisites(conn)
            conn.executescript(self.new_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_reapply_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prerequisites(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    def test_final_schema_matches_model_exactly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prerequisites(conn)
            conn.executescript(self.new_sql)

            db_cols = _db_columns(conn, "marketplace_listings")
            model_cols = _model_columns(MarketplaceListing)
            self.assertEqual(db_cols, model_cols)
        finally:
            conn.close()

    def test_preserves_preexisting_listing_rows(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_prerequisites(conn)

            conn.execute(
                "INSERT INTO marketplace_channels "
                "(id, code, name, doc_verification_status, is_active, "
                "created_at, updated_at) VALUES (1, 'COUPANG', '쿠팡', "
                "'VERIFIED', 1, '2026-01-01T00:00:00', "
                "'2026-01-01T00:00:00')",
            )
            conn.execute(
                "INSERT INTO marketplace_accounts "
                "(id, company_id, channel_id, account_code, account_name, "
                "direct_purchase_contract_status, is_active, created_at, "
                "updated_at) VALUES (1, 1, 1, 'acct-1', '테스트 계정', "
                "'NONE', 1, '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            )
            conn.execute(
                "INSERT INTO marketplace_listings "
                "(id, company_id, draft_id, product_candidate_id, "
                "marketplace_account_id, external_listing_id, status, "
                "platform_sync_status, created_at, updated_at) VALUES "
                "(1, 1, NULL, 1, 1, NULL, 'DRAFT', 'UNKNOWN', "
                "'2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            )
            conn.commit()

            conn.executescript(self.new_sql)

            row = conn.execute(
                "SELECT id, status, rate_limit_retry_after_seconds, "
                "rate_limit_retry_available_at FROM marketplace_listings "
                "WHERE id = 1",
            ).fetchone()
            self.assertEqual(row, (1, "DRAFT", None, None))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
