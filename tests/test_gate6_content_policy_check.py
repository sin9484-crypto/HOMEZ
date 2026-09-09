"""
=========================================================
Homez OS

File : tests/test_gate6_content_policy_check.py

V7 Gate 6 — 저작권/금지품목 간단 체크리스트(규칙 기반) 검증. 실제
이미지 인식 AI를 쓰지 않는다는 계약과, 결정적(순수 함수)임을
확인한다.
=========================================================
"""

import unittest

from app.domains.media_asset.content_policy_check import (
    run_content_policy_checklist,
)


class ContentPolicyCheckTestCase(unittest.TestCase):

    def test_safe_candidate_passes_with_no_flags(self):

        result = run_content_policy_checklist(
            "친환경 대나무 칫솔", "생활용품", "HOMEZ",
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_flags, ())
        self.assertEqual(result.warning_flags, ())

    def test_prohibited_keyword_in_product_name_blocks(self):

        result = run_content_policy_checklist(
            "모형 총기 완구", "완구", None,
        )

        self.assertFalse(result.passed)
        self.assertIn("총기", result.blocking_flags)

    def test_prohibited_keyword_in_category_blocks(self):

        result = run_content_policy_checklist(
            "테스트 상품", "마약 관련 카테고리", None,
        )

        self.assertFalse(result.passed)
        self.assertIn("마약", result.blocking_flags)

    def test_high_ip_risk_brand_warns_but_does_not_block(self):

        result = run_content_policy_checklist(
            "운동화", "신발", "나이키",
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.blocking_flags, ())
        self.assertIn("나이키", result.warning_flags)

    def test_case_insensitive_english_brand_warns(self):

        result = run_content_policy_checklist(
            "sneakers", "shoes", "Nike Air",
        )

        self.assertTrue(result.passed)
        self.assertIn("nike", result.warning_flags)

    def test_deterministic_same_input_same_output(self):

        first = run_content_policy_checklist("상품", "카테고리", "브랜드")
        second = run_content_policy_checklist("상품", "카테고리", "브랜드")

        self.assertEqual(first, second)

    def test_none_hints_do_not_crash(self):

        result = run_content_policy_checklist("상품", None, None)

        self.assertTrue(result.passed)

    def test_checklist_version_is_reported(self):

        result = run_content_policy_checklist("상품", None, None)

        self.assertEqual(result.checklist_version, "content_policy_check.v1")


if __name__ == "__main__":
    unittest.main()
