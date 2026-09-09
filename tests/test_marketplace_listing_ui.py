"""
=========================================================
Homez OS

File : tests/test_marketplace_listing_ui.py

Gate 5(2026-08-01, HOMEZ Phase 1 이후 Gate 1~5) — "플랫폼별 등록
상태 화면"/"조회" 요구사항 대응으로 추가한 제출 이력 조회 UI의
정적 검증. 이 화면은 조회(GET)만 하고, 실제 제출(POST
/marketplace-listings/submissions)을 자동 호출하지 않는다.
=========================================================
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MarketplaceListingSubmissionHistoryUiTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(
            os.path.join(REPO_ROOT, "app", "web", "console.html"),
            encoding="utf-8",
        ) as f:
            cls.html = f.read()

        with open(
            os.path.join(REPO_ROOT, "app", "web", "console.js"),
            encoding="utf-8",
        ) as f:
            cls.js = f.read()

    def test_submissions_panel_exists_in_html(self):

        self.assertIn('id="ml-submissions-panel"', self.html)
        self.assertIn('id="ml-submissions-table-wrap"', self.html)

    def test_show_submissions_function_exists(self):

        self.assertIn("function mlShowSubmissions", self.js)

    def test_summary_table_has_per_row_history_button(self):

        self.assertIn("data-ml-show-submissions", self.js)

    def test_submission_history_only_fetches_does_not_auto_submit(self):
        """
        mlShowSubmissions 함수 본문이 GET 조회만 하고, 실제 제출
        엔드포인트(POST /marketplace-listings/submissions)를 자동으로
        호출하지 않는지 확인한다.
        """

        start = self.js.index("async function mlShowSubmissions")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]

        self.assertIn("/submissions`)", body)
        self.assertNotIn('"/marketplace-listings/submissions"', body)
        self.assertNotIn("method: \"POST\"", body)

    # ----------------------------------------------------
    # Gate 5(2026-08-01, CTO 2차 지적) — PAUSED/SUBMITTING 상태 및
    # 승인/거절/취소/재시도 UI
    # ----------------------------------------------------

    def test_live_e2e_pending_credential_banner_exists(self):

        self.assertIn('id="ml-live-e2e-pending-banner"', self.html)
        self.assertIn("LIVE_E2E_PENDING_USER_CREDENTIAL", self.html)

    def test_pause_resume_buttons_and_handler_exist(self):

        self.assertIn("data-ml-pause", self.js)
        self.assertIn("data-ml-resume", self.js)
        self.assertIn("function mlPauseOrResumeListing", self.js)
        self.assertIn('mlPauseOrResumeListing(Number(btn.dataset.mlPause), "pause")', self.js)
        self.assertIn('mlPauseOrResumeListing(Number(btn.dataset.mlResume), "resume")', self.js)
        self.assertIn("/marketplace-listings/${listingId}/${action}", self.js)

    def test_approval_action_functions_exist(self):

        for name in (
            "mlRenderApprovalActions", "mlDecideApproval", "mlRetrySubmit",
        ):
            self.assertIn(f"function {name}", self.js)

    def test_approve_reject_revoke_call_correct_endpoints(self):

        self.assertIn("/approvals/${approvalId}/approve", self.js)
        self.assertIn("/approvals/${approvalId}/${action}", self.js)

    def test_retry_submit_calls_real_submission_endpoint_not_auto_wired(self):
        """
        재시도는 실제 제출 엔드포인트를 호출하지만(그것이 재시도의
        의미다), 오직 사용자가 버튼을 눌렀을 때만 실행된다 — 페이지
        로드 시 자동 호출되지 않는다(mlRetrySubmit은 addEventListener
        콜백으로만 연결되어 있어야 한다).
        """

        # Gate X-3(2026-08-12) — 중복 클릭 방지 가드(withButtonGuard)가
        # 추가돼 호출 형태가 바뀌었다. mlRetrySubmit이 여전히 click
        # 리스너 콜백 "안"에서만 호출되는지가 이 테스트의 본래 의도이며,
        # 그 사실은 그대로 유지된다.
        self.assertIn(
            'retryBtn.addEventListener("click", () => withButtonGuard(retryBtn, () => mlRetrySubmit(',
            self.js,
        )


if __name__ == "__main__":
    unittest.main()
