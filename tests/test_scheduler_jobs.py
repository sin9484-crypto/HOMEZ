"""
=========================================================
Homez OS

File : tests/test_scheduler_jobs.py

2026-09-10 Phase 6 — app/domains/scheduler/jobs.py::
run_backup_rehearsal_job() 검증. 실제 homez.db/SessionLocal은 전혀
쓰지 않는다 — session_factory 인자로 격리된 임시 DB만 주입한다
(모듈 docstring에 이 이유가 명시돼 있음).
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.restore.service import RehearsalResult
from app.domains.restore.service import RestoreService
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.scheduler.jobs import run_backup_rehearsal_job
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용

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

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    _create_schema_migrations_table(db_path)


class BackupRehearsalJobTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp(prefix="homez_job_backups_"))
        self.rehearsal_dir = Path(tempfile.mkdtemp(prefix="homez_job_rehearsal_"))
        self.source_dir = Path(tempfile.mkdtemp(prefix="homez_job_source_"))

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.credential_store = InMemoryCredentialStore()

        db = self.SessionLocal()
        self.active_company_1 = Company(
            name="스케줄러테스트 회사1", business_number="555-55-55551",
            ceo="테스트", phone="02-000-0000",
            email="sched1@example.com", address="테스트", active=True,
        )
        self.active_company_2 = Company(
            name="스케줄러테스트 회사2", business_number="555-55-55552",
            ceo="테스트", phone="02-000-0000",
            email="sched2@example.com", address="테스트", active=True,
        )
        self.inactive_company = Company(
            name="스케줄러테스트 비활성회사", business_number="555-55-55553",
            ceo="테스트", phone="02-000-0000",
            email="sched3@example.com", address="테스트", active=False,
        )
        db.add_all([
            self.active_company_1, self.active_company_2,
            self.inactive_company,
        ])
        db.commit()
        db.refresh(self.active_company_1)
        db.refresh(self.active_company_2)
        db.refresh(self.inactive_company)
        db.close()

        self.source_db_path = self.source_dir / "source.db"
        _make_valid_homez_backup_source(self.source_db_path)

    def tearDown(self):

        self.engine.dispose()
        for p in (self.db_path,):
            if p.exists():
                p.unlink()
        shutil.rmtree(self.backups_dir, ignore_errors=True)
        shutil.rmtree(self.rehearsal_dir, ignore_errors=True)
        shutil.rmtree(self.source_dir, ignore_errors=True)

    def test_runs_rehearsal_only_for_active_companies(self):

        with patch.object(
            RestoreService, "run_weekly_rehearsal",
        ) as mock_rehearsal:
            mock_rehearsal.return_value = RehearsalResult(
                success=True, backup_record=None, restore_attempt=None,
                error_message=None,
            )

            run_backup_rehearsal_job(
                source_db_path=self.source_db_path,
                backups_dir=self.backups_dir,
                rehearsal_dir=self.rehearsal_dir,
                session_factory=self.SessionLocal,
                credential_store=self.credential_store,
            )

        called_company_ids = {
            call.kwargs["company_id"]
            for call in mock_rehearsal.call_args_list
        }
        self.assertEqual(
            called_company_ids,
            {self.active_company_1.id, self.active_company_2.id},
        )

    def test_end_to_end_real_rehearsal_succeeds(self):
        """모킹 없이 실제 RestoreService.run_weekly_rehearsal()까지
        전부 태워서, Job이 실제로 백업 파일을 만드는지 확인한다."""

        run_backup_rehearsal_job(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
            session_factory=self.SessionLocal,
            credential_store=self.credential_store,
        )

        backup_files = list(self.backups_dir.glob("homez_backup_*.db"))
        # 활성 회사 2곳 각각 백업 1개씩 만들었어야 한다.
        self.assertEqual(len(backup_files), 2)

    def test_one_company_failure_does_not_block_others(self):
        """부분 실패 격리 — 한 회사에서 예상치 못한 예외가 나도 나머지
        회사는 계속 처리된다."""

        real_rehearsal = RestoreService.run_weekly_rehearsal
        fail_for_company_id = self.active_company_1.id

        def flaky_rehearsal(restore_self, *, company_id, **kwargs):
            if company_id == fail_for_company_id:
                raise RuntimeError("예상 밖 오류(테스트 주입)")
            return real_rehearsal(restore_self, company_id=company_id, **kwargs)

        with patch.object(
            RestoreService, "run_weekly_rehearsal", flaky_rehearsal,
        ):
            # 예외가 run_backup_rehearsal_job 밖으로 전파되면 안 된다.
            run_backup_rehearsal_job(
                source_db_path=self.source_db_path,
                backups_dir=self.backups_dir,
                rehearsal_dir=self.rehearsal_dir,
                session_factory=self.SessionLocal,
                credential_store=self.credential_store,
            )

        # company_2는 정상 처리돼 백업이 1개는 생겼어야 한다.
        backup_files = list(self.backups_dir.glob("homez_backup_*.db"))
        self.assertEqual(len(backup_files), 1)

    def test_no_active_companies_does_nothing(self):

        db = self.SessionLocal()
        self.active_company_1.active = False
        self.active_company_2.active = False
        db.merge(self.active_company_1)
        db.merge(self.active_company_2)
        db.commit()
        db.close()

        # 예외 없이 조용히 끝나야 한다.
        run_backup_rehearsal_job(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            rehearsal_dir=self.rehearsal_dir,
            session_factory=self.SessionLocal,
            credential_store=self.credential_store,
        )

        backup_files = list(self.backups_dir.glob("homez_backup_*.db"))
        self.assertEqual(len(backup_files), 0)


if __name__ == "__main__":
    unittest.main()
