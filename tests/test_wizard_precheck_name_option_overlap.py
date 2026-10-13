"""
=========================================================
Homez OS

File : tests/test_wizard_precheck_name_option_overlap.py

상품명과 구매옵션 중복(2026-10-12). 백산수 등록(상품 16411745286)이 쿠팡 심사에서
"상품명에서 옵션과 중복된 단어를 지워주세요. 고객에게는 상품명 뒤에 옵션 정보가 함께
노출됩니다"로 반려됐다(상품명 "…500ml x 20PET" + 옵션 "20개, 500ml"). 사전검사가 같은 조건을
8단계 승인 전에 차단 사유로 알려 주는지 고정한다. 임시 DB도 쓰지 않는 순수 함수 검사다.
=========================================================
"""

import unittest
from pathlib import Path

from app.domains.marketplace_listing.listing_wizard_precheck import (
    product_name_option_overlaps,
)

DEFINITIONS = [
    {"attribute_type_name": "총 수량", "data_type": "NUMBER", "basic_unit": "개", "usable_units": ["개", "박스", "세트"]},
    {"attribute_type_name": "개당 용량", "data_type": "NUMBER", "basic_unit": "ml", "usable_units": ["L", "ml"]},
    {"attribute_type_name": "색상", "data_type": "STRING", "basic_unit": "없음", "usable_units": []},
]


class OverlapTests(unittest.TestCase):

    def test_the_rejected_name_is_flagged_for_the_volume_option(self):
        found = product_name_option_overlaps(
            "백산수 생수 500ml x 20PET", {"총 수량": "20", "개당 용량": "500"}, DEFINITIONS,
        )
        self.assertEqual(found, ["500ml"])

    def test_clean_name_passes(self):
        self.assertEqual(
            product_name_option_overlaps("백산수 생수", {"총 수량": "20", "개당 용량": "500"}, DEFINITIONS),
            [],
        )

    def test_comparison_ignores_spaces_and_case(self):
        self.assertEqual(
            product_name_option_overlaps("백산수 500 ML 생수", {"개당 용량": "500ml"}, DEFINITIONS),
            ["500ml"],
        )

    def test_value_with_unit_already_written_is_compared_as_is(self):
        self.assertEqual(
            product_name_option_overlaps("생수 20개입", {"총 수량": "20개"}, DEFINITIONS), ["20개"],
        )

    def test_string_options_such_as_color_are_checked_too(self):
        self.assertEqual(
            product_name_option_overlaps("무지 티셔츠 블랙", {"색상": "블랙"}, DEFINITIONS), ["블랙"],
        )

    def test_no_options_or_empty_values_never_flag(self):
        self.assertEqual(product_name_option_overlaps("백산수 생수 500ml", {}, DEFINITIONS), [])
        self.assertEqual(product_name_option_overlaps("백산수 생수 500ml", None, DEFINITIONS), [])
        self.assertEqual(product_name_option_overlaps("백산수 생수", {"색상": ""}, DEFINITIONS), [])

    def test_one_character_values_are_ignored_to_avoid_accidental_matches(self):
        self.assertEqual(product_name_option_overlaps("생수 가", {"색상": "가"}, DEFINITIONS), [])

    def test_purchase_option_without_definitions_still_compares_raw_value(self):
        self.assertEqual(product_name_option_overlaps("생수 500", {"개당 용량": "500"}, []), ["500"])


class WiringTests(unittest.TestCase):

    def test_precheck_blocks_with_a_translated_message(self):
        root = Path(__file__).resolve().parents[1] / "app"
        source = (root / "domains" / "marketplace_listing" / "listing_wizard_precheck.py").read_text(encoding="utf-8")
        self.assertIn('"PRODUCT_NAME_REPEATS_OPTION", WizardStep.DRAFT, True', source)
        for locale in ("ko-KR.js", "en-US.js"):
            text = (root / "web" / "i18n" / locale).read_text(encoding="utf-8")
            self.assertIn('"listing_wizard.precheck.product_name_repeats_option"', text)


if __name__ == "__main__":
    unittest.main()
