"""
=========================================================
Homez OS

File : tests/test_console_duplicate_submit_guard.py

Gate X-3(2026-08-12) — 항목 11 "중복 제출 방지가 다른 주요 쓰기
작업에도 적용됐는지 확인" 검증. lsDownloadCsv/lwDownloadCsv/
lwHandleNext에서 반복되던 "요청 중 버튼 비활성화" 패턴을 공용
헬퍼(withButtonGuard)로 추출해, pause/resume·승인/거절/취소·재시도·
EStop·자동화 모드·AI 상품 등록 생명주기·위저드 정밀검사/승인/취소/
실행/재시도·가입 승인/거절·초대 취소·관리자 사용자 역할부여/
비밀번호초기화/활성화토글·세션 전체 해제·판매채널 연동 행 액션에
실제로 배선됐는지 정적으로 확인한다(다른 *_ui.py 테스트 파일과 동일
패턴 — console.js 소스 텍스트만 확인, 서버/브라우저를 띄우지 않는다).

일부 버튼(pe-submit, sc-run-verify-btn, sc-run-save-btn)은 이 헬퍼
이전부터 자체적으로 disabled 토글 + finally 복구를 이미 구현하고
있었다 — 재구현하지 않고 그대로 둔다(이미 구현된 기능은 재구현하지
않는다는 지시에 따름). 이 파일은 그 기존 구현이 여전히 존재하는지도
함께 고정한다.
=========================================================
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class WithButtonGuardHelperTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_helper_defined(self):

        self.assertIn("async function withButtonGuard(btn, fn)", self.js)
        self.assertIn("if (!btn || btn.disabled) return;", self.js)

    def test_helper_wired_to_marketplace_listing_write_actions(self):

        for needle in (
            'withButtonGuard(btn, () => mlPauseOrResumeListing(',
            'withButtonGuard(approveBtn, () => mlDecideApproval(',
            'withButtonGuard(rejectBtn, () => mlDecideApproval(',
            'withButtonGuard(revokeBtn, () => mlDecideApproval(',
            'withButtonGuard(retryBtn, () => mlRetrySubmit(',
        ):
            self.assertIn(needle, self.js, f"누락: {needle}")

    def test_helper_wired_to_safety_controls(self):

        self.assertIn('withButtonGuard(el("btn-toggle-estop")', self.js)
        self.assertIn('withButtonGuard(el("btn-set-mode")', self.js)

    def test_helper_wired_to_listing_package_lifecycle(self):

        for btn_id in ("lp-create-btn", "lp-regenerate-btn", "lp-approve-btn", "lp-reject-btn", "lp-submit-btn"):
            self.assertIn(f'withButtonGuard(el("{btn_id}")', self.js)

    def test_helper_wired_to_listing_wizard_write_actions(self):

        for needle in (
            'withButtonGuard(el("lw-run-precheck-btn")',
            'withButtonGuard(confirmBtn, async () => {\n      const errEl = el("lw-revoke-error");',
            'withButtonGuard(el("lw-approve-btn")',
            'withButtonGuard(el("lw-revoke-load-preview-btn")',
            'withButtonGuard(el("lw-load-preview-btn")',
            'withButtonGuard(el("lw-draft-save-btn")',
            'withButtonGuard(el("lw-submit-btn")',
            'withButtonGuard(retryBtn, async () => {\n          try {\n            const fresh = await apiFetch(`/listing-wizards/${lwState.wizard.id}/retry-failed`',
        ):
            self.assertIn(needle, self.js, f"누락: {needle}")

    def test_helper_wired_to_account_and_admin_write_actions(self):

        for needle in (
            "wrap.querySelectorAll(\"[data-approve]\").forEach((btn) => {\n      btn.addEventListener(\"click\", () => withButtonGuard(btn,",
            "wrap.querySelectorAll(\"[data-reject]\").forEach((btn) => {\n      btn.addEventListener(\"click\", () => withButtonGuard(btn,",
            "wrap.querySelectorAll(\"[data-revoke-invitation]\").forEach((btn) => {\n      btn.addEventListener(\"click\", () => withButtonGuard(btn,",
            "wrap.querySelectorAll(\"[data-assign-role]\").forEach((btn) => {\n      btn.addEventListener(\"click\", () => withButtonGuard(btn,",
            "wrap.querySelectorAll(\"[data-init-reset]\").forEach((btn) => {\n      btn.addEventListener(\"click\", () => withButtonGuard(btn,",
            "wrap.querySelectorAll(\"[data-toggle-active]\").forEach((btn) => {\n      btn.addEventListener(\"click\", () => withButtonGuard(btn,",
            'withButtonGuard(el("btn-revoke-all-sessions")',
        ):
            self.assertIn(needle, self.js, f"누락: {needle}")

    def test_helper_wired_to_store_connection_row_actions(self):

        self.assertIn(
            'btn.addEventListener("click", () => withButtonGuard(btn, () => scHandleRowAction(',
            self.js,
        )

    def test_preexisting_manual_guards_not_removed(self):
        """
        withButtonGuard 도입 이전부터 자체적으로 disabled 토글을 하던
        버튼(pe-submit/sc-run-verify-btn/sc-run-save-btn)은 그대로
        유지돼야 한다 — 재구현하지 않는다.
        """

        self.assertIn('const submitBtn = el("pe-submit");\n      submitBtn.disabled = true;', self.js)
        self.assertIn('const btn = el("sc-run-verify-btn");\n    btn.disabled = true;', self.js)
        self.assertIn('const btn = el("sc-run-save-btn");', self.js)


if __name__ == "__main__":
    unittest.main()
