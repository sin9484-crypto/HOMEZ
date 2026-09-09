"""
=========================================================
Homez OS

File : tests/test_trend_discovery.py

HOMEZ V3 Trend Discovery AI 검증
Fixture Adapter만 사용(외부 네트워크 없음), 결정론적 점수 계산 검증.
=========================================================
"""

import unittest
from datetime import datetime
from datetime import timezone

from app.domains.trend_discovery.adapter import FixtureTrendAdapter
from app.domains.trend_discovery.adapter import TrendSignal
from app.domains.trend_discovery.service import TrendDiscoveryService


class TrendDiscoveryTestCase(unittest.TestCase):

    def setUp(self):

        self.service = TrendDiscoveryService()
        self.now = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)

    # --------------------------------------------------
    # 상승 / 하락 추세
    # --------------------------------------------------

    def test_rising_trend_scores_above_baseline(self):

        signal = TrendSignal(
            keyword="rising-item",
            market="COUPANG",
            search_volume_series=[100, 120, 150, 200],
            competitor_count=5,
            observed_at=self.now.isoformat(),
            source="fixture",
        )
        result = self.service.evaluate(signal, now=self.now)

        self.assertGreater(result.trend_score, 0.5)
        self.assertGreater(result.confidence, 0.0)

    def test_declining_trend_scores_below_baseline(self):

        signal = TrendSignal(
            keyword="declining-item",
            market="COUPANG",
            search_volume_series=[200, 150, 100, 50],
            competitor_count=5,
            observed_at=self.now.isoformat(),
            source="fixture",
        )
        result = self.service.evaluate(signal, now=self.now)

        self.assertLess(result.trend_score, 0.5)

    # --------------------------------------------------
    # 데이터 부족
    # --------------------------------------------------

    def test_insufficient_data_points_yields_low_confidence(self):

        signal = TrendSignal(
            keyword="scarce",
            market="COUPANG",
            search_volume_series=[100, 110],  # MIN_DATA_POINTS(3) 미만
            observed_at=self.now.isoformat(),
            source="fixture",
        )
        result = self.service.evaluate(signal, now=self.now)

        self.assertEqual(result.trend_score, 0.0)
        self.assertLess(result.confidence, 0.3)
        self.assertTrue(len(result.limitations) > 0)

    def test_no_signal_returns_zero_score_and_zero_confidence(self):

        result = self.service.evaluate(None, now=self.now)

        self.assertEqual(result.trend_score, 0.0)
        self.assertEqual(result.confidence, 0.0)

    # --------------------------------------------------
    # 오래된 데이터
    # --------------------------------------------------

    def test_stale_data_reduces_confidence(self):

        old_observed = datetime(2026, 7, 1, tzinfo=timezone.utc)  # 27일 전
        signal = TrendSignal(
            keyword="stale-item",
            market="COUPANG",
            search_volume_series=[100, 120, 150],
            competitor_count=5,
            observed_at=old_observed.isoformat(),
            source="fixture",
        )

        fresh_signal = TrendSignal(
            keyword="fresh-item",
            market="COUPANG",
            search_volume_series=[100, 120, 150],
            competitor_count=5,
            observed_at=self.now.isoformat(),
            source="fixture",
        )

        stale_result = self.service.evaluate(signal, now=self.now)
        fresh_result = self.service.evaluate(fresh_signal, now=self.now)

        self.assertLess(stale_result.confidence, fresh_result.confidence)
        self.assertTrue(
            any("오래된" in limitation for limitation in stale_result.limitations),
        )

    # --------------------------------------------------
    # 중복 근거(변화 없는 데이터)
    # --------------------------------------------------

    def test_flat_duplicate_evidence_yields_zero_growth_and_lower_confidence(self):

        signal = TrendSignal(
            keyword="flat-item",
            market="COUPANG",
            search_volume_series=[100, 100, 100, 100],
            competitor_count=5,
            observed_at=self.now.isoformat(),
            source="fixture",
        )
        result = self.service.evaluate(signal, now=self.now)

        self.assertTrue(
            any("동일함" in limitation for limitation in result.limitations),
        )
        self.assertLess(result.confidence, 0.9)

    # --------------------------------------------------
    # 금지상품
    # --------------------------------------------------

    def test_banned_category_forces_zero_score(self):

        signal = TrendSignal(
            keyword="banned-item",
            market="COUPANG",
            search_volume_series=[100, 500, 1000],
            competitor_count=1,
            observed_at=self.now.isoformat(),
            source="fixture",
            is_banned_category=True,
        )
        result = self.service.evaluate(signal, now=self.now)

        self.assertEqual(result.trend_score, 0.0)
        self.assertEqual(result.confidence, 0.0)
        self.assertTrue(len(result.limitations) > 0)

    # --------------------------------------------------
    # 경계값 (클리핑)
    # --------------------------------------------------

    def test_extreme_growth_clips_to_one(self):

        signal = TrendSignal(
            keyword="extreme-growth",
            market="COUPANG",
            search_volume_series=[1, 500, 100000],
            competitor_count=0,
            observed_at=self.now.isoformat(),
            source="fixture",
        )
        result = self.service.evaluate(signal, now=self.now)

        self.assertEqual(result.trend_score, 1.0)

    def test_extreme_decline_and_competition_clips_to_zero(self):

        signal = TrendSignal(
            keyword="extreme-decline",
            market="COUPANG",
            search_volume_series=[100000, 500, 1],
            competitor_count=1000,
            observed_at=self.now.isoformat(),
            source="fixture",
        )
        result = self.service.evaluate(signal, now=self.now)

        self.assertEqual(result.trend_score, 0.0)

    # --------------------------------------------------
    # 결정론적 재실행
    # --------------------------------------------------

    def test_same_input_produces_identical_result_on_rerun(self):

        signal = TrendSignal(
            keyword="deterministic-item",
            market="COUPANG",
            search_volume_series=[100, 130, 160],
            competitor_count=10,
            observed_at=self.now.isoformat(),
            source="fixture",
        )

        first = self.service.evaluate(signal, now=self.now)
        second = self.service.evaluate(signal, now=self.now)

        self.assertEqual(first.as_dict(), second.as_dict())

    # --------------------------------------------------
    # FixtureTrendAdapter 자체 검증 (외부 네트워크 없이 keyword로 조회)
    # --------------------------------------------------

    def test_fixture_adapter_returns_none_for_unknown_keyword(self):

        adapter = FixtureTrendAdapter(fixtures={})
        self.assertIsNone(adapter.fetch("unknown-keyword"))

    def test_fixture_adapter_returns_configured_signal(self):

        signal = TrendSignal(
            keyword="known-item",
            market="COUPANG",
            search_volume_series=[1, 2, 3],
            observed_at=self.now.isoformat(),
        )
        adapter = FixtureTrendAdapter(fixtures={"known-item": signal})

        fetched = adapter.fetch("known-item")
        self.assertIs(fetched, signal)

    # --------------------------------------------------
    # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry 실연결
    # --------------------------------------------------

    def test_evaluate_blocks_when_product_analysis_capability_deactivated(self):

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        signal = TrendSignal(
            keyword="known-item", market="COUPANG",
            search_volume_series=[1, 2, 3, 4],
            observed_at=self.now.isoformat(),
        )

        with deactivated_capability("PRODUCT_ANALYSIS"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.evaluate(signal, now=self.now)

    def test_fetch_blocks_when_product_discovery_capability_deactivated(self):

        from app.domains.ai_governance.service import InactiveCapabilityError
        from tests.ai_governance_test_helpers import deactivated_capability

        signal = TrendSignal(
            keyword="known-item", market="COUPANG",
            search_volume_series=[1, 2, 3, 4],
            observed_at=self.now.isoformat(),
        )
        adapter = FixtureTrendAdapter(fixtures={"known-item": signal})

        with deactivated_capability("PRODUCT_DISCOVERY"):
            with self.assertRaises(InactiveCapabilityError):
                adapter.fetch("known-item")


if __name__ == "__main__":
    unittest.main()
