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
        chunk = self.js[idx:idx + 800]
        self.assertIn("lwClearFulfillmentDraft(block, w.id)", chunk)
        clear_pos = chunk.index("lwClearFulfillmentDraft")
        advance_pos = chunk.index("lwAdvanceAfterSave(fresh)")
        self.assertLess(clear_pos, advance_pos)


if __name__ == "__main__":
    unittest.main()
