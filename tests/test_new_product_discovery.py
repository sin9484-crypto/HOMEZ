"""
=========================================================
Homez OS

File : tests/test_new_product_discovery.py

HOMEZ V3 New Product Discovery AI 검증
15일 신제품 기준, Asia/Seoul(KST, UTC+9) timezone 경계, 결정론적 재실행.
=========================================================
"""

import unittest
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from app.domains.new_product_discovery.service import KST
from app.domains.new_product_discovery.service import NewProductDiscoveryService
from app.domains.new_product_discovery.service import NewProductEvaluationError


class NewProductDiscoveryTestCase(unittest.TestCase):

    def setUp(self):

        self.service = NewProductDiscoveryService()

    # --------------------------------------------------
    # 출시 당일 / 경계값
    # --------------------------------------------------

    def test_release_day_zero_is_new_product(self):

        now = datetime(2026, 7, 28, 12, 0, tzinfo=KST)
        result = self.service.evaluate(date(2026, 7, 28), now=now)

        self.assertTrue(result.is_new_product)
        self.assertEqual(result.age_days, 0)
        self.assertEqual(result.novelty_score, 1.0)

    def test_age_14_days_is_new_product(self):

        now = datetime(2026, 7, 28, 12, 0, tzinfo=KST)
        result = self.service.evaluate(date(2026, 7, 14), now=now)

        self.assertTrue(result.is_new_product)
        self.assertEqual(result.age_days, 14)

    def test_age_15_days_is_new_product_boundary_inclusive(self):

        now = datetime(2026, 7, 28, 12, 0, tzinfo=KST)
        result = self.service.evaluate(date(2026, 7, 13), now=now)

        self.assertTrue(result.is_new_product)
        self.assertEqual(result.age_days, 15)

    def test_age_16_days_is_not_new_product(self):

        now = datetime(2026, 7, 28, 12, 0, tzinfo=KST)
        result = self.service.evaluate(date(2026, 7, 12), now=now)

        self.assertFalse(result.is_new_product)
        self.assertEqual(result.age_days, 16)
        self.assertEqual(result.novelty_score, 0.0)

    # --------------------------------------------------
    # 미래 날짜
    # --------------------------------------------------

    def test_future_release_date_raises(self):

        now = datetime(2026, 7, 28, 12, 0, tzinfo=KST)

        with self.assertRaises(NewProductEvaluationError):
            self.service.evaluate(date(2026, 8, 1), now=now)

    # --------------------------------------------------
    # 날짜 불명
    # --------------------------------------------------

    def test_unknown_release_date_is_not_auto_new_product(self):

        result = self.service.evaluate(None)

        self.assertFalse(result.is_new_product)
        self.assertIsNone(result.age_days)
        self.assertEqual(result.confidence, 0.0)
        self.assertTrue(len(result.limitations) > 0)

    # --------------------------------------------------
    # timezone 경계 (Asia/Seoul, UTC+9)
    # --------------------------------------------------

    def test_timezone_boundary_uses_kst_date_not_utc_date(self):
        """
        UTC 2026-07-27 16:00은 KST로는 이미 2026-07-28 01:00이다.
        release_date=2026-07-28이면 KST 기준으로는 "당일"(age_days=0)이어야
        하며, UTC 날짜(2026-07-27)만으로 순진하게 계산하면 미래 날짜로
        오판(age_days=-1, 오류 발생)하게 되는 것이 바로 이 테스트가 막는
        회귀다.
        """

        now_utc = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)
        result = self.service.evaluate(date(2026, 7, 28), now=now_utc)

        self.assertEqual(result.age_days, 0)
        self.assertTrue(result.is_new_product)

    def test_timezone_naive_datetime_is_treated_as_kst(self):

        naive_now = datetime(2026, 7, 28, 9, 0)  # tzinfo 없음
        result = self.service.evaluate(date(2026, 7, 28), now=naive_now)

        self.assertEqual(result.age_days, 0)

    # --------------------------------------------------
    # 결정론적 재실행
    # --------------------------------------------------

    def test_same_input_produces_identical_result_on_rerun(self):

        now = datetime(2026, 7, 28, 12, 0, tzinfo=KST)

        first = self.service.evaluate(date(2026, 7, 20), now=now)
        second = self.service.evaluate(date(2026, 7, 20), now=now)

        self.assertEqual(first.as_dict(), second.as_dict())

    def test_kst_is_fixed_utc_plus_9_no_dst(self):
        """한국 표준시는 서머타임이 없는 연중 고정 UTC+9이어야 한다."""

        self.assertEqual(KST.utcoffset(None), timedelta(hours=9))

    # --------------------------------------------------
    # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry 실연결
    # --------------------------------------------------

    def test_evaluate_blocks_when_product_analysis_capability_deactivated(self):

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        now = datetime(2026, 7, 28, 12, 0, tzinfo=KST)

        with deactivated_capability("PRODUCT_ANALYSIS"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.evaluate(date(2026, 7, 20), now=now)


if __name__ == "__main__":
    unittest.main()
