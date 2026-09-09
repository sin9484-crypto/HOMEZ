"""
=========================================================
Homez OS

File : tests/test_desktop_bootstrap.py

HOMEZ Desktop Shell — 최상위 bootstrap 진입점(app/desktop/bootstrap.py)
검증(2026-07-30). import 단계 실패까지 로그·MessageBox로 잡히는지
확인한다. 실제 %LOCALAPPDATA%\\HOMEZ\\logs는 건드리지 않도록 임시
디렉터리로 오버라이드한다.

2026-07-30: 처음에는 `builtins.__import__` 전체를 몽키패치해 import
실패를 흉내냈으나, 이는 전역 import 훅을 건드리는 방식이라 같은
프로세스에서 함께 실행되는 다른 테스트 파일(tests/test_homez_
desktop.py 등)과 타이밍이 겹치면 예측하기 어려운 상호작용이 생겨
불안정했다(실제 재현·확인됨 — 단독 실행 시는 통과하지만 전체
스위트와 함께 실행하면 실패). `app/desktop/bootstrap.py`에
`_import_desktop_main()` 헬퍼를 분리해 그 함수 하나만 patch하는
방식으로 바꿔 이 문제를 해결했다.
=========================================================
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from app.desktop import bootstrap


class BootstrapLogPathTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"LOCALAPPDATA": self.tmp_dir})
        self._patcher.start()

    def tearDown(self):

        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_log_path_under_localappdata(self):

        path = bootstrap._bootstrap_log_path()
        self.assertTrue(str(path).startswith(self.tmp_dir))
        self.assertEqual(path.name, "homez-desktop.log")

    def test_bootstrap_log_never_raises(self):

        with patch("pathlib.Path.mkdir", side_effect=OSError("simulated")):
            try:
                bootstrap._bootstrap_log("실패해도 죽지 않아야 함")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_bootstrap_log가 예외를 던졌습니다: {exc}")

    def test_message_box_never_raises(self):

        with patch("ctypes.windll.user32.MessageBoxW", side_effect=OSError("simulated")):
            try:
                bootstrap._bootstrap_message_box(bootstrap.ERROR_CODE_BOOTSTRAP_FAILURE)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_bootstrap_message_box가 예외를 던졌습니다: {exc}")


class BootstrapMainOrchestrationTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"LOCALAPPDATA": self.tmp_dir})
        self._patcher.start()

    def tearDown(self):

        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_successful_run_delegates_to_desktop_main(self):

        fake_module = type("FakeModule", (), {"run": staticmethod(lambda: 0)})()

        with patch.object(bootstrap, "_import_desktop_main", return_value=fake_module):
            exit_code = bootstrap.main()

        self.assertEqual(exit_code, 0)

    def test_import_failure_is_caught_logged_and_shown(self):
        """
        app.desktop.main을 import하는 단계 자체가 실패해도(예: webview
        관련 문제를 흉내) bootstrap.main()이 예외를 전파하지 않고
        오류 코드로 로그·메시지 박스를 남겨야 한다.
        """

        with patch.object(
            bootstrap, "_import_desktop_main",
            side_effect=ImportError("simulated import failure"),
        ):
            with patch.object(bootstrap, "_bootstrap_message_box") as message_box_mock:
                exit_code = bootstrap.main()

        self.assertEqual(exit_code, 1)
        message_box_mock.assert_called_once_with(bootstrap.ERROR_CODE_BOOTSTRAP_FAILURE)

        log_content = bootstrap._bootstrap_log_path().read_text(encoding="utf-8")
        self.assertIn(bootstrap.ERROR_CODE_BOOTSTRAP_FAILURE, log_content)
        self.assertIn("ImportError", log_content)

    def test_run_call_failure_is_caught_as_defense_in_depth(self):

        def _raising_run():
            raise RuntimeError("simulated run() escape")

        fake_module = type("FakeModule", (), {"run": staticmethod(_raising_run)})()

        with patch.object(bootstrap, "_import_desktop_main", return_value=fake_module):
            with patch.object(bootstrap, "_bootstrap_message_box") as message_box_mock:
                exit_code = bootstrap.main()

        self.assertEqual(exit_code, 1)
        message_box_mock.assert_called_once_with(bootstrap.ERROR_CODE_BOOTSTRAP_FAILURE)

    def test_log_contains_no_secrets(self):
        """
        _bootstrap_message_box를 patch하지 않으면 실제 MessageBoxW가
        호출되어, 대화형 데스크톱 세션이 연결된 환경에서는 아무도
        클릭하지 않는 모달 대화상자로 테스트 프로세스가 영원히
        멈춘다(2026-07-30 실제 재현 — 전체 회귀 스위트가 이 지점에서
        두 차례 결정적으로 멈추는 것을 py-spy로 직접 확인함). 다른
        형제 테스트와 동일하게 반드시 patch해야 한다.
        """

        def _raising_run():
            raise RuntimeError("token=SHOULD_NOT_APPEAR password=ALSO_NOT")

        fake_module = type("FakeModule", (), {"run": staticmethod(_raising_run)})()

        with patch.object(bootstrap, "_import_desktop_main", return_value=fake_module):
            with patch.object(bootstrap, "_bootstrap_message_box"):
                bootstrap.main()

        log_content = bootstrap._bootstrap_log_path().read_text(encoding="utf-8")
        self.assertNotIn("SHOULD_NOT_APPEAR", log_content)
        self.assertNotIn("ALSO_NOT", log_content)
        # 예외 종류(RuntimeError)만 기록되고 메시지 본문은 기록되지 않는다.
        self.assertIn("RuntimeError", log_content)


if __name__ == "__main__":
    unittest.main()
