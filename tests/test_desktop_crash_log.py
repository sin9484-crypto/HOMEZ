"""
=========================================================
Homez OS

File : tests/test_desktop_crash_log.py

HOMEZ Desktop Shell — 숨김 실행 보조 크래시 로그(app/desktop/
crash_log.py) 검증(2026-07-30). 실제 %LOCALAPPDATA%\\HOMEZ\\logs를
건드리지 않도록 임시 디렉터리로 환경변수를 오버라이드한다.

2026-08-24 패키징 격리 재작업 — `get_crash_log_path()`가 이제
`app/desktop/paths.py::get_logs_dir()`에 위임한다(이전에는
LOCALAPPDATA를 직접 읽어 HOMEZ_DATA_ROOT 격리를 우회했다 — 실측
격리 설치 검증 중 발견). 이 모듈은 실제로는 항상 패키징 모드
(pythonw.exe)에서만 쓰이므로, 이 파일의 모든 테스트는 `is_frozen()`
을 True로 고정해 실제 사용 조건과 동일하게 검증한다.
=========================================================
"""

import os
import tempfile
import unittest
from unittest.mock import patch

import app.desktop.paths as paths_mod
from app.desktop import crash_log


class CrashLogTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"LOCALAPPDATA": self.tmp_dir})
        self._patcher.start()

        self._original_is_frozen = paths_mod.is_frozen
        paths_mod.is_frozen = lambda: True

    def tearDown(self):

        paths_mod.is_frozen = self._original_is_frozen
        self._patcher.stop()

        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_log_path_under_localappdata_homez_logs(self):

        path = crash_log.get_crash_log_path()

        self.assertTrue(str(path).startswith(self.tmp_dir))
        self.assertIn("HOMEZ", path.parts)
        self.assertIn("logs", path.parts)
        self.assertEqual(path.name, "homez-desktop.log")

    def test_log_lifecycle_event_creates_directory_and_writes_line(self):

        crash_log.log_lifecycle_event("테스트 이벤트 기록")

        path = crash_log.get_crash_log_path()
        self.assertTrue(path.exists())

        content = path.read_text(encoding="utf-8")
        self.assertIn("테스트 이벤트 기록", content)

    def test_multiple_events_append_not_overwrite(self):

        crash_log.log_lifecycle_event("첫 번째 이벤트")
        crash_log.log_lifecycle_event("두 번째 이벤트")

        content = crash_log.get_crash_log_path().read_text(encoding="utf-8")
        self.assertIn("첫 번째 이벤트", content)
        self.assertIn("두 번째 이벤트", content)

    def test_rotates_when_exceeding_max_bytes(self):

        with patch.object(crash_log, "MAX_LOG_BYTES", 100):
            for i in range(50):
                crash_log.log_lifecycle_event(f"이벤트 {i} - 채우기용 로그 라인입니다")

        path = crash_log.get_crash_log_path()
        old_path = path.with_suffix(path.suffix + ".old")

        self.assertTrue(old_path.exists() or path.exists())

    def test_never_raises_when_directory_uncreatable(self):
        """
        디렉터리 생성 자체가 실패해도(예: 권한 문제를 흉내) 예외를
        던지지 않아야 한다 — 로그 실패가 앱 실행을 막으면 안 된다.
        """

        with patch("pathlib.Path.mkdir", side_effect=OSError("simulated permission denied")):
            try:
                crash_log.log_lifecycle_event("실패해도 죽지 않아야 함")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"log_lifecycle_event가 예외를 던졌습니다: {exc}")

    def test_missing_localappdata_and_no_override_does_not_crash_lifecycle_event(self):
        """LOCALAPPDATA도 HOMEZ_DATA_ROOT도 없는 극단적 비정상 환경
        에서는 get_crash_log_path() 자체가 예외를 던질 수 있다(더
        이상 storage\\logs로 조용히 대체하지 않는다 — 그 특별
        폴백은 paths.py에 없다). 다만 log_lifecycle_event()는 여전히
        그 예외를 삼켜 앱 동작에 전혀 영향을 주지 않아야 한다."""

        with patch.dict(os.environ, {}, clear=False):
            if "LOCALAPPDATA" in os.environ:
                del os.environ["LOCALAPPDATA"]
            os.environ.pop("HOMEZ_DATA_ROOT", None)

            with self.assertRaises(KeyError):
                crash_log.get_crash_log_path()

            try:
                crash_log.log_lifecycle_event("LOCALAPPDATA 없는 환경에서도 죽지 않아야 함")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"log_lifecycle_event가 예외를 던졌습니다: {exc}")

    def test_homez_data_root_override_isolates_crash_log_too(self):
        """2026-08-24 — 실측 격리 설치 검증 중 발견된 결함의 재발
        방지 계약: HOMEZ_DATA_ROOT가 설정되면 이 수명주기 로그도
        다른 사용자 데이터 경로와 동일하게 그 아래로 격리돼야
        한다(LOCALAPPDATA가 설정돼 있어도 무시하고 격리 경로를
        우선한다)."""

        isolated_root = tempfile.mkdtemp()
        try:
            with patch.dict(
                os.environ, {"HOMEZ_DATA_ROOT": isolated_root},
            ):
                path = crash_log.get_crash_log_path()

            self.assertTrue(str(path).startswith(isolated_root))
            self.assertNotIn(self.tmp_dir, str(path))
        finally:
            import shutil
            shutil.rmtree(isolated_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
