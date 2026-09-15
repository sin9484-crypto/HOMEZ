"""
=========================================================
Homez OS

File : tests/test_restore_helper.py

V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-3. 전부 임시
SQLite 파일만 사용한다(실제 homez.db는 이 테스트에서 전혀 열지
않는다). Gate R-0/R-1/R-2에서 나온 15개 시나리오를 커버한다.

기존 tests/test_restore_engine.py의 setUp()은 "restore_attempts
기록용 DB"(self.db)와 "복원 대상 DB"(target_db_path)를 서로 다른
파일에 바인딩한다 — 이는 실제 HOMEZ 단일 homez.db 아키텍처와
다르다(V7 Live Gate 4 재작업이 실제로 발견한 커버리지 공백, 스크래치
패드 live_gate4_restore_fix_progress.md Gate R-0 참고). 이 파일의
테스트는 그 공백을 메우기 위해 **self.db가 target_db_path 자신에
바인딩된 단일 파일 시나리오**를 기본 패턴으로 쓴다.
=========================================================
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.desktop import restore_helper
from tests.support.real_credential_gate import requires_real_credential_manager
from app.domains.backup.service import TRIGGER_SOURCE_MANUAL
from app.domains.backup.service import BackupService
from app.domains.backup.service import sha256_of_file
from app.domains.company.model import Company  # noqa: F401 (User FK 해석용)
from app.domains.restore.model import RESTORE_STATUS_FAILED
from app.domains.restore.model import RESTORE_STATUS_SUCCEEDED
from app.domains.restore.service import RestoreError
from app.domains.restore.service import RestoreService
from app.domains.role.model import Role  # noqa: F401 (User FK 해석용)
from app.domains.user.model import User  # noqa: F401 (User FK 해석용)


def _make_probe_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO probe (value) VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _create_schema_migrations_table(path: Path) -> None:
    """migration_runner.py가 실제로 쓰는 것과 동일한 DDL(순수 SQL,
    SQLAlchemy Base.metadata에는 매핑돼 있지 않다)."""

    conn = sqlite3.connect(str(path))
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


def _make_full_schema_source_db(path: Path, probe_value: str) -> None:
    """
    실제 HOMEZ 백업(homez.db 전체 복사본)과 동등하게, 복원 대상이 될
    소스 DB는 항상 전체 HOMEZ 스키마(restore_attempts/backup_records
    포함)를 갖춰야 한다 — 그래야 복원 "이후" self.db가 새(=교체된)
    target_db_path에 감사 행을 남기는 단계가 실제 프로덕션과 동일하게
    성립한다. `_make_probe_db()`(단일 테이블)는 검증 단계(2~5번
    시나리오) 전용이다.

    V7 Live Gate 4 복원 결함 수정(2026-08-17) — 코디네이터 판단으로
    `validate_backup_file()`의 `REQUIRED_CORE_TABLES` 검증이
    재활성화됨에 따라, `schema_migrations` 테이블(SQLAlchemy
    `Base.metadata`에는 매핑돼 있지 않다 — `migration_runner.py`가
    순수 SQL로 직접 만드는 이 앱의 Migration 부기 테이블)도 함께
    만들어야 이 함수로 만든 백업이 여전히 restorable=True로 승인된다.
    """

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    _create_schema_migrations_table(path)

    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO probe (value) VALUES (?)", (probe_value,))
    conn.commit()
    conn.close()


def _single_file_session(db_path: Path):
    """
    실제 HOMEZ 아키텍처와 동일하게 self.db가 target_db_path 자신에
    바인딩되도록 세션을 만든다(get_db()가 실행 엔드포인트에 주입할
    세션과 동일한 패턴).
    """

    engine = create_engine(f"sqlite:///{db_path}", pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory()


class RestoreSingleFileArchitectureTestCase(unittest.TestCase):
    """
    시나리오 1, 7, 8, 9, 10, 12, 13, 14, 15 — 실제 단일 DB 아키텍처
    기준.
    """

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_restore_helper_test_"))
        self.credential_store = InMemoryCredentialStore()

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_valid_backup(self, tag: str = "v1") -> tuple[Path, str]:

        source = self.tmp_dir / f"source_{tag}.db"
        _make_full_schema_source_db(source, f"value-{tag}")
        backups_dir = self.tmp_dir / "backups"
        backup_engine, backup_db = _single_file_session(self.tmp_dir / f"bookkeeping_{tag}.db")
        try:
            svc = BackupService(backup_db, self.credential_store)
            record = svc.create_backup(
                source_db_path=source,
                backups_dir=backups_dir,
                trigger_source=TRIGGER_SOURCE_MANUAL,
            )
            return Path(record.file_path), record.sha256
        finally:
            backup_db.close()
            backup_engine.dispose()

    # ------------------------------------------------------------
    # 시나리오 1 — 정상 복원(단일 DB 아키텍처)
    # ------------------------------------------------------------

    def test_scenario1_normal_restore_single_file_architecture(self):

        backup_path, sha = self._make_valid_backup("normal")
        target = self.tmp_dir / "homez_like.db"

        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            attempt = service.restore(
                source_backup_path=backup_path,
                target_db_path=target,
                expected_sha256=sha,
                pre_restore_backups_dir=self.tmp_dir / "pre_restore",
            )
            self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        finally:
            db.close()
            engine.dispose()

    # ------------------------------------------------------------
    # 시나리오 7 — 자기 Session/Engine 미종료 (핵심 회귀 테스트)
    # ------------------------------------------------------------

    def test_scenario7_own_session_not_pre_closed_regression(self):
        """
        Gate R-0 실험 1/4/4b가 증명한 정확한 그 결함의 회귀 테스트다.
        self.db를 target_db_path 자신에 바인딩하고, restore() 호출
        "전에" 이미 한 번 쿼리를 실행해 풀에 커넥션을 살려둔 상태(=
        admin_guard의 사용자 조회처럼 restore() 진입 전에 이미 DB를
        한 번 쓴 상황)에서도 restore()가 성공해야 한다 — 수정 전에는
        WinError 5로 실패했다.
        """

        backup_path, sha = self._make_valid_backup("regress")
        target = self.tmp_dir / "regress_target.db"

        engine, db = _single_file_session(target)
        try:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))  # admin_guard 등 사전 쿼리 흉내

            service = RestoreService(db, self.credential_store)
            attempt = service.restore(
                source_backup_path=backup_path,
                target_db_path=target,
                expected_sha256=sha,
                pre_restore_backups_dir=self.tmp_dir / "pre_restore",
            )
            self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        finally:
            db.close()
            engine.dispose()

    def test_scenario7b_existing_target_forces_mid_flow_write_still_succeeds(self):
        """
        target이 이미 존재해 사전 안전 백업(BackupRecord 행 쓰기)이
        실행되는 경로 — 이게 정확히 실험 4b가 재현한 "restore() 자신의
        중간 쓰기가 커넥션을 되살리는" 경로다.
        """

        backup_path, sha = self._make_valid_backup("midflow")
        target = self.tmp_dir / "midflow_target.db"
        _make_full_schema_source_db(target, "old-target-value")

        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            attempt = service.restore(
                source_backup_path=backup_path,
                target_db_path=target,
                expected_sha256=sha,
                pre_restore_backups_dir=self.tmp_dir / "pre_restore",
            )
            self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
            self.assertIsNotNone(attempt.pre_restore_backup_path)
            self.assertTrue(Path(attempt.pre_restore_backup_path).exists())
        finally:
            db.close()
            engine.dispose()

    # ------------------------------------------------------------
    # 시나리오 6 — 다른 프로세스(대리: 별도 raw sqlite3 커넥션)가
    # DB를 점유한 경우 — 우리 자신의 SQLAlchemy 풀이 아니라 진짜
    # 외부 커넥션이면 여전히 실패해야 한다(수정이 "모든 잠금을
    # 무시"하는 게 아니라 "우리 앱 자신의 잔존 커넥션만 정리"함을
    # 증명).
    # ------------------------------------------------------------

    def test_scenario6_external_raw_connection_still_blocks_replace(self):

        backup_path, sha = self._make_valid_backup("external")
        target = self.tmp_dir / "external_lock_target.db"
        _make_full_schema_source_db(target, "value")

        external_conn = sqlite3.connect(str(target))
        external_conn.execute("SELECT * FROM probe")  # 실제로 파일을 연다

        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            with self.assertRaises(RestoreError):
                service.restore(
                    source_backup_path=backup_path,
                    target_db_path=target,
                    expected_sha256=sha,
                    pre_restore_backups_dir=self.tmp_dir / "pre_restore",
                )
        finally:
            db.close()
            engine.dispose()
            external_conn.close()

        # 외부 커넥션을 닫은 뒤에는 정상적으로 복원 가능해야 한다.
        engine2, db2 = _single_file_session(target)
        try:
            service2 = RestoreService(db2, self.credential_store)
            attempt = service2.restore(
                source_backup_path=backup_path,
                target_db_path=target,
                expected_sha256=sha,
                pre_restore_backups_dir=self.tmp_dir / "pre_restore2",
            )
            self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        finally:
            db2.close()
            engine2.dispose()

    # ------------------------------------------------------------
    # 시나리오 8 — 교체 직전 실패(임시파일 생성 단계에서 실패)
    # ------------------------------------------------------------

    def test_scenario8_failure_before_replace_leaves_target_untouched(self):
        """
        target_db_path 자체가 self.db의 대상이기도 한 단일 DB
        아키텍처에서는, 실패해도 "실패했다"는 감사 행(RestoreAttempt)
        자체는 target에 정상적으로 남는다(의도된 동작 — 실패도 감사
        대상이라는 model.py 자체의 설계 철학, restore/model.py
        docstring 참고). 그래서 "바이트 단위로 완전히 그대로"가 아니라
        **실제 업무 데이터(probe 테이블)가 변경되지 않았는지**를
        확인한다 — os.replace() 자체가 시도조차 되지 않았어야 한다.
        """

        backup_path, sha = self._make_valid_backup("prefail")
        target = self.tmp_dir / "prefail_target.db"
        _make_full_schema_source_db(target, "keep-me")

        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)

            import app.domains.restore.service as service_module

            original_mkstemp = service_module.tempfile.mkstemp

            def _boom(*args, **kwargs):
                # 2026-09-15 전면 감사 후속(Phase 5) — validate_backup_
                # file()이 암호화된 백업을 복호화할 때도 이제
                # tempfile.mkstemp를 쓴다(prefix=".restore_decrypt_").
                # 이 테스트는 "교체 직전(.restore_tmp_) 임시 파일 생성
                # 실패"만 재현하려는 것이므로, 그 prefix가 아니면 원래
                # mkstemp를 그대로 호출한다.
                if kwargs.get("prefix") != ".restore_tmp_":
                    return original_mkstemp(*args, **kwargs)
                raise OSError("시뮬레이션된 임시 파일 생성 실패")

            service_module.tempfile.mkstemp = _boom
            try:
                with self.assertRaises(RestoreError):
                    service.restore(
                        source_backup_path=backup_path,
                        target_db_path=target,
                        expected_sha256=sha,
                        pre_restore_backups_dir=self.tmp_dir / "pre_restore",
                    )
            finally:
                service_module.tempfile.mkstemp = original_mkstemp

            verify = sqlite3.connect(str(target))
            probe_value = verify.execute(
                "SELECT value FROM probe WHERE id = 1",
            ).fetchone()[0]
            verify.close()
            self.assertEqual(probe_value, "keep-me")

            leftovers = list(target.parent.glob(".restore_tmp_*"))
            self.assertEqual(leftovers, [])

            attempts = service.list_attempts()
            self.assertEqual(attempts[0].status, RESTORE_STATUS_FAILED)
        finally:
            db.close()
            engine.dispose()

    # ------------------------------------------------------------
    # 시나리오 9/10 — 교체 직후(재시작 후) 무결성 실패 시 target은
    # 이미 교체된 상태로 남지만(os.replace 이후 실패이므로), 사전
    # 안전 백업이 유효한 "롤백 경로"로 남아 있어야 한다.
    # ------------------------------------------------------------

    def test_scenario9_10_post_replace_integrity_failure_leaves_valid_rollback_path(self):
        """
        "교체 직후 무결성 실패"를 재현하되, target 파일을 물리적으로
        손상시키지 않는다 — 이 아키텍처에서는 target이 self.db 자신의
        대상이기도 해서, 헤더 근처를 깨뜨리면 그 직후 실패 감사 행을
        쓰는 단계까지 함께 깨져(2차 미처리 예외) 테스트가 검증하려는
        지점("무결성 실패 자체를 RestoreError로 정상 변환하는지")을
        가려버린다. 대신 최종 검증에서만 실제 `sqlite3.connect`를
        감싸 `PRAGMA integrity_check` 결과만 가짜로 "손상"으로
        바꾼다 — 파일 자체는 끝까지 정상이므로 그 다음 실패 감사 행
        쓰기는 정상 동작한다(실제로 무결성 검사만 우연히 실패하는
        디스크 오류류 시나리오와 더 가깝다).
        """

        backup_path, sha = self._make_valid_backup("postfail")
        target = self.tmp_dir / "postfail_target.db"
        _make_full_schema_source_db(target, "rollback-source-value")

        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)

            import app.domains.restore.service as service_module

            real_connect = service_module.sqlite3.connect
            target_ro_calls = {"n": 0}
            target_uri_fragment = f"file:{target}?mode=ro"

            class _FakeCursor:
                def fetchone(self):
                    return ("simulated-corruption",)

            class _FakeConn:
                def __init__(self, real_conn):
                    self._real = real_conn

                def execute(self, sql, *a, **kw):
                    if "integrity_check" in sql:
                        return _FakeCursor()
                    return self._real.execute(sql, *a, **kw)

                def close(self):
                    self._real.close()

            def _fake_connect(database, *args, **kwargs):
                conn = real_connect(database, *args, **kwargs)
                if database == target_uri_fragment:
                    target_ro_calls["n"] += 1
                    if target_ro_calls["n"] == 2:  # final_verify 호출
                        return _FakeConn(conn)
                return conn

            service_module.sqlite3.connect = _fake_connect
            try:
                with self.assertRaises(RestoreError) as ctx:
                    service.restore(
                        source_backup_path=backup_path,
                        target_db_path=target,
                        expected_sha256=sha,
                        pre_restore_backups_dir=self.tmp_dir / "pre_restore",
                    )
            finally:
                service_module.sqlite3.connect = real_connect

            self.assertIn("무결성", str(ctx.exception))

            attempts = service.list_attempts()
            failed = attempts[0]
            self.assertEqual(failed.status, RESTORE_STATUS_FAILED)
            self.assertIsNotNone(failed.pre_restore_backup_path)

            rollback_path = Path(failed.pre_restore_backup_path)
            self.assertTrue(rollback_path.exists())

            # 2026-09-15 전면 감사 후속(Phase 5) — 이 안전 백업도
            # BackupService.create_backup()이 만들므로 이제
            # 암호화되어 있다 — 같은 키로 복호화한 사본에
            # integrity_check를 실행한다.
            from app.domains.backup.encryption import decrypt_file
            from app.domains.backup.encryption import (
                get_or_create_backup_encryption_key,
            )

            key = get_or_create_backup_encryption_key(self.credential_store)
            decrypted_rollback_path = self.tmp_dir / "decrypted_rollback_for_test.db"
            decrypt_file(rollback_path, decrypted_rollback_path, key)

            verify = sqlite3.connect(
                f"file:{decrypted_rollback_path}?mode=ro", uri=True,
            )
            integrity = verify.execute("PRAGMA integrity_check").fetchone()[0]
            verify.close()
            self.assertEqual(integrity, "ok")
        finally:
            db.close()
            engine.dispose()

    # ------------------------------------------------------------
    # 시나리오 12 — Unicode·공백 경로
    # ------------------------------------------------------------

    def test_scenario12_unicode_and_space_paths(self):

        weird_dir = self.tmp_dir / "한글 폴더 with space 스페이스"
        weird_dir.mkdir()

        source = weird_dir / "원본 소스.db"
        _make_full_schema_source_db(source, "unicode-value")

        backups_dir = weird_dir / "백업 저장소"
        backup_engine, backup_db = _single_file_session(weird_dir / "bookkeeping.db")
        try:
            record = BackupService(backup_db, self.credential_store).create_backup(
                source_db_path=source,
                backups_dir=backups_dir,
                trigger_source=TRIGGER_SOURCE_MANUAL,
            )
        finally:
            backup_db.close()
            backup_engine.dispose()

        target = weird_dir / "대상 홈즈.db"
        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            attempt = service.restore(
                source_backup_path=Path(record.file_path),
                target_db_path=target,
                expected_sha256=record.sha256,
                pre_restore_backups_dir=weird_dir / "사전 백업",
            )
            self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        finally:
            db.close()
            engine.dispose()

    # ------------------------------------------------------------
    # 시나리오 13 — 동일 백업 반복 복원
    # ------------------------------------------------------------

    def test_scenario13_repeated_restore_of_same_backup(self):
        """
        같은 백업을 3회 연속 복원해도 매번 성공해야 하고(멱등성 —
        같은 입력을 반복해도 매번 동일하게 정상 완료), 2회차부터는
        target이 이미 존재하므로 사전 안전 백업이 매번 새로 생겨야
        한다. **참고(기존 아키텍처의 자연스러운 특성, 이번 Gate가
        바꾸지 않음)**: `os.replace()`가 target 파일 전체를 백업의
        스냅샷으로 통째로 교체하므로, target 자신에 쌓여 있던 이전
        `restore_attempts` 이력도 그 백업 시점 상태로 함께 교체된다
        — 그래서 매 회차 직후 `list_attempts()`는 항상 "이번 회차의
        1건"만 보인다(이전 회차 이력이 사라진 게 아니라, 그 이력은
        각 회차의 사전 안전 백업 파일 안에 스냅샷으로 남아 있다).
        """

        backup_path, sha = self._make_valid_backup("repeat")
        target = self.tmp_dir / "repeat_target.db"

        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            for i in range(3):
                attempt = service.restore(
                    source_backup_path=backup_path,
                    target_db_path=target,
                    expected_sha256=sha,
                    pre_restore_backups_dir=self.tmp_dir / f"pre_restore_{i}",
                )
                self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)

                # _single_file_session()이 Base.metadata.create_all()로
                # target 파일을 미리 만들어 두므로(get_db()가 항상
                # 스키마가 갖춰진 기존 homez.db에 바인딩되는 실제
                # 프로덕션과 동일한 조건), target은 1회차부터 이미
                # "존재"해 매 회차 사전 안전 백업이 생긴다.
                self.assertIsNotNone(attempt.pre_restore_backup_path)
                self.assertTrue(Path(attempt.pre_restore_backup_path).exists())

            # 이번 회차(마지막) 이력은 target 자신에 항상 정확히 1건
            # 남아 있어야 한다(위 docstring 참고).
            self.assertEqual(len(service.list_attempts()), 1)
        finally:
            db.close()
            engine.dispose()

    # ------------------------------------------------------------
    # 시나리오 14 — 회사·사용자·권한 행 수 보존
    # ------------------------------------------------------------

    def test_scenario14_company_user_role_row_counts_preserved(self):

        source = self.tmp_dir / "full_schema_source.db"
        src_engine = create_engine(f"sqlite:///{source}")
        Base.metadata.create_all(bind=src_engine)
        _create_schema_migrations_table(source)
        SrcSession = sessionmaker(bind=src_engine)
        src_db = SrcSession()

        company = Company(name="테스트 회사", business_number="123-45-67890")
        src_db.add(company)
        src_db.commit()

        role = Role(name="admin_role_test", code="admin_role_test_code")
        src_db.add(role)
        src_db.commit()

        user = User(
            email="gate_r3_probe@example.com",
            username="gate_r3_probe",
            password_hash="x",
            company_id=company.id,
            role_id=role.id,
        )
        src_db.add(user)
        src_db.commit()

        src_db.close()
        src_engine.dispose()

        backups_dir = self.tmp_dir / "full_schema_backups"
        backup_engine, backup_db = _single_file_session(
            self.tmp_dir / "full_schema_bookkeeping.db",
        )
        try:
            record = BackupService(backup_db, self.credential_store).create_backup(
                source_db_path=source,
                backups_dir=backups_dir,
                trigger_source=TRIGGER_SOURCE_MANUAL,
            )
        finally:
            backup_db.close()
            backup_engine.dispose()

        target = self.tmp_dir / "full_schema_target.db"
        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            attempt = service.restore(
                source_backup_path=Path(record.file_path),
                target_db_path=target,
                expected_sha256=record.sha256,
                pre_restore_backups_dir=self.tmp_dir / "full_schema_pre_restore",
            )
            self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        finally:
            db.close()
            engine.dispose()

        verify = sqlite3.connect(str(target))
        company_count = verify.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        role_count = verify.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
        user_count = verify.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        verify.close()

        self.assertEqual(company_count, 1)
        self.assertEqual(role_count, 1)
        self.assertEqual(user_count, 1)

    # ------------------------------------------------------------
    # 시나리오 15 — 임시 파일 잔존 없음
    # ------------------------------------------------------------

    def test_scenario15_no_leftover_temp_files(self):

        backup_path, sha = self._make_valid_backup("cleanup")
        target = self.tmp_dir / "cleanup_target.db"

        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            service.restore(
                source_backup_path=backup_path,
                target_db_path=target,
                expected_sha256=sha,
                pre_restore_backups_dir=self.tmp_dir / "pre_restore",
            )
        finally:
            db.close()
            engine.dispose()

        leftovers = list(target.parent.glob(".restore_tmp_*"))
        self.assertEqual(leftovers, [])

    # ------------------------------------------------------------
    # 시나리오 11 — WAL journal DB도 정상 복원되고 사이드카가 남지
    # 않아야 한다.
    # ------------------------------------------------------------

    def test_scenario11_wal_journal_source_restores_cleanly(self):
        """
        원본 소스가 WAL journal_mode인 경우도 복원이 정상 동작해야
        한다. **알려진 사실(코디네이터 보고 대상, 이번 Gate에서 고치지
        않음)**: SQLite는 journal_mode를 커넥션이 아니라 "데이터베이스
        파일 헤더"에 기록하므로, WAL로 설정된 적 있는 DB를
        `sqlite3.Connection.backup()`(BackupService가 쓰는 온라인 백업
        API)으로 복사해도 그 설정이 결과 파일에 그대로 전이된다 —
        따라서 백업 파일이나 복원된 target 자체를 다시 열면(예: 다음
        쓰기 트랜잭션에서) `-wal`/`-shm` 사이드카가 다시 나타날 수
        있다. `os.replace()`는 target_db_path 파일 하나만 원자적으로
        교체할 뿐 사이드카 파일의 존재는 알지 못한다(Gate R-0 사용자
        지시 3번 항목이 우려한 지점) — 이번 Gate는 WinError 5(파일
        교체 자체의 실패)를 고치는 것이 범위이며, WAL 사이드카
        정리/차단은 별도 결정이 필요하므로 이 테스트는 "복원 자체는
        성공하고 데이터가 보존되는지"만 확인한다.
        """

        source = self.tmp_dir / "wal_source.db"
        src_engine = create_engine(f"sqlite:///{source}")
        Base.metadata.create_all(bind=src_engine)
        src_engine.dispose()
        _create_schema_migrations_table(source)

        conn = sqlite3.connect(str(source))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO probe (value) VALUES ('wal-value')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        backups_dir = self.tmp_dir / "wal_backups"
        backup_engine, backup_db = _single_file_session(self.tmp_dir / "wal_bookkeeping.db")
        try:
            record = BackupService(backup_db, self.credential_store).create_backup(
                source_db_path=source,
                backups_dir=backups_dir,
                trigger_source=TRIGGER_SOURCE_MANUAL,
            )
        finally:
            backup_db.close()
            backup_engine.dispose()

        target = self.tmp_dir / "wal_target.db"
        engine, db = _single_file_session(target)
        try:
            service = RestoreService(db, self.credential_store)
            attempt = service.restore(
                source_backup_path=Path(record.file_path),
                target_db_path=target,
                expected_sha256=record.sha256,
                pre_restore_backups_dir=self.tmp_dir / "wal_pre_restore",
            )
            self.assertEqual(attempt.status, RESTORE_STATUS_SUCCEEDED)
        finally:
            db.close()
            engine.dispose()

        verify = sqlite3.connect(str(target))
        row = verify.execute("SELECT value FROM probe WHERE id = 1").fetchone()
        verify.close()
        self.assertEqual(row[0], "wal-value")


class RestoreValidationEdgeCaseTestCase(unittest.TestCase):
    """시나리오 2, 3, 4, 5 — 검증 단계 경계값."""

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_restore_helper_validate_"))
        engine, db = _single_file_session(self.tmp_dir / "app.db")
        self.engine = engine
        self.db = db
        self.credential_store = InMemoryCredentialStore()
        self.service = RestoreService(db, self.credential_store)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_scenario2_corrupted_file_blocked(self):

        corrupted = self.tmp_dir / "corrupted.db"
        _make_probe_db(corrupted, "v1")
        with open(corrupted, "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 32)

        result = self.service.validate_backup_file(corrupted)
        self.assertFalse(result["restorable"])

    def test_scenario3_non_sqlite_file_blocked(self):

        not_sqlite = self.tmp_dir / "not_a_db.txt"
        not_sqlite.write_text("이건 SQLite 파일이 아닙니다.", encoding="utf-8")

        result = self.service.validate_backup_file(not_sqlite)
        self.assertFalse(result["restorable"])
        self.assertIn("SQLite", result["reason"])

    def test_scenario4_missing_required_table_now_blocked(self):
        """
        **코디네이터 판단으로 재활성화됨(2026-08-17)**: 이전에는
        `validate_backup_file()`이 2026-08-16 재검토로 "필수 테이블
        누락" 판정을 되돌려 이 시나리오가 "정상 복원 가능"으로 잘못
        기대되고 있었다 — HOMEZ 스키마와 무관한 단일 테이블짜리
        파일을 복원 가능으로 판정하면 실제로 복원했을 때 앱이 즉시
        깨지므로(로그인조차 불가), 코디네이터가 이 결정을 명시적으로
        뒤집었다(테스트 삭제가 아니라 기대치를 안전 요구사항에 맞게
        수정 — `app/domains/restore/service.py::REQUIRED_CORE_TABLES`
        참고).
        """

        single_table = self.tmp_dir / "single_table.db"
        _make_probe_db(single_table, "no-homez-schema")

        result = self.service.validate_backup_file(single_table)
        self.assertFalse(result["restorable"])
        self.assertIsNotNone(result["reason"])

    def test_scenario5_schema_migrations_absent_now_blocked(self):
        """
        HOMEZ 도메인 테이블(users/companies/roles)은 다 있어도
        `schema_migrations`만 없는 백업도 거부돼야 한다 — 시나리오
        4와 같은 이유로 재활성화됨.
        """

        from app.domains.restore.service import REQUIRED_CORE_TABLES

        no_migrations = self.tmp_dir / "no_migrations.db"
        engine = create_engine(f"sqlite:///{no_migrations}")
        Base.metadata.create_all(bind=engine)
        engine.dispose()
        # 의도적으로 schema_migrations 테이블은 만들지 않는다.

        result = self.service.validate_backup_file(no_migrations)
        self.assertFalse(result["restorable"])
        self.assertIn("schema_migrations", result["reason"])
        self.assertIn("schema_migrations", REQUIRED_CORE_TABLES)


class RestoreHelperModuleTestCase(unittest.TestCase):
    """
    Gate R-1 Option B 계약 자체(plan 직렬화, 부모 종료 대기, Helper
    프로세스 실행 로직)에 대한 단위 테스트. 실제 pywebview 창은
    헤드리스 테스트 환경에서 만들 수 없으므로, 창이 필요한 경로는
    `RestoreHelperError`로 fail-closed되는지만 확인한다.
    """

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_restore_helper_module_"))
        restore_helper.clear_desktop_context()
        self.credential_store = InMemoryCredentialStore()

    def tearDown(self):

        restore_helper.clear_desktop_context()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_plan_json_roundtrip_has_no_secret_fields(self):

        plan = restore_helper.RestorePlan(
            plan_id="p1",
            source_backup_path=str(self.tmp_dir / "b.db"),
            target_db_path=str(self.tmp_dir / "t.db"),
            expected_sha256="0" * 64,
            pre_restore_backups_dir=str(self.tmp_dir / "pre"),
            triggered_by_user_id=7,
            requested_at="2026-08-17T00:00:00+00:00",
            relaunch_argv=[sys.executable, "-m", "app.desktop.main"],
        )

        plan_path = self.tmp_dir / "plan.json"
        restore_helper.save_plan(plan_path, plan)
        raw = plan_path.read_text(encoding="utf-8")

        for forbidden in ("password", "token", "secret", "SECRET_KEY"):
            self.assertNotIn(forbidden, raw.lower())

        loaded = restore_helper.load_plan(plan_path)
        self.assertEqual(loaded, plan)

    def test_request_restore_shutdown_fails_closed_without_registered_window(self):
        """창이 등록되지 않은 상태(테스트/CLI 단독 실행)에서는 절대
        진행하지 않는다 — Helper 프로세스가 이미 떠 있어도 창을 닫지
        못하면 예외를 던진다."""

        plan = restore_helper.RestorePlan(
            plan_id="p2",
            source_backup_path=str(self.tmp_dir / "b.db"),
            target_db_path=str(self.tmp_dir / "t.db"),
            expected_sha256="0" * 64,
            pre_restore_backups_dir=str(self.tmp_dir / "pre"),
            triggered_by_user_id=None,
            requested_at="2026-08-17T00:00:00+00:00",
            relaunch_argv=[],
        )

        original_spawn = restore_helper.spawn_helper_process
        spawned = {}

        class _FakeProc:
            def __init__(self):
                self.pid = -1

        def _fake_spawn(plan_path, parent_pid):
            spawned["called"] = True
            return _FakeProc()

        restore_helper.spawn_helper_process = _fake_spawn
        try:
            with self.assertRaises(restore_helper.RestoreHelperError):
                restore_helper.request_restore_shutdown(
                    plan, self.tmp_dir / "plan.json",
                )
        finally:
            restore_helper.spawn_helper_process = original_spawn

        self.assertTrue(spawned.get("called"))

    def test_wait_for_process_exit_true_after_child_exits(self):

        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
        )
        proc.wait(timeout=10)

        result = restore_helper.wait_for_process_exit(proc.pid, timeout=5.0)
        self.assertTrue(result)

    def test_wait_for_process_exit_false_when_still_running(self):

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
        )
        try:
            result = restore_helper.wait_for_process_exit(proc.pid, timeout=0.5)
            self.assertFalse(result)
        finally:
            proc.kill()
            proc.wait(timeout=10)

    def test_run_restore_in_helper_process_end_to_end_business_logic(self):
        """
        Helper 프로세스 "안"에서 실행될 실제 비즈니스 로직
        (`run_restore_in_helper_process`)을 부모 대기 없이 직접
        호출해 검증한다 — plan 재검증 + RestoreService.restore() 호출
        + 결과 JSON 기록까지 전 과정.
        """

        source = self.tmp_dir / "helper_source.db"
        _make_full_schema_source_db(source, "helper-value")

        backups_dir = self.tmp_dir / "helper_backups"
        backup_engine, backup_db = _single_file_session(self.tmp_dir / "helper_bookkeeping.db")
        try:
            record = BackupService(backup_db, self.credential_store).create_backup(
                source_db_path=source,
                backups_dir=backups_dir,
                trigger_source=TRIGGER_SOURCE_MANUAL,
            )
        finally:
            backup_db.close()
            backup_engine.dispose()

        target = self.tmp_dir / "helper_target.db"
        plan = restore_helper.RestorePlan(
            plan_id="helper-e2e",
            source_backup_path=record.file_path,
            target_db_path=str(target),
            expected_sha256=record.sha256,
            pre_restore_backups_dir=str(self.tmp_dir / "helper_pre_restore"),
            triggered_by_user_id=None,
            requested_at="2026-08-17T00:00:00+00:00",
            relaunch_argv=[],
        )

        result_path = self.tmp_dir / "result.json"
        outcome = restore_helper.run_restore_in_helper_process(
            plan, result_path, credential_store=self.credential_store,
        )

        self.assertEqual(outcome["status"], "succeeded")
        self.assertTrue(result_path.exists())
        self.assertTrue(target.exists())

    def test_run_restore_in_helper_process_sha256_tamper_detected(self):
        """계획 저장 이후 백업 파일이 바뀌면(변조) 실행 직전 재검증에서
        차단돼야 한다."""

        source = self.tmp_dir / "tamper_source.db"
        _make_probe_db(source, "original")

        plan = restore_helper.RestorePlan(
            plan_id="tamper",
            source_backup_path=str(source),
            target_db_path=str(self.tmp_dir / "tamper_target.db"),
            expected_sha256="0" * 64,  # 의도적으로 불일치
            pre_restore_backups_dir=str(self.tmp_dir / "tamper_pre_restore"),
            triggered_by_user_id=None,
            requested_at="2026-08-17T00:00:00+00:00",
            relaunch_argv=[],
        )

        outcome = restore_helper.run_restore_in_helper_process(
            plan, self.tmp_dir / "tamper_result.json",
            credential_store=self.credential_store,
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("SHA-256", outcome["reason"])

    @requires_real_credential_manager
    def test_full_subprocess_helper_cli_end_to_end(self):
        """
        가장 강한 증거 — 실제 별도 프로세스 경계를 넘어 CLI 진입점
        (`main_cli`)까지 전부 태워서 복원이 되는지 확인한다. 부모
        프로세스 역할은 더미 자식 프로세스로 대신한다(빨리 끝나야
        Helper의 "부모 종료 대기"가 곧바로 통과한다).

        2026-09-15 전면 감사 후속(Phase 5) — main_cli()가 부르는
        run_restore_in_helper_process()는 credential_store를 넘기지
        않으면 실제 WindowsCredentialStore()를 만든다(진짜 Helper
        프로세스와 동일하게). 이 테스트는 완전히 별도의 OS 프로세스를
        띄우므로 InMemoryCredentialStore를 공유할 방법이 없다 — 그래서
        이 테스트만 예외적으로 실제 Windows Credential Manager를
        쓴다(백업 생성도 같은 실제 저장소를 써야 서로 다른 프로세스가
        같은 암호화 키를 본다).

        2026-09-15 Phase 6(테스트 격리, IA-012) — 그래서 이 테스트는
        `@requires_real_credential_manager`로 기본 전체 회귀에서
        제외되고, `HOMEZ_RUN_REAL_CREDENTIAL_TESTS=1`을 명시적으로
        설정했을 때만 실행된다(tests/support/real_credential_gate.py
        참고). 이 파일의 나머지 테스트는 전부 InMemoryCredentialStore만
        쓴다.
        """

        from app.core.windows_credential_store import WindowsCredentialStore

        source = self.tmp_dir / "cli_source.db"
        _make_full_schema_source_db(source, "cli-value")

        backups_dir = self.tmp_dir / "cli_backups"
        backup_engine, backup_db = _single_file_session(self.tmp_dir / "cli_bookkeeping.db")
        try:
            record = BackupService(backup_db, WindowsCredentialStore()).create_backup(
                source_db_path=source,
                backups_dir=backups_dir,
                trigger_source=TRIGGER_SOURCE_MANUAL,
            )
        finally:
            backup_db.close()
            backup_engine.dispose()

        target = self.tmp_dir / "cli_target.db"
        plan = restore_helper.RestorePlan(
            plan_id="cli-e2e",
            source_backup_path=record.file_path,
            target_db_path=str(target),
            expected_sha256=record.sha256,
            pre_restore_backups_dir=str(self.tmp_dir / "cli_pre_restore"),
            triggered_by_user_id=None,
            requested_at="2026-08-17T00:00:00+00:00",
            relaunch_argv=[],  # 테스트에서는 재기동하지 않음
        )

        plan_path = self.tmp_dir / "cli_plan.json"
        restore_helper.save_plan(plan_path, plan)

        dummy_parent = subprocess.Popen([sys.executable, "-c", "pass"])
        dummy_parent.wait(timeout=10)

        proc = subprocess.run(
            [
                sys.executable, "-m", "app.desktop.restore_helper",
                restore_helper.RESTORE_HELPER_CLI_FLAG,
                restore_helper.PLAN_ARG, str(plan_path),
                restore_helper.PARENT_PID_ARG, str(dummy_parent.pid),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertTrue(target.exists())

        result_path = plan_path.parent / restore_helper.RESULT_FILENAME
        self.assertTrue(result_path.exists())

        # plan 파일은 실행 후 정리돼야 한다.
        self.assertFalse(plan_path.exists())

        verify = sqlite3.connect(str(target))
        row = verify.execute("SELECT value FROM probe WHERE id = 1").fetchone()
        verify.close()
        self.assertEqual(row[0], "cli-value")


if __name__ == "__main__":
    unittest.main()
