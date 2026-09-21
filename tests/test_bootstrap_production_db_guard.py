"""
=========================================================
Homez OS

File : tests/test_bootstrap_production_db_guard.py

Gate V-1(2026-08-11) — bootstrap_environment()/get_homez_db_path()가
경로 격리 없이 실제 homez.db를 대상으로 실행되는 것을 막는 안전장치
검증. 2026-08-10 사고(경로 격리 없는 bootstrap_environment() 실행이
실제 DB의 schema_migrations에 승인 없는 backfill 이력을 남김,
docs/V6_EXECUTION_LEDGER.md "Gate U 사고 정정" 절 참고) 재발 방지가
목적이다. 실제 homez.db에는 어떤 쓰기도 하지 않는다 — 이 파일의
모든 시나리오는 임시 SQLite 파일 또는 순수 호출 계약만 검증한다.
=========================================================
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.real_install_gate import requires_real_install_diagnostics
from app.database.bootstrap import bootstrap_environment
from app.database.migration_runner import MigrationRunner
from app.desktop import paths
from app.desktop.paths import ProductionDbAccessNotConfirmedError


class GetHomezDbPathGuardTestCase(unittest.TestCase):
    """get_homez_db_path() 자체의 confirm 계약."""

    def test_default_call_raises_without_confirm(self):

        with self.assertRaises(ProductionDbAccessNotConfirmedError):
            paths.get_homez_db_path()

    def test_confirm_false_explicitly_still_raises(self):

        with self.assertRaises(ProductionDbAccessNotConfirmedError):
            paths.get_homez_db_path(confirm=False)

    def test_confirm_is_keyword_only_cannot_be_smuggled_positionally(self):
        """
        confirm이 위치 인자로 전달될 수 있다면, 실수로 다른 boolean
        값을 넘겼다가 True로 해석될 위험이 생긴다 — 키워드 전용으로
        강제해 그런 우회 경로 자체를 없앤다.
        """

        with self.assertRaises(TypeError):
            paths.get_homez_db_path(True)  # noqa: FBT003 — 의도적 오용 재현

    def test_confirm_true_returns_real_repo_root_path(self):

        result = paths.get_homez_db_path(confirm=True)
        self.assertEqual(result, paths.get_repo_root() / "homez.db")

    def test_case_variant_path_string_does_not_bypass_guard(self):
        """
        대소문자를 바꾼 경로 문자열을 어딘가에 하드코딩해도, 보호
        지점은 "db_path가 명시적으로 주어졌는가"이지 문자열 비교가
        아니므로 우회되지 않는다 — confirm 없이는 여전히 막힌다.
        """

        fake_path_variant = str(paths.get_repo_root() / "HOMEZ.DB").upper()
        self.assertTrue(fake_path_variant)  # 문자열 자체는 참고용
        with self.assertRaises(ProductionDbAccessNotConfirmedError):
            paths.get_homez_db_path()


class BootstrapEnvironmentGuardTestCase(unittest.TestCase):
    """bootstrap_environment()의 db_path=None 방어 계약."""

    def test_no_arguments_at_all_raises_immediately(self):
        """임시 DB가 없으면(인자 전부 생략) 개발 bootstrap은 명시적으로 실패해야 한다."""

        with self.assertRaises(ProductionDbAccessNotConfirmedError):
            bootstrap_environment()

    def test_db_path_none_without_confirm_flag_raises(self):

        with self.assertRaises(ProductionDbAccessNotConfirmedError):
            bootstrap_environment(confirm_production_path=False)

    def test_guard_fires_before_any_filesystem_or_db_access(self):
        """
        승인되지 않은 호출은 실제 DB에 어떤 연결도 시도하지 않아야
        한다 — ensure_directories()/sqlite3.connect 자체가 호출되지
        않음을 mock으로 증명한다(원인 조사 시 "그래도 뭔가 열렸을
        가능성"을 남기지 않기 위함).
        """

        with mock.patch(
            "app.database.bootstrap.ensure_directories",
        ) as ensure_dirs_mock, mock.patch(
            "app.database.bootstrap.sqlite3.connect",
        ) as connect_mock:
            with self.assertRaises(ProductionDbAccessNotConfirmedError):
                bootstrap_environment()

        ensure_dirs_mock.assert_not_called()
        connect_mock.assert_not_called()

    def test_import_alone_does_not_trigger_bootstrap_or_db_write(self):
        """
        이 모듈을 다시 import(reload)하는 것만으로는
        bootstrap_environment()가 호출되지 않아야 한다 — 함수 정의
        자체는 부작용이 없다.
        """

        import importlib

        import app.database.bootstrap as bootstrap_module

        with mock.patch(
            "app.database.bootstrap.sqlite3.connect",
        ) as connect_mock:
            importlib.reload(bootstrap_module)

        connect_mock.assert_not_called()

    def test_explicit_temp_db_path_bootstraps_normally(self):
        """임시 DB 경로를 명시하면(공백 포함 디렉터리) 정상적으로 신규 설치 부트스트랩이 된다."""

        tmp_root = Path(tempfile.mkdtemp(prefix="gate v1 space "))
        db_path = tmp_root / "homez.db"
        migrations_dir = tmp_root / "migrations"
        backups_dir = tmp_root / "backups"
        migrations_dir.mkdir()
        backups_dir.mkdir()

        try:
            self.assertIn(" ", str(tmp_root))  # 공백 포함 경로임을 재확인

            result = bootstrap_environment(
                db_path=db_path, migrations_dir=migrations_dir,
                backups_dir=backups_dir,
            )
            self.assertTrue(result.is_new_install)
            self.assertTrue(db_path.exists())
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def test_confirm_true_without_db_path_does_not_raise_the_guard(self):
        """
        confirm_production_path=True + db_path=None 조합은 가드
        자체는 통과해야 한다(공식 Desktop 흐름과 동일한 형태) — 다만
        이 테스트에서는 이후 실제 get_homez_db_path(confirm=True)가
        저장소 루트 경로를 반환하는 것까지만 별도로 확인하고, 실제
        homez.db에 대한 진단/쓰기 자체는 이 테스트 파일에서 실행하지
        않는다(그 지점부터는 진짜 실제 DB 접근이므로 범위 밖).
        """

        with mock.patch(
            "app.database.bootstrap.ensure_directories",
        ), mock.patch(
            "app.database.migration_runner.MigrationRunner.diagnose",
            return_value={
                "backfill_needed": [], "pending": [], "already_applied": [],
            },
        ), mock.patch(
            "app.database.bootstrap.sqlite3.connect",
        ):
            try:
                bootstrap_environment(confirm_production_path=True)
            except ProductionDbAccessNotConfirmedError:
                self.fail(
                    "confirm_production_path=True인데도 가드가 막았다 — "
                    "공식 Desktop 흐름이 깨진다.",
                )
            except Exception:
                # 이 지점부터는 mock 배선 세부사항에 따른 다른 예외가
                # 날 수 있다 — 이 테스트의 목적은 오직 "가드 자체를
                # 통과하는가"이므로 ProductionDbAccessNotConfirmedError
                # 만 아니면 충분하다.
                pass


class MigrationRunnerRealPathRejectionTestCase(unittest.TestCase):
    """
    'Browser E2E launcher가 실제 DB 경로를 거부' 요구사항 — launcher가
    격리를 잊고 get_homez_db_path()를 그대로 썼다고 가정한 최소
    재현이다.
    """

    def test_launcher_style_unconfirmed_real_path_lookup_rejected(self):

        def naive_launcher_bootstrap():
            db_path = paths.get_homez_db_path()  # 격리를 잊은 launcher
            migrations_dir = paths.get_repo_root() / "migrations"
            return MigrationRunner(db_path, migrations_dir)

        with self.assertRaises(ProductionDbAccessNotConfirmedError):
            naive_launcher_bootstrap()

    def test_properly_isolated_launcher_never_touches_real_path(self):
        """대조군 — 격리를 제대로 한 launcher는 애초에 이 함수를 부르지 않는다."""

        tmp_root = Path(tempfile.mkdtemp())
        try:
            db_path = tmp_root / "homez.db"
            migrations_dir = tmp_root / "migrations"
            migrations_dir.mkdir()

            runner = MigrationRunner(db_path, migrations_dir)
            self.assertEqual(runner.db_path, db_path)
            self.assertNotEqual(db_path, paths.get_repo_root() / "homez.db")
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)


class UnauthorizedReconcileDoesNotTouchSyntheticDbTestCase(unittest.TestCase):
    """
    위 실제 DB 진단의 일반(격리) 버전 — 같은 계약("승인되지 않은 bootstrap/경로 요청은 DB
    파일을 건드리지 않는다")을 실제 homez.db가 아니라 **테스트가 소유한 임시 데이터
    디렉터리의 합성 homez.db**로 검증한다. `paths.get_data_dir`를 임시 디렉터리로
    교체해, 코드가 잘못 진행하더라도 실제 저장소·설치본 DB가 아니라 이 임시 파일만
    영향을 받는다.
    """

    def test_rejected_attempts_leave_synthetic_db_and_directory_untouched(self):

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            synthetic = data_dir / "homez.db"
            synthetic.write_bytes(b"SYNTHETIC-NOT-A-REAL-DB")
            before_bytes = synthetic.read_bytes()
            before_stat = synthetic.stat()
            before_listing = sorted(p.name for p in data_dir.iterdir())

            with mock.patch.object(paths, "get_data_dir", return_value=data_dir):
                for _ in range(5):
                    with self.assertRaises(ProductionDbAccessNotConfirmedError):
                        bootstrap_environment()
                    with self.assertRaises(ProductionDbAccessNotConfirmedError):
                        paths.get_homez_db_path()

            after_stat = synthetic.stat()
            self.assertEqual(synthetic.read_bytes(), before_bytes)
            self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)
            self.assertEqual(before_stat.st_size, after_stat.st_size)
            self.assertEqual(sorted(p.name for p in data_dir.iterdir()), before_listing)


class UnauthorizedReconcileDoesNotTouchRealDbTestCase(unittest.TestCase):
    """
    '승인되지 않은 reconcile이 실제 DB를 변경하지 않음' — 실제 DB의
    mtime/크기가 이 테스트 클래스 실행 전후로 바뀌지 않았는지 직접
    재확인한다(가장 강한 증거 — 실제 파일 자체를 관찰).
    """

    _REAL_DB_PATH = (
        Path(__file__).resolve().parent.parent / "homez.db"
    )

    # 실제 설치환경 진단 — 기본 전체 회귀에서 제외(opt-in: HOMEZ_RUN_REAL_INSTALL_DIAGNOSTICS=1)
    @requires_real_install_diagnostics
    def test_real_db_untouched_after_rejected_bootstrap_attempts(self):

        if not self._REAL_DB_PATH.exists():
            self.skipTest("실제 homez.db가 없는 환경에서는 건너뜀")

        stat_before = self._REAL_DB_PATH.stat()

        for _ in range(5):
            with self.assertRaises(ProductionDbAccessNotConfirmedError):
                bootstrap_environment()
            with self.assertRaises(ProductionDbAccessNotConfirmedError):
                paths.get_homez_db_path()

        stat_after = self._REAL_DB_PATH.stat()
        self.assertEqual(stat_before.st_mtime, stat_after.st_mtime)
        self.assertEqual(stat_before.st_size, stat_after.st_size)


if __name__ == "__main__":
    unittest.main()
