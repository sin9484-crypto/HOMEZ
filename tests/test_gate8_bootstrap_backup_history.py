"""
=========================================================
Homez OS

File : tests/test_gate8_bootstrap_backup_history.py

V7 Gate 8(2026-08-15) — `app/database/bootstrap.py`의 pre-migration
백업이 `app/domains/backup`의 `BackupRecord` 이력에도 남는지 검증.

배경: `bootstrap_environment()`는 Migration 적용 직전 항상
`MigrationRunner.create_backup()`으로 파일을 만들지만, 그 결과는
지금까지 `backup_records` 테이블에 전혀 기록되지 않았다 — 운영자가
백업 이력 화면(`GET /backups`)을 봐도 이 자동 백업의 존재를 알 수
없었다(데이터 자체는 안전 — 파일은 무결성 검증까지 마치고 실제로
존재한다, 순수 가시성 결함). Gate 8이 `_record_backup_history_event()`
헬퍼를 추가해 두 지점(backfill만 있는 경우 / 실제 pending 적용 시)
모두에서 최선 노력으로 이 기록을 남기도록 보강했다 — 이 테스트는
그 보강이 실제로 동작하는지, 그리고 실패해도 부트스트랩 자체를
막지 않는지(backup_records 테이블이 아직 없는 경우) 확인한다.
실제 homez.db는 이 테스트 전체에서 전혀 사용하지 않는다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database.bootstrap import bootstrap_environment


def _write_migration(path: Path, sql: str) -> None:

    path.write_text(sql, encoding="utf-8")


_BACKUP_RECORDS_DDL = (
    "BEGIN;\n"
    "CREATE TABLE backup_records (\n"
    "\tid INTEGER NOT NULL,\n"
    "\tfile_path VARCHAR(500) NOT NULL,\n"
    "\tfile_size_bytes BIGINT NOT NULL,\n"
    "\tsha256 VARCHAR(64) NOT NULL,\n"
    "\tintegrity_check_result VARCHAR(50) NOT NULL,\n"
    "\ttrigger_source VARCHAR(50) NOT NULL,\n"
    "\ttriggered_by_user_id INTEGER,\n"
    "\tlabel VARCHAR(200),\n"
    "\tcreated_at DATETIME NOT NULL,\n"
    "\tPRIMARY KEY (id)\n"
    ");\n"
    "COMMIT;\n"
)


class BootstrapBackupHistoryTestCase(unittest.TestCase):

    def setUp(self):

        self.tmpdir = tempfile.TemporaryDirectory()
        self.migrations_dir = Path(self.tmpdir.name) / "migrations"
        self.migrations_dir.mkdir()
        self.backups_dir = Path(self.tmpdir.name) / "backups"

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)

    def tearDown(self):

        self.tmpdir.cleanup()

        if self.db_path.exists():
            os.remove(self.db_path)

    def test_approved_pending_apply_records_backup_history(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_backup_records.sql",
            _BACKUP_RECORDS_DDL,
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_widgets.sql",
            "BEGIN;\nCREATE TABLE widgets (id INTEGER PRIMARY KEY);\n"
            "COMMIT;\n",
        )

        # 1차: 신규 설치 — backup_records까지 전부 적용됨(빈 DB라
        # 백업 자체가 생략되므로 이력도 없음, 정상).
        bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
        )

        conn = sqlite3.connect(str(self.db_path))
        try:
            count_before = conn.execute(
                "SELECT COUNT(*) FROM backup_records",
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count_before, 0)

        # 2차: 이제 신규 pending Migration을 추가하고, 승인 없이 먼저
        # 호출(backfill 없음·pending만 있음 — 이 케이스는 백업 자체를
        # 만들지 않는 기존 분기이므로 이력도 안 생기는 게 정상)한
        # 뒤, 승인해서 실제로 적용한다 — 이때 백업이 만들어지고
        # 그 이력이 backup_records에 남아야 한다.
        _write_migration(
            self.migrations_dir / "20260103_00_create_gizmos.sql",
            "BEGIN;\nCREATE TABLE gizmos (id INTEGER PRIMARY KEY);\n"
            "COMMIT;\n",
        )

        approved_result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
            approved_migration_files=["20260103_00_create_gizmos.sql"],
        )

        self.assertEqual(
            approved_result.applied, ["20260103_00_create_gizmos.sql"],
        )
        self.assertIsNotNone(approved_result.backup_path)

        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT file_path, trigger_source, "
                "integrity_check_result, label FROM backup_records",
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(len(rows), 1)
        file_path, trigger_source, integrity, label = rows[0]
        self.assertEqual(file_path, str(approved_result.backup_path))
        self.assertEqual(trigger_source, "pre_migration")
        self.assertEqual(integrity, "ok")
        self.assertEqual(label, "bootstrap_migration")
        self.assertTrue(Path(file_path).exists())

    def test_missing_backup_records_table_does_not_block_bootstrap(self):
        """
        backup_records 테이블이 아직 없는(이 Migration 자체가 아직
        적용 안 된) 상태에서도 이력 기록 시도가 실패해 부트스트랩
        전체를 막으면 안 된다 — 최선 노력 원칙 확인.
        """

        _write_migration(
            self.migrations_dir / "20260101_00_create_widgets.sql",
            "BEGIN;\nCREATE TABLE widgets (id INTEGER PRIMARY KEY);\n"
            "COMMIT;\n",
        )

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        _write_migration(
            self.migrations_dir / "20260102_00_create_gizmos.sql",
            "BEGIN;\nCREATE TABLE gizmos (id INTEGER PRIMARY KEY);\n"
            "COMMIT;\n",
        )

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
            approved_migration_files=["20260102_00_create_gizmos.sql"],
        )

        self.assertEqual(result.applied, ["20260102_00_create_gizmos.sql"])
        self.assertEqual(result.integrity_check_result, "ok")


if __name__ == "__main__":
    unittest.main()
