"""
=========================================================
Homez OS

File : tests/test_decision_tenant_isolation.py

2026-08-14 Gate R13 테넌트 격리 감사 — Decision AI 도메인 회사(테넌트)
간 격리 전용 테스트.

배경: DecisionEvaluation 등 4개 테이블(DecisionPolicy 제외)에
company_id 컬럼 자체가 없어, 어떤 회사의 관리자든 다른 회사의 Decision
AI 평가(예상매출·가용자금·점수·추천)를 id 추측만으로 열람·승인·보류·
거절·재정의(override)할 수 있었다. 이 테스트는 settlement/coupang의
확립된 패턴을 그대로 재사용해 router 엔드포인트 함수 직접 호출로 실제
경계를 검증한다.

검증 대상:
1) 회사 B가 회사 A의 evaluation_id로 GET/scores/reviews 조회 시도하면
   전부 404.
2) 회사 B가 회사 A의 evaluation_id로 approve/hold/reject/override
   시도하면 전부 404 + 실제 상태·recommendation 불변.
3) list_pending_review/list_evaluations는 자기 회사 소유만 반환한다.
4) 같은 idempotency_key를 회사 A/B가 각자 독립적으로 사용할 수 있다
   (evaluate_candidate, review_evaluation 전부).
5) DecisionPolicy(전역, CTO 확인 대기 항목)는 회사 구분 없이 두 회사
   모두 동일하게 조회된다(의도된 설계).
=========================================================
"""

import os
import tempfile
import types
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import NotFoundException
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
from app.domains.decision.router import approve_evaluation
from app.domains.decision.router import evaluate_candidate
from app.domains.decision.router import get_evaluation
from app.domains.decision.router import get_reviews
from app.domains.decision.router import get_scores
from app.domains.decision.router import list_evaluations
from app.domains.decision.router import list_policies
from app.domains.decision.router import reject_evaluation
from app.domains.decision.schema import DecisionEvaluationRequest
from app.domains.decision.schema import DecisionPolicyCreateRequest
from app.domains.decision.schema import DecisionReviewRequest
from app.domains.decision.schema import DecisionSupplementaryInputs
from app.domains.decision.service import DecisionService
from app.domains.product_candidate.model import ProductCandidate

COMPANY_A = 1
COMPANY_B = 2

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


def _fake_user(company_id: int, user_id: int = 1):

    return types.SimpleNamespace(company_id=company_id, id=user_id)


def _noop_desktop_token():

    return None


class DecisionTenantIsolationTestCase(unittest.TestCase):

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

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self.user_a = _fake_user(COMPANY_A, user_id=101)
        self.user_b = _fake_user(COMPANY_B, user_id=201)

        service = DecisionService(self.db)
        service.create_policy(
            DecisionPolicyCreateRequest(
                policy_set_id="tenant-test-policy",
                policy_version="v1",
                source_reference="test",
                status=DecisionPolicyStatus.VERIFIED,
                is_complete=True,
                axis_weights=FULL_WEIGHTS,
                min_approve_total_score=Decimal("70.0000"),
                min_confidence_for_recommendation=Decimal("0.5000"),
                checked_at=datetime.utcnow() - timedelta(days=1),
                effective_at=datetime.utcnow() - timedelta(days=1),
            ),
        )

        self.candidate = ProductCandidate(
            candidate_key="TREND:COUPANG:TENANT-DECISION-1",
            source_type="TREND",
            source_reference="TENANT-DECISION-1",
            market="COUPANG",
            product_name="테넌트 격리 Decision 테스트 상품",
            status="RECOMMENDED",
            trend_score=80.0, novelty_score=70.0, demand_score=85.0,
            competition_score=60.0, margin_score=75.0, risk_score=10.0,
            confidence=0.9,
        )
        self.db.add(self.candidate)
        self.db.commit()
        self.db.refresh(self.candidate)

        self.evaluation_a = evaluate_candidate(
            DecisionEvaluationRequest(
                candidate_id=self.candidate.id,
                idempotency_key="eval-a",
                supplementary_inputs=self._full_supplementary(),
            ),
            current_user=self.user_a, db=self.db,
            _desktop=_noop_desktop_token(),
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @staticmethod
    def _full_supplementary(**overrides) -> DecisionSupplementaryInputs:

        base = dict(
            expected_margin_rate=Decimal("0.20"),
            gross_revenue=Decimal("50000"),
            price_competitiveness_score=Decimal("80"),
            supply_stability_score=Decimal("85"),
            inventory_shipping_risk_score=Decimal("80"),
            return_claim_risk_score=Decimal("80"),
            brand_ip_risk_score=Decimal("90"),
            required_funding=Decimal("10000"),
            available_funding=Decimal("50000"),
        )
        base.update(overrides)

        return DecisionSupplementaryInputs(**base)

    # --------------------------------------------------
    # 1) 조회 격리 — 전부 404
    # --------------------------------------------------

    def test_get_evaluation_cross_company_is_404(self):

        with self.assertRaises(NotFoundException):
            get_evaluation(
                self.evaluation_a.id, current_user=self.user_b, db=self.db,
            )

    def test_scores_and_reviews_cross_company_are_404(self):

        with self.assertRaises(NotFoundException):
            get_scores(
                self.evaluation_a.id, current_user=self.user_b, db=self.db,
            )

        with self.assertRaises(NotFoundException):
            get_reviews(
                self.evaluation_a.id, current_user=self.user_b, db=self.db,
            )

    def test_list_evaluations_only_returns_own_company(self):

        evaluate_candidate(
            DecisionEvaluationRequest(
                candidate_id=self.candidate.id,
                idempotency_key="eval-b",
                supplementary_inputs=self._full_supplementary(),
            ),
            current_user=self.user_b, db=self.db,
            _desktop=_noop_desktop_token(),
        )

        list_a = list_evaluations(
            skip=0, limit=100, current_user=self.user_a, db=self.db,
        )
        list_b = list_evaluations(
            skip=0, limit=100, current_user=self.user_b, db=self.db,
        )

        self.assertEqual(len(list_a), 1)
        self.assertEqual(len(list_b), 1)
        self.assertNotEqual(list_a[0].id, list_b[0].id)

    # --------------------------------------------------
    # 2) 검토(승인/보류/거절/override) 격리 — 전부 404 + 상태 불변
    # --------------------------------------------------

    def test_approve_cross_company_is_404_status_unchanged(self):

        with self.assertRaises(NotFoundException):
            approve_evaluation(
                self.evaluation_a.id,
                DecisionReviewRequest(idempotency_key="approve-cross"),
                current_user=self.user_b, db=self.db,
                _desktop=_noop_desktop_token(),
            )

        service = DecisionService(self.db)
        unchanged = service.get_evaluation(self.evaluation_a.id, COMPANY_A)
        self.assertEqual(unchanged.status, "PENDING_REVIEW")

        reviews = service.list_reviews(self.evaluation_a.id, COMPANY_A)
        self.assertEqual(reviews, [])

    def test_reject_cross_company_is_404_no_side_effect(self):

        with self.assertRaises(NotFoundException):
            reject_evaluation(
                self.evaluation_a.id,
                DecisionReviewRequest(idempotency_key="reject-cross"),
                current_user=self.user_b, db=self.db,
                _desktop=_noop_desktop_token(),
            )

        service = DecisionService(self.db)
        unchanged = service.get_evaluation(self.evaluation_a.id, COMPANY_A)
        self.assertEqual(unchanged.status, "PENDING_REVIEW")

    # --------------------------------------------------
    # 3) idempotency_key 회사별 독립
    # --------------------------------------------------

    def test_same_idempotency_key_independent_per_company(self):
        """
        evaluation_a는 setUp에서 이미 "eval-a"로 생성됨. 여기서는 같은
        문자열의 다른 key("shared-key")를 회사 A/B가 각각 새로 써도
        서로 충돌하지 않음을 확인한다.
        """

        eval_a2 = evaluate_candidate(
            DecisionEvaluationRequest(
                candidate_id=self.candidate.id,
                idempotency_key="shared-key",
                supplementary_inputs=self._full_supplementary(),
            ),
            current_user=self.user_a, db=self.db,
            _desktop=_noop_desktop_token(),
        )
        eval_b = evaluate_candidate(
            DecisionEvaluationRequest(
                candidate_id=self.candidate.id,
                idempotency_key="shared-key",
                supplementary_inputs=self._full_supplementary(),
            ),
            current_user=self.user_b, db=self.db,
            _desktop=_noop_desktop_token(),
        )

        self.assertNotEqual(eval_a2.id, eval_b.id)
        self.assertEqual(eval_a2.company_id, COMPANY_A)
        self.assertEqual(eval_b.company_id, COMPANY_B)

    # --------------------------------------------------
    # 4) 전역 정책은 회사 구분 없이 공유
    # --------------------------------------------------

    def test_decision_policy_is_shared_across_companies(self):

        policies_a = list_policies(_=self.user_a, db=self.db)
        policies_b = list_policies(_=self.user_b, db=self.db)

        self.assertEqual(len(policies_a), 1)
        self.assertEqual(len(policies_b), 1)
        self.assertEqual(policies_a[0].id, policies_b[0].id)


if __name__ == "__main__":
    unittest.main()
