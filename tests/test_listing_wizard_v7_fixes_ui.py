"""
=========================================================
Homez OS

File : tests/test_listing_wizard_v7_fixes_ui.py

2026-08-31 V7 후속 결함 수정(Phase 7.5 브라우저 E2E에서 발견된
쿠팡 상품등록 결함) — app/web/console.js 소스 패턴 검증. 실제 DOM
렌더링/클릭은 브라우저 E2E가 담당하므로(코드로 검증 불가능한 영역),
여기서는 "고쳤다고 말한 코드가 실제로 소스에 있는지"를 문자열
패턴으로 확인한다(tests/test_i18n.py의
ConsoleMoneyFormattingLocaleTestCase와 동일한 기법).
=========================================================
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(REPO_ROOT, "app", "web")


class ConsoleJsSourceTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        with open(os.path.join(WEB_DIR, "console.js"), encoding="utf-8") as f:
            cls.js = f.read()

    def _fulfillment_step_body(self):

        start = self.js.index("async function lwRenderFulfillmentStep(content) {")
        end = self.js.index("\n  async function lwRenderPrecheckStep(", start)
        return self.js[start:end]


class LogisticsRestoreTestCase(ConsoleJsSourceTestCase):
    """결함 2 — 5단계를 다시 열었을 때 저장된 출고지/반품지가
    select에 표시되지 않던 문제."""

    def test_initial_render_seeds_saved_code_as_an_option(self):

        body = self._fulfillment_step_body()

        self.assertIn(
            'sel.outbound_shipping_place_code ? `<option value="${escapeHtml(sel.outbound_shipping_place_code)}"',
            body,
        )
        self.assertIn(
            'sel.return_center_code ? `<option value="${escapeHtml(sel.return_center_code)}"',
            body,
        )

    def test_populate_logistics_detects_stale_saved_value(self):

        body = self._fulfillment_step_body()
        start = body.index("const populateLogistics = ")
        end = body.index("\n    };", start)
        fn = body[start:end]

        self.assertIn("savedStillValid", fn)
        # 유효하지 않으면 다음 저장 때 그대로 다시 보내지 않도록
        # savedCode를 비운다 — 자동으로 다른 값으로 대체하지 않는다.
        self.assertIn('select.dataset.savedCode = savedStillValid ? saved : "";', fn)

    def test_lookup_handlers_surface_stale_value_warning(self):

        body = self._fulfillment_step_body()

        self.assertIn("lw.logistics_saved_value_stale_warning", body)
        # 두 조회 버튼(출고지/반품지) 핸들러 모두 "저장값이 있었는데
        # 새로 받은 목록에 없다"는 조건에서만 경고를 띄운다 — 목록이
        # 아예 비었을 때(logistics_empty)와는 다른 분기다.
        self.assertEqual(
            body.count("else if (saved && !savedStillValid) error.textContent = HomezI18n.t(\"lw.logistics_saved_value_stale_warning\");"),
            2,
        )


class ContentsUiTestCase(ConsoleJsSourceTestCase):
    """결함 1 — required_fields.contents를 원본 JSON 편집 없이
    구성할 수 있는 UI가 전혀 없던 문제."""

    def test_contents_panel_present_with_media_reuse(self):

        body = self._fulfillment_step_body()

        self.assertIn("lw-contents-panel", body)
        self.assertIn("data-lw-contents-host", body)
        # 새 이미지를 만들지 않고 3단계에서 이미 만든 media_asset(
        # PRODUCT_CANDIDATE 소유, purpose === DETAIL)만 재사용한다.
        self.assertIn(
            "`/media-assets/owners/PRODUCT_CANDIDATE/${w.product_candidate_id}/assets`",
            body,
        )
        self.assertIn('a.purpose === "DETAIL"', body)

    def test_no_detail_images_shows_korean_guidance_and_jump(self):

        body = self._fulfillment_step_body()

        self.assertIn("lw.contents_no_detail_images", body)
        self.assertIn("data-lw-contents-goto-media", body)
        self.assertIn('lwJumpToStep("MEDIA")', body)

    def test_build_button_warns_it_replaces_existing_contents(self):
        """2026-08-31 Phase 7.6 감사 — 기존 contents를 덮어쓸 때
        사용자에게 결과가 명확히 표시되는가. 클릭 전 경고 문구와,
        성공 후 "대체했습니다" 문구가 모두 있어야 한다."""

        body = self._fulfillment_step_body()

        self.assertIn("lw.contents_replace_hint", body)
        self.assertIn("lw.contents_saved_status", body)

    def test_build_button_calls_dedicated_endpoint_not_raw_json(self):

        body = self._fulfillment_step_body()

        self.assertIn("data-lw-contents-build", body)
        self.assertIn(
            "/coupang/contents-from-media",
            body,
        )
        self.assertIn("lw.contents_selection_required", body)

    def test_save_step_merges_built_contents_without_overwriting_when_untouched(self):
        """저장 시 사용자가 이번 화면 방문에서 실제로 "구성" 버튼을
        누른 경우에만 덮어쓰고, 아니면 원본 JSON의 기존 contents를
        그대로 둔다 — 단계를 오갔다 돌아와도 값이 유지되는 이유."""

        body = self._fulfillment_step_body()
        start = body.index("lwSaveCurrentStep = async (opts = {}) => {")
        section = body[start:]

        self.assertIn('block.querySelector("[data-lw-contents-host]")', section)
        self.assertIn("contentsHost.dataset.lwContentsJson", section)
        self.assertIn("if (builtContents.length)", section)
        self.assertIn("requiredFields.contents = builtContents;", section)


class ItemNameUnitCountLabelTestCase(ConsoleJsSourceTestCase):
    """결함 4 — itemName 열이 "상품명"(draft_name_label) 재사용으로
    전체 상품명을 그대로 넣도록 오인시키던 문제, unitCount가 "개당
    수량"만으로는 가격계산 전용이라는 의미가 드러나지 않던 문제."""

    def _combo_builder_body(self):

        start = self.js.index("function lwRenderItemComboBuilder(block, definitions, savedItems) {")
        end = self.js.index("\n  function ", start + 10)
        return self.js[start:end]

    def test_item_name_column_uses_dedicated_label_not_product_name_label(self):

        body = self._combo_builder_body()

        self.assertIn('HomezI18n.t("lw.combo_col_item_name")', body)
        # 이전에는 이 테이블 헤더가 draft_name_label("상품명")을
        # 재사용해 전체 상품명 복사를 유도했다 — 더는 그러지 않는다.
        table_header_start = body.index("<thead><tr>")
        table_header_end = body.index("</tr></thead>", table_header_start)
        self.assertNotIn(
            "lw.draft_name_label", body[table_header_start:table_header_end],
        )

    def test_item_name_input_has_example_placeholder(self):

        body = self._combo_builder_body()

        self.assertIn('data-lw-item-field="itemName"', body)
        self.assertIn("lw.combo_item_name_placeholder", body)

    def test_help_text_explains_item_name_and_unit_count_semantics(self):

        body = self._combo_builder_body()

        self.assertIn("lw.combo_item_name_help", body)
        self.assertIn("lw.combo_unit_count_help", body)


if __name__ == "__main__":
    unittest.main()
