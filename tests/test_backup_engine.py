"""
=========================================================
Homez OS

File : tests/test_backup_engine.py

Gate Y-1(2026-08-12) — 백업 엔진 검증. 전부 임시 SQLite 파일만
사용한다(실제 homez.db는 이 테스트에서 전혀 열지 않는다).
=========================================================
"""

import os
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
from app.domains.backup import router as backup_router_module
from app.domains.backup.encryption import decrypt_file
from app.domains.backup.encryption import get_or_create_backup_encryption_key
from app.domains.backup.model import BackupRecord
from app.domains.backup.service import (
    TRIGGER_SOURCE_MANUAL,
    TRIGGER_SOURCE_PRE_MIGRATION,
    BackupError,
    BackupService,
)
from app.domains.company.model import Company  # noqa: F401 (User FK 해석용)
from app.domains.role.model import Role  # noqa: F401 (User FK 해석용)
from app.domains.user.model import User  # noqa: F401 (User FK 해석용)


class BackupEngineTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_backup_test_"))
        self.source_db_path = self.tmp_dir / "source.db"
        self.backups_dir = self.tmp_dir / "backups"

        # 원본(백업 대상) DB — 임의 데이터 1행을 넣어 백업본에도
        # 실제로 데이터가 복사됐는지 확인할 수 있게 한다.
        src_conn = sqlite3.connect(str(self.source_db_path))
        src_conn.execute(
            "CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)",
        )
        src_conn.execute(
            "INSERT INTO probe (value) VALUES ('gate-y1')",
        )
        src_conn.commit()
        src_conn.close()

        # BackupRecord 이력을 저장할 별도 임시 앱 DB.
        self.app_db_path = self.tmp_dir / "app.db"
        self.engine = create_engine(
            f"sqlite:///{self.app_db_path}",
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.credential_store = InMemoryCredentialStore()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ----------------------------------------------------
    # 성공 경로
    # ----------------------------------------------------

    def test_create_backup_succeeds_and_records_history(self):

        service = BackupService(self.db, self.credential_store)

        record = service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source=TRIGGER_SOURCE_MANUAL,
            triggered_by_user_id=42,
            label="테스트 백업",
        )

        self.assertIsInstance(record, BackupRecord)
        self.assertIsNotNone(record.id)
        self.assertEqual(record.integrity_check_result, "ok")
        self.assertEqual(record.trigger_source, TRIGGER_SOURCE_MANUAL)
        self.assertEqual(record.triggered_by_user_id, 42)
        self.assertEqual(record.label, "테스트 백업")
        self.assertTrue(Path(record.file_path).exists())
        self.assertGreater(record.file_size_bytes, 0)
        self.assertEqual(len(record.sha256), 64)

    def test_backup_file_is_encrypted_and_decrypts_to_real_data_copy(self):
        """2026-09-15 전면 감사 후속(Phase 5) — 실제 백업 파일은
        암호화되어 있어야 한다(HOMEZ_USER_OPERATION_SETTINGS.md 11번).
        디스크에 남은 파일 자체는 평문 SQLite로 열리면 안 되고, 같은
        키로 복호화해야만 원본 데이터를 볼 수 있어야 한다."""

        service = BackupService(self.db, self.credential_store)

        record = service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source=TRIGGER_SOURCE_MANUAL,
        )

        self.assertTrue(record.is_encrypted)
        encrypted_conn = sqlite3.connect(record.file_path)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                encrypted_conn.execute("SELECT 1").fetchone()
        finally:
            encrypted_conn.close()

        key = get_or_create_backup_encryption_key(self.credential_store)
        decrypted_path = self.tmp_dir / "decrypted_for_test.db"
        decrypt_file(Path(record.file_path), decrypted_path, key)

        verify = sqlite3.connect(str(decrypted_path))
        row = verify.execute(
            "SELECT value FROM probe WHERE id = 1",
        ).fetchone()
        verify.close()

        self.assertEqual(row[0], "gate-y1")

    def test_backup_sha256_is_plaintext_hash_not_ciphertext_hash(self):
        """2026-09-15 전면 감사 후속(Phase 5) — BackupRecord.sha256은
        항상 "평문 내용"의 해시다(docstring 계약). 디스크의 암호화된
        파일 바이트를 직접 해시한 값과는 다르다는 것과, 복호화한
        평문을 해시하면 record.sha256과 일치한다는 것을 함께
        확인한다."""

        import hashlib

        service = BackupService(self.db, self.credential_store)

        record = service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source=TRIGGER_SOURCE_MANUAL,
        )

        ciphertext_hash = hashlib.sha256(
            Path(record.file_path).read_bytes(),
        ).hexdigest()
        self.assertNotEqual(
            record.sha256, ciphertext_hash,
            "암호화된 파일이라면 평문 해시와 암호문 해시가 같으면 "
            "안 된다(=암호화가 실제로 적용되지 않았다는 뜻).",
        )

        key = get_or_create_backup_encryption_key(self.credential_store)
        decrypted_path = self.tmp_dir / "decrypted_for_sha_test.db"
        decrypt_file(Path(record.file_path), decrypted_path, key)
        plaintext_hash = hashlib.sha256(
            decrypted_path.read_bytes(),
        ).hexdigest()

        self.assertEqual(record.sha256, plaintext_hash)

    def test_list_backups_returns_most_recent_first(self):

        service = BackupService(self.db, self.credential_store)

        first = service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source=TRIGGER_SOURCE_MANUAL,
            label="first",
        )
        second = service.create_backup(
            source_db_path=self.source_db_path,
            backups_dir=self.backups_dir,
            trigger_source=TRIGGER_SOURCE_PRE_MIGRATION,
            label="second",
        )

        results = service.list_backups()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].id, second.id)
        self.assertEqual(results[1].id, first.id)

    # ----------------------------------------------------
    # 실패 경로 — fail-closed 확인
    # ----------------------------------------------------

    def test_missing_source_db_raises_and_records_nothing(self):

        service = BackupService(self.db, self.credential_store)

        with self.assertRaises(BackupError):
            service.create_backup(
                source_db_path=self.tmp_dir / "does_not_exist.db",
                backups_dir=self.backups_dir,
                trigger_source=TRIGGER_SOURCE_MANUAL,
            )

        self.assertEqual(len(service.list_backups()), 0)

    def test_unknown_trigger_source_rejected(self):

        service = BackupService(self.db, self.credential_store)

        with self.assertRaises(BackupError):
            service.create_backup(
                source_db_path=self.source_db_path,
                backups_dir=self.backups_dir,
                trigger_source="not_a_real_trigger",
            )

        self.assertEqual(len(service.list_backups()), 0)

    def test_backup_path_equal_to_source_path_is_blocked(self):
        """
        service.py는 생성될 백업 파일명이 원본 DB 경로와 우연히
        같아지는 경우를 차단한다. 실제 파일명에는 마이크로초 단위
        타임스탬프가 들어가 우연히 충돌할 확률은 사실상 0이므로,
        `datetime.now()`를 고정해 결정적으로 그 충돌을 재현한다.
        """

        import unittest.mock as mock
        from datetime import datetime as real_datetime

        frozen = real_datetime(2026, 8, 12, 12, 0, 0, 0)
        expected_name = (
            f"homez_backup_{frozen.strftime('%Y%m%d_%H%M%S_%f')}.db"
        )
        colliding_source = self.backups_dir / expected_name
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source_db_path, colliding_source)

        service = BackupService(self.db, self.credential_store)

        with mock.patch(
            "app.domains.backup.service.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = frozen

            with self.assertRaises(BackupError):
                service.create_backup(
                    source_db_path=colliding_source,
                    backups_dir=self.backups_dir,
                    trigger_source=TRIGGER_SOURCE_MANUAL,
                )

    def test_corrupted_backup_is_not_recorded_as_history(self):
        """
        integrity_check가 ok가 아니면 BackupRecord를 절대 만들지
        않는다 — service.py의 fail-closed 계약을 직접 검증한다.
        `sqlite3.Connection.backup()` 자체를 손상시키긴 어려우므로,
        `PRAGMA integrity_check`가 "ok"가 아닌 값을 반환하는 상황을
        monkeypatch로 재현해 이 시점 이후 코드 경로(레코드 미생성)만
        확인한다.
        """

        import unittest.mock as mock

        service = BackupService(self.db, self.credential_store)

        class _FakeCursor:
            def fetchone(self_inner):
                return ("corrupted-for-test",)

        class _CorruptedIntegrityConnection(sqlite3.Connection):
            def execute(self_conn, sql, *a, **kw):
                if isinstance(sql, str) and "integrity_check" in sql:
                    return _FakeCursor()
                return super().execute(sql, *a, **kw)

        original_connect = sqlite3.connect
        backups_dir_str = str(self.backups_dir)

        def _connect_side_effect(*args, **kwargs):
            uri_arg = args[0] if args else kwargs.get("database", "")
            if (
                isinstance(uri_arg, str)
                and "mode=ro" in uri_arg
                and backups_dir_str in uri_arg
            ):
                kwargs["factory"] = _CorruptedIntegrityConnection
            return original_connect(*args, **kwargs)

        with mock.patch(
            "sqlite3.connect",
            side_effect=_connect_side_effect,
        ):
            with self.assertRaises(BackupError):
                service.create_backup(
                    source_db_path=self.source_db_path,
                    backups_dir=self.backups_dir,
                    trigger_source=TRIGGER_SOURCE_MANUAL,
                )

        self.assertEqual(len(service.list_backups()), 0)


# ----------------------------------------------------
# Router 계약 — admin_guard 커버리지 정적 검증
# ----------------------------------------------------


class BackupRouterGuardCoverageTestCase(unittest.TestCase):

    def test_all_backup_routes_require_admin_guard(self):
        """
        2026-08-15 V7 Gate 8 — `GET /backups/retention-check`(보존
        정책 조회 전용, 삭제는 하지 않음)가 추가되어 2→3개로 갱신
        (기존 admin_guard 커버리지 전수 검증 루프 자체는 그대로).
        """

        routes = backup_router_module.router.routes
        self.assertEqual(len(routes), 3)

        for route in routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                admin_guard,
                calls,
                f"{route.path} 이(가) admin_guard 없이 노출되어 "
                "있습니다(인증 우회 가능성).",
            )

    def test_backup_router_does_not_accept_client_supplied_db_path(self):
        """
        BackupCreateRequest에 파일 경로 필드가 없는지 정적으로
        확인한다 — 있다면 경로 주입(임의 파일 백업/덮어쓰기)
        위험이다.
        """

        from app.domains.backup.schema import BackupCreateRequest

        field_names = set(BackupCreateRequest.model_fields.keys())
        self.assertEqual(field_names, {"label"})


if __name__ == "__main__":
    unittest.main()
