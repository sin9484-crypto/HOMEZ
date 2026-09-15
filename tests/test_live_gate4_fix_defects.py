"""
=========================================================
Homez OS

File : tests/test_live_gate4_fix_defects.py

V7 Live Gate 4 재작업(2026-08-16) — CTO가 명시 승인한 릴리스 차단 결함
3건에 대한 회귀 테스트.

  1. DB 경로 이원화(app/core/config.py 기본값 vs app/desktop/paths.py
     공식 경로) — 세션 엔진과 bootstrap이 항상 같은 절대경로를
     가리키는지, CWD와 무관한지, 환경변수 override가 유지되는지,
     경로가 갈라지면 fail-fast하는지 검증한다.
  2. SUPER_ADMIN 등 기준 역할·권한 미시딩 — `active` NOT NULL 결함이
     고쳐졌는지, `initialize_seed()`가 공식 bootstrap 흐름에 연결됐는지,
     멱등성·단일 Transaction rollback·기존 데이터 불변·회사/사용자
     미생성을 검증한다.
  3. 손상 백업 검증 예외처리 — 손상 SQLite/비-SQLite 파일/무결성
     실패/필수 테이블 누락이 구조화된 결과로 반환되고 예외가 밖으로
     새지 않는지, 원본 파일이 절대 변경되지 않는지 검증한다.

이 파일 전체가 임시 디렉터리만 사용한다 — 실제 homez.db는 어디에서도
열거나 참조하지 않는다.
=========================================================
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.core.first_admin_setup import (
    FirstAdminSetupStatus,
    atomic_create_first_admin,
)
from app.database.bootstrap import bootstrap_environment
from app.database.seed import (
    initialize_seed,
    seed_environment,
    seed_permissions,
    seed_roles,
)
from app.database.session import get_engine_db_path
from app.desktop import paths as desktop_paths
from app.domains.backup.service import sha256_of_file
from app.domains.permission.model import Permission
from app.domains.restore.service import RestoreService
from app.domains.role.model import Role

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATIONS_DIR = REPO_ROOT / "migrations"


# =========================================================
# 결함 1 — DB 경로 단일화
# =========================================================

class DatabaseUrlDefaultTestCase(unittest.TestCase):
    """app/core/config.py::Settings.DATABASE_URL 기본값 계산."""

    def test_default_matches_official_path_when_no_override(self):
        """
        .env/OS 환경변수 override가 전혀 없을 때, DATABASE_URL 기본값이
        app.desktop.paths.get_homez_db_path()가 계산하는 절대경로와
        정확히 같은 파일을 가리켜야 한다(세션 엔진 ≡ bootstrap 경로).
        """

        from app.core.config import Settings

        settings = Settings(_env_file=None)

        expected_path = desktop_paths.get_homez_db_path(confirm=True)

        self.assertTrue(settings.DATABASE_URL.startswith("sqlite:///"))
        resolved = Path(settings.DATABASE_URL[len("sqlite:///"):]).resolve()

        self.assertEqual(resolved, expected_path.resolve())

    def test_default_is_absolute_and_cwd_independent(self):
        """
        결함 1 재현 조건 그대로: 프로세스 CWD를 저장소 밖 임의의 임시
        디렉터리로 옮겨도(=시작메뉴/바탕화면 바로가기로 실행해 CWD가
        설치 폴더가 되는 것과 동일한 조건) 기본값이 달라지면 안 된다.
        """

        from app.core.config import Settings

        expected_path = desktop_paths.get_homez_db_path(confirm=True).resolve()

        tmp_cwd = Path(tempfile.mkdtemp(prefix="homez_gate4_cwd_"))
        original_cwd = Path.cwd()

        try:
            os.chdir(tmp_cwd)

            settings = Settings(_env_file=None)
            resolved = Path(
                settings.DATABASE_URL[len("sqlite:///"):],
            ).resolve()

            self.assertEqual(resolved, expected_path)

            # 이 CWD 안에 의도치 않은 homez.db가 새로 생기지 않아야 한다
            # — Settings()는 경로 문자열만 계산할 뿐 파일을 만들지 않는다.
            self.assertFalse((tmp_cwd / "homez.db").exists())

        finally:
            os.chdir(original_cwd)
            shutil.rmtree(tmp_cwd, ignore_errors=True)

    def test_env_var_override_still_wins(self):
        """DATABASE_URL 환경변수 override는 기본값 계산보다 우선해야 한다."""

        from app.core.config import Settings

        override = "sqlite:///./explicit_override_for_test.db"

        with mock.patch.dict(os.environ, {"DATABASE_URL": override}):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.DATABASE_URL, override)

    def test_frozen_mode_resolves_under_localappdata(self):
        """
        PyInstaller 패키징 환경(sys.frozen=True) 시뮬레이션 — 기본값이
        %LOCALAPPDATA%\\HOMEZ\\data\\homez.db를 가리켜야 한다.
        """

        from app.core.config import Settings

        tmp_local_appdata = Path(tempfile.mkdtemp(prefix="homez_gate4_lad_"))

        try:
            with mock.patch.object(desktop_paths.sys, "frozen", True, create=True):
                with mock.patch.dict(
                    os.environ, {"LOCALAPPDATA": str(tmp_local_appdata)},
                ):
                    settings = Settings(_env_file=None)

            resolved = Path(settings.DATABASE_URL[len("sqlite:///"):])

            self.assertEqual(
                resolved,
                tmp_local_appdata / "HOMEZ" / "data" / "homez.db",
            )

        finally:
            shutil.rmtree(tmp_local_appdata, ignore_errors=True)


class EngineDbPathHelperTestCase(unittest.TestCase):
    """app/database/session.py::get_engine_db_path()."""

    def test_resolves_sqlite_file_engine(self):

        tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate4_engine_"))

        try:
            db_path = tmp_dir / "sub" / "probe.db"
            engine = create_engine(f"sqlite:///{db_path}")

            with mock.patch("app.database.session.engine", engine):
                resolved = get_engine_db_path()

            self.assertEqual(resolved, db_path.resolve())

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_in_memory_engine_returns_none(self):

        engine = create_engine("sqlite:///:memory:")

        with mock.patch("app.database.session.engine", engine):
            self.assertIsNone(get_engine_db_path())


class BootstrapSessionSamePathTestCase(unittest.TestCase):
    """bootstrap과 session engine이 실제로 같은 DB를 가리키는지."""

    def test_bootstrap_and_settings_default_agree_on_same_path(self):

        from app.core.config import Settings

        settings = Settings(_env_file=None)
        session_engine_path = Path(
            settings.DATABASE_URL[len("sqlite:///"):],
        ).resolve()

        bootstrap_path = desktop_paths.get_homez_db_path(confirm=True).resolve()

        self.assertEqual(session_engine_path, bootstrap_path)


class DesktopMainDbPathFailFastTestCase(unittest.TestCase):
    """
    app/desktop/main.py::run()의 새 fail-fast 경로 일치 확인 — 두 경로가
    갈라지면 서버를 절대 기동하지 않아야 한다. 실제 homez.db는 이
    클래스 전체에서 전혀 사용하지 않는다(bootstrap_environment 자체도
    mock).
    """

    def setUp(self):

        self._tmp_local_appdata = tempfile.mkdtemp(prefix="homez_gate4_run_")
        self._env_patcher = mock.patch.dict(
            os.environ, {"LOCALAPPDATA": self._tmp_local_appdata},
        )
        self._env_patcher.start()

    def tearDown(self):

        self._env_patcher.stop()
        shutil.rmtree(self._tmp_local_appdata, ignore_errors=True)

    def test_run_aborts_when_bootstrap_and_engine_paths_differ(self):

        import app.database.bootstrap as bootstrap_module
        import app.desktop.main as desktop_main

        noop_result = bootstrap_module.BootstrapResult(is_new_install=False)

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment", return_value=noop_result,
            ):
                with mock.patch.object(
                    desktop_main, "get_homez_db_path",
                    return_value=Path(self._tmp_local_appdata) / "official.db",
                ):
                    with mock.patch.object(
                        desktop_main, "get_engine_db_path",
                        return_value=Path(self._tmp_local_appdata) / "DIFFERENT.db",
                    ):
                        with mock.patch.object(
                            desktop_main, "start_server",
                        ) as start_server_mock:
                            with mock.patch.object(
                                desktop_main, "seed_environment",
                            ) as seed_mock:
                                with mock.patch.object(
                                    desktop_main, "_show_error_message_box",
                                ) as message_box_mock:
                                    exit_code = desktop_main.run()

            start_server_mock.assert_not_called()
            seed_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            message_box_mock.assert_called_once()
            args, _ = message_box_mock.call_args
            self.assertEqual(args[0], desktop_main.ERROR_CODE_DB_PATH_MISMATCH)

    def test_run_aborts_when_seeding_fails(self):

        import app.database.bootstrap as bootstrap_module
        import app.desktop.main as desktop_main

        noop_result = bootstrap_module.BootstrapResult(is_new_install=False)
        # 실제 get_engine_db_path()/get_homez_db_path()는 항상 .resolve()된
        # 값을 반환한다 — mock도 동일하게 이미 resolve()된 경로를 줘야
        # main.py의 `bootstrap_db_path == engine_db_path` 비교가 테스트
        # 환경(tempfile 8.3 단축 경로 등)에서도 정확히 일치한다.
        same_path = (Path(self._tmp_local_appdata) / "official.db").resolve()

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment", return_value=noop_result,
            ):
                with mock.patch.object(
                    desktop_main, "get_homez_db_path", return_value=same_path,
                ):
                    with mock.patch.object(
                        desktop_main, "get_engine_db_path", return_value=same_path,
                    ):
                        with mock.patch.object(
                            desktop_main, "seed_environment",
                            side_effect=RuntimeError("테스트 강제 시딩 실패"),
                        ):
                            with mock.patch.object(
                                desktop_main, "start_server",
                            ) as start_server_mock:
                                with mock.patch.object(
                                    desktop_main, "_show_error_message_box",
                                ) as message_box_mock:
                                    exit_code = desktop_main.run()

            start_server_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            args, _ = message_box_mock.call_args
            self.assertEqual(args[0], desktop_main.ERROR_CODE_SEED_FAILED)
            self.assertNotIn("테스트 강제 시딩 실패", args[0])

    def test_run_aborts_when_channel_policy_seeding_fails(self):
        """
        Audit(2026-08-21, CTO 후속 지시) — 채널 정책 카탈로그 자동
        시딩도 역할·권한 시딩과 동일하게 fail-closed다: 실패하면
        서버를 절대 시작하지 않는다(정책 카탈로그가 비어있는 채로
        서버가 뜨면 모든 채널이 CHANNEL_DATA_REQUIRED로 영구 고정될
        뿐 서버 자체는 뜨는 상태가 되어, 오류를 조용히 숨기게 된다).
        """

        import app.database.bootstrap as bootstrap_module
        import app.desktop.main as desktop_main

        noop_result = bootstrap_module.BootstrapResult(is_new_install=False)
        same_path = (Path(self._tmp_local_appdata) / "official.db").resolve()

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment", return_value=noop_result,
            ):
                with mock.patch.object(
                    desktop_main, "get_homez_db_path", return_value=same_path,
                ):
                    with mock.patch.object(
                        desktop_main, "get_engine_db_path", return_value=same_path,
                    ):
                        with mock.patch.object(
                            desktop_main, "seed_environment",
                            return_value={
                                "roles_added": 0, "permissions_added": 0,
                            },
                        ):
                            with mock.patch.object(
                                desktop_main,
                                "seed_channel_policy_catalog_at_boot",
                                side_effect=RuntimeError(
                                    "테스트 강제 정책 카탈로그 시딩 실패",
                                ),
                            ):
                                with mock.patch.object(
                                    desktop_main, "start_server",
                                ) as start_server_mock:
                                    with mock.patch.object(
                                        desktop_main, "_show_error_message_box",
                                    ) as message_box_mock:
                                        exit_code = desktop_main.run()

            start_server_mock.assert_not_called()
            self.assertEqual(exit_code, 1)
            args, _ = message_box_mock.call_args
            self.assertEqual(
                args[0], desktop_main.ERROR_CODE_CHANNEL_POLICY_SEED_FAILED,
            )
            self.assertNotIn("테스트 강제 정책 카탈로그 시딩 실패", args[0])

    def test_run_proceeds_to_start_server_when_paths_match_and_seed_succeeds(self):

        import app.database.bootstrap as bootstrap_module
        import app.desktop.main as desktop_main

        noop_result = bootstrap_module.BootstrapResult(is_new_install=False)
        # 실제 get_engine_db_path()/get_homez_db_path()는 항상 .resolve()된
        # 값을 반환한다 — mock도 동일하게 이미 resolve()된 경로를 줘야
        # main.py의 `bootstrap_db_path == engine_db_path` 비교가 테스트
        # 환경(tempfile 8.3 단축 경로 등)에서도 정확히 일치한다.
        same_path = (Path(self._tmp_local_appdata) / "official.db").resolve()
        fake_handle = mock.Mock()
        fake_handle.port = 1234

        with mock.patch.object(
            desktop_main, "SingleInstanceGuard",
        ) as guard_cls:
            guard_instance = guard_cls.return_value
            guard_instance.acquire.return_value = True

            with mock.patch.object(
                desktop_main, "bootstrap_environment", return_value=noop_result,
            ):
                with mock.patch.object(
                    desktop_main, "get_homez_db_path", return_value=same_path,
                ):
                    with mock.patch.object(
                        desktop_main, "get_engine_db_path", return_value=same_path,
                    ):
                        with mock.patch.object(
                            desktop_main, "seed_environment",
                            return_value={
                                "roles_added": 5, "permissions_added": 36,
                            },
                        ) as seed_mock:
                            # Audit(2026-08-21, CTO 후속 지시) — 채널
                            # 정책 카탈로그도 이제 seed_environment
                            # 바로 다음 단계에서 자동 시딩된다. 실제
                            # 파일(same_path)에는 channel_policy_rules
                            # 테이블이 없으므로(Migration 미실행) mock
                            # 해야 이 단계가 실패하지 않는다.
                            with mock.patch.object(
                                desktop_main,
                                "seed_channel_policy_catalog_at_boot",
                                return_value={"rules_seeded": 10},
                            ) as policy_seed_mock:
                                with mock.patch.object(
                                    desktop_main, "start_server",
                                    return_value=fake_handle,
                                ) as start_server_mock:
                                    with mock.patch(
                                        "webview.create_window",
                                        side_effect=RuntimeError(
                                            "창 생성은 이 테스트 범위 밖",
                                        ),
                                    ):
                                        with mock.patch.object(
                                            desktop_main,
                                            "_show_error_message_box",
                                        ):
                                            desktop_main.run()

            seed_mock.assert_called_once_with(same_path)
            policy_seed_mock.assert_called_once_with(same_path)
            start_server_mock.assert_called_once()


# =========================================================
# 결함 2 — SUPER_ADMIN 등 기준 역할·Permission 시딩
# =========================================================

class SeedEnvironmentTestCase(unittest.TestCase):
    """
    app/database/seed.py — 실제 Migration SQL로 만든 임시 DB(=운영
    스키마와 동일, roles.active BOOLEAN NOT NULL 포함)를 대상으로
    검증한다. 실제 homez.db는 사용하지 않는다.
    """

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate4_seed_"))
        self.db_path = self.tmp_dir / "data" / "homez.db"
        self.backups_dir = self.tmp_dir / "backups"

        bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )

        self.assertTrue(self.db_path.exists())

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _session(self):

        engine = create_engine(f"sqlite:///{self.db_path}")
        return sessionmaker(bind=engine)(), engine

    def test_empty_new_db_gets_super_admin_role(self):

        result = seed_environment(self.db_path)

        self.assertGreater(result["roles_added"], 0)
        self.assertGreater(result["permissions_added"], 0)

        db, engine = self._session()
        try:
            role = db.query(Role).filter(Role.code == "SUPER_ADMIN").first()
            self.assertIsNotNone(role)
            self.assertIs(role.active, True)
        finally:
            db.close()
            engine.dispose()

    def test_active_column_not_null_no_longer_raises(self):
        """이전 결함: active를 채우지 않아 IntegrityError로 실패했었다."""

        db, engine = self._session()
        try:
            added = seed_roles(db)
            db.commit()  # 결함이 있었다면 여기서 IntegrityError.
            self.assertGreater(added, 0)
        finally:
            db.close()
            engine.dispose()

    def test_idempotent_repeat_run_creates_no_duplicates(self):

        first = seed_environment(self.db_path)
        second = seed_environment(self.db_path)

        self.assertGreater(first["roles_added"], 0)
        self.assertEqual(second["roles_added"], 0)
        self.assertEqual(second["permissions_added"], 0)

        db, engine = self._session()
        try:
            role_count = db.query(Role).filter(
                Role.code == "SUPER_ADMIN",
            ).count()
            self.assertEqual(role_count, 1)
        finally:
            db.close()
            engine.dispose()

    def test_partial_failure_rolls_back_entire_transaction(self):
        """
        seed_permissions 도중 실패하면, 그 이전에 add()된 role도 전혀
        commit되지 않아야 한다(단일 Transaction 요구사항).
        """

        db, engine = self._session()
        try:
            pre_existing_roles = db.query(Role).count()
        finally:
            db.close()
            engine.dispose()

        self.assertEqual(pre_existing_roles, 0)

        with mock.patch(
            "app.database.seed.seed_permissions",
            side_effect=RuntimeError("테스트 강제 실패"),
        ):
            with self.assertRaises(RuntimeError):
                seed_environment(self.db_path)

        db, engine = self._session()
        try:
            self.assertEqual(db.query(Role).count(), 0)
        finally:
            db.close()
            engine.dispose()

    def test_existing_roles_and_permissions_untouched(self):

        db, engine = self._session()
        try:
            db.add(Role(name="Custom Role", code="CUSTOM_ROLE", active=False))
            db.add(Permission(name="Custom Permission", code="CUSTOM_PERM"))
            db.commit()
        finally:
            db.close()
            engine.dispose()

        seed_environment(self.db_path)

        db, engine = self._session()
        try:
            custom_role = db.query(Role).filter(
                Role.code == "CUSTOM_ROLE",
            ).first()
            custom_permission = db.query(Permission).filter(
                Permission.code == "CUSTOM_PERM",
            ).first()

            self.assertIsNotNone(custom_role)
            self.assertIs(custom_role.active, False)  # 덮어쓰지 않음.
            self.assertIsNotNone(custom_permission)
        finally:
            db.close()
            engine.dispose()

    def test_no_company_or_user_auto_created(self):

        seed_environment(self.db_path)

        raw = sqlite3.connect(str(self.db_path))
        try:
            companies = raw.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            users = raw.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            raw.close()

        self.assertEqual(companies, 0)
        self.assertEqual(users, 0)

    def test_initialize_seed_not_duplicated_in_source(self):
        """
        결함 2 부수 발견: initialize_seed()가 seed.py에 두 번 정의돼
        있었다(뒤 정의가 이김) — 정리 후 하나만 남아야 한다.
        """

        import inspect

        import app.database.seed as seed_module

        source = inspect.getsource(seed_module)
        self.assertEqual(source.count("def initialize_seed"), 1)


class ChannelPolicyCatalogBootSeedTestCase(unittest.TestCase):
    """
    Audit(2026-08-21, CTO 후속 지시) —
    `app.domains.channel_policy.service.seed_channel_policy_catalog_
    at_boot()` — 역할·권한(seed_environment)과 동일한 계약(멱등,
    실패 시 전체 rollback)을 실제 Migration으로 만든 DB(channel_
    policy_rules 테이블 포함)로 검증한다.
    """

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate4_cpseed_"))
        self.db_path = self.tmp_dir / "data" / "homez.db"
        self.backups_dir = self.tmp_dir / "backups"

        bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )

        self.assertTrue(self.db_path.exists())

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _session(self):

        engine = create_engine(f"sqlite:///{self.db_path}")
        return sessionmaker(bind=engine)(), engine

    def test_empty_new_db_gets_full_rule_catalog(self):

        from app.domains.channel_policy.model import ChannelPolicyRule
        from app.domains.channel_policy.rule_catalog import (
            CHANNEL_POLICY_RULE_CATALOG,
        )
        from app.domains.channel_policy.service import (
            seed_channel_policy_catalog_at_boot,
        )

        result = seed_channel_policy_catalog_at_boot(self.db_path)
        self.assertEqual(
            result["rules_seeded"], len(CHANNEL_POLICY_RULE_CATALOG),
        )

        db, engine = self._session()
        try:
            active_coupang = db.query(ChannelPolicyRule).filter(
                ChannelPolicyRule.channel == "COUPANG",
                ChannelPolicyRule.active.is_(True),
            ).count()
            self.assertGreater(active_coupang, 0)
        finally:
            db.close()
            engine.dispose()

    def test_idempotent_repeat_run_creates_no_duplicate_rows(self):

        from app.domains.channel_policy.model import ChannelPolicyRule
        from app.domains.channel_policy.service import (
            seed_channel_policy_catalog_at_boot,
        )

        seed_channel_policy_catalog_at_boot(self.db_path)
        seed_channel_policy_catalog_at_boot(self.db_path)

        db, engine = self._session()
        try:
            total = db.query(ChannelPolicyRule).count()
            distinct = db.query(
                ChannelPolicyRule.channel, ChannelPolicyRule.rule_code,
            ).distinct().count()
            self.assertEqual(total, distinct)
        finally:
            db.close()
            engine.dispose()

    def test_partial_failure_rolls_back_entire_transaction(self):
        """
        카탈로그 중간 항목에서 실패해도(즉 일부 행은 이미 flush된
        뒤라도) 최종 commit 전 예외면 아무것도 남지 않아야 한다 —
        seed_environment()의 동일 계약과 같은 원칙.
        """

        from app.domains.channel_policy.model import ChannelPolicyRule
        from app.domains.channel_policy import repository as cp_repo_module
        from app.domains.channel_policy import service as cp_service_module

        original_add = cp_repo_module.ChannelPolicyRepository.add_rule_no_commit
        call_count = {"n": 0}

        def _flaky_add(self_repo, rule):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("테스트 강제 실패(3번째 규칙)")
            return original_add(self_repo, rule)

        with mock.patch.object(
            cp_repo_module.ChannelPolicyRepository, "add_rule_no_commit",
            _flaky_add,
        ):
            with self.assertRaises(RuntimeError):
                cp_service_module.seed_channel_policy_catalog_at_boot(
                    self.db_path,
                )

        self.assertGreaterEqual(call_count["n"], 3)

        db, engine = self._session()
        try:
            self.assertEqual(db.query(ChannelPolicyRule).count(), 0)
        finally:
            db.close()
            engine.dispose()


class FirstAdminAfterSeedIntegrationTestCase(unittest.TestCase):
    """
    결함 1 + 결함 2를 함께 검증하는 통합 테스트 — 완전히 빈 신규 DB에
    실제 Migration을 적용하고, 공식 시딩을 실행한 뒤, 공식 최초 관리자
    생성 함수(app.core.first_admin_setup.atomic_create_first_admin)로
    SUPER_ADMIN 계정을 실제로 만들 수 있는지 확인한다(테스트 자격증명만
    사용, 실제 homez.db 무관).
    """

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate4_admin_"))
        self.db_path = self.tmp_dir / "data" / "homez.db"
        self.backups_dir = self.tmp_dir / "backups"

        bootstrap_environment(
            db_path=self.db_path,
            migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        seed_environment(self.db_path)

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_first_admin_with_seeded_super_admin_role(self):

        engine = create_engine(f"sqlite:///{self.db_path}")
        db = sessionmaker(bind=engine)()

        try:
            super_admin_role = db.query(Role).filter(
                Role.code == "SUPER_ADMIN",
            ).first()
            self.assertIsNotNone(
                super_admin_role,
                "SUPER_ADMIN 역할이 시딩되지 않으면 최초 관리자 설정 "
                "화면 자체가 성립할 수 없다.",
            )
            role_id = super_admin_role.id
        finally:
            db.close()
            engine.dispose()

        result = atomic_create_first_admin(
            str(self.db_path),
            username="gate4_test_admin",
            email="gate4_test_admin@example.invalid",
            password_hash="not-a-real-hash-test-only",
            role_id=role_id,
            company_name="Gate4 Test Company",
            name="Gate4 Tester",
        )

        self.assertEqual(result.status, FirstAdminSetupStatus.SUCCESS)
        self.assertIsNotNone(result.user_id)
        self.assertIsNotNone(result.company_id)


# =========================================================
# 결함 3 — 손상 백업 검증 예외처리
# =========================================================

class ValidateBackupFileTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_gate4_restore_"))

        from app.database.base import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine)()
        self.credential_store = InMemoryCredentialStore()
        self.service = RestoreService(self.db, self.credential_store)

    def tearDown(self):

        self.db.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_valid_backup(self, name: str = "valid.db", *, rows: int = 0) -> Path:
        """
        V7 Live Gate 4 복원 결함 수정(2026-08-17) — 코디네이터 지시로
        `RestoreService.REQUIRED_CORE_TABLES` 검증이 재활성화되면서,
        "유효한 백업"으로 인정받으려면 이제 `users`/`roles`까지 갖춰야
        한다(기존에는 `schema_migrations`/`companies`만 있으면
        충분했다). 이 테스트가 검증하려는 것은 "필수 테이블을 갖춘
        정상 백업이 승인되는지"이므로, 기존 helper 자체를 그 최신
        계약에 맞게 갱신한다.
        """

        path = self.tmp_dir / name
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                "CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY)",
            )
            conn.execute(
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, blob TEXT)",
            )
            conn.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY)",
            )
            conn.execute(
                "CREATE TABLE roles (id INTEGER PRIMARY KEY)",
            )
            for i in range(rows):
                # 페이지가 여러 개로 나뉘도록 충분히 큰 텍스트를 넣는다
                # (기본 page_size=4096) — 손상 재현 테스트가 헤더가 아닌
                # 실제 데이터 페이지를 잘라내도 integrity_check가 이를
                # 안정적으로 잡아내게 하기 위함.
                conn.execute(
                    "INSERT INTO companies (blob) VALUES (?)",
                    ("x" * 500 + str(i),),
                )
            conn.commit()
        finally:
            conn.close()
        return path

    def test_valid_backup_is_restorable(self):

        path = self._make_valid_backup()
        before_hash = sha256_of_file(path)

        result = self.service.validate_backup_file(path)

        self.assertTrue(result["file_exists"])
        self.assertTrue(result["sha256_matches"])
        self.assertEqual(result["integrity_check_result"], "ok")
        self.assertTrue(result["restorable"])
        self.assertIsNone(result["reason"])

        # 검증은 읽기 전용이어야 한다 — 원본 불변.
        self.assertEqual(sha256_of_file(path), before_hash)

    def test_missing_file_returns_structured_result(self):

        path = self.tmp_dir / "does_not_exist.db"

        result = self.service.validate_backup_file(path)

        self.assertFalse(result["file_exists"])
        self.assertFalse(result["restorable"])
        self.assertIsNotNone(result["reason"])

    def test_non_sqlite_file_does_not_raise(self):
        """이전 결함: sqlite3.DatabaseError가 그대로 전파돼 500이 났었다."""

        path = self.tmp_dir / "not_a_database.db"
        path.write_bytes(os.urandom(4096))
        before_hash = sha256_of_file(path)

        try:
            result = self.service.validate_backup_file(path)
        except Exception as exc:  # noqa: BLE001
            self.fail(
                f"validate_backup_file()이 예외를 던지면 안 된다: {exc!r}",
            )

        self.assertTrue(result["file_exists"])
        self.assertFalse(result["restorable"])
        self.assertIsNotNone(result["reason"])
        self.assertIsNone(result["integrity_check_result"])

        # 내부 경로/SQL이 사용자 메시지에 노출되지 않아야 한다.
        self.assertNotIn(str(path), result["reason"])
        self.assertNotIn("PRAGMA", result["reason"])
        self.assertNotIn("sqlite3", result["reason"])

        # 원본 파일 불변.
        self.assertEqual(sha256_of_file(path), before_hash)

    def test_corrupted_sqlite_fails_integrity_check_without_raising(self):

        path = self._make_valid_backup("corrupt.db", rows=200)
        original_size = path.stat().st_size

        # 유효한 SQLite 헤더(첫 페이지)는 그대로 둔 채 파일 뒷부분을
        # 잘라내(마지막 데이터 페이지 일부를 없앰) "database disk image
        # is malformed"를 유도한다 — connect() 자체는 성공하고
        # integrity_check가 실패하는 경로를 재현한다. 여러 페이지가
        # 있어야 truncate가 스키마 페이지 자체를 날리지 않는다.
        self.assertGreater(
            original_size, 8192, "테스트 전제: 최소 2페이지 이상 필요",
        )

        with open(path, "r+b") as f:
            f.truncate(int(original_size * 0.7))

        before_hash = sha256_of_file(path)

        try:
            result = self.service.validate_backup_file(path)
        except Exception as exc:  # noqa: BLE001
            self.fail(
                f"손상된 SQLite 파일 검증이 예외를 던지면 안 된다: {exc!r}",
            )

        self.assertTrue(result["file_exists"])
        self.assertFalse(result["restorable"])
        self.assertIsNotNone(result["reason"])

        self.assertEqual(sha256_of_file(path), before_hash)

    def test_empty_sqlite_file_is_rejected_missing_required_tables(self):
        """
        **2026-08-17 코디네이터 명시 지시로 기대치 반전(삭제 아님)**:
        이 테스트는 원래 "HOMEZ 전용 테이블 존재 여부는
        validate_backup_file()의 계약 밖이다"라는 이전 설계 결정을
        고정하고 있었다 — 헤더만 있는 빈 sqlite 파일도 무결성 검사만
        통과하면 restorable=True였다. 코디네이터가 이 결정을 뒤집었다:
        "validate_backup_file()은 범용 SQLite 검증기가 아니라 'HOMEZ
        백업을 복원해도 안전한가'를 판정하는 기능이다 — HOMEZ 스키마와
        무관한 파일을 restorable=True로 판정하면 사용자가 실제로
        복원했을 때 앱이 즉시 깨진다." 헤더만 있는 빈 파일은
        `REQUIRED_CORE_TABLES`가 하나도 없는 극단적인 경우이므로,
        이제는 명확히 거부돼야 한다.
        """

        path = self.tmp_dir / "empty_but_valid.db"
        conn = sqlite3.connect(str(path))
        conn.close()  # 헤더만 있는 유효한 빈 sqlite 파일.

        result = self.service.validate_backup_file(path)

        self.assertTrue(result["file_exists"])
        self.assertEqual(result["integrity_check_result"], "ok")
        self.assertFalse(result["restorable"])
        self.assertIsNotNone(result["reason"])

    def test_sha256_mismatch_takes_priority_and_does_not_raise(self):

        path = self._make_valid_backup("mismatch.db")

        result = self.service.validate_backup_file(
            path, expected_sha256="0" * 64,
        )

        self.assertFalse(result["sha256_matches"])
        self.assertFalse(result["restorable"])
        self.assertIn("SHA-256", result["reason"])

    def test_restore_not_attempted_when_validation_fails(self):
        """검증 실패 시 restore()가 실제 쓰기를 절대 시작하지 않아야 한다."""

        from app.domains.restore.service import RestoreError

        source = self.tmp_dir / "broken_source.db"
        source.write_bytes(os.urandom(2048))

        target = self.tmp_dir / "target.db"
        target.write_bytes(b"ORIGINAL-TARGET-CONTENT")
        original_target_hash = sha256_of_file(target)

        with self.assertRaises(RestoreError):
            self.service.restore(
                source_backup_path=source,
                target_db_path=target,
                pre_restore_backups_dir=self.tmp_dir / "pre_restore_backups",
            )

        self.assertEqual(sha256_of_file(target), original_target_hash)


if __name__ == "__main__":
    unittest.main()
