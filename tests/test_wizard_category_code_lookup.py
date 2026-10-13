"""
=========================================================
Homez OS

File : tests/test_wizard_category_code_lookup.py

5단계의 "코드로 조회"를 고정한다.

백산수 다음 상품(2026-10-13)에서 카테고리 추천이 쿠팡 POST라 호출 예산·승인 문제로 막혔다.
카테고리 코드를 이미 아는 경우(WING 확인) 추천 POST 없이 메타데이터 GET 1회만으로
같은 입력 반영을 하도록 한 변경이 유지되는지 정적으로 검사한다.
=========================================================
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
CONSOLE = (APP / "web" / "console.js").read_text(encoding="utf-8")
KO = (APP / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (APP / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")

KEYS = (
    "lw.category_code_lookup_label",
    "lw.category_code_lookup_placeholder",
    "lw.category_code_lookup_btn",
    "lw.category_code_lookup_hint",
    "lw.category_code_lookup_needs_code",
)


class CategoryCodeLookupTests(unittest.TestCase):

    def test_button_and_input_are_rendered(self):
        self.assertIn("data-lw-category-lookup", CONSOLE)
        self.assertIn('class="lw-category-code-input"', CONSOLE)

    def test_recommendation_post_is_skipped_when_a_code_is_typed(self):
        self.assertIn('const byCode = btn.hasAttribute("data-lw-category-lookup");', CONSOLE)
        self.assertIn("? { display_category_code: typedCode }", CONSOLE)
        # 추천 POST는 byCode가 아닐 때만 호출한다(같은 삼항식의 else 가지).
        self.assertRegex(
            CONSOLE,
            re.compile(
                r"byCode\s*\?\s*\{ display_category_code: typedCode \}\s*:\s*await apiFetch\("
                r"`/listing-wizards/\$\{w\.id\}/category-recommendation`, \{ method: \"POST\" \}\)",
            ),
        )

    def test_typed_code_must_be_numeric_before_any_request(self):
        self.assertIn("/^\\d{3,9}$/.test(typedCode)", CONSOLE)
        self.assertIn('HomezI18n.t("lw.category_code_lookup_needs_code")', CONSOLE)

    def test_metadata_request_is_still_a_get_for_the_code(self):
        self.assertIn(
            "`/listing-wizards/${w.id}/category-metadata/${encodeURIComponent(recommendation.display_category_code)}`",
            CONSOLE,
        )

    def test_readonly_official_category_field_is_unchanged(self):
        self.assertIn('class="lw-policy-official-category"', CONSOLE)
        self.assertRegex(CONSOLE, r'class="lw-policy-official-category"[^>]*readonly')

    def test_labels_exist_in_both_locales(self):
        for key in KEYS:
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', KO)
                self.assertIn(f'"{key}"', EN)


if __name__ == "__main__":
    unittest.main()
