import sqlite3
import tempfile
import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260827_00_repair_notification_audit_user_fk.sql"
)


class NotificationAuditForeignKeyRepairMigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp.close()
        self.db = sqlite3.connect(self.temp.name)
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                action TEXT NOT NULL,
                description TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            INSERT INTO users (id) VALUES (1);
            INSERT INTO audit_logs VALUES
                (1, 0, 'NOTIFICATION_EMAIL_SKIPPED', 'legacy system event'),
                (2, 0, 'UNRELATED_ACTION', 'must remain untouched'),
                (3, 1, 'NOTIFICATION_EMAIL_SKIPPED', 'real user'),
                (4, NULL, 'NOTIFICATION_EMAIL_SKIPPED', 'already correct');
            """
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Path(self.temp.name).unlink(missing_ok=True)

    def test_repairs_only_known_notification_sentinel_row(self):
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))
        rows = self.db.execute(
            "SELECT id, user_id FROM audit_logs ORDER BY id"
        ).fetchall()
        self.assertEqual(rows, [(1, None), (2, 0), (3, 1), (4, None)])

    def test_does_not_rewrite_user_zero_when_it_really_exists(self):
        self.db.execute("INSERT INTO users (id) VALUES (0)")
        self.db.commit()
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.assertEqual(
            self.db.execute(
                "SELECT user_id FROM audit_logs WHERE id=1"
            ).fetchone()[0],
            0,
        )

    def test_migration_is_narrow_and_non_destructive(self):
        sql = MIGRATION.read_text(encoding="utf-8").upper()
        self.assertNotIn("DROP ", sql)
        self.assertNotIn("DELETE ", sql)
        self.assertNotIn("ALTER TABLE", sql)
        self.assertIn("ACTION = 'NOTIFICATION_EMAIL_SKIPPED'", sql)
        self.assertIn("NOT EXISTS", sql)


if __name__ == "__main__":
    unittest.main()
