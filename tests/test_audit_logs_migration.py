"""
=========================================================
Homez OS

File : tests/test_audit_logs_migration.py

Gate U-2(2026-08-10) — migrations/20260810_00_create_audit_logs_schema.sql
리허설 검증. 실제 homez.db에는 절대 쓰지 않는다 — 실제 DB는 오직
읽기 전용으로 복사본을 만드는 데만 쓴다(그 복사본만 조작한다).

세 경로를 검증한다:
1) 빈(clean) 임시 DB — MigrationRunner가 이 파일을 pending으로
   분류하고, 적용하면 정확히 이 컬럼/인덱스로 테이블이 생성된다.
2) 실제 homez.db 복사본 — 이 Migration 파일의 CREATE TABLE 선언이
   실제 DDL과 정규화 비교 시 정확히 일치함을 확인한다. 2026-08-10
   세션 중 `bootstrap_environment()`가 격리 없이 실제 DB를 대상으로
   실행되어 이 파일이 이미 `BACKFILLED`로 기록됐다(경위는
   docs/V6_EXECUTION_LEDGER.md의 "Gate U 사고 정정" 절 참고, 데이터·
   스키마 손상 없음, 이력 1행만 추가) — 그래서 이제 실제 DB
   복사본에서는 `already_applied`로 분류되고, `reconcile_backfill()`을
   다시 호출해도 이미 기록된 항목이라 아무것도 재기록하지 않는
   idempotent no-op임을 검증한다.
3) 합성 "스키마가 다른" DB — audit_logs_schema_check가 자동으로
   고치지 않고 즉시 예외를 던지는지 확인한다(MigrationRunner 자체는
   이름 존재 여부만 보므로 이 검증은 별도 모듈이 담당한다).
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support.real_install_gate import requires_real_install_diagnostics
from app.database.audit_logs_schema_check import AuditLogsSchemaMismatchError
from app.database.audit_logs_schema_check import _extract_create_table_sql
from app.database.audit_logs_schema_check import _normalize_ddl
from app.database.audit_logs_schema_check import get_actual_ddl
from app.database.audit_logs_schema_check import verify_matches_migration_or_raise
from app.database.migration_runner import MigrationRunner

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"
_MIGRATION_FILENAME = "20260810_00_create_audit_logs_schema.sql"
_REAL_DB_PATH = _REPO_ROOT / "homez.db"

# 2026-08-27 갱신 — migrations/20260821_00_add_audit_logs_created_at.sql이
# (별도의 정식 추적 Migration, checksum으로 보호됨) audit_logs에
# created_at 컬럼을 추가로 적용했다. verify_matches_migration_or_raise는
# 원래 base 생성 Migration 파일 하나만의 DDL과 정확히 일치하는지 보는
# 도구라(Gate U-2 백필 전용 목적), 그 이후 정당하게 추가된 컬럼까지는
# 알지 못한다 — 이 테스트 파일에서만 그 알려진 차이 하나를 제거하고
# 비교한다(도구 자체는 건드리지 않는다 — 여전히 예상 못한 다른 drift는
# 그대로 차단해야 한다).
_KNOWN_ADDITIVE_COLUMN_SQL = ", created_at TEXT"


def _strip_known_additive_columns(ddl: str) -> str:

    return ddl.replace(_KNOWN_ADDITIVE_COLUMN_SQL, "")


def _verify_matches_migration_tolerating_known_additions(
    conn: sqlite3.Connection, migration_path: Path, table_name: str,
) -> str:
    """verify_matches_migration_or_raise와 동일하지만, 실제 DDL에서
    2026-08-21에 정식 추적 Migration으로 추가된 것으로 이미 알려진
    컬럼(created_at)만 비교 전에 제거한다. 그 외 어떤 예상 못한 차이도
    여전히 AuditLogsSchemaMismatchError로 차단한다."""

    actual_ddl = get_actual_ddl(conn, table_name)
    if actual_ddl is None:
        raise AuditLogsSchemaMismatchError(
            f"{table_name} 테이블이 존재하지 않습니다.",
        )

    expected_ddl = _extract_create_table_sql(
        migration_path.read_text(encoding="utf-8"), table_name,
    )

    actual_normalized = _normalize_ddl(_strip_known_additive_columns(actual_ddl))
    expected_normalized = _normalize_ddl(expected_ddl)

    if actual_normalized != expected_normalized:
        raise AuditLogsSchemaMismatchError(
            f"{table_name}의 실제 DB 스키마가 알려진 추가 컬럼을 제외하고도 "
            f"Migration 파일의 선언과 다릅니다.\n실제(정리 후): "
            f"{actual_normalized}\nMigration 파일: {expected_normalized}",
        )

    return "MATCH"


class AuditLogsMigrationCleanDbTestCase(unittest.TestCase):
    """빈 임시 DB에서 이 Migration 파일 하나만 적용한다."""

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)

        # 이 Migration 파일만 격리해서 검증한다(다른 pending Migration과
        # 섞이면 이 테스트의 목적—이 파일 하나의 정확성—이 흐려진다).
        self.isolated_migrations_dir = Path(tempfile.mkdtemp())
        shutil.copy(
            _MIGRATIONS_DIR / _MIGRATION_FILENAME,
            self.isolated_migrations_dir / _MIGRATION_FILENAME,
        )

        self.runner = MigrationRunner(self.db_path, self.isolated_migrations_dir)

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)
        shutil.rmtree(self.isolated_migrations_dir, ignore_errors=True)

    def test_pending_on_clean_db(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            diagnosis = self.runner.diagnose(conn)
        finally:
            conn.close()

        self.assertEqual(diagnosis["pending"], [_MIGRATION_FILENAME])
        self.assertEqual(diagnosis["backfill_needed"], [])

    def test_apply_creates_expected_schema(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            applied = self.runner.apply_pending(conn)
            self.assertEqual(applied, [_MIGRATION_FILENAME])

            columns = conn.execute("PRAGMA table_info(audit_logs)").fetchall()
            column_names = [c[1] for c in columns]
            self.assertEqual(
                column_names,
                [
                    "id", "company_id", "user_id", "action", "entity",
                    "entity_id", "description", "ip_address",
                ],
            )

            not_null = {c[1]: bool(c[3]) for c in columns}
            self.assertTrue(not_null["id"])
            self.assertFalse(not_null["company_id"])
            self.assertFalse(not_null["user_id"])
            self.assertTrue(not_null["action"])
            self.assertTrue(not_null["entity"])
            self.assertTrue(not_null["entity_id"])
            self.assertFalse(not_null["description"])
            self.assertFalse(not_null["ip_address"])

            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='audit_logs'",
            ).fetchall()
            self.assertIn(("ix_audit_logs_id",), indexes)

            # 실제 INSERT가 정상 동작하는지도 확인한다(app/core/audit_db.py
            # ::write_audit_log()가 쓰는 것과 동일한 컬럼 조합).
            conn.execute(
                "INSERT INTO audit_logs "
                "(company_id, user_id, action, entity, entity_id, "
                "description, ip_address) VALUES "
                "(NULL, 1, 'TEST_ACTION', 'TestEntity', '1', 'test', NULL)",
            )
            conn.commit()
            row = conn.execute(
                "SELECT action, entity FROM audit_logs",
            ).fetchone()
            self.assertEqual(row, ("TEST_ACTION", "TestEntity"))
        finally:
            conn.close()

    def test_reapply_fails_fast_rather_than_silently_skipping(self):
        """
        이 저장소의 확립된 정책(homez-migration-safety 스킬 기록) —
        이미 적용된 뒤 같은 파일을 다시 적용 시도하면 "table already
        exists"로 실패하는 것이 의도된 fail-fast 동작이다(결함이
        아니다). apply_pending()은 이미 적용된(APPLIED) 파일은
        pending 목록에서 제외하므로, 여기서는 이력 없이 SQL만 강제로
        재실행했을 때의 충돌 차단(diagnose 재호출 시 backfill로 분류)
        을 확인한다.
        """

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.apply_pending(conn)

            # 이력을 지운 뒤(마치 부기 없이 테이블만 존재하는 상태를
            # 재현) 다시 진단하면, 이제는 이미 테이블이 존재하므로
            # pending이 아니라 backfill_needed로 분류되어야 한다 —
            # 즉 "이미 존재하면 절대 재실행하지 않는다"는 계약을
            # diagnose() 스스로도 지킨다.
            conn.execute("DELETE FROM schema_migrations")
            conn.commit()

            diagnosis = self.runner.diagnose(conn)
            self.assertEqual(diagnosis["pending"], [])
            self.assertIn(_MIGRATION_FILENAME, [
                fn for fn, _ in diagnosis["backfill_needed"]
            ])
        finally:
            conn.close()


# 실제 설치환경 진단 — 기본 전체 회귀에서 제외(opt-in: HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)
@requires_real_install_diagnostics
class AuditLogsMigrationRealDbCopyTestCase(unittest.TestCase):
    """
    실제 homez.db는 읽기 전용으로만 접근해 복사본을 만든다 — 이 클래스
    안의 모든 조작은 그 복사본에서만 일어난다. 원본은 절대 열지
    않는다(테스트 전체에서 `_REAL_DB_PATH`를 sqlite3.connect()에 직접
    넘기는 코드가 없어야 한다).
    """

    def setUp(self):

        if not _REAL_DB_PATH.exists():
            self.skipTest("실제 homez.db가 이 환경에 없습니다.")

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.copy_path = Path(path)

        src = sqlite3.connect(f"file:{_REAL_DB_PATH}?mode=ro", uri=True)
        dst = sqlite3.connect(str(self.copy_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        self.isolated_migrations_dir = Path(tempfile.mkdtemp())
        shutil.copy(
            _MIGRATIONS_DIR / _MIGRATION_FILENAME,
            self.isolated_migrations_dir / _MIGRATION_FILENAME,
        )

        self.runner = MigrationRunner(self.copy_path, self.isolated_migrations_dir)

    def tearDown(self):

        if self.copy_path.exists():
            os.remove(self.copy_path)
        shutil.rmtree(self.isolated_migrations_dir, ignore_errors=True)

    def test_diagnose_classifies_as_already_applied_not_pending(self):
        """
        2026-08-10 세션 중 실제 DB가 격리 없는 bootstrap 실행으로 이미
        BACKFILLED 기록을 갖게 됐다(Ledger "Gate U 사고 정정" 참고) —
        그래서 이 Migration은 더 이상 backfill_needed가 아니라
        already_applied로 분류되는 것이 현재 실제 DB 상태에서 맞는
        기대값이다.
        """

        conn = sqlite3.connect(str(self.copy_path))
        try:
            diagnosis = self.runner.diagnose(conn)
        finally:
            conn.close()

        self.assertEqual(diagnosis["pending"], [])
        self.assertEqual(diagnosis["backfill_needed"], [])
        self.assertEqual(diagnosis["already_applied"], [_MIGRATION_FILENAME])

    def test_migration_file_ddl_matches_real_existing_schema(self):

        conn = sqlite3.connect(str(self.copy_path))
        try:
            result = _verify_matches_migration_tolerating_known_additions(
                conn, self.isolated_migrations_dir / _MIGRATION_FILENAME,
                "audit_logs",
            )
            self.assertEqual(result, "MATCH")
        finally:
            conn.close()

    def test_reconcile_backfill_is_idempotent_noop_on_already_applied(self):
        """
        실제 DB는 2026-08-10 사고(Ledger "Gate U 사고 정정" 참고)로
        이미 이 파일을 BACKFILLED로 기록하고 있다 — 그래서
        `reconcile_backfill()`을 다시 호출해도 SQL을 재실행하지 않고
        (데이터 보존), 이미 기록된 항목을 중복 기록하지도 않는
        idempotent no-op이어야 한다.
        """

        conn = sqlite3.connect(str(self.copy_path))
        try:
            before_count = conn.execute(
                "SELECT COUNT(*) FROM audit_logs",
            ).fetchone()[0]

            # 리허설 절차: backfill 전에 반드시 스키마 일치를 먼저
            # 확인한다(differs 시나리오였다면 여기서 멈춰야 한다).
            _verify_matches_migration_tolerating_known_additions(
                conn, self.isolated_migrations_dir / _MIGRATION_FILENAME,
                "audit_logs",
            )

            backfilled = self.runner.reconcile_backfill(conn)
            self.assertEqual(backfilled, [])

            after_count = conn.execute(
                "SELECT COUNT(*) FROM audit_logs",
            ).fetchone()[0]
            self.assertEqual(before_count, after_count)

            history = self.runner.get_history_readonly(conn)
            self.assertEqual(
                history[_MIGRATION_FILENAME].status, "BACKFILLED",
            )

            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            conn.close()

    def test_real_db_file_untouched_by_this_test_class(self):
        """
        원본 파일의 mtime/크기가 이 테스트 클래스 실행 전후로 바뀌지
        않았는지 재확인한다 — setUp()이 읽기 전용 연결로만 원본을
        열었음을 다시 한번 실측으로 뒷받침한다.
        """

        stat_before = _REAL_DB_PATH.stat()

        conn = sqlite3.connect(str(self.copy_path))
        try:
            self.runner.reconcile_backfill(conn)
        finally:
            conn.close()

        stat_after = _REAL_DB_PATH.stat()
        self.assertEqual(stat_before.st_mtime, stat_after.st_mtime)
        self.assertEqual(stat_before.st_size, stat_after.st_size)


class AuditLogsSchemaMismatchTestCase(unittest.TestCase):
    """
    실제로는 재현 불가능한(현재 real DB와 항상 일치하는) '스키마가
    다른' 상황을 합성 DB로 만들어, 자동으로 고치지 않고 즉시 실패하는
    지만 검증한다 — MigrationRunner.diagnose() 자체는 이 차이를 보지
    못한다(이름만 본다)는 것이 이 검증이 별도 모듈로 필요한 이유다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        self.migration_path = _MIGRATIONS_DIR / _MIGRATION_FILENAME

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)

    def test_missing_column_is_detected_and_raises(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            # ip_address 컬럼이 빠진, 실제와 다른 audit_logs를 합성한다.
            conn.execute(
                """
                CREATE TABLE audit_logs (
                    id INTEGER NOT NULL,
                    company_id INTEGER,
                    user_id INTEGER,
                    action VARCHAR(100) NOT NULL,
                    entity VARCHAR(100) NOT NULL,
                    entity_id VARCHAR(100) NOT NULL,
                    description VARCHAR(500),
                    PRIMARY KEY (id)
                )
                """,
            )
            conn.commit()

            with self.assertRaises(AuditLogsSchemaMismatchError):
                verify_matches_migration_or_raise(
                    conn, self.migration_path, "audit_logs",
                )
        finally:
            conn.close()

    def test_identical_schema_passes(self):

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript(
                self.migration_path.read_text(encoding="utf-8"),
            )

            result = verify_matches_migration_or_raise(
                conn, self.migration_path, "audit_logs",
            )
            self.assertEqual(result, "MATCH")
        finally:
            conn.close()


class AuditLogsMigrationSyntheticEdgeCasesTestCase(unittest.TestCase):
    """
    2026-08-11 Gate V-2 — 실제 DB 복사본에 의존하지 않는 합성 임시 DB
    fixture로 남은 시나리오를 보완한다(실제 homez.db는 이미 BACKFILLED
    상태가 돼 버려서, "테이블은 있지만 이력이 없는" 최초 상태나
    "APPLIED"(신규 DDL 실행) 상태를 더 이상 실제 DB 복사본으로는
    재현할 수 없다 — 그래서 이 클래스는 실제 DB를 전혀 열지 않고
    필요한 상태를 직접 만든다).
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)

        self.isolated_migrations_dir = Path(tempfile.mkdtemp())
        shutil.copy(
            _MIGRATIONS_DIR / _MIGRATION_FILENAME,
            self.isolated_migrations_dir / _MIGRATION_FILENAME,
        )

        self.runner = MigrationRunner(self.db_path, self.isolated_migrations_dir)

    def tearDown(self):

        if self.db_path.exists():
            os.remove(self.db_path)
        shutil.rmtree(self.isolated_migrations_dir, ignore_errors=True)

    def _create_matching_audit_logs_table_without_history(self, conn):

        conn.executescript(
            (self.isolated_migrations_dir / _MIGRATION_FILENAME).read_text(
                encoding="utf-8",
            ).split("-- Rollback")[0],
        )

    def test_table_exists_without_history_is_backfill_needed(self):
        """
        시나리오 2 — audit_logs 테이블은 있지만 schema_migrations
        이력이 아예 없는 최초 상태(실제 DB가 2026-08-10 사고 전에
        있었던 상태와 동일). 반드시 backfill_needed로 분류돼야 하고,
        pending·already_applied에는 없어야 한다.
        """

        conn = sqlite3.connect(str(self.db_path))
        try:
            self._create_matching_audit_logs_table_without_history(conn)

            diagnosis = self.runner.diagnose(conn)
            self.assertEqual(diagnosis["pending"], [])
            self.assertEqual(diagnosis["already_applied"], [])
            self.assertEqual(
                [fn for fn, _ in diagnosis["backfill_needed"]],
                [_MIGRATION_FILENAME],
            )

            before = conn.execute(
                "SELECT COUNT(*) FROM audit_logs",
            ).fetchone()[0]
            backfilled = self.runner.reconcile_backfill(conn)
            self.assertEqual(backfilled, [_MIGRATION_FILENAME])
            after = conn.execute(
                "SELECT COUNT(*) FROM audit_logs",
            ).fetchone()[0]
            self.assertEqual(before, after)

            history = self.runner.get_history_readonly(conn)
            self.assertEqual(history[_MIGRATION_FILENAME].status, "BACKFILLED")
        finally:
            conn.close()

    def test_applied_status_is_distinct_from_backfilled_status(self):
        """
        시나리오 4 — BACKFILLED와 APPLIED는 의미가 다르다: APPLIED는
        MigrationRunner가 직접 DDL을 실행해서 만든 것(apply_pending()),
        BACKFILLED는 이미 존재하던 스키마의 이력만 나중에 채운 것
        (reconcile_backfill(), SQL 재실행 없음). 빈 DB에 apply_pending()
        으로 정식 적용하면 반드시 APPLIED로 기록돼야 하고, 그 뒤
        diagnose()는 pending도 backfill_needed도 아닌 already_applied로
        분류해야 한다 — BACKFILLED와 같은 최종 분류(already_applied)를
        받더라도 status 컬럼 자체는 절대 혼용되지 않는다.
        """

        conn = sqlite3.connect(str(self.db_path))
        try:
            applied = self.runner.apply_pending(conn)
            self.assertEqual(applied, [_MIGRATION_FILENAME])

            history = self.runner.get_history_readonly(conn)
            self.assertEqual(history[_MIGRATION_FILENAME].status, "APPLIED")
            self.assertNotEqual(
                history[_MIGRATION_FILENAME].status, "BACKFILLED",
            )

            diagnosis = self.runner.diagnose(conn)
            self.assertEqual(diagnosis["pending"], [])
            self.assertEqual(diagnosis["backfill_needed"], [])
            self.assertEqual(diagnosis["already_applied"], [_MIGRATION_FILENAME])
        finally:
            conn.close()

    def test_checksum_mismatch_is_detected_and_raises(self):
        """
        시나리오 5 — 이력에 기록된 checksum과 현재 파일의 checksum이
        다르면(파일 변조·손상 의심) diagnose()가 자동으로 넘어가지
        않고 즉시 ChecksumMismatchError로 중단해야 한다.
        """

        from app.database.migration_runner import ChecksumMismatchError

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.runner.apply_pending(conn)

            conn.execute(
                "UPDATE schema_migrations SET checksum = ? WHERE filename = ?",
                ("0" * 64, _MIGRATION_FILENAME),
            )
            conn.commit()

            with self.assertRaises(ChecksumMismatchError):
                self.runner.diagnose(conn)
        finally:
            conn.close()

    def test_failed_apply_rolls_back_and_leaves_no_partial_table(self):
        """
        시나리오 8 — Migration 파일 자체가 명시적 BEGIN/COMMIT으로
        감싸져 있으므로(migrations/20260810_00_create_audit_logs_
        schema.sql 원문 참고), CREATE TABLE 이후 어떤 문장이 실패하면
        COMMIT에 도달하지 못해 테이블도 이력도 전부 남지 않아야 한다
        — "부분 적용" 상태(테이블만 있고 이력이 없거나, 그 반대)가
        생기지 않는 것이 이 테스트의 핵심 주장이다.
        """

        broken_migrations_dir = Path(tempfile.mkdtemp())
        try:
            original = (
                _MIGRATIONS_DIR / _MIGRATION_FILENAME
            ).read_text(encoding="utf-8")
            # CREATE TABLE 뒤, COMMIT 전에 반드시 실패하는 문장을
            # 끼워 넣는다(존재하지 않는 테이블에 대한 CREATE INDEX).
            broken = original.replace(
                "CREATE INDEX ix_audit_logs_id ON audit_logs (id);",
                "CREATE INDEX ix_audit_logs_id ON audit_logs (id);\n"
                "CREATE INDEX ix_broken ON no_such_table (id);",
            )
            self.assertNotEqual(original, broken)  # 치환이 실제로 됐는지 확인
            (broken_migrations_dir / _MIGRATION_FILENAME).write_text(
                broken, encoding="utf-8",
            )

            broken_runner = MigrationRunner(self.db_path, broken_migrations_dir)

            conn = sqlite3.connect(str(self.db_path))
            try:
                from app.database.migration_runner import MigrationExecutionError

                with self.assertRaises(MigrationExecutionError):
                    broken_runner.apply_pending(conn)
            finally:
                # 실패한 실행 뒤 같은 커넥션은 여전히 미완료 트랜잭션을
                # 들고 있어(sqlite3의 executescript는 스크립트 안의
                # BEGIN을 그대로 존중한다) 그 커넥션으로 조회하면
                # "아직 commit되지 않은 자기 자신의 쓰기"가 보여
                # 실제로 디스크에 남았는지와 무관하게 존재하는 것처럼
                # 보인다 — 반드시 커넥션을 닫고 새로 열어 실제 영속
                # 상태만 확인해야 한다.
                conn.close()

            fresh_conn = sqlite3.connect(str(self.db_path))
            try:
                tables = {
                    row[0] for row in fresh_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'",
                    ).fetchall()
                }
                self.assertNotIn(
                    "audit_logs", tables,
                    "COMMIT 전 실패인데 테이블이 실제로 영속됐다 — 부분 적용 결함",
                )
                if "schema_migrations" in tables:
                    history_rows = fresh_conn.execute(
                        "SELECT filename FROM schema_migrations",
                    ).fetchall()
                    self.assertEqual(
                        history_rows, [],
                        "부분 실패인데 이력이 영속됐다 — 부분 적용 결함",
                    )

                integrity = fresh_conn.execute(
                    "PRAGMA integrity_check",
                ).fetchone()[0]
                self.assertEqual(integrity, "ok")
            finally:
                fresh_conn.close()
        finally:
            shutil.rmtree(broken_migrations_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
