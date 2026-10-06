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


if __name__ == "__main__":
    unittest.main()
