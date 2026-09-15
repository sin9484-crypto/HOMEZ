"""
=========================================================
Homez OS

File : tests/test_restore_weekly_rehearsal.py

2026-09-09 Phase 5(HOMEZ_USER_OPERATION_SETTINGS.md 11번 — "DB 복구
가능 여부를 매주 자동 또는 안내 기반으로 시험하고 결과를 기록한다")
검증. RestoreService.run_weekly_rehearsal()은:

  1) source_db_path를 새 백업으로 뜬다(trigger_source=
     scheduled_rehearsal).
  2) 그 백업을 rehearsal_dir 아래 "버릴 목적의" 임시 경로에만
     복원해 본다 — 실제 homez.db는 절대 건드리지 않는다.
  3) 검증이 끝나면 그 임시 파일만 지운다. 백업 자체는 남는다.
  4) 실패하면(백업 실패든 복원 검증 실패든) 항상 예외 없이
     RehearsalResult를 반환하고 회사 SUPER_ADMIN에게
     BACKUP_RESTORE_REHEARSAL_FAILED를 통지한다.

실제 homez.db는 이 파일 어디에서도 열지 않는다 — self.db는
bootstrap_environment()가 만든 임시 파일 DB이고, 백업 대상
source_db_path도 별도의 임시 파일이다.
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.database.bootstrap import bootstrap_environment
from app.domains.backup.service import TRIGGER_SOURCE_SCHEDULED_REHEARSAL
from app.domains.company.model import Company
from app.domains.restore.model import RESTORE_STATUS_FAILED
from app.domains.restore.model import RESTORE_STATUS_SUCCEEDED
from app.domains.restore.service import RestoreService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def _create_schema_migrations_table(db_path: Path) -> None:

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            status TEXT NOT NULL,
            execution_ms INTEGER,
            notes TEXT
        )
        """,
    )
    conn.commit()
    conn.close()


def _make_valid_homez_backup_source(db_path: Path) -> None:
    """복원 가능(REQUIRED_CORE_TABLES 충족) 판정을 받는 최소 소스."""

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    _create_schema_migrations_table(db_path)


def _make_incomplete_backup_source(db_path: Path) -> None:
    """HOMEZ 핵심 테이블이 없는(복원 검증에서 거부되어야 하는) 소스."""

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


class WeeklyRehearsalTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp(prefix="homez_rehearsal_backups_"))
        self.rehearsal_dir = Path(tempfile.mkdtemp(prefix="homez_rehearsal_targets_"))
        self.source_dir = Path(tempfile.mkdtemp(prefix="homez_rehearsal_source_"))

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="리허설테스트 회사", business_number="444-44-44444",
            ceo="테스트", phone="02-000-0000",
            email="rehearsal@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="rehearsaladmin",
            email="rehearsaladmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.credential_store = InMemoryCredentialStore()
        self.service = RestoreService(self.db, self.credential_store)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        for p in (self.db_path,):
            if p.exists():
                p.unlink()
        shutil.rmtree(self.backups_dir, ignore_errors=True)
        shutil.rmtree(self.rehearsal_dir, ignore_errors=True)
        shutil.rmtree(self.source_dir, ignore_errors=True)

    def _notification_rows(self):

        return self.db.execute(
            text(
                "SELECT user_id, event_code FROM notification_email_logs "
                "WHERE event_code = 'BACKUP_RESTORE_REHEARSAL_FAILED'",
            ),
        ).fetchall()

    # ----------------------------------------------------
    # 성공 경로
    # ----------------------------------------------------

    def test_successful_rehearsal_returns_success_result(self):

        source_db_path = self.source_dir / "source.db"
        _make_valid_homez_backup_source(source_db_path)

        result = self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertTrue(result.success)
        self.assertIsNone(result.error_message)
        self.assertIsNotNone(result.backup_record)
        self.assertIsNotNone(result.restore_attempt)
        self.assertEqual(
            result.restore_attempt.status, RESTORE_STATUS_SUCCEEDED,
        )

    def test_successful_rehearsal_uses_scheduled_rehearsal_trigger_source(self):

        source_db_path = self.source_dir / "source.db"
        _make_valid_homez_backup_source(source_db_path)

        result = self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertEqual(
            result.backup_record.trigger_source,
            TRIGGER_SOURCE_SCHEDULED_REHEARSAL,
        )

    def test_successful_rehearsal_keeps_backup_but_deletes_throwaway_restore_target(self):

        source_db_path = self.source_dir / "source.db"
        _make_valid_homez_backup_source(source_db_path)

        result = self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        # 백업 파일은 남아있어야 한다(보존 정책 대상).
        self.assertTrue(Path(result.backup_record.file_path).exists())

        # 복원 리허설 대상이었던 임시 파일은 지워졌어야 한다.
        restored_target = Path(result.restore_attempt.target_db_path)
        self.assertFalse(restored_target.exists())

    def test_successful_rehearsal_does_not_notify(self):

        source_db_path = self.source_dir / "source.db"
        _make_valid_homez_backup_source(source_db_path)

        self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertEqual(len(self._notification_rows()), 0)

    def test_source_db_path_is_never_modified(self):

        source_db_path = self.source_dir / "source.db"
        _make_valid_homez_backup_source(source_db_path)
        original_bytes = source_db_path.read_bytes()

        self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertEqual(source_db_path.read_bytes(), original_bytes)

    # ----------------------------------------------------
    # 실패 경로 — 백업 생성 자체가 실패
    # ----------------------------------------------------

    def test_rehearsal_fails_when_source_db_missing(self):

        missing_source = self.source_dir / "does_not_exist.db"

        result = self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=missing_source,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.backup_record)
        self.assertIsNone(result.restore_attempt)
        self.assertIsNotNone(result.error_message)

    def test_backup_creation_failure_notifies_super_admin(self):

        missing_source = self.source_dir / "does_not_exist.db"

        self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=missing_source,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        rows = self._notification_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], self.admin.id)

    # ----------------------------------------------------
    # 실패 경로 — 백업은 성공했지만 복원 검증이 거부
    # ----------------------------------------------------

    def test_rehearsal_fails_when_backup_lacks_required_tables(self):

        source_db_path = self.source_dir / "incomplete.db"
        _make_incomplete_backup_source(source_db_path)

        result = self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertFalse(result.success)
        self.assertIsNotNone(result.backup_record, "백업 자체는 성공했어야 한다")
        self.assertIsNone(result.restore_attempt)
        self.assertIsNotNone(result.error_message)

    def test_restore_validation_failure_still_records_restore_attempt_row(self):
        """반환값의 restore_attempt는 None이지만, restore() 내부가 이미
        RestoreAttempt(FAILED) 행을 남겼어야 한다 — "결과를 기록한다"
        요구사항은 반환값이 아니라 이 DB 행으로 충족된다."""

        source_db_path = self.source_dir / "incomplete.db"
        _make_incomplete_backup_source(source_db_path)

        self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        failed_count = self.db.execute(
            text(
                "SELECT COUNT(*) FROM restore_attempts WHERE status = :s",
            ),
            {"s": RESTORE_STATUS_FAILED},
        ).scalar()
        self.assertEqual(failed_count, 1)

    def test_restore_validation_failure_notifies_super_admin(self):

        source_db_path = self.source_dir / "incomplete.db"
        _make_incomplete_backup_source(source_db_path)

        self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertEqual(len(self._notification_rows()), 1)

    # ----------------------------------------------------
    # 알림 멱등성
    # ----------------------------------------------------

    def test_repeated_same_day_failures_notify_only_once(self):

        missing_source = self.source_dir / "does_not_exist.db"

        self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=missing_source,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )
        self.service.run_weekly_rehearsal(
            company_id=self.company.id,
            source_db_path=missing_source,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
        )

        self.assertEqual(len(self._notification_rows()), 1)


if __name__ == "__main__":
    unittest.main()
