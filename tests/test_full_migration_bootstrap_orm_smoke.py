"""
=========================================================
Homez OS

File : tests/test_full_migration_bootstrap_orm_smoke.py

2026-09-08 사고 재발 방지 — item 7 작업 중 PurchaseTask/PurchaseRecord
Model에 channel_connection_id 컬럼을 추가한 뒤, 그 Migration이 아직
적용되지 않은 실제 개발 homez.db에서 서버를 재시작했다가 GET
/purchase-tasks(기존, 무관한 엔드포인트)까지 500으로 깨졌다 — Model이
SELECT하는 모든 컬럼이 실제 DB에 있어야 하는데, 새 Migration이
`migrations/`에 있어도 실제 DB에 적용되기 전까지는 그 컬럼이 없기
때문이다. `_enforce_migration_restricted_mode`(app/main.py)는 쓰기만
막고 GET/HEAD/OPTIONS는 항상 통과시키므로 이 실패를 막지 못했다(설계상
의도 — 읽기는 항상 안전하다는 가정이 이번에 깨졌다).

이 테스트는 저장소의 전체 Migration 이력을 공식 bootstrap_environment
(MigrationRunner)로 빈 파일 DB에 처음부터 적용한 뒤, 이번에 새로
추가된 컬럼/테이블을 실제로 SELECT하는 ORM 경로(PurchaseTaskRepository.
list_tasks, PurchaseChannelConnectionService.list_connections)가
OperationalError 없이 동작하는지 확인한다 — "Migration 파일이 존재
한다"와 "그 파일이 적용된 DB에서 관련 ORM 쿼리가 실제로 성공한다"는
서로 다른 사실이며, 이 테스트는 후자를 검증한다. 실제 homez.db는
전혀 열지 않는다.
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.repository import PurchaseTaskRepository
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


class FullMigrationBootstrapOrmSmokeTestCase(unittest.TestCase):
    """전체 Migration 이력을 처음부터 재생한 새 DB에서, 오늘 이 세션이
    실제로 겪은 정확한 실패(no such column: purchase_tasks.channel_
    connection_id / no such table: purchase_channel_connections)가
    재발하지 않는지 확인한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())
        self.addCleanup(
            lambda: self.db_path.exists() and self.db_path.unlink(),
        )

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)
        self.assertFalse(result.migration_approval_required)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        company = Company(
            name="스모크 회사", business_number="999-99-99999",
            ceo="스모크", phone="02-000-0000",
            email="smoke@example.com", address="스모크",
        )
        self.db.add(company)
        self.db.commit()
        self.company_id = company.id

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

    def test_list_tasks_does_not_raise_operational_error(self):
        """오늘 실제로 500을 냈던 정확한 그 호출 경로."""

        repository = PurchaseTaskRepository(self.db)
        result = repository.list_tasks(self.company_id)
        self.assertEqual(result, [])

    def test_list_channel_connections_does_not_raise_operational_error(self):
        """오늘 실제로 "no such table"을 냈던 정확한 그 호출 경로."""

        service = PurchaseChannelConnectionService(self.db)
        result = service.list_connections(self.company_id)
        self.assertEqual(result, [])

    def test_channel_connection_full_lifecycle_on_freshly_migrated_db(self):

        service = PurchaseChannelConnectionService(self.db)
        connection = service.create_connection(
            self.company_id, mall_code="NAVER_SHOPPING", account_label="스모크 계정",
        )
        service.mark_verified(connection.id, self.company_id)
        service.rename_connection(connection.id, self.company_id, "새 이름")
        service.deactivate_connection(connection.id, self.company_id)
        reactivated = service.reactivate_connection(connection.id, self.company_id)
        self.assertTrue(reactivated.is_active)


if __name__ == "__main__":
    unittest.main()
