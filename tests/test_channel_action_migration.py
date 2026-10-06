"""
=========================================================
Homez OS

File : tests/test_channel_action_migration.py

2026-10-05 — `channel_action_requests` Migration이 `ChannelActionRequest` 모델과 정확히 같은
DDL인지, 제약이 DB 수준에서 동작하는지, 재적용을 거부하는지 임시 DB에서 검증한다
(실제 DB에는 적용하지 않는다).
=========================================================
"""

import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from app.domains.purchase_task.model import ChannelActionRequest

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "20261005_00_create_channel_action_request_schema.sql"


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.replace(",\n", ", ")).replace("( ", "(").replace(" )", ")").strip()


class ChannelActionMigrationTestCase(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _apply(self, conn):
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))

    def test_migration_ddl_is_the_models_canonical_sqlite_ddl(self):
        table = ChannelActionRequest.__table__
        dialect = sqlite_dialect.dialect()
        expected = [_normalize(str(CreateTable(table).compile(dialect=dialect)))] + [
            _normalize(str(CreateIndex(ix).compile(dialect=dialect)))
            for ix in sorted(table.indexes, key=lambda i: i.name)
        ]
        text = MIGRATION.read_text(encoding="utf-8")
        statements = [
            _normalize(m) for m in re.findall(
                r"(CREATE (?:UNIQUE )?(?:TABLE|INDEX)[^;]*);", text, flags=re.S)
        ]
        self.assertEqual(
            [s.replace(" ,", ",").rstrip(",") for s in statements],
            [e.rstrip(";") for e in expected],
        )

    def test_applies_cleanly_and_keeps_the_database_healthy(self):
        conn = sqlite3.connect(self.path)
        try:
            self._apply(conn)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            columns = [r[1] for r in conn.execute("PRAGMA table_info(channel_action_requests)")]
            self.assertEqual(columns, [c.name for c in ChannelActionRequest.__table__.columns])
        finally:
            conn.close()

    def test_unique_target_is_enforced_by_the_database(self):
        conn = sqlite3.connect(self.path)
        try:
            self._apply(conn)
            insert = (
                "INSERT INTO channel_action_requests (company_id, store_connection_id, "
                "action_type, target_key, status, attempt_count, requested_at, created_at, "
                "updated_at) VALUES (1, 1, ?, ?, 'REQUESTING', 1, '2026-10-05', '2026-10-05', "
                "'2026-10-05')"
            )
            conn.execute(insert, ("SALE_STOP", "vi:1"))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(insert, ("SALE_STOP", "vi:1"))
            conn.execute(insert, ("ORDER_CANCEL", "vi:1"))   # 동작이 다르면 별개 대상
            conn.execute(insert, ("SALE_STOP", "vi:2"))
        finally:
            conn.close()

    def test_migration_rejects_reapplication_and_documents_rollback_without_running_it(self):
        conn = sqlite3.connect(self.path)
        try:
            self._apply(conn)
            with self.assertRaises(sqlite3.OperationalError):
                self._apply(conn)
        finally:
            conn.close()
        text = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("-- DROP TABLE IF EXISTS channel_action_requests;", text)
        self.assertNotRegex(text, r"(?m)^DROP TABLE")
        # 기존 테이블·데이터를 건드리는 구문이 없다(추가형)
        self.assertNotRegex(text.upper(), r"\b(ALTER|UPDATE|DELETE|INSERT)\b")

    def test_no_secret_or_personal_data_columns(self):
        names = {c.name for c in ChannelActionRequest.__table__.columns}
        for forbidden in ("secret", "key", "token", "password", "address", "phone", "name"):
            self.assertFalse(
                [n for n in names if forbidden in n and n != "target_key"], forbidden)


if __name__ == "__main__":
    unittest.main()
