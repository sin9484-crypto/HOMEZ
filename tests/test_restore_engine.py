"""
=========================================================
Homez OS

File : tests/test_restore_engine.py

Gate Y-2(2026-08-12) — 복원 엔진 검증. 전부 임시 SQLite 파일만
사용한다(실제 homez.db는 이 테스트에서 전혀 열지 않는다).

V7 Live Gate 4 복원 결함 수정(2026-08-17) — 코디네이터의 명시적 판단
으로 `validate_backup_file()`의 필수 테이블(`REQUIRED_CORE_TABLES`)/
`schema_migrations` 검증이 재활성화됐다. 이 파일의 `setUp()`이 쓰던
"probe 테이블 하나짜리" 백업 소스는 그 자체로는 더 이상 restorable=
True가 아니므로, HOMEZ 핵심 테이블(schema_migrations/users/companies
/roles)을 갖춘 최소 스키마로 바꿨다 — 이 파일의 테스트들은 애초에
"필수 테이블 검증"이 아니라 "복원 메커니즘"(안전 백업/원자적 교체/
이력 기록)을 검증하는 것이 목적이므로, 유효한 HOMEZ 백업을 쓰는 것이
맞다. "HOMEZ 스키마와 무관한 백업은 거부돼야 한다"는 시나리오는 별도
테스트(`RestoreValidateRequiredTablesTestCase`)로 분리해 명시적으로
검증한다 — 이전에는 이 시나리오가 반대로 "정상 복원돼야 한다"고
잘못 기대되어 있었다(코디네이터 지시에 따라 기대치를 수정, 삭제
아님).
=========================================================
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.guard import admin_guard
from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.backup.encryption import decrypt_file
from app.domains.backup.encryption import get_or_create_backup_encryption_key
from app.domains.backup.model import BackupRecord
from app.domains.backup.service import TRIGGER_SOURCE_MANUAL
from app.domains.backup.service import BackupService
from app.domains.company.model import Company  # noqa: F401 (User FK 해석용)
from app.domains.restore import router as restore_router_module
from app.domains.restore.model import RESTORE_STATUS_FAILED
from app.domains.restore.model import RESTORE_STATUS_SUCCEEDED
from app.domains.restore.model import RestoreAttempt
from app.domains.restore.service import REQUIRED_CORE_TABLES
from app.domains.restore.service import RestoreError
from app.domains.restore.service import RestoreService
from app.domains.role.model import Role  # noqa: F401 (User FK 해석용)
from app.domains.user.model import User  # noqa: F401 (User FK 해석용)


def _create_schema_migrations_table(db_path: Path) -> None:
    """migration_runner.py가 실제로 쓰는 것과 동일한 DDL(순수 SQL,
    ORM 모델 없음 — schema_migrations는 SQLAlchemy Base.metadata에
    매핑돼 있지 않다)."""

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


def _make_valid_homez_backup_source(db_path: Path, probe_value: str) -> None:
    """
    HOMEZ 핵심 테이블(REQUIRED_CORE_TABLES)을 전부 갖춘 최소 백업
    소스를 만든다 — 이 파일의 테스트는 "복원 메커니즘"을 검증하는
    것이 목적이므로, 실제로 restorable=True가 되는 유효한 HOMEZ
    백업을 써야 한다.
    """

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    _create_schema_migrations_table(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO probe (value) VALUES (?)", (probe_value,))
    conn.commit()
    conn.close()


class RestoreEngineTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_restore_test_"))
        self.backups_dir = self.tmp_dir / "backups"

        self.app_db_path = self.tmp_dir / "app.db"
        self.engine = create_engine(f"sqlite:///{self.app_db_path}")
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.credential_store = InMemoryCredentialStore()

        # 실제(유효한 HOMEZ 스키마) 백업 하나를 만들어 복원 대상으로
        # 쓴다 — REQUIRED_CORE_TABLES 재활성화 이후에도 이 파일의
        # "복원 메커니즘" 테스트들이 계속 유효하려면 필수.
        self.source_db_path = self.tmp_dir / "source.db"
        _make_valid_homez_backup_source(self.source_db_path, "v1")

        backup_service = BackupService(self.db, self.credential_store)
        self.backup_record = backup_service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source=TRIGGER_SOURCE_MANUAL,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ----------------------------------------------------
    # validate_backup_file() — 읽기 전용
    # ----------------------------------------------------

    def test_validate_backup_file_ok(self):

        service = RestoreService(self.db, self.credential_store)

        result = service.validate_backup_file(
            Path(self.backup_record.file_path),
            expected_sha256=self.backup_record.sha256,
        )

        self.assertTrue(result["file_exists"])
        self.assertTrue(result["sha256_matches"])
        self.assertEqual(result["integrity_check_result"], "ok")
        self.assertTrue(result["restorable"])
        self.assertIsNone(result["reason"])

    def test_validate_backup_file_missing(self):

        service = RestoreService(self.db, self.credential_store)

        result = service.validate_backup_file(
            self.tmp_dir / "does_not_exist.db",
        )

        self.assertFalse(result["file_exists"])
        self.assertFalse(result["restorable"])

    def test_validate_backup_file_sha256_mismatch_blocked(self):

        service = RestoreService(self.db, self.credential_store)

        result = service.validate_backup_file(
            Path(self.backup_record.file_path),
            expected_sha256="0" * 64,
        )

        self.assertFalse(result["sha256_matches"])
        self.assertFalse(result["restorable"])
        self.assertIn("SHA-256", result["reason"])

    def test_validate_does_not_write_anything(self):
        """
        검증은 읽기 전용이어야 한다 — RestoreAttempt 이력이 전혀
        생기지 않는지 확인한다.
        """

        service = RestoreService(self.db, self.credential_store)
        service.validate_backup_file(Path(self.backup_record.file_path))

        self.assertEqual(len(service.list_attempts()), 0)

    # ----------------------------------------------------
    # restore() — target이 존재하지 않는 최초 복원
    # ----------------------------------------------------

    def test_restore_into_nonexistent_target_succeeds(self):

        service = RestoreService(self.db, self.credential_store)
        target = self.tmp_dir / "restored_fresh.db"

        attempt = service.restore(
            source_backup_path=Path(self.backup_record.file_path),
            target_db_path=target,
            expected_sha256=self.backup_record.sha256,
        )

        self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        self.assertIsNone(attempt.pre_restore_backup_path)
        self.assertTrue(target.exists())

        verify = sqlite3.connect(str(target))
        row = verify.execute(
            "SELECT value FROM probe WHERE id = 1",
        ).fetchone()
        verify.close()
        self.assertEqual(row[0], "v1")

    # ----------------------------------------------------
    # restore() — 기존 target을 덮어쓰는 경우: 안전 백업 필수
    # ----------------------------------------------------

    def test_restore_over_existing_target_without_safety_dir_blocked(self):

        service = RestoreService(self.db, self.credential_store)
        target = self.tmp_dir / "existing_target.db"
        shutil.copyfile(self.source_db_path, target)

        with self.assertRaises(RestoreError):
            service.restore(
                source_backup_path=Path(self.backup_record.file_path),
                target_db_path=target,
                expected_sha256=self.backup_record.sha256,
                pre_restore_backups_dir=None,
            )

        attempts = service.list_attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].status, RESTORE_STATUS_FAILED)

    def test_restore_over_existing_target_creates_safety_backup_first(self):

        service = RestoreService(self.db, self.credential_store)
        target = self.tmp_dir / "existing_target2.db"

        # 기존 target에는 다른 값을 넣어 "안전백업이 실제로 이전
        # 상태를 보존하는지"를 확인할 수 있게 한다.
        existing_conn = sqlite3.connect(str(target))
        existing_conn.execute(
            "CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)",
        )
        existing_conn.execute(
            "INSERT INTO probe (value) VALUES ('old-value')",
        )
        existing_conn.commit()
        existing_conn.close()

        safety_dir = self.tmp_dir / "pre_restore_backups"

        attempt = service.restore(
            source_backup_path=Path(self.backup_record.file_path),
            target_db_path=target,
            expected_sha256=self.backup_record.sha256,
            pre_restore_backups_dir=safety_dir,
            triggered_by_user_id=7,
        )

        self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        self.assertIsNotNone(attempt.pre_restore_backup_path)
        self.assertTrue(Path(attempt.pre_restore_backup_path).exists())

        # 안전 백업에는 old-value가 남아 있어야 한다(교체 전 스냅샷).
        # 2026-09-15 전면 감사 후속(Phase 5) — 이 안전 백업도
        # BackupService.create_backup()이 만들므로 이제 암호화되어
        # 있다 — 같은 키로 복호화한 사본을 열어 확인한다.
        key = get_or_create_backup_encryption_key(self.credential_store)
        decrypted_safety_path = self.tmp_dir / "decrypted_safety_for_test.db"
        decrypt_file(
            Path(attempt.pre_restore_backup_path), decrypted_safety_path, key,
        )
        safety_conn = sqlite3.connect(str(decrypted_safety_path))
        old_row = safety_conn.execute(
            "SELECT value FROM probe WHERE id = 1",
        ).fetchone()
        safety_conn.close()
        self.assertEqual(old_row[0], "old-value")

        # target 자체는 새 값(v1)으로 교체돼 있어야 한다.
        new_conn = sqlite3.connect(str(target))
        new_row = new_conn.execute(
            "SELECT value FROM probe WHERE id = 1",
        ).fetchone()
        new_conn.close()
        self.assertEqual(new_row[0], "v1")

    def test_corrupted_backup_blocks_restore_and_leaves_target_untouched(
        self,
    ):

        service = RestoreService(self.db, self.credential_store)
        target = self.tmp_dir / "protected_target.db"
        shutil.copyfile(self.source_db_path, target)

        before_bytes = target.read_bytes()

        with self.assertRaises(RestoreError):
            service.restore(
                source_backup_path=Path(self.backup_record.file_path),
                target_db_path=target,
                expected_sha256="0" * 64,  # 강제 불일치
                pre_restore_backups_dir=self.tmp_dir / "unused_safety",
            )

        after_bytes = target.read_bytes()
        self.assertEqual(before_bytes, after_bytes)

    def test_list_attempts_returns_most_recent_first(self):

        service = RestoreService(self.db, self.credential_store)

        first = service.restore(
            source_backup_path=Path(self.backup_record.file_path),
            target_db_path=self.tmp_dir / "t1.db",
        )
        second = service.restore(
            source_backup_path=Path(self.backup_record.file_path),
            target_db_path=self.tmp_dir / "t2.db",
        )

        attempts = service.list_attempts()
        self.assertEqual(attempts[0].id, second.id)
        self.assertEqual(attempts[1].id, first.id)


# ----------------------------------------------------
# validate_backup_file() — 필수 테이블/schema_migrations 검증
# (V7 Live Gate 4 복원 결함 수정, 2026-08-17 코디네이터 판단으로
# 재활성화. 이 클래스는 그 이전에는 반대로 "HOMEZ 스키마와 무관한
# 단일 테이블짜리 파일도 정상 복원 가능해야 한다"고 잘못 기대하고
# 있던 시나리오를 대체한다.)
# ----------------------------------------------------


class RestoreValidateRequiredTablesTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_restore_schema_test_"))
        self.app_db_path = self.tmp_dir / "app.db"
        self.engine = create_engine(f"sqlite:///{self.app_db_path}")
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.credential_store = InMemoryCredentialStore()
        self.service = RestoreService(self.db, self.credential_store)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_schema_agnostic_single_table_backup_is_rejected(self):
        """
        HOMEZ 스키마와 완전히 무관한(유효한 SQLite이지만 REQUIRED_
        CORE_TABLES가 하나도 없는) 백업은 restorable=False로 거부돼야
        한다 — 이 판정 없이 실제로 복원했다면 로그인조차 안 되는 앱이
        됐을 것이다. **이전에는 이 정확한 시나리오가 "정상 복원
        가능"으로 잘못 기대되고 있었다** — 코디네이터의 명시적 지시로
        기대치를 안전 요구사항에 맞게 수정했다(테스트 삭제가 아님).
        """

        schema_agnostic = self.tmp_dir / "schema_agnostic.db"
        conn = sqlite3.connect(str(schema_agnostic))
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO probe (value) VALUES ('v1')")
        conn.commit()
        conn.close()

        result = self.service.validate_backup_file(schema_agnostic)

        self.assertTrue(result["file_exists"])
        self.assertEqual(result["integrity_check_result"], "ok")
        self.assertFalse(result["restorable"])
        self.assertIsNotNone(result["reason"])
        for table_name in REQUIRED_CORE_TABLES:
            self.assertIn(table_name, result["reason"])

    def test_backup_missing_only_schema_migrations_is_rejected(self):
        """HOMEZ 도메인 테이블은 다 있어도 schema_migrations 하나만
        없어도 거부돼야 한다(Migration 이력 없이 임의로 만든 스키마를
        "진짜 HOMEZ 백업"으로 오인하지 않기 위함)."""

        no_migrations = self.tmp_dir / "no_migrations.db"
        engine = create_engine(f"sqlite:///{no_migrations}")
        Base.metadata.create_all(bind=engine)
        engine.dispose()
        # 의도적으로 schema_migrations는 만들지 않는다.

        result = self.service.validate_backup_file(no_migrations)

        self.assertFalse(result["restorable"])
        self.assertIn("schema_migrations", result["reason"])

    def test_valid_homez_schema_backup_is_restorable(self):
        """REQUIRED_CORE_TABLES를 전부 갖춘 백업은 여전히 정상
        승인돼야 한다(과잉 차단이 아님을 함께 확인)."""

        valid = self.tmp_dir / "valid.db"
        _make_valid_homez_backup_source(valid, "v1")

        result = self.service.validate_backup_file(valid)

        self.assertTrue(result["restorable"])
        self.assertIsNone(result["reason"])


# ----------------------------------------------------
# Router 계약 — admin_guard 커버리지 + 실행 엔드포인트 부재 확인
# ----------------------------------------------------


class RestoreRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_restore_routes_require_admin_guard(self):

        routes = restore_router_module.router.routes
        self.assertGreaterEqual(len(routes), 2)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                admin_guard,
                calls,
                f"{route.path} 이(가) admin_guard 없이 노출되어 "
                "있습니다(인증 우회 가능성).",
            )

    def test_execute_restore_endpoint_exposed_with_admin_guard_and_202(self):
        """
        V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-2에서
        `POST /restores` 실행 엔드포인트가 명시적으로 승인·신설됐다
        (Gate Y-2의 "노출 보류" 결정을 대체함 — docs/V6_EXECUTION_
        LEDGER.md 이번 세션 절 참고). 이 테스트는 그 신설이 의도대로
        됐는지(경로/메서드/응답 코드/admin_guard) 지킨다. 실제 파일
        교체 로직은 이 살아있는 서버 프로세스 안에서 절대 동기 호출되지
        않는다는 것은 tests/test_restore_helper.py가 별도로 검증한다.
        """

        paths = {r.path for r in restore_router_module.router.routes}
        self.assertEqual(paths, {"/restores/validate", "/restores"})

        execute_route = next(
            r for r in restore_router_module.router.routes
            if r.path == "/restores" and "POST" in r.methods
        )
        self.assertEqual(execute_route.status_code, 202)


if __name__ == "__main__":
    unittest.main()
