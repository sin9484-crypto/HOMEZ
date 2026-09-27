"""
2026-09-27 — 5단계(FULFILLMENT) 반복 입력 손실 방지용 로컬 초안
(localStorage) 기능이 소스에 고정돼 있는지 정적으로 확인한다(서버/
브라우저를 띄우지 않는다, 다른 *_ui.py 테스트와 동일 패턴).
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FulfillmentDraftUiTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(
            os.path.join(REPO_ROOT, "app", "web", "console.js"),
            encoding="utf-8",
        ) as f:
            cls.js = f.read()

    def _fn_body(self, signature, length=2000):

        idx = self.js.index(signature)
        return self.js[idx:idx + length]

    def test_draft_key_scoped_to_wizard_and_account(self):

        body = self._fn_body("function lwFulfillmentDraftKey(wizardId, accountId)")
        self.assertIn("${wizardId}", body)
        self.assertIn("${accountId}", body)

    def test_collect_excludes_phone_field(self):

        body = self._fn_body("function lwCollectFulfillmentDraftFromBlock(block)")
        self.assertIn("LW_PHONE_NOTICE_SUFFIX", body)
        self.assertIn("endsWith(LW_PHONE_NOTICE_SUFFIX)", body)

    def test_collect_never_touches_rights_or_notice_confirmed_checkboxes(self):

        body = self._fn_body("function lwCollectFulfillmentDraftFromBlock(block)")
        self.assertNotIn("lw-live-image-rights", body)
        self.assertNotIn("lw-policy-notice-confirmed", body)

    def test_field_selector_list_excludes_category_identity_fields(self):
        """카테고리 코드·버전·지문을 정의(definitions) 없이 복원하면
        서버 검증이 조용히 우회될 위험이 있다 — 이 셋은 절대 목록에
        없어야 한다(재조회로만 채운다)."""

        idx = self.js.index("const LW_FULFILLMENT_DRAFT_FIELD_SELECTORS = [")
        list_src = self.js[idx:idx + 800]
        end = list_src.index("];")
        list_src = list_src[:end]
        self.assertNotIn("lw-policy-official-category", list_src)
        self.assertNotIn("lw-category-metadata-version", list_src)
        self.assertNotIn("lw-category-metadata-fingerprint", list_src)

    def test_restore_only_fills_empty_fields(self):

        body = self._fn_body("function lwRestoreFulfillmentDraft(block, wizardId)")
        self.assertIn("!input.value.trim()", body)

    def test_restore_logistics_uses_saved_code_dataset_not_direct_value(self):
        """출고지/반품지는 재조회 전까지 옵션 자체가 없다 — 직접 .value를
        설정하지 않고 기존 populateLogistics()의 재검증 경로(dataset.
        savedCode)만 되살린다."""

        body = self._fn_body("function lwRestoreFulfillmentDraft(block, wizardId)")
        self.assertIn("outboundSel.dataset.savedCode = draft.outboundCode", body)
        self.assertIn("returnSel.dataset.savedCode = draft.returnCode", body)

    def _fulfillment_step_body(self):

        start = self.js.index("async function lwRenderFulfillmentStep(content) {")
        end = self.js.index("\n  async function ", start + 10)
        return self.js[start:end]

    def test_mount_wires_restore_and_autosave(self):

        step_body = self._fulfillment_step_body()
        idx = step_body.index("lwSaveCurrentStep = async (opts = {}) => {")
        preceding = step_body[max(0, idx - 500):idx]
        self.assertIn("lwRestoreFulfillmentDraft(block, w.id)", preceding)
        self.assertIn("lwWireFulfillmentDraftAutosave(block, w.id)", preceding)

    def test_category_recommend_reapplies_draft_after_metadata_refetch(self):

        idx = self.js.index("lwRenderItemComboBuilder(block, metadata.purchase_option_fields || [], []);")
        nearby = self.js[idx:idx + 400]
        self.assertIn("lwRestoreFulfillmentDraftOptionsAndNotice(block, w.id)", nearby)

    def test_notice_category_dropdown_reapplies_draft_on_every_rerender(self):
        """실제 브라우저 재현으로 발견된 결함 — 정보고시 "유형" 드롭다운
        자체를 전환할 때도(카테고리 추천·조회 버튼과 무관하게)
        renderGroup()이 그 그룹을 매번 빈 값으로 다시 그린다. 다른
        그룹을 거쳐 되돌아오면 그 사이 자동저장이 빈 상태를 그대로
        저장해 이전 입력을 지웠다 — 재렌더 직후 즉시 복원해야 한다."""

        idx = self.js.index("const renderGroup = () => {")
        end = self.js.index("host.querySelector(\"[data-lw-notice-category]\")?.addEventListener", idx)
        body = self.js[idx:end]
        self.assertIn("lwWirePhoneDefault(grid);", body)
        self.assertIn(
            "lwRestoreFulfillmentDraftOptionsAndNotice(block, lwState.wizard.id);",
            body,
        )
        # 전화번호 자동채움 다음, refresh() 이전에 와야 한다(빈 값으로
        # 갓 그려진 직후·검증 표시 이전에 채워야 에러가 잘못 뜨지 않는다).
        self.assertLess(
            body.index("lwWirePhoneDefault(grid)"),
            body.index("lwRestoreFulfillmentDraftOptionsAndNotice"),
        )
        self.assertLess(
            body.index("lwRestoreFulfillmentDraftOptionsAndNotice"),
            body.rindex("refresh();"),
        )

    def test_save_preserves_purchase_options_and_notice_when_not_yet_rendered(self):
        """실제 브라우저 재현으로 확인된 결함 — 카테고리 조회 전에는
        구매옵션·정보고시 입력칸이 아예 없어서, 그 상태로 자동저장하면
        이전에 저장해 둔 값이 빈 값으로 덮어써졌다. 그 그룹의 입력칸이
        지금 화면에 없으면 기존 초안 값을 유지해야 한다."""

        body = self._fn_body("function lwSaveFulfillmentDraft(block, wizardId)")
        self.assertIn('!block.querySelector("[data-lw-purchase-option-key]")', body)
        self.assertIn("draft.purchaseOptions = existing.purchaseOptions", body)
        self.assertIn('!block.querySelector("[data-lw-notice-key]")', body)
        self.assertIn("draft.notice = existing.notice", body)

    def test_successful_real_save_clears_draft_before_advancing(self):

        idx = self.js.index("/listing-wizards/${w.id}/fulfillment")
        chunk = self.js[idx:idx + 1200]
        self.assertIn("lwClearFulfillmentDraft(block, w.id)", chunk)
        clear_pos = chunk.index("lwClearFulfillmentDraft")
        advance_pos = chunk.index("lwAdvanceAfterSave(fresh)")
        self.assertLess(clear_pos, advance_pos)

    def test_draft_clear_skips_blocks_edited_during_the_save_request(self):
        """실제 브라우저에서 확인 가능한 경쟁 조건 — PATCH 응답을
        기다리는 동안 사용자가 다른 칸을 더 입력하면, 그 입력은 이번
        payload에 없다. 저장 성공만으로 무조건 초안을 지우면 서버에
        전달되지 않은 새 입력까지 함께 사라진다."""

        idx = self.js.index("const preSaveDraftSnapshots = blocks.map(")
        body = self.js[idx:idx + 1100]
        self.assertIn("nowSnapshot === preSaveDraftSnapshots[idx]", body)
        self.assertIn("lwClearFulfillmentDraft(block, w.id)", body)
        # 스냅샷은 실제 fetch 호출보다 앞서 찍혀야 한다.
        fetch_idx = self.js.index("await apiFetch(`/listing-wizards/${w.id}/fulfillment`")
        self.assertLess(idx, fetch_idx)

    def test_autosave_reports_when_nothing_was_actually_saved(self):
        """lwSaveCurrentStep(silent)의 반환값을 버리면, 필수값 미충족으로
        서버에 아무것도 저장되지 않았을 때도 "자동 저장 중…" 문구가
        영원히 남아 사용자를 오도한다 — 실제 화면 재현으로 확인된 결함."""

        idx = self.js.index("function lwScheduleAutosave()")
        body = self.js[idx:idx + 900]
        self.assertIn("const saved = await lwSaveCurrentStep({ silent: true })", body)
        self.assertIn('if (!saved && statusEl) statusEl.textContent = HomezI18n.t("lw.autosave_not_saved")', body)

    def test_brand_state_is_collected_only_when_resolved(self):
        """실제 브라우저 재현으로 발견된 결함 — 브랜드 패널은 이전까지
        로컬 초안에 전혀 포함되지 않아서, 카테고리 재조회·서버 재시작
        마다 이미 확인·선택한 공식 브랜드가 UNRESOLVED로 되돌아갔다."""

        body = self._fn_body("function lwCollectFulfillmentDraftFromBlock(block)", 1600)
        self.assertIn("lw-brand-state-panel", body)
        self.assertIn('.brandState === "UNRESOLVED" ? "" : json', body)

    def test_brand_state_restore_never_overwrites_a_resolved_state(self):
        body = self._fn_body("function lwRestoreFulfillmentDraft(block, wizardId)", 2200)
        self.assertIn('!currentBrandState.brandState || currentBrandState.brandState === "UNRESOLVED"', body)
        self.assertIn("lwRenderBrandStatePanel(JSON.parse(draft.brandStateJson))", body)
        self.assertIn("lwWireBrandStatePanel(block)", body)

    def test_brand_tab_and_select_clicks_are_not_lost_by_local_draft_autosave(self):
        """브랜드 탭·검색결과 "선택"은 click만 내고 input/change를
        내지 않는다 — 로컬 초안 자동저장이 click도 감시해야 브랜드
        선택 직후 바로 저장된다(다음 재렌더까지 기다리지 않음)."""

        body = self._fn_body("function lwWireFulfillmentDraftAutosave(block, wizardId)", 700)
        self.assertIn('block.addEventListener("click", schedule)', body)


if __name__ == "__main__":
    unittest.main()
