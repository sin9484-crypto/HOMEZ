"""
=========================================================
Homez OS

File : tests/test_desktop_paths_isolation.py

2026-08-24 패키징 격리 재작업 — `app/desktop/paths.py`의
`HOMEZ_DATA_ROOT` 환경변수 오버라이드 계약을 검증한다:
  - 미설정 시(운영 기본값) data/logs/backups/config/media가 전부
    기존 %LOCALAPPDATA%\\HOMEZ\\* 그대로여야 한다(동작 불변).
  - 설정 시(격리 테스트) 전부 그 값 아래로 옮겨가야 한다.
  - 빈 문자열은 무시하고 기본값으로 fail-safe해야 한다.
  - 개발 모드(is_frozen()=False)에서는 이 환경변수가 있어도 전혀
    영향을 주지 않아야 한다(개발 모드는 항상 저장소 경로 계약).
=========================================================
"""

import os
import unittest
from pathlib import Path

import app.desktop.paths as paths_mod


class DesktopDataRootIsolationTestCase(unittest.TestCase):

    def setUp(self):

        self._original_is_frozen = paths_mod.is_frozen
        self._original_environ = dict(os.environ)

    def tearDown(self):

        paths_mod.is_frozen = self._original_is_frozen
        os.environ.clear()
        os.environ.update(self._original_environ)

    def _force_frozen(self):

        paths_mod.is_frozen = lambda: True

    def test_frozen_without_override_uses_localappdata_unchanged(self):

        self._force_frozen()
        os.environ.pop("HOMEZ_DATA_ROOT", None)
        os.environ["LOCALAPPDATA"] = r"C:\FakeLocalAppData"

        self.assertEqual(
            paths_mod.get_data_dir(),
            Path(r"C:\FakeLocalAppData\HOMEZ\data"),
        )
        self.assertEqual(
            paths_mod.get_logs_dir(),
            Path(r"C:\FakeLocalAppData\HOMEZ\logs"),
        )
        self.assertEqual(
            paths_mod.get_backups_dir(),
            Path(r"C:\FakeLocalAppData\HOMEZ\backups"),
        )
        self.assertEqual(
            paths_mod.get_config_dir(),
            Path(r"C:\FakeLocalAppData\HOMEZ\config"),
        )
        self.assertEqual(
            paths_mod.get_media_dir(),
            Path(r"C:\FakeLocalAppData\HOMEZ\media"),
        )

    def test_frozen_with_override_isolates_all_five_dirs(self):

        self._force_frozen()
        os.environ["HOMEZ_DATA_ROOT"] = r"C:\IsolatedTestRoot"
        os.environ["LOCALAPPDATA"] = r"C:\FakeLocalAppData"

        self.assertEqual(
            paths_mod.get_data_dir(), Path(r"C:\IsolatedTestRoot\data"),
        )
        self.assertEqual(
            paths_mod.get_logs_dir(), Path(r"C:\IsolatedTestRoot\logs"),
        )
        self.assertEqual(
            paths_mod.get_backups_dir(),
            Path(r"C:\IsolatedTestRoot\backups"),
        )
        self.assertEqual(
            paths_mod.get_config_dir(), Path(r"C:\IsolatedTestRoot\config"),
        )
        self.assertEqual(
            paths_mod.get_media_dir(), Path(r"C:\IsolatedTestRoot\media"),
        )

        # 격리된 동안 실제 운영 경로(LOCALAPPDATA 기준)는 결과값 중
        # 어디에도 나타나지 않아야 한다 — 부분적으로만 격리되는
        # 결함을 잡기 위함.
        for p in (
            paths_mod.get_data_dir(), paths_mod.get_logs_dir(),
            paths_mod.get_backups_dir(), paths_mod.get_config_dir(),
            paths_mod.get_media_dir(),
        ):
            self.assertNotIn("FakeLocalAppData", str(p))

    def test_frozen_with_blank_override_falls_back_to_localappdata(self):
        """빈 문자열은 "설정 안 함"과 동일하게 취급한다(공백만 있는
        값으로 조용히 잘못된 경로를 만들지 않는다 — fail-safe)."""

        self._force_frozen()
        os.environ["HOMEZ_DATA_ROOT"] = "   "
        os.environ["LOCALAPPDATA"] = r"C:\FakeLocalAppData"

        self.assertEqual(
            paths_mod.get_data_dir(),
            Path(r"C:\FakeLocalAppData\HOMEZ\data"),
        )

    def test_dev_mode_ignores_override_entirely(self):
        """개발 모드(is_frozen()=False)는 HOMEZ_DATA_ROOT가 설정돼
        있어도 항상 저장소 경로를 그대로 쓴다 — 이 오버라이드는
        패키징 모드 전용 계약이다."""

        # setUp에서 is_frozen을 아직 바꾸지 않았으므로 실제 개발
        # 모드(False)가 그대로 적용된다.
        os.environ["HOMEZ_DATA_ROOT"] = r"C:\IsolatedTestRoot"

        self.assertEqual(paths_mod.get_data_dir(), paths_mod.get_repo_root())
        self.assertNotIn(
            "IsolatedTestRoot", str(paths_mod.get_logs_dir()),
        )


class MediaRootTestOverrideTestCase(unittest.TestCase):
    """
    2026-08-31 — `HOMEZ_TEST_MEDIA_ROOT`는 `HOMEZ_DATA_ROOT`와 정확히
    반대 방향의 계약이다: `HOMEZ_DATA_ROOT`는 "패키징 모드 전용"이고,
    이 오버라이드는 `DATABASE_URL`/`HOMEZ_TEST_FAKE_COUPANG_PROVIDER`와
    같은 계열의 "개발 모드 전용" 신호다.

    2026-08-31 Phase 7.6 감사 — 최초 버전은 `is_frozen()`과 무관하게
    항상 이 환경변수를 따랐다. 실제 설치된 실행 파일(패키징 모드) 위에
    이 값이 남아있거나(설치 스크립트 잔재) 실수로/악의적으로
    설정되면, 환경변수 하나만으로 운영 상품 이미지 경로가 임의의
    위치로 조용히 바뀔 수 있었다 — 이번 수정으로 패키징 모드에서는
    이 값이 있어도 완전히 무시된다(fail-closed). E2E 테스트는 항상
    개발 모드(`python -m uvicorn ...`)로만 실행되므로 테스트 기능은
    줄지 않는다.
    """

    def setUp(self):

        self._original_is_frozen = paths_mod.is_frozen
        self._original_environ = dict(os.environ)

    def tearDown(self):

        paths_mod.is_frozen = self._original_is_frozen
        os.environ.clear()
        os.environ.update(self._original_environ)

    def test_dev_mode_without_override_uses_repo_storage_media(self):

        os.environ.pop("HOMEZ_TEST_MEDIA_ROOT", None)

        self.assertEqual(
            paths_mod.get_media_dir(),
            paths_mod.get_repo_root() / "storage" / "media",
        )

    def test_dev_mode_with_override_uses_override_path(self):

        os.environ["HOMEZ_TEST_MEDIA_ROOT"] = r"C:\IsolatedTestMedia"

        self.assertEqual(
            paths_mod.get_media_dir(), Path(r"C:\IsolatedTestMedia"),
        )

    def test_frozen_mode_ignores_override_entirely(self):
        """2026-08-31 Phase 7.6 감사 수정 — 패키징 모드(실제 설치된
        실행 파일)에서는 이 환경변수가 설정돼 있어도 절대 참조하지
        않는다. 설치본 위에 이 값이 남아있거나 실수로/악의적으로
        설정돼도 운영 media 경로가 바뀌지 않아야 한다(fail-closed)."""

        paths_mod.is_frozen = lambda: True
        os.environ["HOMEZ_TEST_MEDIA_ROOT"] = r"C:\IsolatedTestMedia"
        os.environ["LOCALAPPDATA"] = r"C:\FakeLocalAppData"

        media_dir = paths_mod.get_media_dir()

        self.assertEqual(media_dir, Path(r"C:\FakeLocalAppData\HOMEZ\media"))
        self.assertNotEqual(media_dir, Path(r"C:\IsolatedTestMedia"))

    def test_frozen_mode_with_data_root_override_also_ignores_media_test_override(self):
        """패키징 모드에서 정식 오버라이드(HOMEZ_DATA_ROOT)와 테스트
        전용 오버라이드(HOMEZ_TEST_MEDIA_ROOT)가 동시에 설정돼도,
        전자만 적용되고 후자는 여전히 무시된다."""

        paths_mod.is_frozen = lambda: True
        os.environ["HOMEZ_DATA_ROOT"] = r"C:\IsolatedTestRoot"
        os.environ["HOMEZ_TEST_MEDIA_ROOT"] = r"C:\ShouldBeIgnored"

        self.assertEqual(
            paths_mod.get_media_dir(), Path(r"C:\IsolatedTestRoot\media"),
        )

    def test_blank_override_falls_back_to_default(self):

        os.environ["HOMEZ_TEST_MEDIA_ROOT"] = "   "

        media_dir = paths_mod.get_media_dir()

        self.assertNotEqual(media_dir, Path("   "))
        self.assertEqual(
            media_dir, paths_mod.get_repo_root() / "storage" / "media",
        )

    def test_relative_path_override_is_used_as_is_in_dev_mode(self):
        """상대경로를 그대로 받아들인다 — 개발자가 자신의 dev 환경에서
        직접 지정하는 값이라 DATABASE_URL의 상대경로 사용과 같은
        수준으로 취급한다(패키징 모드에서는 애초에 무시되므로 실
        설치본에는 영향이 없다)."""

        os.environ["HOMEZ_TEST_MEDIA_ROOT"] = "relative/media/fixtures"

        self.assertEqual(
            paths_mod.get_media_dir(), Path("relative/media/fixtures"),
        )

    def test_unc_path_override_is_accepted_in_dev_mode(self):
        """UNC 경로도 그대로 받아들인다 — 개발 모드 전용이므로
        개발자가 자신의 네트워크 공유를 fixture 저장소로 쓰겠다는
        명시적 선택으로 취급한다."""

        os.environ["HOMEZ_TEST_MEDIA_ROOT"] = r"\\test-server\share\media"

        self.assertEqual(
            paths_mod.get_media_dir(), Path(r"\\test-server\share\media"),
        )


class CredentialNamespaceIsolationTestCase(unittest.TestCase):
    """store_connection/service.py와 desktop_console_session_store.py
    양쪽의 Credential Manager 이름공간이 동일한 HOMEZ_DATA_ROOT 신호로
    함께 분리되는지 확인한다 — 실제 Windows Credential Manager는
    전혀 호출하지 않는다(문자열 계약만 검증)."""

    def setUp(self):

        self._original_environ = dict(os.environ)

    def tearDown(self):

        os.environ.clear()
        os.environ.update(self._original_environ)

    def test_store_connection_namespace_switches_with_override(self):

        from app.domains.store_connection.service import (
            _credential_namespace,
        )

        os.environ.pop("HOMEZ_DATA_ROOT", None)
        self.assertEqual(_credential_namespace(), "HOMEZ")

        os.environ["HOMEZ_DATA_ROOT"] = r"C:\IsolatedTestRoot"
        self.assertEqual(_credential_namespace(), "HOMEZ_TEST")

    def test_console_session_namespace_switches_with_override(self):

        from app.core.desktop_console_session_store import (
            _target_prefix, _active_user_target,
        )

        os.environ.pop("HOMEZ_DATA_ROOT", None)
        self.assertEqual(_target_prefix(), "HOMEZ:console_session:")
        self.assertEqual(_active_user_target(), "HOMEZ:console_active_user")

        os.environ["HOMEZ_DATA_ROOT"] = r"C:\IsolatedTestRoot"
        self.assertEqual(_target_prefix(), "HOMEZ_TEST:console_session:")
        self.assertEqual(
            _active_user_target(), "HOMEZ_TEST:console_active_user",
        )


class CoreLoggerDataRootIsolationTestCase(unittest.TestCase):
    """2026-08-24 — 격리 설치 검증 중 발견된 세 번째 누수(첫째:
    crash_log.py, 둘째: 없음/바로 발견, 셋째: app/core/logger.py가
    CWD 기준 상대경로 "logs"를 무조건 써서 설치 폴더 안에
    logs/homez.log가 그대로 생겼다) 재발 방지 계약. 실제 로거를
    새로 만들지 않고 `_resolve_log_dir()` 순수 함수만 검증한다."""

    def setUp(self):

        self._original_is_frozen = paths_mod.is_frozen
        self._original_environ = dict(os.environ)

    def tearDown(self):

        paths_mod.is_frozen = self._original_is_frozen
        os.environ.clear()
        os.environ.update(self._original_environ)

    def test_dev_mode_keeps_relative_logs_path_unchanged(self):

        from app.core.logger import _resolve_log_dir

        paths_mod.is_frozen = lambda: False

        self.assertEqual(_resolve_log_dir(), Path("logs"))

    def test_frozen_mode_follows_homez_data_root_override(self):

        from app.core.logger import _resolve_log_dir

        paths_mod.is_frozen = lambda: True
        os.environ["HOMEZ_DATA_ROOT"] = r"C:\IsolatedTestRoot"

        self.assertEqual(
            _resolve_log_dir(), Path(r"C:\IsolatedTestRoot\logs"),
        )

    def test_frozen_mode_without_override_uses_localappdata(self):

        from app.core.logger import _resolve_log_dir

        paths_mod.is_frozen = lambda: True
        os.environ.pop("HOMEZ_DATA_ROOT", None)
        os.environ["LOCALAPPDATA"] = r"C:\FakeLocalAppData"

        self.assertEqual(
            _resolve_log_dir(), Path(r"C:\FakeLocalAppData\HOMEZ\logs"),
        )


if __name__ == "__main__":
    unittest.main()
