"""
=========================================================
Homez OS

File : tests/test_homez_launcher.py

HOMEZ Windows 런처(scripts/start_homez.{ps1,cmd},
scripts/create_homez_shortcut.ps1) 및 아이콘 자산 검증

- PowerShell 구문 검사 (실제 PowerShell 파서 사용)
- UTF-8 BOM 인코딩 확인 (한글 mojibake 방지)
- .cmd 래퍼가 순수 ASCII이고 UTF-8 콘솔 코드페이지를 설정하는지
- 포트 오인 방지 로직(코드 마커) 및 실제 포트 충돌 시나리오
- /console(‘/docs’ 아님)을 여는지, storage/logs에 기록하는지
- ICO 프레임 크기 6종, PNG 정사각형 여부

표준 라이브러리 unittest + subprocess만 사용. 신규 패키지 없음.
homez.db는 사용하지 않는다. 실제 서버를 띄우는 통합 테스트는 별도
포트(8000이 아님)를 사용하고 항상 정리한다.
=========================================================
"""

import os
import shutil
import socket
import struct
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")

PS1_FILES = ["start_homez.ps1", "create_homez_shortcut.ps1"]

# Phase 0 안정화(2026-07-30): 이전에는 여기서 실제 powershell
# subprocess를 15초 timeout으로 실행해 가용성을 확인했다 — 이 호출은
# 모듈 import 시점(= unittest discover가 테스트를 수집하는 시점)에
# 실행되므로, 시스템 부하로 15초를 넘기면 subprocess.TimeoutExpired가
# (FileNotFoundError/OSError가 아니라서) 잡히지 않고 그대로 전체 테스트
# 수집을 멈춰 세운다 — 실제로 이전 회귀 실행에서 이 예외로 스위트가
# 멈췄다. shutil.which()는 실제 프로세스를 띄우지 않고 PATH만 조회하는
# 즉시 반환 호출이라 이 위험이 없고, PowerShell 존재 여부라는 동일한
# skip 조건을 그대로 유지한다.
POWERSHELL_AVAILABLE = shutil.which("powershell") is not None


class LauncherEncodingTestCase(unittest.TestCase):

    def test_ps1_files_have_utf8_bom(self):

        for name in PS1_FILES:
            path = os.path.join(SCRIPTS_DIR, name)
            with open(path, "rb") as f:
                head = f.read(3)
            self.assertEqual(
                head, b"\xef\xbb\xbf",
                f"{name}에 UTF-8 BOM이 없습니다 — PowerShell 5.1에서 "
                "한글이 mojibake될 위험이 있습니다.",
            )

    def test_cmd_wrapper_is_pure_ascii(self):

        path = os.path.join(SCRIPTS_DIR, "start_homez.cmd")
        with open(path, "rb") as f:
            data = f.read()

        try:
            text = data.decode("ascii")
        except UnicodeDecodeError as exc:
            self.fail(f"start_homez.cmd에 비ASCII 바이트가 있습니다: {exc}")

        self.assertIn("chcp 65001", text)
        self.assertIn("start_homez.ps1", text)


@unittest.skipUnless(POWERSHELL_AVAILABLE, "PowerShell을 찾을 수 없음")
class LauncherSyntaxTestCase(unittest.TestCase):

    def test_ps1_files_parse_without_syntax_errors(self):

        for name in PS1_FILES:
            path = os.path.join(SCRIPTS_DIR, name)
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "$errors = $null; $tokens = $null; "
                    "[void][System.Management.Automation.Language.Parser]::"
                    f"ParseFile('{path}', [ref]$tokens, [ref]$errors); "
                    "Write-Output $errors.Count",
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(
                result.stdout.strip(), "0",
                f"{name} 구문 오류: stdout={result.stdout} stderr={result.stderr}",
            )


class LauncherLogicMarkersTestCase(unittest.TestCase):
    """실제 실행 없이, 소스 코드에 필수 로직이 존재하는지 확인한다."""

    def setUp(self):

        path = os.path.join(SCRIPTS_DIR, "start_homez.ps1")
        with open(path, encoding="utf-8-sig") as f:
            self.content = f.read()

    def test_checks_health_response_fields_not_just_port(self):

        self.assertIn("Test-HomezHealth", self.content)
        self.assertIn("/health", self.content)
        self.assertIn("json.success", self.content)
        self.assertIn('json.status -eq "healthy"', self.content)

    def test_opens_console_not_docs(self):
        """
        헤더 주석에는 "/docs가 아니라 /console을 연다"는 설명 자체가 있어
        "/docs"라는 부분 문자열은 존재할 수 있다(문서화 목적). 실제로
        실행되는 브라우저 오픈 URL 구성 부분만 정확히 검사한다.
        """

        self.assertIn('"http://$BindHost`:$Port/console"', self.content)
        self.assertNotIn('Port/docs"', self.content)

    def test_fails_without_opening_browser_when_not_ready(self):

        self.assertIn("Exit-WithFailure", self.content)
        # $serverReady가 실패 경로 전부에서 false로 남아 있다가
        # 마지막 Start-Process(브라우저 오픈)는 그 이후에만 실행된다.
        browser_open_index = self.content.rindex("Start-Process $url")
        exit_failure_def_index = self.content.index("function Exit-WithFailure")
        self.assertGreater(browser_open_index, exit_failure_def_index)

    def test_logs_to_storage_logs_with_no_secrets(self):

        self.assertIn("storage\\logs", self.content)
        self.assertIn("homez-launcher.log", self.content)
        # 비밀정보로 의심되는 키워드가 로그 관련 코드에 등장하지 않는지 확인
        lowered = self.content.lower()
        for forbidden in ("password", "token", "secret"):
            self.assertNotIn(forbidden, lowered)

    def test_read_host_failure_does_not_mask_exit_code(self):

        self.assertIn("Read-Host", self.content)
        # Read-Host 호출이 try/catch로 감싸져 있어 비대화형 환경에서도
        # exit 1이 항상 실행되어야 한다.
        idx = self.content.index("Read-Host")
        surrounding = self.content[max(0, idx - 200): idx + 200]
        self.assertIn("try", surrounding)
        self.assertIn("catch", surrounding)


class IconAssetTestCase(unittest.TestCase):

    def test_ico_has_all_required_sizes(self):

        path = os.path.join(ASSETS_DIR, "homez-app.ico")
        with open(path, "rb") as f:
            data = f.read()

        _, img_type, count = struct.unpack("<HHH", data[:6])
        self.assertEqual(img_type, 1)

        sizes = []
        for i in range(count):
            entry = data[6 + i * 16: 6 + (i + 1) * 16]
            w, h = struct.unpack("<BB", entry[:2])
            self.assertEqual(w, h, "ICO 프레임이 정사각형이 아닙니다.")
            sizes.append(w or 256)

        # Gate F-10(2026-08-07): Windows 작업표시줄·Alt+Tab 렌더링 품질을
        # 위해 20/24/40 3개 해상도를 추가해 9개로 확장했다(같은 승인
        # 로고에서 재생성 — 새 디자인 아님). 상세 검증은
        # tests/test_homez_desktop.py::DesktopIconAssetTestCase 참고.
        self.assertEqual(
            sorted(sizes), [16, 20, 24, 32, 40, 48, 64, 128, 256],
        )

    def test_master_png_is_square(self):

        path = os.path.join(ASSETS_DIR, "homez-app.png")
        with open(path, "rb") as f:
            header = f.read(24)

        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        width = struct.unpack(">I", header[16:20])[0]
        height = struct.unpack(">I", header[20:24])[0]
        self.assertEqual(width, height, "homez-app.png이 1:1 정사각형이 아닙니다.")
        self.assertGreaterEqual(width, 256)

    def test_web_console_logo_exists_and_is_square(self):

        path = os.path.join(REPO_ROOT, "app", "web", "assets", "homez-logo.png")
        self.assertTrue(os.path.exists(path))

        with open(path, "rb") as f:
            header = f.read(24)

        width = struct.unpack(">I", header[16:20])[0]
        height = struct.unpack(">I", header[20:24])[0]
        self.assertEqual(width, height)


@unittest.skipUnless(POWERSHELL_AVAILABLE, "PowerShell을 찾을 수 없음")
class LauncherPortConflictIntegrationTestCase(unittest.TestCase):
    """
    실제 PowerShell 프로세스를 실행해 "포트가 열려 있지만 HOMEZ가 아님"
    시나리오를 검증한다. 실제 homez.db 포트(기본 8000)와 겹치지 않는
    별도 포트를 사용하고, 테스트 종료 시 더미 리스너를 반드시 정리한다.
    """

    def test_launcher_refuses_when_port_is_other_service(self):

        test_port = 18099  # 실제 서비스와 충돌 가능성이 매우 낮은 포트

        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server_sock.bind(("127.0.0.1", test_port))
        except OSError:
            self.skipTest(f"테스트 포트 {test_port}를 바인딩할 수 없습니다(이미 사용 중).")
            return

        server_sock.listen(1)

        try:
            ps1_path = os.path.join(SCRIPTS_DIR, "start_homez.ps1")
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", ps1_path,
                    "-Port", str(test_port),
                ],
                capture_output=True, timeout=30,
                stdin=subprocess.DEVNULL,
            )
            result_stdout = result.stdout.decode("utf-8", errors="replace")
            result_stderr = result.stderr.decode("utf-8", errors="replace")

            self.assertNotEqual(
                result.returncode, 0,
                "다른 프로그램이 포트를 사용 중인데도 런처가 성공(exit 0)"
                f"으로 종료했습니다.\nstdout={result_stdout}\nstderr={result_stderr}",
            )

            combined = result_stdout + result_stderr
            self.assertIn("다른 프로그램", combined)

        finally:
            server_sock.close()


if __name__ == "__main__":
    unittest.main()
