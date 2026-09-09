"""
=========================================================
Homez OS

File : tests/test_submission_reconciliation_matching.py

2026-08-31 V7 필수 작업 2번(제출 장부 정합화 완성) —
submission_reconciliation_matching.py 순수 함수 단위 테스트. DB·
Provider 없이 evaluate_identifier_match()의 판정 규칙만 검증한다.
=========================================================
"""

import unittest

from app.domains.marketplace_listing.submission_reconciliation_matching import (
    MATCH_TIER_AUTO_ELIGIBLE,
    MATCH_TIER_BLOCKED,
    MATCH_TIER_MANUAL_REVIEW_REQUIRED,
    evaluate_identifier_match,
)


class EvaluateIdentifierMatchTestCase(unittest.TestCase):

    def test_no_comparable_values_is_manual_review_required(self):

        assessment = evaluate_identifier_match(
            expected_product_name=None, expected_vendor_user_id=None,
            expected_display_category_code=None,
            observed_seller_product_name=None, observed_vendor_user_id=None,
            observed_display_category_code=None,
        )

        self.assertEqual(assessment.tier, MATCH_TIER_MANUAL_REVIEW_REQUIRED)
        self.assertEqual(assessment.matched_fields, ())
        self.assertEqual(len(assessment.unavailable_fields), 3)

    def test_single_match_is_manual_review_required_not_auto(self):

        assessment = evaluate_identifier_match(
            expected_product_name="상품 A", expected_vendor_user_id=None,
            expected_display_category_code=None,
            observed_seller_product_name="상품 A", observed_vendor_user_id=None,
            observed_display_category_code=None,
        )

        self.assertEqual(assessment.tier, MATCH_TIER_MANUAL_REVIEW_REQUIRED)
        self.assertEqual(assessment.matched_fields, ("product_name",))

    def test_two_matches_is_auto_eligible(self):

        assessment = evaluate_identifier_match(
            expected_product_name="상품 A", expected_vendor_user_id="VENDOR-1",
            expected_display_category_code=None,
            observed_seller_product_name="상품 A", observed_vendor_user_id="VENDOR-1",
            observed_display_category_code=None,
        )

        self.assertEqual(assessment.tier, MATCH_TIER_AUTO_ELIGIBLE)

    def test_three_matches_is_auto_eligible(self):

        assessment = evaluate_identifier_match(
            expected_product_name="상품 A", expected_vendor_user_id="VENDOR-1",
            expected_display_category_code="80754",
            observed_seller_product_name="상품 A", observed_vendor_user_id="VENDOR-1",
            observed_display_category_code="80754",
        )

        self.assertEqual(assessment.tier, MATCH_TIER_AUTO_ELIGIBLE)
        self.assertEqual(len(assessment.matched_fields), 3)

    def test_single_mismatch_blocks_even_with_other_matches(self):

        assessment = evaluate_identifier_match(
            expected_product_name="상품 A", expected_vendor_user_id="VENDOR-1",
            expected_display_category_code="80754",
            observed_seller_product_name="상품 A", observed_vendor_user_id="VENDOR-1",
            observed_display_category_code="99999",
        )

        self.assertEqual(assessment.tier, MATCH_TIER_BLOCKED)
        self.assertEqual(assessment.mismatched_fields, ("display_category_code",))

    def test_product_name_comparison_ignores_case_and_whitespace(self):

        assessment = evaluate_identifier_match(
            expected_product_name="  Homez   Kitchen Towel  ",
            expected_vendor_user_id=None, expected_display_category_code=None,
            observed_seller_product_name="homez kitchen towel",
            observed_vendor_user_id=None, observed_display_category_code=None,
        )

        self.assertEqual(assessment.matched_fields, ("product_name",))

    def test_product_name_is_not_fuzzy_matched(self):
        # 유사도 매칭을 하지 않는다 — 부분 문자열이 겹쳐도 다르면
        # MISMATCH다(과잉 신뢰 방지).

        assessment = evaluate_identifier_match(
            expected_product_name="Homez Kitchen Towel Set A",
            expected_vendor_user_id=None, expected_display_category_code=None,
            observed_seller_product_name="Homez Kitchen Towel Set B",
            observed_vendor_user_id=None, observed_display_category_code=None,
        )

        self.assertEqual(assessment.tier, MATCH_TIER_BLOCKED)
        self.assertEqual(assessment.mismatched_fields, ("product_name",))

    def test_vendor_user_id_is_case_sensitive_exact_match(self):

        assessment = evaluate_identifier_match(
            expected_product_name=None, expected_vendor_user_id="Vendor-001",
            expected_display_category_code=None,
            observed_seller_product_name=None, observed_vendor_user_id="vendor-001",
            observed_display_category_code=None,
        )

        self.assertEqual(assessment.tier, MATCH_TIER_BLOCKED)

    def test_empty_string_is_treated_as_unavailable_not_mismatch(self):

        assessment = evaluate_identifier_match(
            expected_product_name="", expected_vendor_user_id=None,
            expected_display_category_code=None,
            observed_seller_product_name="상품 A", observed_vendor_user_id=None,
            observed_display_category_code=None,
        )

        self.assertEqual(assessment.tier, MATCH_TIER_MANUAL_REVIEW_REQUIRED)
        self.assertEqual(assessment.unavailable_fields, (
            "product_name", "vendor_user_id", "display_category_code",
        ))


if __name__ == "__main__":
    unittest.main()
