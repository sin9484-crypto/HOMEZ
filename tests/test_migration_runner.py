"""
=========================================================
Homez OS

File : tests/test_migration_runner.py

Migration Runner 재감사: checksum 불일치, 순서 역전, 부분 적용,
중복 적용 차단, backfill(재실행 없이 이력만 채움), 정상 적용 순서,
백업 생성·무결성, 신규 설치/기존 설치 부트스트랩.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database.bootstrap import bootstrap_environment
from app.database.migration_runner import ChecksumMismatchError
from app.database.migration_runner import DuplicateApplicationError
from app.database.migration_runner import MigrationRunner
from app.database.migration_runner import OrderInversionError
from app.database.migration_runner import PartialApplicationError
from app.database.migration_runner import compute_checksum


def _write_migration(path: Path, sql: str) -> None:

    path.write_text(sql, encoding="utf-8")


class MigrationRunnerTestCase(unittest.TestCase):

    def setUp(self):

        self.tmpdir = tempfile.TemporaryDirectory()
        self.migrations_dir = Path(self.tmpdir.name) / "migrations"
        self.migrations_dir.mkdir()
        self.backups_dir = Path(self.tmpdir.name) / "backups"

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)  # 실제 시작은 "DB 파일이 없는" 상태부터
        self.db_path = Path(path)

    def tearDown(self):

        self.tmpdir.cleanup()

        if self.db_path.exists():
            os.remove(self.db_path)

    def _runner(self) -> MigrationRunner:

        return MigrationRunner(self.db_path, self.migrations_dir)

    def _fresh_db_conn(self) -> sqlite3.Connection:

        return sqlite3.connect(str(self.db_path))

    def test_guarded_redundant_index_migration_is_noop_not_blocked(self):

        # 실제 버그 재현: 첫 파일이 이미 인덱스를 만들고, 뒤 파일이 같은
        # 인덱스를 IF NOT EXISTS로 다시 선언하는 "문서화된 안전장치"
        # 패턴(migrations/20260727_00_create_funding_settlement_schema.sql
        # + migrations/20260727_add_settlement_ledger_unique_index.sql과
        # 동일한 구조)이 신규 설치의 단일 apply_pending() 배치 안에서
        # DuplicateApplicationError로 잘못 차단되면 안 된다.
        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\n"
            "CREATE TABLE a (id INTEGER PRIMARY KEY);\n"
            "CREATE UNIQUE INDEX uq_a_id ON a (id);\n"
            "COMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_add_redundant_index.sql",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_a_id ON a (id);\n",
        )

        conn = self._fresh_db_conn()
        try:
            runner = self._runner()

            applied = runner.apply_pending(conn)
            self.assertEqual(
                applied,
                ["20260101_00_create_a.sql", "20260102_00_add_redundant_index.sql"],
            )

            history = runner.get_history(conn)
            self.assertEqual(history["20260101_00_create_a.sql"].status, "APPLIED")
            self.assertEqual(
                history["20260102_00_add_redundant_index.sql"].status, "APPLIED",
            )

            index_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='index' AND name='uq_a_id'",
            ).fetchone()[0]
            self.assertEqual(index_count, 1)

        finally:
            conn.close()

    def test_bootstrap_new_install_applies_real_repo_migrations_directory(self):

        # 실제 버그 재현: bootstrap_environment()의 is_new_install 경로가
        # 실제 저장소 migrations/ 디렉터리(20260727_00_create_funding_
        # settlement_schema.sql이 uq_funding_ledger_settlement_type을
        # 만든 뒤, 20260727_add_settlement_ledger_unique_index.sql이
        # 같은 인덱스를 IF NOT EXISTS로 다시 선언하는 실제 파일 쌍 포함)
        # 를 대상으로 완전한 신규 설치를 예외 없이 끝내야 한다.
        real_migrations_dir = (
            Path(__file__).resolve().parent.parent / "migrations"
        )
        self.assertTrue(
            (
                real_migrations_dir
                / "20260727_add_settlement_ledger_unique_index.sql"
            ).exists(),
            "이 테스트는 실제 저장소 migrations/ 디렉터리를 대상으로 한다 "
            "— 해당 파일이 없으면 테스트 전제가 깨진 것이다.",
        )

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=real_migrations_dir,
            backups_dir=self.backups_dir,
        )

        self.assertTrue(result.is_new_install)
        self.assertIn(
            "20260727_00_create_funding_settlement_schema.sql", result.applied,
        )
        self.assertIn(
            "20260727_add_settlement_ledger_unique_index.sql", result.applied,
        )

        conn = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(
                conn.execute("PRAGMA integrity_check").fetchone()[0], "ok",
            )
            index_count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
                "AND name='uq_funding_ledger_settlement_type'",
            ).fetchone()[0]
            self.assertEqual(index_count, 1)
        finally:
            conn.close()

    # ----------------------------------------------------
    # 정상 순차 적용
    # ----------------------------------------------------

    def test_migrations_apply_in_filename_order_exactly_once(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        runner = self._runner()
        conn = self._fresh_db_conn()

        applied = runner.apply_pending(conn)
        self.assertEqual(applied, ["20260101_00_create_a.sql", "20260102_00_create_b.sql"])

        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        self.assertIn("a", tables)
        self.assertIn("b", tables)

        # 재실행 시 아무것도 다시 적용하지 않는다(멱등).
        applied_again = runner.apply_pending(conn)
        self.assertEqual(applied_again, [])

        conn.close()

    def test_untracked_existing_table_is_routed_to_backfill_not_applied(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        conn = self._fresh_db_conn()
        try:
            conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
            conn.commit()

            runner = self._runner()

            # 이력 없이 테이블만 존재 -> diagnose는 이를 pending이 아닌
            # backfill 대상으로 분류해야 한다. reconcile 없이 곧바로
            # apply_pending을 호출하면 이 파일은 pending 목록에 없으므로
            # 조용히 건너뛴다(재실행 시도 자체를 하지 않는다) — 이것이
            # 바로 "중복 실행을 시도하지 않는다"는 안전장치다.
            diagnosis = runner.diagnose(conn)
            self.assertEqual(
                [fn for fn, _ in diagnosis["backfill_needed"]],
                ["20260101_00_create_a.sql"],
            )
            self.assertEqual(diagnosis["pending"], [])

            applied = runner.apply_pending(conn)
            self.assertEqual(applied, [])

        finally:
            conn.close()

    def test_duplicate_application_is_blocked_on_intra_batch_collision(self):

        # 두 미적용 파일이 실수로 같은 테이블명을 대상으로 하는 경우 —
        # 첫 파일 적용 직후 두 번째 파일 적용 시점에 충돌이 감지돼야
        # 한다(같은 apply_pending() 호출 내의 사전 확인).
        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_a_again.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        conn = self._fresh_db_conn()
        try:
            runner = self._runner()

            with self.assertRaises(DuplicateApplicationError):
                runner.apply_pending(conn)

            # 첫 번째 파일은 정상적으로 이력에 남아 있어야 한다(원자적
            # 실패는 실패한 그 파일에만 국한된다).
            history = runner.get_history(conn)
            self.assertIn("20260101_00_create_a.sql", history)
            self.assertNotIn("20260102_00_create_a_again.sql", history)

        finally:
            conn.close()

    # ----------------------------------------------------
    # checksum 불일치
    # ----------------------------------------------------

    def test_checksum_mismatch_after_applied_blocks_everything(self):

        path = self.migrations_dir / "20260101_00_create_a.sql"
        _write_migration(
            path, "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        runner = self._runner()
        conn = self._fresh_db_conn()
        runner.apply_pending(conn)

        # 적용 후 파일 내용을 변조.
        _write_migration(
            path,
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY, extra TEXT);\nCOMMIT;\n",
        )

        with self.assertRaises(ChecksumMismatchError):
            runner.diagnose(conn)

        conn.close()

    # ----------------------------------------------------
    # 순서 역전
    # ----------------------------------------------------

    def test_order_inversion_is_detected(self):

        _write_migration(
            self.migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        runner = self._runner()
        conn = self._fresh_db_conn()
        try:
            runner.apply_pending(conn)

            # 이미 적용된 20260102보다 사전순으로 앞서는 새 파일을 나중에
            # 추가 — 이 파일은 원래 먼저 적용됐어야 한다.
            _write_migration(
                self.migrations_dir / "20260101_00_create_a.sql",
                "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
            )

            with self.assertRaises(OrderInversionError):
                runner.diagnose(conn)

        finally:
            conn.close()

    def test_backfilled_out_of_order_history_does_not_block_earlier_pending_file(self):

        # 실제 운영 이력 재현: b(늦은 날짜)는 Gate 승인을 받아 먼저
        # 적용됐고, a(이른 날짜)는 그 뒤에 의도적으로 보류됐다가 이제야
        # 적용 대상이 되는 상황 — 이 러너가 처음 붙기 전부터 이미
        # 그렇게 운영돼 온 이력이므로 BACKFILLED로 인정해야 하며, a를
        # OrderInversionError로 막아서는 안 된다.
        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        conn = self._fresh_db_conn()
        try:
            # b만 먼저 존재하는(러너 도입 이전 실제 적용) 상태를 재현.
            conn.execute("CREATE TABLE b (id INTEGER PRIMARY KEY)")
            conn.commit()

            runner = self._runner()
            backfilled = runner.reconcile_backfill(conn)
            self.assertEqual(backfilled, ["20260102_00_create_b.sql"])

            # a는 여전히 diagnose에서 pending으로 정상 분류돼야 하고,
            # OrderInversionError가 발생하면 안 된다.
            diagnosis = runner.diagnose(conn)
            self.assertEqual(diagnosis["pending"], ["20260101_00_create_a.sql"])

            applied = runner.apply_pending(conn)
            self.assertEqual(applied, ["20260101_00_create_a.sql"])

        finally:
            conn.close()

    # ----------------------------------------------------
    # "IF NOT EXISTS" 대상 이름 파싱 — 실제 migrations/에 존재하는
    # 20260727_add_settlement_ledger_unique_index.sql과 동일한 스타일
    # (CREATE UNIQUE INDEX IF NOT EXISTS ...)이 대상 객체명을 "IF"로
    # 잘못 추출하지 않고 정확히 backfill로 분류되는지 확인한다.
    # ----------------------------------------------------

    def test_create_index_if_not_exists_target_name_parsed_correctly(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_add_index.sql",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_a_id ON a (id);\n",
        )

        conn = self._fresh_db_conn()
        try:
            runner = self._runner()
            runner.apply_pending(conn)

            # 인덱스가 이미 존재하는 상태에서 다시 diagnose하면 "IF"라는
            # 가짜 이름이 아니라 실제 인덱스명(uq_a_id)으로 already_applied
            # 처리돼야 한다.
            diagnosis = runner.diagnose(conn)
            self.assertIn("20260102_00_add_index.sql", diagnosis["already_applied"])
            self.assertEqual(diagnosis["pending"], [])
            self.assertEqual(diagnosis["backfill_needed"], [])

        finally:
            conn.close()

    def test_create_table_if_not_exists_target_name_parsed_correctly(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "CREATE TABLE IF NOT EXISTS a (id INTEGER PRIMARY KEY);\n",
        )

        conn = self._fresh_db_conn()
        conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
        conn.commit()

        try:
            runner = self._runner()
            diagnosis = runner.diagnose(conn)

            # 대상명이 정확히 "a"로 파싱돼야 backfill로 올바르게 분류된다
            # ("IF"로 잘못 파싱되면 실제 존재하는 "a"와 매치되지 않아
            # pending으로 잘못 분류된다).
            self.assertEqual(
                [fn for fn, _ in diagnosis["backfill_needed"]],
                ["20260101_00_create_a.sql"],
            )
            self.assertEqual(diagnosis["pending"], [])

        finally:
            conn.close()

    # ----------------------------------------------------
    # 부분 적용
    # ----------------------------------------------------

    def test_partial_application_is_detected_and_blocks(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_ab.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        conn = self._fresh_db_conn()
        # a만 존재 -> 이 Migration 파일의 대상 중 일부만 존재하는
        # "부분 적용" 상태를 인위적으로 재현한다.
        conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
        conn.commit()

        runner = self._runner()

        with self.assertRaises(PartialApplicationError):
            runner.diagnose(conn)

        conn.close()

    # ----------------------------------------------------
    # backfill — SQL 재실행 없이 이력만 채움
    # ----------------------------------------------------

    def test_backfill_does_not_re_execute_sql(self):

        path = self.migrations_dir / "20260101_00_create_a.sql"
        _write_migration(
            path,
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        conn = self._fresh_db_conn()
        conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO a (id) VALUES (42)")
        conn.commit()

        runner = self._runner()
        backfilled = runner.reconcile_backfill(conn)

        self.assertEqual(backfilled, ["20260101_00_create_a.sql"])

        # 기존 데이터가 그대로 있어야 한다(재실행됐다면 이미 존재하는
        # CREATE TABLE에서 에러가 나거나 데이터가 사라졌을 것).
        rows = conn.execute("SELECT id FROM a").fetchall()
        self.assertEqual(rows, [(42,)])

        history = runner.get_history(conn)
        self.assertEqual(history[path.name].status, "BACKFILLED")

        conn.close()

    def test_backfill_then_apply_pending_only_applies_new_ones(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        conn = self._fresh_db_conn()
        conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
        conn.commit()

        runner = self._runner()
        backfilled = runner.reconcile_backfill(conn)
        self.assertEqual(backfilled, ["20260101_00_create_a.sql"])

        applied = runner.apply_pending(conn)
        self.assertEqual(applied, ["20260102_00_create_b.sql"])

        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        self.assertIn("a", tables)
        self.assertIn("b", tables)

        conn.close()

    # ----------------------------------------------------
    # 실행 실패 시 이력에 남기지 않음(재시도 가능하게 유지)
    # ----------------------------------------------------

    def test_execution_failure_does_not_record_history_and_stays_pending(self):

        _write_migration(
            self.migrations_dir / "20260101_00_broken.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\n"
            "THIS IS NOT VALID SQL;\nCOMMIT;\n",
        )

        conn = self._fresh_db_conn()
        runner = self._runner()

        with self.assertRaises(Exception):
            runner.apply_pending(conn)

        history = runner.get_history(conn)
        self.assertNotIn("20260101_00_broken.sql", history)

        conn.close()

    # ----------------------------------------------------
    # 백업
    # ----------------------------------------------------

    def test_create_backup_produces_verified_copy(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        conn = self._fresh_db_conn()
        runner = self._runner()
        runner.apply_pending(conn)
        conn.execute("INSERT INTO a (id) VALUES (1)")
        conn.commit()
        conn.close()

        backups_dir = Path(self.tmpdir.name) / "backups"
        backup_path = runner.create_backup(backups_dir, "test_label")

        self.assertTrue(backup_path.exists())
        verify = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        verify.execute("PRAGMA query_only=ON")
        self.assertEqual(
            verify.execute("PRAGMA integrity_check").fetchone()[0], "ok",
        )
        self.assertEqual(
            verify.execute("SELECT id FROM a").fetchall(), [(1,)],
        )
        verify.close()

    # ----------------------------------------------------
    # 부트스트랩 — 신규 설치
    # ----------------------------------------------------

    def test_bootstrap_new_install_applies_all_migrations(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        self.assertFalse(self.db_path.exists())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
        )

        self.assertTrue(result.is_new_install)
        self.assertEqual(
            result.applied,
            ["20260101_00_create_a.sql", "20260102_00_create_b.sql"],
        )
        self.assertIsNone(result.backup_path)
        self.assertTrue(self.db_path.exists())

        conn = sqlite3.connect(str(self.db_path))
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        self.assertIn("a", tables)
        self.assertIn("b", tables)
        conn.close()

    # ----------------------------------------------------
    # 부트스트랩 — 기존 설치(일부 이미 적용)
    # ----------------------------------------------------

    def test_bootstrap_existing_install_backfills_and_backs_up_before_pending(self):
        """
        2026-08-05 CTO 재검증 지시 Gate E — pending(실제 DDL)은 더
        이상 승인 없이 자동 적용되지 않는다. backfill(이미 존재하는
        객체의 이력만 채움, 스키마 변경 없음)만 계속 자동 처리된다.
        """

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )
        _write_migration(
            self.migrations_dir / "20260102_00_create_b.sql",
            "BEGIN;\nCREATE TABLE b (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        # 기존 DB를 미리 만들어 a 테이블만 있는 "추적 이전" 상태를
        # 재현한다.
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        # 1) 승인 없이 호출 — backfill은 자동 처리되지만 pending은
        # 적용되지 않고 승인 요청 상태로만 돌아온다.
        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
        )

        self.assertFalse(result.is_new_install)
        self.assertEqual(result.backfilled, ["20260101_00_create_a.sql"])
        self.assertEqual(result.applied, [])
        self.assertTrue(result.migration_approval_required)
        self.assertIsNotNone(result.pending_migration_plan)
        self.assertEqual(
            result.pending_migration_plan.files,
            ["20260102_00_create_b.sql"],
        )
        self.assertIn(
            "b",
            result.pending_migration_plan.targets_by_file[
                "20260102_00_create_b.sql"
            ]["tables"],
        )

        # 2) 정확히 그 pending 목록을 승인해 다시 호출하면 그때만
        # 실제로 적용되고, 적용 전 백업도 만들어진다.
        approved_result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
            approved_migration_files=["20260102_00_create_b.sql"],
        )
        self.assertEqual(approved_result.applied, ["20260102_00_create_b.sql"])
        self.assertFalse(approved_result.migration_approval_required)
        self.assertIsNotNone(approved_result.backup_path)
        self.assertTrue(approved_result.backup_path.exists())
        self.assertEqual(approved_result.integrity_check_result, "ok")

    def test_bootstrap_idempotent_second_run_does_nothing(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        # 이 시점에는 아직 self.db_path가 없다(신규 설치 경로) —
        # 신규 설치는 빈 DB라 보호할 기존 상태가 없으므로 승인 없이도
        # 전체 순차 적용된다(변경 없음, 아래에서 "기존 설치"로 전환된
        # 뒤부터 승인 게이트가 적용됨을 확인한다).
        result1 = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
        )
        self.assertEqual(result1.applied, ["20260101_00_create_a.sql"])

        result2 = bootstrap_environment(
            db_path=self.db_path, migrations_dir=self.migrations_dir,
            backups_dir=self.backups_dir,
        )
        self.assertFalse(result2.is_new_install)
        self.assertEqual(result2.applied, [])
        self.assertEqual(result2.backfilled, [])
        self.assertFalse(result2.migration_approval_required)
        self.assertIsNone(result2.backup_path)

    def test_ensure_directories_creates_expected_paths(self):

        from app.database import bootstrap as bootstrap_mod

        with tempfile.TemporaryDirectory() as tmp:
            import app.desktop.paths as paths_mod

            original_is_frozen = paths_mod.is_frozen
            original_environ = dict(os.environ)

            try:
                os.environ["LOCALAPPDATA"] = tmp
                paths_mod.is_frozen = lambda: True

                bootstrap_mod.ensure_directories()

                expected = [
                    Path(tmp) / "HOMEZ" / "data",
                    Path(tmp) / "HOMEZ" / "logs",
                    Path(tmp) / "HOMEZ" / "backups",
                    Path(tmp) / "HOMEZ" / "config",
                    Path(tmp) / "HOMEZ" / "media",
                ]
                for p in expected:
                    self.assertTrue(p.exists(), f"{p} 생성되지 않음")

            finally:
                paths_mod.is_frozen = original_is_frozen
                os.environ.clear()
                os.environ.update(original_environ)


class MigrationRunnerReadOnlyContractTestCase(unittest.TestCase):
    """
    2026-08-02 CTO 보안 보완 Gate R1 — diagnose()/get_history_readonly()가
    실제로 DB에 어떤 쓰기도 하지 않음을 검증한다(이전 버전은 diagnose()
    호출만으로 schema_migrations + sqlite_sequence 부기 테이블이
    commit돼, "읽기 전용 진단"이라는 계약을 위반했다).
    """

    def setUp(self):

        self.tmpdir = tempfile.TemporaryDirectory()
        self.migrations_dir = Path(self.tmpdir.name) / "migrations"
        self.migrations_dir.mkdir()
        self.backups_dir = Path(self.tmpdir.name) / "backups"

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)  # 이번엔 "이미 존재하는 빈 DB 파일"부터 시작

    def tearDown(self):

        self.tmpdir.cleanup()

        if self.db_path.exists():
            os.remove(self.db_path)

    def _runner(self) -> MigrationRunner:

        return MigrationRunner(self.db_path, self.migrations_dir)

    def _table_names(self, conn) -> set:

        return {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }

    def test_diagnose_on_empty_db_creates_no_tables(self):

        runner = self._runner()
        conn = sqlite3.connect(str(self.db_path))
        try:
            diagnosis = runner.diagnose(conn)
            self.assertEqual(diagnosis["pending"], [])
            self.assertEqual(diagnosis["backfill_needed"], [])
            self.assertEqual(diagnosis["already_applied"], [])

            self.assertEqual(self._table_names(conn), set())
        finally:
            conn.close()

    def test_diagnose_does_not_create_schema_migrations_table(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        runner = self._runner()
        conn = sqlite3.connect(str(self.db_path))
        try:
            runner.diagnose(conn)
            self.assertNotIn("schema_migrations", self._table_names(conn))

            history = runner.get_history_readonly(conn)
            self.assertEqual(history, {})
            self.assertNotIn("schema_migrations", self._table_names(conn))
        finally:
            conn.close()

    def test_diagnose_leaves_existing_populated_db_byte_for_byte_unchanged(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        runner = self._runner()
        conn = sqlite3.connect(str(self.db_path))
        try:
            runner.apply_pending(conn)
            conn.execute("INSERT INTO a (id) VALUES (1)")
            conn.commit()
        finally:
            conn.close()

        stat_before = self.db_path.stat()
        size_before, mtime_before = stat_before.st_size, stat_before.st_mtime_ns
        hash_before = compute_checksum(self.db_path)

        conn2 = sqlite3.connect(str(self.db_path))
        try:
            diagnosis = runner.diagnose(conn2)
            self.assertEqual(diagnosis["already_applied"], ["20260101_00_create_a.sql"])
        finally:
            conn2.close()

        stat_after = self.db_path.stat()
        self.assertEqual(stat_after.st_size, size_before)
        self.assertEqual(stat_after.st_mtime_ns, mtime_before)
        self.assertEqual(compute_checksum(self.db_path), hash_before)

    def test_checksum_mismatch_produces_zero_writes(self):

        path = self.migrations_dir / "20260101_00_create_a.sql"
        _write_migration(
            path, "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        runner = self._runner()
        conn = sqlite3.connect(str(self.db_path))
        try:
            runner.apply_pending(conn)
        finally:
            conn.close()

        _write_migration(
            path,
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY, extra TEXT);\nCOMMIT;\n",
        )

        hash_before = compute_checksum(self.db_path)
        stat_before = self.db_path.stat()

        conn2 = sqlite3.connect(str(self.db_path))
        try:
            with self.assertRaises(ChecksumMismatchError):
                runner.diagnose(conn2)
        finally:
            conn2.close()

        self.assertEqual(compute_checksum(self.db_path), hash_before)
        self.assertEqual(self.db_path.stat().st_size, stat_before.st_size)

    def test_history_table_only_created_on_actual_mutation(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        runner = self._runner()
        conn = sqlite3.connect(str(self.db_path))
        try:
            self.assertNotIn("schema_migrations", self._table_names(conn))

            runner.diagnose(conn)
            self.assertNotIn("schema_migrations", self._table_names(conn))

            runner.apply_pending(conn)
            self.assertIn("schema_migrations", self._table_names(conn))
        finally:
            conn.close()

    def test_backup_failure_leaves_bookkeeping_table_absent(self):

        _write_migration(
            self.migrations_dir / "20260101_00_create_a.sql",
            "BEGIN;\nCREATE TABLE a (id INTEGER PRIMARY KEY);\nCOMMIT;\n",
        )

        # 기존 데이터가 있는 DB로 시작(신규 설치 경로를 타지 않도록).
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        from unittest import mock

        with mock.patch.object(
            MigrationRunner, "create_backup",
            side_effect=RuntimeError("시뮬레이션된 백업 실패"),
        ):
            with self.assertRaises(RuntimeError):
                # 승인된 pending 적용 경로에서만 실제 백업이 시도된다
                # (2026-08-05 Gate E 승인 게이트 반영) — 이 시나리오는
                # "승인 후 백업 실패"를 재현해야 하므로 명시적으로
                # 승인한다.
                bootstrap_environment(
                    db_path=self.db_path, migrations_dir=self.migrations_dir,
                    backups_dir=self.backups_dir,
                    approved_migration_files=["20260101_00_create_a.sql"],
                )

        verify_conn = sqlite3.connect(str(self.db_path))
        try:
            self.assertNotIn(
                "schema_migrations", self._table_names(verify_conn),
            )
        finally:
            verify_conn.close()

    def test_backup_path_equal_to_source_is_blocked(self):

        from unittest import mock
        from app.database.migration_runner import MigrationRunnerError

        # backups_dir가 실수로 원본 DB와 같은 디렉터리를 가리키고,
        # label/timestamp 조합이 우연히 원본과 같은 파일명을 만들어내는
        # 상황을 재현한다 — datetime.now()를 고정해 계산될 백업 파일명이
        # db_path와 정확히 같아지도록 만든다.
        same_dir = Path(self.tmpdir.name)
        forced_db_path = same_dir / "homez_pre_test_FIXEDTS.db"
        sqlite3.connect(str(forced_db_path)).close()

        runner = MigrationRunner(forced_db_path, self.migrations_dir)

        with mock.patch(
            "app.database.migration_runner.datetime",
        ) as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "FIXEDTS"

            with self.assertRaises(MigrationRunnerError):
                runner.create_backup(same_dir, "test")

        forced_db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
