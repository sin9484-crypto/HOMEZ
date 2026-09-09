from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine

from app.database.base import Base
from app.domains.order.collection_model import (
    OrderChannelFulfillment, OrderCollectionCursor, OrderSkuResolution,
    UnresolvedOrderItem,
)


ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "20260831_00_create_coupang_order_collection_schema.sql"


class CoupangOrderCollectionMigrationTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_draft_migration_matches_model_tables(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            actual = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            self.assertTrue({
                "order_channel_fulfillments", "unresolved_order_items",
                "order_collection_cursors", "order_sku_resolutions",
            }.issubset(actual))
        finally:
            conn.close()

        model_path = self.path + ".model"
        engine = create_engine(f"sqlite:///{model_path}")
        try:
            Base.metadata.create_all(
                engine,
                tables=[
                    OrderChannelFulfillment.__table__,
                    UnresolvedOrderItem.__table__,
                    OrderCollectionCursor.__table__,
                    OrderSkuResolution.__table__,
                ],
            )
            left = sqlite3.connect(self.path)
            right = sqlite3.connect(model_path)
            try:
                for table in (
                    "order_channel_fulfillments", "unresolved_order_items",
                    "order_collection_cursors", "order_sku_resolutions",
                ):
                    migration_columns = {
                        row[1]: row[2] for row in left.execute(
                            f"PRAGMA table_info({table})",
                        )
                    }
                    model_columns = {
                        row[1]: row[2] for row in right.execute(
                            f"PRAGMA table_info({table})",
                        )
                    }
                    self.assertEqual(migration_columns, model_columns)
            finally:
                left.close()
                right.close()
        finally:
            engine.dispose()
            if os.path.exists(model_path):
                os.remove(model_path)


if __name__ == "__main__":
    unittest.main()
