"""
=========================================================
Homez OS

File : tests/test_decision_scoring_v11.py

2026-10-29 — 의사결정 AI 평가기 1.1.0(D1 평가 기준 초안과 함께)을 고정한다.

  - 후보 점수는 V3 계약대로 0~1을 0~100으로 읽는다(실제 트렌드 0.8이 0.8점이 되던 결함).
  - 근거 없는 축은 0점으로 더하지 않고 근거 있는 축의 가중치로 나눈다. 근거 비중 70% 미만이면 데이터 부족.
  - 승인 추천에는 마진·정책·상표·공급 근거가 모두 있어야 한다(없으면 보류 추천까지).
  - 예상 마진율이 회사 최소 목표 마진율 미만이면 검토 필요. 검토 사유는 실제로 걸린 축으로 쓴다.
  - 후보 사전 평가(S2) → 보강 입력 제안 변환표.
격리 SQLite(실제 homez.db 사용 안 함), 외부 호출 없음.
=========================================================
"""

import json
import unittest
from decimal import Decimal

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.channel_policy.model import CompanyChannelPolicySettings
from app.domains.decision.constants import EvaluationRecommendation
from app.domains.decision.constants import ScoreAxis
from app.domains.decision.input_suggestion import suggest_inputs
from app.domains.decision.schema import DecisionSupplementaryInputs
from app.domains.decision.service import EVALUATOR_VERSION
from app.domains.decision.service import REQUIRED_FOR_APPROVE
from app.domains.decision.service import aggregate_scores
from app.domains.decision.service import evaluate_axes
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from tests.test_decision_ai import COMPANY_ID
from tests.test_decision_ai import FULL_WEIGHTS
import tests.test_decision_ai as decision_base  # 클래스를 직접 가져오면 그 시험들이 여기서 다시 돈다


def axis(results, name):
    return next(r for r in results if r.axis == name)


class Base_(unittest.TestCase):
    # 기존 시험 클래스의 준비 함수만 빌린다(상속하면 그 시험들까지 다시 돈다).
    _base_setup = decision_base.DecisionAITestCase.setUp
    tearDown = decision_base.DecisionAITestCase.tearDown
    _create_safety_tables = decision_base.DecisionAITestCase._create_safety_tables
    _seed_policy = decision_base.DecisionAITestCase._seed_policy
    _create_candidate = decision_base.DecisionAITestCase._create_candidate
    _full_supplementary = decision_base.DecisionAITestCase._full_supplementary

    def setUp(self):
        self._base_setup()
        self._create_safety_tables()
        self._seed_policy()

    def bare_candidate(self, ref="BARE", **scores):
        candidate = ProductCandidate(
            candidate_key=f"COUPANG_API:COUPANG:{ref}", source_type="COUPANG_API", market="COUPANG",
            source_reference=ref, product_name="평가 상품", status=CandidateStatus.DISCOVERED, **scores,
        )
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)
        return candidate

    def evaluate(self, candidate, key, supplementary):
        return self.service.evaluate_candidate(candidate.id, COMPANY_ID, key, supplementary)


class ScaleTests(Base_):

    def test_candidate_scores_are_read_as_zero_to_one(self):
        candidate = self.bare_candidate(trend_score=0.8, competition_score=0.6, risk_score=0.1, confidence=0.9)
        results = evaluate_axes(candidate, DecisionSupplementaryInputs())
        self.assertEqual(axis(results, ScoreAxis.DEMAND_TREND_STRENGTH).raw_score, Decimal("80.0000"))
        self.assertEqual(axis(results, ScoreAxis.COMPETITION_INTENSITY).raw_score, Decimal("60.0000"))
        self.assertEqual(axis(results, ScoreAxis.POLICY_PROHIBITED_RISK).raw_score, Decimal("90.0000"))   # 위험 0.1 → 안전 90

    def test_hundred_scale_values_are_refused_not_guessed(self):
        candidate = self.bare_candidate(ref="H", trend_score=80.0)
        with self.assertRaises(BadRequestException):
            evaluate_axes(candidate, DecisionSupplementaryInputs())

    def test_the_evaluator_version_and_margin_floor_are_recorded(self):
        candidate = self._create_candidate()
        evaluation, _, _ = self.evaluate(candidate, "v", self._full_supplementary())
        self.assertEqual((evaluation.evaluator_version, EVALUATOR_VERSION), ("1.1.0", "1.1.0"))
        self.assertEqual(json.loads(evaluation.input_snapshot_json)["min_margin_rate"], "0.18")


class AggregateTests(unittest.TestCase):

    def results(self, present):
        from app.domains.decision.service import AxisResult
        out = []
        for name in ScoreAxis.ALL:
            ok = name in present
            out.append(AxisResult(name, Decimal(present.get(name, 0)), Decimal("0.9") if ok else Decimal("0"), ok, False, ""))
        return out

    def test_missing_axes_do_not_count_as_zero(self):
        present = {a: 80 for a in ScoreAxis.ALL if a != ScoreAxis.REVENUE_POTENTIAL}
        total, confidence, coverage = aggregate_scores(self.results(present), FULL_WEIGHTS)
        self.assertEqual(total, Decimal("80.0000"))                       # 전에는 80 × 0.88 = 70.4로 끌려 내려갔다
        self.assertEqual(confidence, Decimal("0.9000"))
        self.assertEqual(coverage, Decimal("0.8800"))

    def test_mixed_scores_are_weighted_over_present_axes_only(self):
        present = {ScoreAxis.MARGIN_AND_FEES: 100, ScoreAxis.POLICY_PROHIBITED_RISK: 50}
        total, _, coverage = aggregate_scores(self.results(present), FULL_WEIGHTS)
        self.assertEqual(coverage, Decimal("0.2500"))
        self.assertEqual(total, Decimal("80.0000"))                       # (100×0.15 + 50×0.10) / 0.25

    def test_no_evidence_gives_zeros(self):
        self.assertEqual(aggregate_scores(self.results({}), FULL_WEIGHTS), (Decimal("0.0000"),) * 3)


class RecommendationTests(Base_):

    def test_low_weight_coverage_is_insufficient_even_with_six_axes(self):
        # 근거 있는 축 7개(매출·가격·공급·배송·반품·상표·자금) = 가중치 0.52 → 70% 미만
        candidate = self.bare_candidate(ref="COV")
        evaluation, scores, _ = self.evaluate(candidate, "cov", self._full_supplementary(expected_margin_rate=None))
        self.assertGreaterEqual(sum(1 for s in scores if s.data_sufficient), 6)
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.INSUFFICIENT_DATA)
        self.assertIn("근거 있는 항목 비중 52%", evaluation.recommendation_reason)

    def test_a_high_score_without_required_evidence_is_held_not_approved(self):
        candidate = self._create_candidate(ref="REQ")
        evaluation, _, _ = self.evaluate(candidate, "req", self._full_supplementary(supply_stability_score=None))
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.RECOMMEND_HOLD)
        self.assertIn(ScoreAxis.SUPPLY_STABILITY, evaluation.recommendation_reason)

    def test_complete_evidence_can_be_recommended_for_approval(self):
        candidate = self._create_candidate(ref="OK")
        evaluation, _, _ = self.evaluate(candidate, "ok", self._full_supplementary())
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.RECOMMEND_APPROVE)
        self.assertIn("근거 있는 항목 비중 100%", evaluation.recommendation_reason)
        self.assertEqual(set(REQUIRED_FOR_APPROVE), {ScoreAxis.MARGIN_AND_FEES, ScoreAxis.POLICY_PROHIBITED_RISK,
                                                     ScoreAxis.BRAND_IP_RISK, ScoreAxis.SUPPLY_STABILITY})

    def test_margin_below_the_company_floor_needs_review(self):
        candidate = self._create_candidate(ref="M")
        evaluation, scores, _ = self.evaluate(candidate, "m", self._full_supplementary(expected_margin_rate=Decimal("0.17")))
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.REVIEW_REQUIRED)
        self.assertIn("최소 목표 마진율", evaluation.recommendation_reason)
        self.assertNotIn("정책·금지상품", evaluation.recommendation_reason)
        self.assertTrue(axis(scores, ScoreAxis.MARGIN_AND_FEES).risk_flag)
        exact, scores, _ = self.evaluate(candidate, "m2", self._full_supplementary(expected_margin_rate=Decimal("0.18")))
        self.assertFalse(axis(scores, ScoreAxis.MARGIN_AND_FEES).risk_flag)    # 기준과 같으면 통과

    def test_the_company_margin_setting_overrides_the_default(self):
        Base.metadata.create_all(bind=self.engine, tables=[CompanyChannelPolicySettings.__table__])
        self.db.add(CompanyChannelPolicySettings(company_id=COMPANY_ID, min_target_margin_rate=Decimal("0.25"), updated_by=1))
        self.db.commit()
        candidate = self._create_candidate(ref="CS")
        evaluation, _, _ = self.evaluate(candidate, "cs", self._full_supplementary(expected_margin_rate=Decimal("0.20")))
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.REVIEW_REQUIRED)
        self.assertEqual(json.loads(evaluation.input_snapshot_json)["min_margin_rate"], "0.2500")

    def test_a_funding_shortfall_is_named_as_such(self):
        candidate = self._create_candidate(ref="F")
        evaluation, _, _ = self.evaluate(candidate, "f", self._full_supplementary(required_funding=Decimal("90000")))
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.REVIEW_REQUIRED)
        self.assertIn("필요 자금", evaluation.recommendation_reason)
        self.assertNotIn("정책·금지상품", evaluation.recommendation_reason)        # 전에는 자금 부족도 '정책 위험'으로 적혔다

    def test_the_policy_score_input_fills_in_and_can_block(self):
        candidate = self._create_candidate(ref="P", risk_score=None)
        safe = evaluate_axes(candidate, self._full_supplementary(policy_prohibited_score=Decimal("90")))
        self.assertEqual((axis(safe, ScoreAxis.POLICY_PROHIBITED_RISK).raw_score, axis(safe, ScoreAxis.POLICY_PROHIBITED_RISK).risk_flag),
                         (Decimal("90.0000"), False))
        evaluation, scores, _ = self.evaluate(candidate, "p", self._full_supplementary(policy_prohibited_score=Decimal("0")))
        self.assertTrue(axis(scores, ScoreAxis.POLICY_PROHIBITED_RISK).risk_flag)
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.REVIEW_REQUIRED)
        self.assertIn("정책·금지상품", evaluation.recommendation_reason)
        unknown = evaluate_axes(candidate, self._full_supplementary())
        self.assertFalse(axis(unknown, ScoreAxis.POLICY_PROHIBITED_RISK).data_sufficient)

    def test_a_risky_brand_is_reviewed_not_approved(self):
        candidate = self._create_candidate(ref="B")
        evaluation, scores, _ = self.evaluate(candidate, "b", self._full_supplementary(brand_ip_risk_score=Decimal("30")))
        self.assertGreaterEqual(evaluation.total_score, Decimal("70"))                  # 점수만 보면 승인 추천감이다
        self.assertEqual(evaluation.recommendation, EvaluationRecommendation.REVIEW_REQUIRED)
        self.assertIn("상표·지식재산", evaluation.recommendation_reason)
        self.assertNotIn("정책·금지상품", evaluation.recommendation_reason)
        self.assertTrue(axis(scores, ScoreAxis.BRAND_IP_RISK).risk_flag)
        _, scores, _ = self.evaluate(candidate, "b2", self._full_supplementary(brand_ip_risk_score=Decimal("40")))
        self.assertFalse(axis(scores, ScoreAxis.BRAND_IP_RISK).risk_flag)               # 기준과 같으면 통과

    def test_the_candidate_risk_score_wins_over_the_input(self):
        candidate = self._create_candidate(ref="W", risk_score=0.95)
        results = evaluate_axes(candidate, self._full_supplementary(policy_prohibited_score=Decimal("100")))
        self.assertTrue(axis(results, ScoreAxis.POLICY_PROHIBITED_RISK).risk_flag)


class SuggestionTests(unittest.TestCase):

    def reasons(self, *codes):
        return [{"code": c, "level": "HOLD", "params": {}} for c in codes]

    def test_policy_and_brand_mapping(self):
        self.assertEqual(suggest_inputs(self.reasons("PROHIBITED_ITEM", "CLAIM_WORDS"), None)["inputs"]["policy_prohibited_score"], Decimal("0"))
        for code in ("CLAIM_WORDS", "CHILD_TERMS", "REGULATED_CATEGORY", "KC_CERT"):
            self.assertEqual(suggest_inputs(self.reasons(code), None)["inputs"]["policy_prohibited_score"], Decimal("50"), code)
        clear = suggest_inputs(self.reasons("MARGIN_NOT_ANALYZED"), None)
        self.assertEqual((clear["inputs"]["policy_prohibited_score"], clear["inputs"]["brand_ip_risk_score"]), (Decimal("90"), Decimal("70")))
        self.assertEqual(suggest_inputs(self.reasons("BRAND_IP_WORD"), None)["inputs"]["brand_ip_risk_score"], Decimal("30"))

    def test_unknowns_are_not_suggested(self):
        no_notice = suggest_inputs(self.reasons("NO_NOTICE"), None)
        self.assertNotIn("policy_prohibited_score", no_notice["inputs"])
        nothing = suggest_inputs(None, None)
        self.assertEqual(nothing["inputs"], {})                              # 사전 평가가 없으면 '걸린 것 없음'이 아니라 '모름'
        self.assertIn("supply_stability_score", nothing["not_suggested"])

    def test_margin_comes_only_from_a_candidate_snapshot_with_a_rate(self):
        snap = {"verdict": "CANDIDATE", "scenarios": [{"t": 0.18, "p": 15900, "m": 0.2213}]}
        result = suggest_inputs(self.reasons(), snap)
        self.assertEqual(result["inputs"]["expected_margin_rate"], Decimal("0.2213"))
        self.assertIn("15900", next(b["note"] for b in result["basis"] if b["field"] == "expected_margin_rate"))
        for bad in ({"verdict": "NOT_RECOMMENDED"}, {"verdict": "HOLD"}, {"verdict": "CANDIDATE", "scenarios": [{"p": 1, "m": "x"}]},
                    {"verdict": "CANDIDATE", "scenarios": [{"p": 1, "m": 1.5}]}, {"verdict": "CANDIDATE", "scenarios": [{"p": 1, "m": True}]},
                    {"verdict": "HOLD", "scenarios": [{"p": 15900, "m": 0.22}]}):
            self.assertNotIn("expected_margin_rate", suggest_inputs(self.reasons(), bad)["inputs"], bad)


class SuggestionServiceTests(Base_):

    def test_the_service_reads_only_and_hides_other_companies(self):
        from app.domains.product_candidate.model import ProductCandidateEvidence
        Base.metadata.create_all(bind=self.engine, tables=[ProductCandidateEvidence.__table__])
        from app.core.exceptions import NotFoundException
        from app.domains.product_candidate.service import ProductCandidateService

        candidate = self._create_candidate(ref="S")
        counts = lambda: (self.db.query(ProductCandidate).count(), self.db.query(ProductCandidateEvidence).count())  # noqa: E731
        before = counts()
        with unittest.mock.patch(
            "app.domains.product_candidate.candidate_evaluation_service.CandidateEvaluationService.evaluate_all",
            return_value={"items": [{"candidate_id": candidate.id, "decision": "HOLD",
                                     "reasons": [{"code": "KC_CERT", "level": "HOLD", "params": {}}]}]},
        ):
            result = self.service.suggest_inputs(candidate.id, COMPANY_ID)
        self.assertEqual((result["precheck_decision"], result["inputs"]["policy_prohibited_score"]), ("HOLD", "50"))
        self.assertEqual(counts(), before)
        with unittest.mock.patch.object(ProductCandidateService, "get_visible_for_company", side_effect=NotFoundException("x")):
            with self.assertRaises(NotFoundException):
                self.service.suggest_inputs(candidate.id, 2)


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
