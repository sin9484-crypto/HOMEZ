"""
=========================================================
Homez OS

File : tests/test_regression_guard_log_isolation.py

격리 회귀 가드가 앱 로그를 임시 폴더로 보내는지 검증한다(2026-11-02).

배경: 시험이 운영 로그 `storage/logs/homez-desktop.log`·`logs/homez.log`에
기록을 남겨(`port=<Mock ...`, 시험이 만든 `[E1002] 시작 실패`, '읽기 실패:
boom' 등) 앱 중단 원인 조사 때 진짜 기록을 가려내기 어려웠다.

각 시나리오는 가드를 물려받은 **자식 파이썬 프로세스**에서 실제 로거로
임의의 표식을 쓰고, 표식이 임시 폴더에 있는지 읽어서 확인한다. 자식의
작업 폴더는 임시 폴더라 `logs/` 상대경로로 새면 거기에 드러난다.
=========================================================
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_DIR = REPO_ROOT / "tests" / "support" / "regression_guard"
GUARD_FILE = GUARD_DIR / "sitecustomize.py"

WRITE_ALL_LOGS = r'''
import json, os, subprocess, sys
from app.core.logger import logger
from app.desktop.crash_log import get_crash_log_path, log_lifecycle_event
from app.desktop.server import get_desktop_logger

marker = sys.argv[1]
logger.warning(marker)
log_lifecycle_event(marker)
desktop = get_desktop_logger()
desktop.warning(marker)
grandchild = subprocess.run(
    [sys.executable, "-c", "import os; print(os.environ['HOMEZ_TEST_LOG_ROOT'])"],
    capture_output=True, text=True, check=True,
)
print("RESULT" + json.dumps({
    "root": os.environ["HOMEZ_TEST_LOG_ROOT"],
    "grandchild_root": grandchild.stdout.strip(),
    "homez_log": logger.handlers[1].baseFilename,
    "crash_log": str(get_crash_log_path()),
    "desktop_log": desktop.handlers[0].baseFilename,
    "relative_logs_dir_created": os.path.exists("logs"),
}))
'''


class RegressionGuardLogIsolationTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp = Path(tempfile.mkdtemp(prefix="homez_guard_log_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        self.guard_log = self.tmp / "guard.log"
        self.marker = f"log-isolation-{uuid.uuid4().hex}"

    def _child_env(self, **overrides):

        env = dict(os.environ)
        env.pop("HOMEZ_TEST_LOG_ROOT", None)    # 바깥 회귀 가드가 준 값을 물려받지 않고 처음부터 시험한다
        env.update({
            "PYTHONPATH": os.pathsep.join([str(GUARD_DIR), str(REPO_ROOT)]),
            "HOMEZ_GUARD_LOG": str(self.guard_log),
        })
        env.update(overrides)
        return env

    def _run_write_all_logs(self, **env_overrides):

        proc = subprocess.run(
            [sys.executable, "-c", WRITE_ALL_LOGS, self.marker],
            env=self._child_env(**env_overrides), cwd=self.cwd,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("GUARD_ACTIVE", self.guard_log.read_text(encoding="utf-8"))
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
        self.assertEqual(len(lines), 1, proc.stdout[-2000:])
        return json.loads(lines[0][len("RESULT"):])

    def _assert_all_logs_under(self, result, root: Path):

        self.assertEqual(Path(result["root"]), root)
        self.assertEqual(Path(result["homez_log"]), root / "homez.log")
        self.assertEqual(Path(result["crash_log"]), root / "homez-desktop.log")
        self.assertEqual(Path(result["desktop_log"]), root / "desktop-launcher.log")
        self.assertFalse(result["relative_logs_dir_created"])
        self.assertFalse((self.cwd / "logs").exists())

    def test_guard_creates_temporary_log_root_and_removes_it_at_exit(self):

        result = self._run_write_all_logs()

        root = Path(result["root"])
        self._assert_all_logs_under(result, root)
        self.assertNotIn(os.path.normcase(str(REPO_ROOT)), os.path.normcase(str(root)))
        self.assertEqual(Path(result["grandchild_root"]), root)
        self.assertFalse(root.exists(), "프로세스가 끝나면 임시 로그 폴더가 지워져야 한다")

    def test_temporary_log_root_is_removed_after_logging_reconfiguration(self):
        """uvicorn이 쓰는 dictConfig는 logging의 핸들러 목록을 비운다. 그 뒤 다시 쓰인 로그 파일은
        logging.shutdown이 닫지 못해, Windows에서 임시 폴더가 지워지지 않고 남았다(실측)."""

        code = (
            "import logging.config, os\n"
            "from app.core.logger import logger\n"
            "logging.config.dictConfig({'version': 1, 'disable_existing_loggers': False})\n"
            "logger.warning('reopened after dictConfig')\n"
            "print('ROOT=' + os.environ['HOMEZ_TEST_LOG_ROOT'])\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], env=self._child_env(), cwd=self.cwd,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("ROOT=")]
        self.assertEqual(len(lines), 1, proc.stdout[-2000:])
        self.assertFalse(Path(lines[0][len("ROOT="):]).exists())

    def test_preset_log_root_is_used_kept_and_receives_every_log(self):

        root = self.tmp / "kept logs"

        result = self._run_write_all_logs(HOMEZ_TEST_LOG_ROOT=str(root))

        self._assert_all_logs_under(result, root)
        self.assertEqual(Path(result["grandchild_root"]), root)
        for name in ("homez.log", "homez-desktop.log", "desktop-launcher.log"):
            self.assertIn(self.marker, (root / name).read_text(encoding="utf-8"), name)

    def test_blank_log_root_gets_a_temporary_folder_too(self):

        result = self._run_write_all_logs(HOMEZ_TEST_LOG_ROOT="   ")

        self.assertTrue(result["root"].strip())
        self._assert_all_logs_under(result, Path(result["root"]))

    def test_packaged_exe_gets_no_log_root(self):
        """설치본 exe는 이 값을 읽지 않고(paths.py) tools/packaged_guard_hook.py로 같은 파일을 불러온다 —
        그 경로의 시작 동작을 바꾸지 않는다. 합성 보호 경로만 쓴다."""

        code = (
            "import importlib.util, os, sys\n"
            "del os.environ['HOMEZ_GUARD_NO_INSTALL']\n"
            "sys.frozen = True\n"
            "spec = importlib.util.spec_from_file_location('guard_under_test', sys.argv[1])\n"
            "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
            "print('ROOT=' + os.environ.get('HOMEZ_TEST_LOG_ROOT', ''))\n"
        )
        env = self._child_env(
            PYTHONPATH=str(GUARD_DIR),
            HOMEZ_GUARD_NO_INSTALL="1",     # 시작할 때 자동 import되는 쪽은 설치하지 않고, 위에서 exe처럼 직접 불러온다
            HOMEZ_GUARD_NO_DEFAULTS="1",
            HOMEZ_GUARD_PROTECTED_PATHS=str(self.tmp / "synthetic.db"),
        )
        proc = subprocess.run(
            [sys.executable, "-c", code, str(GUARD_FILE)],
            env=env, cwd=self.cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("ROOT=\n", proc.stdout.replace("\r\n", "\n"))
        self.assertIn("GUARD_ACTIVE", self.guard_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
