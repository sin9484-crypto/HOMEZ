"""
=========================================================
Homez OS

File : tests/test_retail_purchase_matching.py

Gate RP-1(2026-08-22) 검증 — evaluate_same_product() 동일상품 판정.
순수 함수라 DB/네트워크 없이 검증한다.
=========================================================
"""

import unittest

from app.domains.retail_purchase.constants import SameProductConfidenceTier
from app.domains.retail_purchase.product_matching import ProductAttributes
from app.domains.retail_purchase.product_matching import evaluate_same_product


def _full_attrs(**overrides):

    defaults = dict(
        brand="브랜드A", manufacturer="제조사A", model_name="MODEL-1",
        gtin="1111111111111", capacity="100ml", quantity=1,
        color_or_scent="블랙", options=("옵션1",), components=("본품", "설명서"),
    )
    defaults.update(overrides)
    return ProductAttributes(**defaults)


class SameProductMatchingTestCase(unittest.TestCase):

    def test_identical_attributes_yield_auto_candidate(self):

        source = _full_attrs()
        candidate = _full_attrs()
        result = evaluate_same_product(source, candidate)

        self.assertEqual(result.tier, SameProductConfidenceTier.AUTO_CANDIDATE)
        self.assertGreaterEqual(
            result.confidence, SameProductConfidenceTier.AUTO_CANDIDATE_THRESHOLD,
        )
        self.assertIsNone(result.blocked_reason)

    def test_gtin_mismatch_blocks_even_with_everything_else_matching(self):

        source = _full_attrs()
        candidate = _full_attrs(gtin="9999999999999")
        result = evaluate_same_product(source, candidate)

        self.assertEqual(result.tier, SameProductConfidenceTier.BLOCKED)
        self.assertEqual(result.blocked_reason, "CORE_ATTRIBUTE_MISMATCH:gtin")

    def test_color_mismatch_blocks(self):

        source = _full_attrs()
        candidate = _full_attrs(color_or_scent="화이트")
        result = evaluate_same_product(source, candidate)

        self.assertEqual(result.tier, SameProductConfidenceTier.BLOCKED)
        self.assertEqual(
            result.blocked_reason, "CORE_ATTRIBUTE_MISMATCH:color_or_scent",
        )

    def test_capacity_mismatch_blocks(self):

        source = _full_attrs()
        candidate = _full_attrs(capacity="250ml")
        result = evaluate_same_product(source, candidate)

        self.assertEqual(result.tier, SameProductConfidenceTier.BLOCKED)
        self.assertEqual(
            result.blocked_reason, "CORE_ATTRIBUTE_MISMATCH:capacity",
        )

    def test_options_mismatch_blocks(self):

        source = _full_attrs()
        candidate = _full_attrs(options=("다른옵션",))
        result = evaluate_same_product(source, candidate)

        self.assertEqual(result.tier, SameProductConfidenceTier.BLOCKED)
        self.assertEqual(
            result.blocked_reason, "CORE_ATTRIBUTE_MISMATCH:options",
        )

    def test_no_comparable_attributes_blocks_with_evidence_required(self):

        source = _full_attrs()
        candidate = ProductAttributes()
        result = evaluate_same_product(source, candidate)

        self.assertEqual(result.tier, SameProductConfidenceTier.BLOCKED)
        self.assertEqual(result.blocked_reason, "EVIDENCE_REQUIRED")
        self.assertEqual(result.confidence, 0.0)

    def test_low_coverage_high_match_ratio_does_not_yield_auto_candidate(self):
        """비교 가능한 항목이 몇 개 안 되는데 우연히 다 일치했다고
        AUTO_CANDIDATE(자동구매 후보)로 격상되면 안 된다 — 상품명
        일부 일치만으로 자동구매하지 않는다는 원칙을 코드로 강제."""

        source = _full_attrs()
        candidate = ProductAttributes(brand="브랜드A", model_name="MODEL-1")
        result = evaluate_same_product(source, candidate)

        self.assertNotEqual(result.tier, SameProductConfidenceTier.AUTO_CANDIDATE)

    def test_missing_manufacturer_and_options_still_allows_auto_candidate(self):
        """핵심 기준(바코드·색상·용량·옵션)이 전부 일치하고 커버리지도
        충분하면, 상대적으로 덜 중요한 필드(제조사) 하나가 빠져도
        AUTO_CANDIDATE에 도달할 수 있어야 한다(과도하게 엄격해서
        정상적인 매칭까지 항상 NEEDS_REVIEW로 떨어뜨리면 안 된다)."""

        source = _full_attrs()
        candidate = _full_attrs(manufacturer=None)
        result = evaluate_same_product(source, candidate)

        self.assertEqual(result.tier, SameProductConfidenceTier.AUTO_CANDIDATE)

    def test_partial_but_sufficient_coverage_yields_needs_review(self):

        source = _full_attrs()
        candidate = ProductAttributes(
            brand="브랜드A", manufacturer="제조사A", model_name="MODEL-1",
        )
        result = evaluate_same_product(source, candidate)

        self.assertIn(
            result.tier,
            (SameProductConfidenceTier.NEEDS_REVIEW, SameProductConfidenceTier.BLOCKED),
        )
        self.assertNotEqual(result.tier, SameProductConfidenceTier.AUTO_CANDIDATE)

    def test_evidence_lists_every_criterion_for_audit_trail(self):

        source = _full_attrs()
        candidate = _full_attrs()
        result = evaluate_same_product(source, candidate)

        criteria = {e.criterion for e in result.evidence}
        self.assertIn("gtin", criteria)
        self.assertIn("brand", criteria)
        self.assertIn("options", criteria)


if __name__ == "__main__":
    unittest.main()
