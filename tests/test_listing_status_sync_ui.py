"""
=========================================================
Homez OS

File : tests/test_listing_status_sync_ui.py

2026-08-05 CTO 재검증 지시 Gate E — 채널별 등록 현황 화면 편의성
보완(오류 유형 필터, 기간 필터, 정렬, 상태별 건수, 세션 필터 상태
보존, 중복 새로고침 방지, 실패 채널만 재시도) 정적 검증. 다른
*_ui.py 테스트 파일과 동일하게 console.html/console.js 텍스트만
확인한다 — 실제 서버/DB를 띄우지 않는다.
=========================================================
"""

import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConsoleHtmlListingStatusSyncFiltersTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_new_filter_and_summary_elements_exist(self):

        for element_id in (
            "ls-filter-error-type", "ls-filter-updated-from",
            "ls-filter-updated-to", "ls-filter-sort",
            "ls-bulk-retry-btn", "ls-status-counts",
        ):
            self.assertIn(f'id="{element_id}"', self.html)


class ConsoleJsListingStatusSyncLogicTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_required_functions_exist(self):

        for name in (
            "lsSaveFilterState", "lsRestoreBasicFilterState",
            "lsPopulateErrorTypeOptions", "lsRenderStatusCounts",
            "lsGetFilteredSortedRows", "lsRenderFilteredTable",
            "lsBulkRetryFailed",
        ):
            self.assertIn(f"function {name}", self.js, f"{name} 함수가 없습니다.")

    def test_query_includes_updated_from_and_to(self):

        start = self.js.index("function lsBuildQuery()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("updated_from", body)
        self.assertIn("updated_to", body)

    def test_load_table_guards_against_overlapping_calls(self):
        """
        새로고침 버튼을 빠르게 두 번 눌러도 먼저 도착한 응답이 나중
        요청을 덮어쓰는 경쟁 조건이 생기지 않아야 한다 — lsState.loading
        플래그로 재진입을 막는다.
        """

        start = self.js.index("async function lsLoadTable()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("lsState.loading", body)

    def test_bulk_retry_previews_target_count_before_executing(self):

        start = self.js.index("async function lsBulkRetryFailed()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("confirmDialog(", body)
        self.assertIn("targets.length", body)

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


class GateHRetryAfterCountdownUiTestCase(unittest.TestCase):
    """
    Gate H(2026-08-07) — Retry-After 카운트다운/일괄 재시도 건너뛰기
    UI 정적 검증. 실제 서버/브라우저 없이 console.js 소스만 확인한다
    (다른 *_ui.py 테스트 파일과 동일한 패턴).
    """

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_countdown_helper_functions_exist(self):

        for name in (
            "lsParseUtc", "lsRateLimitRemainingSeconds", "lsFormatCountdown",
            "lsTickCountdowns",
        ):
            self.assertIn(f"function {name}", self.js, f"{name} 함수가 없습니다.")

    def test_countdown_ticker_runs_every_second_without_hitting_server_each_tick(self):
        """
        카운트다운은 1초마다 로컬로 갱신하고, 0초에 도달했을 때만 서버를
        한 번 다시 조회한다(item 4/5) — setInterval(…, 1000)으로 등록
        되고, tick 함수 자체는 매초 apiFetch를 부르지 않아야 한다.
        """

        self.assertIn("setInterval(lsTickCountdowns, 1000)", self.js)

        start = self.js.index("function lsTickCountdowns()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertNotIn("apiFetch(", body)
        self.assertIn("lsLoadTable()", body)  # 0초 도달 시에만 재조회

    def test_rate_limited_buttons_are_disabled_not_hidden(self):

        start = self.js.index("function lsRenderRow(row)")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("disabledAttr", body)
        self.assertIn("ls-countdown-", body)

    def test_rate_limit_note_is_text_not_color_only(self):
        """색상만으로 제한 상태를 표시하지 않는다(item 11) — 항상 문구가
        함께 나온다."""

        start = self.js.index("function lsRenderRow(row)")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn('HomezI18n.t("ls.rate_limit_banner")', body)
        self.assertIn('HomezI18n.t("ls.rate_limit_countdown"', body)

    def test_bulk_retry_skips_still_waiting_items(self):

        start = self.js.index("async function lsBulkRetryFailed()")
        end = self.js.index("\n  }", start)
        body = self.js[start:end]
        self.assertIn("lsRateLimitRemainingSeconds", body)
        self.assertIn("waitingTargets", body)
        self.assertIn("estopBlockedCount", body)
        self.assertIn("permissionDeniedCount", body)
        self.assertIn('"/console/api/safety-status"', body)

    def test_structured_rate_limited_error_is_preserved_not_stringified(self):
        """
        서버가 error_code가 있는 구조화된 detail(429 RATE_LIMITED,
        Gate J의 409 WIZARD_VERSION_CONFLICT 등)을 주면 apiFetch가 그
        객체를 JSON.stringify로 뭉개지 않고 그대로 ApiError.detail에
        보존해야 한다 — 호출부가 카운트다운·충돌 재조회 등을 바로
        렌더링할 수 있어야 한다. Gate H에서 RATE_LIMITED 전용으로
        시작한 이 계약을 Gate J(2026-08-08)가 error_code 존재 여부로
        일반화했다.
        """

        start = self.js.index("async function apiFetch(")
        end = self.js.index("\n  }", start)
        # apiFetch 함수 자체가 길어 head만 보는 다른 테스트와 달리,
        # 여기서는 "!resp.ok" 블록 전체를 봐야 하므로 충분히 넉넉하게
        # 슬라이스한다.
        body = self.js[start:start + 4000]
        self.assertIn('typeof body.detail.error_code === "string"', body)
        self.assertIn("throw new ApiError(resp.status, body.detail)", body)


class GateHRetryAfterCssTestCase(unittest.TestCase):
    """모바일에서 카운트다운과 버튼이 겹치지 않아야 한다(item 12)."""

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.css")
        with open(path, encoding="utf-8") as f:
            cls.css = f.read()

    def test_action_cell_stacks_countdown_and_buttons_not_absolute_positioned(self):

        start = self.css.index(".ls-action-cell")
        end = self.css.index("}", start)
        rule = self.css[start:end]
        self.assertIn("flex-direction: column", rule)
        self.assertNotIn("position: absolute", rule)
        self.assertNotIn("position: fixed", rule)

    def test_rate_limit_note_wraps_and_does_not_overflow(self):

        start = self.css.index(".ls-rate-limit-note")
        end = self.css.index("}", start)
        rule = self.css[start:end]
        self.assertIn("flex-wrap: wrap", rule)
        self.assertIn("max-width: 100%", rule)


class GateHRetryAfterI18nWordingTestCase(unittest.TestCase):
    """사양이 명시한 정확한 ko-KR/en-US 문구를 그대로 확인한다."""

    def test_exact_spec_wording_present_in_both_locales(self):

        i18n_dir = os.path.join(REPO_ROOT, "app", "web", "i18n")

        with open(os.path.join(i18n_dir, "ko-KR.js"), encoding="utf-8") as f:
            ko = f.read()
        with open(os.path.join(i18n_dir, "en-US.js"), encoding="utf-8") as f:
            en = f.read()

        self.assertIn("채널 요청 제한으로 잠시 후 다시 시도할 수 있습니다.", ko)
        self.assertIn(
            "The channel rate limit is active. You can retry shortly.", en,
        )


if __name__ == "__main__":
    unittest.main()
