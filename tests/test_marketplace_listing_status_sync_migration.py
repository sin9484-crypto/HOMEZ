"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_status_sync_migration.py

2026-08-05 최종 제품화 Phase 4 — marketplace_listings 신규 컬럼 5개 +
marketplace_listing_status_events 테이블(provider_observed_at/applied
포함) 추가 Migration 검증.

2026-08-05 CTO 반려 반영 — out-of-order 방어(platform_status_
observed_at)와 event별 반영 여부(applied) 추적을 위한 컬럼 3개가
최초 초안 대비 추가되었다.

`20260731_00_create_marketplace_fulfillment_schema.sql`은 이미 실제
homez.db에 적용되어(BACKFILLED) checksum이 schema_migrations에 고정돼
있으므로, 그 파일을 수정하지 않고 이 신규 파일(순수 ALTER TABLE ADD
COLUMN + CREATE TABLE)로만 추가한다. 리허설은 항상 "원본 9-테이블
스키마가 이미 적용된 임시 DB" 위에서 이 신규 파일을 적용하는 순서로
진행해, 실제 homez.db가 겪을 상황을 그대로 재현한다. 실제 homez.db는
사용하지 않는다(LIVE_GATE_QUEUE 대기).
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest

from app.domains.marketplace_listing.model import MarketplaceListing
from app.domains.marketplace_listing.model import (
    MarketplaceListingStatusEvent,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260731_00_create_marketplace_fulfillment_schema.sql",
)
NEW_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260805_00_add_marketplace_listing_status_sync.sql",
)
# 2026-08-07 Gate H — marketplace_listings에 컬럼 2개를 더 추가하는
# 후속 Migration. 이 파일이 검증하는 20260805 파일 "단독"으로는 더 이상
# 현재 Model과 완전히 일치하지 않는다(그 사이 새 컬럼이 생겼으므로) —
# "최종 스키마가 Model과 정확히 일치하는지"를 검증하려면 그 시점까지의
# 전체 체인을 적용해야 한다(각 신규 Migration이 그 시점 기준 새 컬럼만
# 순수 추가하는 것은 정적 테스트에서 별도로 이미 검증됨).
RATE_LIMIT_MIGRATION_PATH = os.path.join(
    REPO_ROOT, "migrations",
    "20260807_00_add_marketplace_listing_rate_limit.sql",
)


def _model_columns(model_cls) -> dict[str, bool]:
    """컬럼명 -> nullable 여부."""

    return {
        col.name: col.nullable for col in model_cls.__table__.columns
    }


def _db_columns(conn, table_name: str) -> dict[str, bool]:

    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    # row: (cid, name, type, notnull, dflt_value, pk)
    return {row[1]: not bool(row[3]) for row in rows}


class StatusSyncMigrationStaticTestCase(unittest.TestCase):

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
        # 실행되는 본문(주석 제외)에는 DROP이 없어야 한다 — rollback은
        # 전부 주석 안에만 있다.
        self.assertNotRegex(self.executed_sql.upper(), r"\bDROP\b")

    def test_only_one_justified_default_matching_model(self):
        """
        DEFAULT는 오직 platform_sync_status 한 곳에만, Model의 Python
        default("UNKNOWN")와 동일한 값으로만 존재해야 한다.
        """

        defaults = re.findall(
            r"ADD COLUMN (\w+) [^,]*DEFAULT '([^']*)'", self.executed_sql,
        )
        self.assertEqual(defaults, [("platform_sync_status", "UNKNOWN")])

    def test_alters_only_marketplace_listings_and_creates_only_status_events(self):

        alters = re.findall(
            r"^ALTER TABLE (\w+) ADD COLUMN", self.executed_sql, re.M,
        )
        self.assertTrue(all(t == "marketplace_listings" for t in alters))
        self.assertEqual(len(alters), 5)

        creates = re.findall(
            r"^CREATE TABLE (\w+)", self.executed_sql, re.M,
        )
        self.assertEqual(creates, ["marketplace_listing_status_events"])


class StatusSyncMigrationApplyTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        with open(BASE_MIGRATION_PATH, encoding="utf-8") as f:
            self.base_sql = f.read()
        with open(NEW_MIGRATION_PATH, encoding="utf-8") as f:
            self.new_sql = f.read()

    def tearDown(self):

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _apply_base(self, conn):

        conn.executescript(self.base_sql)

    def test_applies_cleanly_on_top_of_already_applied_base_schema(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_base(conn)
            conn.executescript(self.new_sql)

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_reapply_fails_explicitly(self):

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_base(conn)
            conn.executescript(self.new_sql)

            with self.assertRaises(sqlite3.OperationalError):
                conn.executescript(self.new_sql)
        finally:
            conn.close()

    def test_final_schema_matches_model_exactly(self):
        """
        2026-08-07 Gate H — 이 파일이 검증하는 20260805 Migration
        "단독"으로는 더 이상 현재 Model과 일치하지 않는다(그 뒤에
        20260807 Migration이 컬럼 2개를 더 추가했으므로). "최종
        스키마가 Model과 정확히 일치하는가"를 의미 있게 검증하려면
        그 시점까지의 전체 체인(base → status_sync → rate_limit)을
        적용해야 한다 — 이 Migration 파일 자신의 정적 계약(순수
        추가만 하는지 등)은 위 Static 테스트 클래스가 별도로 이미
        검증한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_base(conn)
            conn.executescript(self.new_sql)
            with open(RATE_LIMIT_MIGRATION_PATH, encoding="utf-8") as f:
                conn.executescript(f.read())

            db_listing_cols = _db_columns(conn, "marketplace_listings")
            model_listing_cols = _model_columns(MarketplaceListing)
            self.assertEqual(db_listing_cols, model_listing_cols)

            db_event_cols = _db_columns(
                conn, "marketplace_listing_status_events",
            )
            model_event_cols = _model_columns(MarketplaceListingStatusEvent)
            self.assertEqual(db_event_cols, model_event_cols)
        finally:
            conn.close()

    def test_preserves_preexisting_listing_rows(self):
        """
        ALTER TABLE ADD COLUMN이 기존 행을 지우거나 훼손하지 않는지 —
        신규 NOT NULL 컬럼은 DEFAULT 'UNKNOWN'으로 backfill되어야 한다.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            self._apply_base(conn)

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
                "created_at, updated_at) VALUES (1, 1, NULL, 1, 1, NULL, "
                "'DRAFT', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            )
            conn.commit()

            conn.executescript(self.new_sql)

            row = conn.execute(
                "SELECT id, status, platform_sync_status FROM "
                "marketplace_listings WHERE id = 1",
            ).fetchone()
            self.assertEqual(row, (1, "DRAFT", "UNKNOWN"))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
