"""
=========================================================
Homez OS

File : tests/test_marketplace_decision_integration.py

Decision AI × 채널별 판매 방식 연동 검증:
12) 채널별 비용 혼합 방지
13) 누락 비용 0원 자동 보정 금지
14) 방식 변경 시 Decision 재평가
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.decision.constants import DecisionPolicyStatus
from app.domains.decision.constants import ScoreAxis
from app.domains.decision.model import CompanyDecisionPolicy
from app.domains.decision.model import DecisionAuditLog
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionPolicy
from app.domains.decision.model import DecisionReview
from app.domains.decision.model import DecisionScore
from app.domains.decision.schema import DecisionPolicyCreateRequest
from app.domains.decision.schema import DecisionSupplementaryInputs
from app.domains.decision.service import DecisionService
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate

FULL_WEIGHTS = {
    ScoreAxis.REVENUE_POTENTIAL: Decimal("0.12"),
    ScoreAxis.MARGIN_AND_FEES: Decimal("0.15"),
    ScoreAxis.PRICE_COMPETITIVENESS: Decimal("0.08"),
    ScoreAxis.DEMAND_TREND_STRENGTH: Decimal("0.12"),
    ScoreAxis.COMPETITION_INTENSITY: Decimal("0.08"),
    ScoreAxis.SUPPLY_STABILITY: Decimal("0.08"),
    ScoreAxis.INVENTORY_SHIPPING_RISK: Decimal("0.07"),
    ScoreAxis.RETURN_CLAIM_RISK: Decimal("0.07"),
    ScoreAxis.POLICY_PROHIBITED_RISK: Decimal("0.10"),
    ScoreAxis.BRAND_IP_RISK: Decimal("0.05"),
    ScoreAxis.DATA_RELIABILITY: Decimal("0.03"),
    ScoreAxis.FUNDING_LIMIT_IMPACT: Decimal("0.05"),
}


COMPANY_ID = 1


class MarketplaceDecisionIntegrationTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                DecisionPolicy.__table__,
                CompanyDecisionPolicy.__table__,
                DecisionEvaluation.__table__,
                DecisionScore.__table__,
                DecisionReview.__table__,
                DecisionAuditLog.__table__,
                AutomationModeState.__table__,
                EmergencyStop.__table__,
                ExecutionLimit.__table__,
                ExecutionUsage.__table__,
                ExecutionPeriodUsage.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = DecisionService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_policy(self) -> DecisionPolicy:

        return self.service.create_policy(
            DecisionPolicyCreateRequest(
                policy_set_id="test-marketplace-policy",
                policy_version="v1",
                source_reference="test-source",
                status=DecisionPolicyStatus.VERIFIED,
                is_complete=True,
                axis_weights=FULL_WEIGHTS,
                min_approve_total_score=Decimal("70.0000"),
                min_confidence_for_recommendation=Decimal("0.5000"),
                checked_at=datetime.utcnow() - timedelta(days=1),
                effective_at=datetime.utcnow() - timedelta(days=1),
            ),
        )

    def _candidate(self, ref="CAND-1") -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"test:COUPANG:{ref}",
            source_type="TREND",
            source_reference=ref,
            market="COUPANG",
            product_name="테스트 상품",
            status=CandidateStatus.RECOMMENDED,
            trend_score=80.0, novelty_score=70.0, demand_score=85.0,
            competition_score=60.0, margin_score=75.0, risk_score=10.0,
            confidence=0.9,
        )
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)

        return candidate

    # ----------------------------------------------------
    # 12) 채널별 비용 혼합 방지
    # ----------------------------------------------------

    def test_different_modes_never_mix_costs(self):

        self._seed_policy()
        candidate = self._candidate()

        eval_rocket_growth, scores_rg, _dup = self.service.evaluate_candidate(
            candidate.id,
            COMPANY_ID,
            idempotency_key="eval-rocket-growth",
            supplementary_inputs=DecisionSupplementaryInputs(
                fulfillment_mode="MARKETPLACE_FULFILLED",
                fulfillment_specific_cost=Decimal("50"),
            ),
        )
        eval_seller, scores_seller, _dup = self.service.evaluate_candidate(
            candidate.id,
            COMPANY_ID,
            idempotency_key="eval-seller-fulfilled",
            supplementary_inputs=DecisionSupplementaryInputs(
                fulfillment_mode="SELLER_FULFILLED",
                fulfillment_specific_cost=Decimal("5"),
            ),
        )

        margin_rg = [
            s for s in scores_rg if s.axis == ScoreAxis.MARGIN_AND_FEES
        ][0]
        margin_seller = [
            s for s in scores_seller if s.axis == ScoreAxis.MARGIN_AND_FEES
        ][0]

        # 서로 다른 모드/비용을 넣었으니 결과 점수와 evidence_text도
        # 달라야 한다 — 한쪽 비용이 다른 쪽 평가에 섞이지 않는다.
        self.assertNotEqual(margin_rg.raw_score, margin_seller.raw_score)
        self.assertIn("MARKETPLACE_FULFILLED", margin_rg.evidence_text)
        self.assertIn("SELLER_FULFILLED", margin_seller.evidence_text)
        self.assertNotEqual(
            eval_rocket_growth.input_fingerprint,
            eval_seller.input_fingerprint,
        )

    # ----------------------------------------------------
    # 13) 누락 비용 0원 자동 보정 금지
    # ----------------------------------------------------

    def test_missing_cost_is_insufficient_data_not_zero(self):

        self._seed_policy()
        candidate = self._candidate()

        evaluation, scores, _dup = self.service.evaluate_candidate(
            candidate.id,
            COMPANY_ID,
            idempotency_key="eval-missing-cost",
            supplementary_inputs=DecisionSupplementaryInputs(
                fulfillment_mode="MARKETPLACE_FULFILLED",
                # fulfillment_specific_cost 미제공
            ),
        )

        margin = [
            s for s in scores if s.axis == ScoreAxis.MARGIN_AND_FEES
        ][0]

        # INSUFFICIENT_DATA는 raw_score=0으로 기록된다(이 코드베이스의
        # 기존 관례 그대로) — 여기서 검증해야 할 것은 "0으로 자동
        # 보정해서 마치 데이터가 있는 것처럼 만들지 않는다"는 것이지,
        # 값이 0이 아니어야 한다는 것이 아니다. data_sufficient=False가
        # 바로 그 구분을 담보한다(총점 계산 시 sufficient_count에
        # 포함되지 않음 — 임의의 긍정 점수로 취급되지 않는다).
        self.assertFalse(margin.data_sufficient)
        self.assertEqual(margin.raw_score, Decimal("0"))
        self.assertIn("fulfillment_specific_cost", margin.evidence_text)
        self.assertIn("미제공", margin.evidence_text)

    # ----------------------------------------------------
    # 14) 방식 변경 시 Decision 재평가
    # ----------------------------------------------------

    def test_changing_mode_produces_new_evaluation(self):
        """
        마켓플레이스 리스팅 서비스는 채널/방식이 바뀔 때마다 새
        idempotency_key로 evaluate_candidate를 호출해야 한다(호출자
        책임) — 같은 후보라도 다른 (mode, idempotency_key) 조합은
        완전히 별개의 DecisionEvaluation 행을 만든다.
        """

        self._seed_policy()
        candidate = self._candidate()

        first, _s1, dup1 = self.service.evaluate_candidate(
            candidate.id,
            COMPANY_ID,
            idempotency_key="listing-42-mode-v1",
            supplementary_inputs=DecisionSupplementaryInputs(
                fulfillment_mode="SELLER_FULFILLED",
                fulfillment_specific_cost=Decimal("5"),
            ),
        )
        second, _s2, dup2 = self.service.evaluate_candidate(
            candidate.id,
            COMPANY_ID,
            idempotency_key="listing-42-mode-v2",
            supplementary_inputs=DecisionSupplementaryInputs(
                fulfillment_mode="MARKETPLACE_FULFILLED",
                fulfillment_specific_cost=Decimal("50"),
            ),
        )

        self.assertFalse(dup1)
        self.assertFalse(dup2)
        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.input_fingerprint, second.input_fingerprint)


if __name__ == "__main__":
    unittest.main()
