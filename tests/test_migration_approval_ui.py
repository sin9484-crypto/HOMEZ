"""
=========================================================
Homez OS

File : tests/test_migration_approval_ui.py

2026-08-05 CTO 재검증 지시 Gate E — Migration 승인 UX(프론트엔드)
정적 검증. 실제 DB/서버를 띄우지 않는다 — console.html/console.js의
정적 텍스트만 확인한다(다른 *_ui.py 테스트 파일과 동일한 패턴).
=========================================================
"""

import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConsoleHtmlMigrationApprovalDialogTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_migration_approval_dialog_exists(self):

        self.assertIn('id="migration-approval-dialog"', self.html)
        for element_id in (
            "ma-plan-list", "ma-backup-preview", "ma-progress",
            "ma-result", "ma-error", "ma-later-btn", "ma-approve-btn",
            "ma-close-btn",
        ):
            self.assertIn(f'id="{element_id}"', self.html)

    def test_limited_mode_banner_exists(self):

        self.assertIn('id="limited-mode-banner"', self.html)
        self.assertIn('id="limited-mode-apply-btn"', self.html)
        # 로그인/셸 전환과 무관하게 항상 뜰 수 있어야 하므로 기본값은
        # hidden이어야 한다(showShell 이후 조회 결과에 따라서만 켜짐).
        start = self.html.index('id="limited-mode-banner"')
        end = self.html.index(">", start)
        self.assertIn("hidden", self.html[start:end])


class ConsoleJsMigrationApprovalLogicTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_required_functions_exist(self):

        for name in (
            "checkMigrationApprovalGate", "showMigrationApprovalDialog",
            "renderMigrationPlanList", "applyLimitedModeUI",
            "initLimitedModeBanner",
        ):
            self.assertIn(f"function {name}", self.js, f"{name} 함수가 없습니다.")

    def test_status_and_approve_endpoints_are_called(self):

        self.assertIn('"/desktop-setup/migration-status"', self.js)
        self.assertIn('"/desktop-setup/migration-status/approve"', self.js)

    def test_show_shell_triggers_gate_check(self):
        """
        showShell()이 checkMigrationApprovalGate()를 호출해야 로그인 직후
        (또는 세션 복원 후) 승인 필요 여부가 확인된다 — 로그인 폼 자체를
        가리지 않도록 로그인 게이트 함수들이 아니라 showShell 안에서만
        호출돼야 한다.
        """

        start = self.js.index("function showShell()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("checkMigrationApprovalGate()", body)

    def test_limited_mode_blocks_mutating_requests_in_api_fetch(self):
        """
        apiFetch가 모든 쓰기 요청의 유일한 통로이므로, 여기서 막는 것이
        곧 콘솔 전체에서 쓰기를 막는 것과 같다. Gate G(2026-08-07)부터는
        이 클라이언트 차단과 별개로 app/main.py의 미들웨어가 서버
        측에서도 동일하게 강제한다(tests/test_migration_restricted_mode.py
        참고) — 이 클라이언트 차단은 그보다 앞서 네트워크 요청 자체를
        보내지 않게 해 사용자 경험을 매끄럽게 하는 1차 방어선이다.
        """

        start = self.js.index("async function apiFetch(")
        # apiFetch 함수 정의부 초반(제한 모드 체크가 있어야 할 위치)만
        # 본다 — 함수 전체를 다 자르면 이후 다른 함수의 여는 중괄호까지
        # 잘못 포함될 수 있으므로 여유 있게 800자만 본다.
        head = self.js[start:start + 800]
        self.assertIn("homezLimitedMode", head)
        self.assertIn("LIMITED_MODE_ALLOWED_PATHS", head)

    def test_limited_mode_allowlist_keeps_approve_and_auth_reachable(self):

        self.assertIn('"/desktop-setup/migration-status/approve"', self.js)
        start = self.js.index("LIMITED_MODE_ALLOWED_PATHS = new Set([")
        end = self.js.index("]);", start)
        allowlist_block = self.js[start:end]
        for path in (
            "/auth/login", "/auth/logout",
            "/desktop-setup/migration-status",
            "/desktop-setup/migration-status/approve",
        ):
            self.assertIn(f'"{path}"', allowlist_block)

    def test_syntax_is_valid_when_node_available(self):

        node_path = shutil.which("node")
        if node_path is None:
            self.skipTest("Node.js를 찾을 수 없어 문법 검사를 건너뜁니다.")

        js_path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        result = subprocess.run(
            [node_path, "--check", js_path],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
