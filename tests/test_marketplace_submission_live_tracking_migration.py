import sqlite3
import tempfile
import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260826_00_add_marketplace_submission_live_tracking.sql"
)


class MarketplaceSubmissionLiveTrackingMigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self.db = sqlite3.connect(self.temp.name)
        self.db.execute(
            """
            CREATE TABLE marketplace_submissions (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL,
                status VARCHAR(30) NOT NULL
            )
            """
        )
        self.db.execute(
            "INSERT INTO marketplace_submissions (id, company_id, status) "
            "VALUES (1, 7, 'PENDING')"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Path(self.temp.name).unlink(missing_ok=True)

    def test_adds_tracking_columns_and_indexes_without_changing_existing_row(self):
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))

        columns = {
            row[1]: row for row in self.db.execute(
                "PRAGMA table_info(marketplace_submissions)"
            )
        }
        self.assertIn("request_fingerprint", columns)
        self.assertIn("correlation_id", columns)
        self.assertIn("external_http_status", columns)
        self.assertEqual(columns["request_fingerprint"][3], 0)
        self.assertEqual(columns["correlation_id"][3], 0)
        self.assertEqual(columns["external_http_status"][3], 0)

        indexes = {
            row[1] for row in self.db.execute(
                "PRAGMA index_list(marketplace_submissions)"
            )
        }
        self.assertIn(
            "ix_marketplace_submissions_request_fingerprint", indexes,
        )
        self.assertIn("ix_marketplace_submissions_correlation_id", indexes)
        self.assertEqual(
            self.db.execute(
                "SELECT id, company_id, status, request_fingerprint, "
                "correlation_id, external_http_status "
                "FROM marketplace_submissions"
            ).fetchone(),
            (1, 7, "PENDING", None, None, None),
        )

    def test_is_additive_only(self):
        sql = MIGRATION.read_text(encoding="utf-8").upper()
        self.assertNotIn("DROP ", sql)
        self.assertNotIn("DELETE ", sql)
        self.assertNotIn("UPDATE ", sql)
        self.assertNotIn("CREATE TABLE", sql)


if __name__ == "__main__":
    unittest.main()
