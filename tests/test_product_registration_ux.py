"""
=========================================================
Homez OS

File : tests/test_product_registration_ux.py

Gate TD-1 결함 수정("직접 상품 후보 작성" UI 추가, ProductCandidate
ID 수기입력 제거, "마법사/Wizard" 용어 변경) 정적 검증. 실제 서버·
DB는 쓰지 않는다 — console.html/console.js/i18n 카탈로그 텍스트만
검사한다.
=========================================================
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ProductRegistrationUxTestCase(unittest.TestCase):

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

        with open(
            os.path.join(REPO_ROOT, "app", "web", "i18n", "ko-KR.js"),
            encoding="utf-8",
        ) as f:
            cls.ko = f.read()

        with open(
            os.path.join(REPO_ROOT, "app", "web", "i18n", "en-US.js"),
            encoding="utf-8",
        ) as f:
            cls.en = f.read()

    # ----------------------------------------------------
    # Task 1 — 상품 후보 직접 작성 UI
    # ----------------------------------------------------

    def test_create_candidate_button_and_dialog_exist_in_html(self):

        self.assertIn('id="candidates-create-btn"', self.html)
        self.assertIn('id="candidate-create-dialog"', self.html)
        self.assertIn('id="cc-product-name"', self.html)
        self.assertIn('id="cc-market"', self.html)
        self.assertIn('id="cc-source-reference"', self.html)
        self.assertIn('id="cc-category-hint"', self.html)
        self.assertIn('id="cc-brand-hint"', self.html)
        self.assertIn('id="cc-release-date"', self.html)
        self.assertIn('id="candidate-create-submit"', self.html)
        self.assertIn('id="candidate-create-cancel"', self.html)

    def test_create_dialog_uses_native_dialog_element_pattern(self):
        """
        기존 confirm-dialog/sc-wizard-dialog와 동일한 <dialog> 패턴을
        재사용하는지 확인한다(중첩 카드 구조 금지, ESC/취소 지원 전제).
        """

        start = self.html.index('id="candidate-create-dialog"')
        window = self.html[max(0, start - 80):start]
        self.assertIn("<dialog", window)

    def test_create_candidate_flow_reuses_existing_private_candidate_api(self):
        """
        직접 상품 추가 흐름은 반드시 기존
        POST /product-candidates/private를 그대로 재사용해야 하며,
        새 엔드포인트를 발명하지 않는다.
        """

        self.assertIn("openCandidateCreateDialog", self.js)
        start = self.js.index("function openCandidateCreateDialog")
        end = self.js.index("\n  async function", start) if "\n  async function" in self.js[start:start + 6000] else start + 6000
        # submit 핸들러가 근처에 있으므로 넉넉히 잘라 검사한다.
        window = self.js[start:start + 6000]
        self.assertIn('"/product-candidates/private"', window)
        self.assertNotIn('"/product-candidates/create"', window)

    def test_empty_state_helper_supports_optional_cta_actions(self):

        self.assertIn(
            "function renderEmptyState(container, message, sub = \"\", actionsHtml = \"\")",
            self.js,
        )

    def test_candidates_empty_state_offers_create_cta(self):

        start = self.js.index("function renderCandidatesTable")
        body = self.js[start:start + 1200]
        self.assertIn("candidates.empty", body)
        self.assertIn("candidates-empty-create-btn", body)

    # ----------------------------------------------------
    # Task 3 — 상품 등록 1단계: 승인된 후보 검색/선택(수기 ID 입력 제거)
    # ----------------------------------------------------

    def test_listing_wizard_source_step_has_no_raw_candidate_id_input(self):
        """
        상품 등록 1단계 화면에는 후보 ID를 직접 입력하는 필드가 더 이상
        존재하지 않는다(검색/선택 UI로 대체됨). 다른 화면(채널별 판매
        방식/AI 상품 등록)의 기존 candidate_id 입력은 이번 범위 밖이라
        의도적으로 남겨둔다 — 그 두 곳의 id는 여기서 검사하지 않는다.
        """

        self.assertNotIn('id="lw-candidate-id"', self.html)
        self.assertIn("lwRenderSourcePicker", self.js)
        self.assertIn("lwFetchApprovedCandidates", self.js)
        self.assertIn("lwRenderSelectedCandidate", self.js)

    def test_source_step_filters_by_company_status_approved(self):

        start = self.js.index("async function lwFetchApprovedCandidates")
        body = self.js[start:start + 800]
        self.assertIn('"APPROVED"', body)

    def test_source_picker_offers_create_and_review_guidance_when_empty(self):

        start = self.js.index("function lwRenderSourcePicker")
        body = self.js[start:start + 1500]
        self.assertIn("lw.step1", body)

    # ----------------------------------------------------
    # Task 4 — "마법사/Wizard" 사용자 용어 변경
    # ----------------------------------------------------

    def test_nav_and_page_title_use_product_registration_wording(self):

        self.assertIn('"nav.listing_wizard": "상품 등록"', self.ko)
        self.assertIn('"lw.title": "상품 등록"', self.ko)
        self.assertIn('"nav.listing_wizard": "Product Registration"', self.en)

    def test_status_labels_localized_not_raw_enum(self):

        self.assertIn("function lwStatusLabel", self.js)
        self.assertIn("String(status).toLowerCase()", self.js)
        for status in (
            "draft", "validating", "needs_correction", "ready_for_approval",
            "approved", "submitting", "partially_succeeded", "succeeded",
            "failed", "cancelled",
        ):
            self.assertIn(f'"lw.status.{status}"', self.ko)
            self.assertIn(f'"lw.status.{status}"', self.en)

    def test_list_and_detail_status_pills_use_lw_status_label(self):

        self.assertIn("escapeHtml(lwStatusLabel(r.status))", self.js)
        self.assertIn("lwStatusLabel(lwState.wizard.status)", self.js)

    def test_permission_keys_unchanged_by_terminology_rename(self):
        """
        사용자 노출 문구만 바꾸고 내부 Permission 키(listing_wizard.*)는
        절대 바꾸지 않았는지 확인한다.
        """

        # console.html/js는 listing_wizard.view만 nav data-permission으로
        # 직접 참조한다 — create/edit/approve는 서버측(admin_guard·
        # ListingWizardPermissionGuard)에서만 강제되고 클라이언트 문자열
        # 리터럴로는 등장하지 않는다(그 자체가 기존 계약이며, 이번
        # 용어 변경으로 새로 참조를 추가하거나 지우지 않았다는 뜻이다).
        self.assertIn("listing_wizard.view", self.html)

    def test_channel_registration_phrase_reserved_for_real_submission(self):
        """
        "판매 채널에 등록"이라는 표현은 실제 외부 제출 단계에만 남아
        있어야 한다 — 이번 변경으로 단순 저장/승인 액션에 새로 붙이지
        않았는지 최소한으로 확인한다(완전한 의미 분석은 아니며, 명백한
        회귀만 잡는 안전장치).
        """

        self.assertNotIn('"candidates.create_success_new": "판매 채널에 등록', self.ko)
        self.assertNotIn('"lw.open_btn": "판매 채널에 등록', self.ko)

    # ----------------------------------------------------
    # 언어 전환 시 즉시 재-렌더링 (동적 목록 화면)
    # ----------------------------------------------------

    def test_locale_change_rerenders_listing_wizard_list_view(self):

        self.assertIn(
            'if (currentView === "listing-wizard" && !lwState.wizard) lwRenderList();',
            self.js,
        )

    def test_locale_change_rerenders_candidates_list_view(self):

        self.assertIn(
            'if (currentView === "candidates" && candidatesCache) {',
            self.js,
        )
        self.assertIn("populateSourceFilter(candidatesCache);", self.js)

    # ----------------------------------------------------
    # 정적 HTML "마법사" 잔존 검사(CTO 후속 지시 — 이전 회귀가
    # i18n 카탈로그 값만 보고 console.html 폴백 텍스트는 놓쳤던
    # 공백을 메운다)
    # ----------------------------------------------------

    def test_no_wizard_wording_left_in_html_markup(self):
        import re

        stripped = re.sub(r"<!--.*?-->", "", self.html, flags=re.DOTALL)
        self.assertNotIn("마법사", stripped)

    def test_wizard_comment_only_occurrences_are_harmless(self):
        """
        마크업 밖 코드 주석에는 "마법사"가 남아 있어도 사용자에게
        노출되지 않으므로 실패 대상이 아니다 — 위 테스트가 마크업
        안쪽만 검사한다는 계약을 명시적으로 문서화한다.
        """

        self.assertIn("<!-- ============ 판매채널 연결 ============ -->", self.html)

    # ----------------------------------------------------
    # 원시 ProductCandidate ID 입력 제거(ml/lp 화면)
    # ----------------------------------------------------

    def test_ml_and_lp_no_longer_expose_raw_numeric_id_text_input(self):

        self.assertNotIn('type="text" id="ml-candidate-id"', self.html)
        self.assertNotIn('type="text" id="lp-candidate-id"', self.html)
        self.assertIn('type="hidden" id="ml-candidate-id"', self.html)
        self.assertIn('type="hidden" id="lp-candidate-id"', self.html)

    def test_ml_and_lp_reuse_shared_approved_candidate_picker(self):

        self.assertIn("function mountApprovedCandidatePicker", self.js)
        self.assertIn('mountApprovedCandidatePicker(wrap, "ml-candidate-picker"', self.js)
        self.assertIn('mountApprovedCandidatePicker(wrap, "lp-candidate-picker"', self.js)

    # ----------------------------------------------------
    # 상품 후보 상세 화면 언어 즉시 재-렌더링(CTO 후속 지시 —
    # 목록 화면만 고치고 상세 화면은 놓쳤던 공백을 메운다)
    # ----------------------------------------------------

    def test_locale_change_rerenders_candidate_detail_view(self):

        self.assertIn("let candidateDetailCurrentId", self.js)
        self.assertIn(
            'if (currentView === "candidate-detail" && candidateDetailCurrentId) {',
            self.js,
        )

    # ----------------------------------------------------
    # 분석 실행 API(section 3) — 정적 배선 확인. 계약 자체(회사
    # 격리/동시성/규칙 기반 점수)는
    # tests/test_product_candidate_analysis_workflow.py에서 검증한다.
    # ----------------------------------------------------

    def test_analyze_and_recommend_buttons_wired_in_candidate_detail(self):

        self.assertIn('id="btn-analyze"', self.js)
        self.assertIn('id="btn-recommend"', self.js)
        self.assertIn('bindWorkflowStep("btn-analyze", "analyze"', self.js)
        self.assertIn('bindWorkflowStep("btn-recommend", "recommend"', self.js)

    # ----------------------------------------------------
    # CTO 보완 지시(2026-08-19) — 추측성 점수/AI 분석 암시 제거 검증.
    # 실제 회귀 로직은 tests/test_product_candidate_analysis_workflow.py
    # 가 검증한다. 여기서는 UI가 "AI 분석"을 암시하지 않는지, 실제
    # 점수가 없을 때 "추천 판단 실행" 버튼을 숨기는지만 정적으로
    # 확인한다.
    # ----------------------------------------------------

    def test_recommend_button_hidden_when_no_real_ai_score_exists(self):
        """
        recommend()는 실제 추천 근거(trend_score/novelty_score)가 없는
        후보에서는 노출되지 않아야 한다 — CTO 지시: "'추천 판단 실행'도
        실제 추천 근거가 없으면 사용하지 않는다."
        """

        self.assertIn(
            'candidate.status === "ANALYZED" && '
            '(candidate.trend_score != null || candidate.novelty_score != null)',
            self.js,
        )
        self.assertIn(
            'candidate.status === "ANALYZED" && '
            'candidate.trend_score == null && candidate.novelty_score == null',
            self.js,
        )

    def test_info_check_wording_does_not_claim_ai_analysis(self):
        """
        기본 정보 확인 흐름의 i18n 문구는 "AI 분석"·"AI가 분석"이라고
        주장하지 않는다 — 실제로는 규칙조차 없는 순수 상태 확인이다.
        """

        for text in (
            self.ko, self.en,
        ):
            self.assertNotIn("AI 분석 완료", text)
            self.assertNotIn("AI Analysis Complete", text)

        self.assertIn('"candidate_detail.verify_info_btn"', self.ko)
        self.assertIn('"candidate_detail.info_check_step_title"', self.ko)
        self.assertIn('"candidate_detail.info_check_step_title"', self.en)


if __name__ == "__main__":
    unittest.main()
