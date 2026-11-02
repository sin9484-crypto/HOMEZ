"""
=========================================================
Homez OS

File : tests/test_packaged_guard_hook.py

`tools/packaged_guard_hook.py`(검증용 PyInstaller 빌드에 가드를 싣는 런타임 훅) 집중 테스트
(2026-10-06, 68차). 합성 보호 파일만 쓰며 실제 DB·자격증명·네트워크는 건드리지 않는다.

주의: 이 테스트는 훅의 **논리**(옵트인·차단·시작 거부)를 파이썬 서브프로세스로 검증한다. 패키징된
exe가 `PYTHONPATH`/`sitecustomize`로는 가드를 로드하지 않는다는 사실(sys.flags.no_site=1,
ignore_environment=1)은 탐침 앱으로 실측했고 문서(RELEASE_CANDIDATE_PREP §9-9)에 기록했다 —
실제 PyInstaller 빌드는 이 테스트에서 하지 않는다.
=========================================================
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "tools" / "packaged_guard_hook.py"
GUARD = ROOT / "tests" / "support" / "regression_guard" / "sitecustomize.py"

PROBE = (
    "import runpy, sys\n"
    f"runpy.run_path({str(HOOK)!r}, run_name='hook')\n"
    "import os\n"
    "try:\n"
    "    open(os.environ['PROBE_FILE'], 'rb').read(1)\n"
    "    print('OPEN_ALLOWED')\n"
    "except PermissionError:\n"
    "    print('OPEN_BLOCKED')\n"
)


def _child_inherits_regression_guard():
    """이 환경을 물려받은 파이썬 자식이 격리 회귀 가드를 불러오는가(PYTHONPATH 기준)."""
    guard_dir = os.path.normcase(os.path.realpath(GUARD.parent))
    entries = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    return any(os.path.normcase(os.path.realpath(e)) == guard_dir for e in entries if e)


class PackagedGuardHookTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="homez_hook_")
        self.addCleanup(self.tmp.cleanup)
        self.protected = Path(self.tmp.name) / "protected_fake.db"
        self.protected.write_bytes(b"synthetic")
        self.log = Path(self.tmp.name) / "guard.log"

    def _run(self, **env_overrides):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("HOMEZ_GUARD", "HOMEZ_PACKAGED"))}
        env.update({
            "PROBE_FILE": str(self.protected), "HOMEZ_GUARD_NO_DEFAULTS": "1",
            "HOMEZ_GUARD_PROTECTED_PATHS": str(self.protected), "HOMEZ_GUARD_LOG": str(self.log),
        })
        env.update({k: v for k, v in env_overrides.items() if v is not None})
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
        before = self.protected.read_bytes()
        proc = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(self.protected.read_bytes(), before)       # 합성 보호 파일 불변
        return proc

    def test_with_the_guard_file_the_protected_file_is_blocked_and_logged(self):
        proc = self._run(HOMEZ_PACKAGED_GUARD_FILE=str(GUARD))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OPEN_BLOCKED", proc.stdout)
        log = self.log.read_text(encoding="utf-8", errors="replace")
        self.assertIn("GUARD_ACTIVE", log)
        self.assertIn("ATTEMPT_BLOCKED", log)

    # 자식이 물려받은 회귀 가드가 합성 보호 파일을 막으므로 "훅이 없으면 열린다"를 볼 수 없다.
    # PYTHONPATH를 지운 자식은 회귀 가드가 차단하므로 가드 없는 실행에서만 판정한다.
    @unittest.skipIf(_child_inherits_regression_guard(), "격리 회귀 가드 아래에서는 판정 불가, 가드 없이 실행")
    def test_without_the_environment_variable_the_hook_is_inert(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OPEN_ALLOWED", proc.stdout)                    # 릴리스 빌드(훅 없음)와 같은 상태
        self.assertFalse(self.log.exists())

    def test_the_hook_refuses_to_start_instead_of_running_unprotected(self):
        missing = self._run(HOMEZ_PACKAGED_GUARD_FILE=str(Path(self.tmp.name) / "nope.py"))
        self.assertEqual(missing.returncode, 97)
        self.assertNotIn("OPEN_", missing.stdout)

        no_install = self._run(HOMEZ_PACKAGED_GUARD_FILE=str(GUARD), HOMEZ_GUARD_NO_INSTALL="1")
        self.assertEqual(no_install.returncode, 98)
        self.assertNotIn("OPEN_", no_install.stdout)

        no_targets = self._run(HOMEZ_PACKAGED_GUARD_FILE=str(GUARD),
                               HOMEZ_GUARD_PROTECTED_PATHS=None)
        self.assertEqual(no_targets.returncode, 98)                   # 보호 대상이 없는 빈 가드
        self.assertNotIn("OPEN_", no_targets.stdout)


class RegressionGuardDetectionTestCase(unittest.TestCase):
    """건너뛰기 조건이 넓어지면 가드 없는 실행에서도 판정이 조용히 사라지므로 조건 자체를 고정한다."""

    def _detect(self, pythonpath):
        with mock.patch.dict(os.environ, {"PYTHONPATH": pythonpath}):
            return _child_inherits_regression_guard()

    def test_true_when_the_guard_directory_is_on_pythonpath(self):
        guard_dir = str(GUARD.parent)
        self.assertTrue(self._detect(guard_dir))
        self.assertTrue(self._detect(os.pathsep.join([str(ROOT), guard_dir])))
        self.assertTrue(self._detect(os.path.relpath(guard_dir)))           # 샤드 실행의 상대 경로 표기
        self.assertTrue(self._detect(guard_dir.upper()))                    # Windows 대소문자

    def test_false_otherwise(self):
        self.assertFalse(self._detect(""))
        self.assertFalse(self._detect(str(ROOT)))
        self.assertFalse(self._detect(str(GUARD.parent.parent)))            # 상위 디렉터리는 가드를 싣지 않는다
        self.assertFalse(self._detect(str(GUARD.parent / "other")))
        with mock.patch.dict(os.environ):
            os.environ.pop("PYTHONPATH", None)
            self.assertFalse(_child_inherits_regression_guard())

    def test_the_inert_test_is_skipped_exactly_when_the_guard_is_inherited(self):
        inert = PackagedGuardHookTestCase.test_without_the_environment_variable_the_hook_is_inert
        self.assertEqual(getattr(inert, "__unittest_skip__", False), _child_inherits_regression_guard())


if __name__ == "__main__":
    unittest.main()
