"""
=========================================================
Homez OS

File : tests/test_product_candidate_private_visibility.py

V7 Gate 2(2026-08-15) — product_candidate 재구조화(요구사항 2) 전용
회귀 테스트: 비공개(PRIVATE) 후보 지원 + 가시성 격리.

배경: 원본 발견 카탈로그(GLOBAL)는 계속 전역 공유로 유지하되, 회사가
자기만 보이는 비공개(PRIVATE) 후보를 등록·취급할 수 있어야 한다. 다른
회사는 candidate_id를 알아도 그 후보를 볼 수 없어야 한다(404, 존재
자체 비노출) — evaluate_candidate()(decision 도메인) 등 다른 도메인이
직접 candidate_id로 조회하는 경로도 동일하게 막혀야 한다.

검증 대상:
1) 비공개 등록 시 visibility="PRIVATE" + owner_company_id가 채워진다.
2) 소유 회사는 get_visible_for_company로 정상 조회.
3) 다른 회사는 get_visible_for_company에서 404(존재 자체 비노출).
4) list_candidates_for_company — 다른 회사의 PRIVATE는 목록에 전혀
   나타나지 않지만, 자기 것과 GLOBAL은 나타난다.
5) 다른 회사는 PRIVATE 후보를 approve/hold/reject 시도해도 404.
6) 다른 회사는 evidence/decisions 조회도 404.
7) decision 도메인의 evaluate_candidate()도 다른 회사의 PRIVATE 후보를
   candidate_id 추측만으로 평가할 수 없다(404) — Gate 2에서 함께 닫은
   교차 도메인 구멍.
8) GLOBAL 후보는 기존과 동일하게 모든 회사에 보인다(회귀 방지).
9) 같은 회사가 동일 source_reference로 두 번 등록하면 멱등(재등록
   근거만 추가, 새 행 생성 없음).
10) 서로 다른 회사가 동일한 source_reference로 각자 비공개 후보를
    등록해도 candidate_key 네임스페이스가 분리되어 충돌하지 않는다.
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

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
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
from app.domains.decision.schema import DecisionPolicyCreateRequest
from app.domains.decision.schema import DecisionSupplementaryInputs
from app.domains.decision.service import DecisionService
from app.domains.product_candidate.constants import CandidateVisibility
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.schema import ProductCandidateDiscover
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.product_candidate.service import ProductCandidateService

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


class ProductCandidatePrivateVisibilityTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                ProductCandidateEvidence.__table__,
                ProductCandidateDecision.__table__,
                ProductCandidateSelection.__table__,
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
        self.service = ProductCandidateService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _register_private(self, company_id, ref="PRIV-1", name="비공개 상품"):

        return self.service.register_private_candidate(
            ProductCandidatePrivateCreate(
                source_reference=ref, market="COUPANG", product_name=name,
            ),
            company_id=company_id,
            correlation_id="corr-private",
        )

    def _register_global(self, ref="GLOBAL-1"):

        return self.service.discover(
            ProductCandidateDiscover(
                source_type="TREND", source_reference=ref, market="COUPANG",
                product_name="전역 상품",
            ),
            correlation_id="corr-global",
        )

    # --------------------------------------------------
    # 1) 등록 시 visibility/owner_company_id
    # --------------------------------------------------

    def test_private_registration_sets_visibility_and_owner(self):

        candidate, events = self._register_private(COMPANY_A)

        self.assertEqual(candidate.visibility, CandidateVisibility.PRIVATE)
        self.assertEqual(candidate.owner_company_id, COMPANY_A)
        self.assertEqual(len(events), 2)

    def test_global_discover_defaults_to_global_visibility(self):

        candidate, _events = self._register_global()

        self.assertEqual(candidate.visibility, CandidateVisibility.GLOBAL)
        self.assertIsNone(candidate.owner_company_id)

    # --------------------------------------------------
    # 2/3) 소유 회사는 조회 가능, 타사는 404
    # --------------------------------------------------

    def test_owner_company_can_view_private_candidate(self):

        candidate, _ = self._register_private(COMPANY_A)

        found = self.service.get_visible_for_company(
            candidate.id, COMPANY_A,
        )
        self.assertEqual(found.id, candidate.id)

    def test_other_company_cannot_view_private_candidate(self):

        candidate, _ = self._register_private(COMPANY_A)

        with self.assertRaises(NotFoundException):
            self.service.get_visible_for_company(candidate.id, COMPANY_B)

    # --------------------------------------------------
    # 4) 목록 스코프
    # --------------------------------------------------

    def test_list_excludes_other_companies_private_candidates(self):

        private_a, _ = self._register_private(COMPANY_A, ref="PRIV-A")
        private_b, _ = self._register_private(COMPANY_B, ref="PRIV-B")
        global_candidate, _ = self._register_global(ref="GLOBAL-SHARED")

        list_for_a = self.service.list_candidates_for_company(COMPANY_A)
        list_for_b = self.service.list_candidates_for_company(COMPANY_B)

        ids_for_a = {c.id for c in list_for_a}
        ids_for_b = {c.id for c in list_for_b}

        self.assertIn(private_a.id, ids_for_a)
        self.assertNotIn(private_b.id, ids_for_a)
        self.assertIn(global_candidate.id, ids_for_a)

        self.assertIn(private_b.id, ids_for_b)
        self.assertNotIn(private_a.id, ids_for_b)
        self.assertIn(global_candidate.id, ids_for_b)

    # --------------------------------------------------
    # 5) 승인/보류/거절도 타사는 404
    # --------------------------------------------------

    def test_other_company_cannot_approve_private_candidate(self):

        candidate, _ = self._register_private(COMPANY_A)

        with self.assertRaises(NotFoundException):
            self.service.approve(
                candidate.id, company_id=COMPANY_B, operator_id=1,
                is_admin=True, memo=None, correlation_id="c",
            )

    def test_owner_company_can_approve_private_candidate(self):
        """
        비공개 후보도 discover()와 동일한 워크플로우(ANALYZED/
        RECOMMENDED까지 진행돼야 결정 가능)를 그대로 따른다.
        """

        candidate, _ = self._register_private(COMPANY_A)
        self.service.apply_trend_analysis(
            candidate.id, company_id=COMPANY_A, trend_score=0.6, confidence=0.7,
            evidence_text="e", correlation_id="c2",
        )
        self.service.recommend(candidate.id, company_id=COMPANY_A, correlation_id="c3")

        new_status, _events = self.service.approve(
            candidate.id, company_id=COMPANY_A, operator_id=1,
            is_admin=True, memo="괜찮은 상품", correlation_id="c4",
        )

        self.assertEqual(new_status, "APPROVED")

        selection = self.service.repository.get_selection(
            candidate.id, COMPANY_A,
        )
        self.assertEqual(selection.memo, "괜찮은 상품")

    # --------------------------------------------------
    # 6) 근거/결정 이력도 타사는 404
    # --------------------------------------------------

    def test_other_company_cannot_list_evidence_or_decisions(self):

        candidate, _ = self._register_private(COMPANY_A)

        with self.assertRaises(NotFoundException):
            self.service.list_evidence(candidate.id, COMPANY_B)

        with self.assertRaises(NotFoundException):
            self.service.list_decisions(candidate.id, COMPANY_B)

    def test_owner_company_can_list_evidence(self):

        candidate, _ = self._register_private(COMPANY_A)

        rows = self.service.list_evidence(candidate.id, COMPANY_A)
        self.assertGreaterEqual(len(rows), 1)

    # --------------------------------------------------
    # 7) decision 도메인도 동일하게 막힌다(교차 도메인 검증)
    # --------------------------------------------------

    def test_decision_evaluate_candidate_blocks_other_companys_private(self):

        candidate, _ = self._register_private(COMPANY_A)
        self.service.apply_trend_analysis(
            candidate.id, company_id=COMPANY_A, trend_score=0.6, confidence=0.7,
            evidence_text="e", correlation_id="c2",
        )
        self.service.recommend(candidate.id, company_id=COMPANY_A, correlation_id="c3")

        decision_service = DecisionService(self.db)
        decision_service.create_policy(
            DecisionPolicyCreateRequest(
                policy_set_id="private-visibility-policy",
                policy_version="v1",
                source_reference="doc",
                status=DecisionPolicyStatus.VERIFIED,
                is_complete=True,
                axis_weights=FULL_WEIGHTS,
                min_approve_total_score=Decimal("70.0000"),
                min_confidence_for_recommendation=Decimal("0.5000"),
                checked_at=datetime.utcnow() - timedelta(days=1),
                effective_at=datetime.utcnow() - timedelta(days=1),
            ),
        )

        supplementary = DecisionSupplementaryInputs(
            expected_margin_rate=Decimal("0.2"),
            gross_revenue=Decimal("50000"),
            price_competitiveness_score=Decimal("80"),
            supply_stability_score=Decimal("85"),
            inventory_shipping_risk_score=Decimal("80"),
            return_claim_risk_score=Decimal("80"),
            brand_ip_risk_score=Decimal("90"),
            required_funding=Decimal("10000"),
            available_funding=Decimal("50000"),
        )

        # 소유 회사(A)는 평가 가능해야 한다.
        evaluation, _scores, _dup = decision_service.evaluate_candidate(
            candidate.id, COMPANY_A,
            idempotency_key="eval-owner",
            supplementary_inputs=supplementary,
        )
        self.assertIsNotNone(evaluation.id)

        # 다른 회사(B)는 이 PRIVATE 후보를 candidate_id 추측만으로
        # 평가(예상매출·가용자금 등 민감 입력 포함)할 수 없어야 한다.
        with self.assertRaises(NotFoundException):
            decision_service.evaluate_candidate(
                candidate.id, COMPANY_B,
                idempotency_key="eval-intruder",
                supplementary_inputs=supplementary,
            )

    # --------------------------------------------------
    # 8) GLOBAL 후보는 회귀 없이 모든 회사에 보인다
    # --------------------------------------------------

    def test_global_candidate_visible_to_all_companies(self):

        candidate, _ = self._register_global()

        found_a = self.service.get_visible_for_company(
            candidate.id, COMPANY_A,
        )
        found_b = self.service.get_visible_for_company(
            candidate.id, COMPANY_B,
        )
        self.assertEqual(found_a.id, found_b.id)

    # --------------------------------------------------
    # 9) 멱등 재등록
    # --------------------------------------------------

    def test_private_registration_is_idempotent(self):

        first, first_events = self._register_private(COMPANY_A, ref="IDEM-1")
        second, second_events = self._register_private(
            COMPANY_A, ref="IDEM-1", name="다른 이름",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(second_events), 0)
        # 원본 product_name은 덮어써지지 않는다(discover()와 동일 원칙).
        self.assertEqual(second.product_name, "비공개 상품")

        evidence_rows = self.service.list_evidence(first.id, COMPANY_A)
        self.assertEqual(len(evidence_rows), 2)

    # --------------------------------------------------
    # 10) 회사별 candidate_key 네임스페이스 분리
    # --------------------------------------------------

    def test_different_companies_same_reference_do_not_collide(self):

        candidate_a, _ = self._register_private(
            COMPANY_A, ref="SAME-REF", name="회사 A 상품",
        )
        candidate_b, _ = self._register_private(
            COMPANY_B, ref="SAME-REF", name="회사 B 상품",
        )

        self.assertNotEqual(candidate_a.id, candidate_b.id)
        self.assertNotEqual(
            candidate_a.candidate_key, candidate_b.candidate_key,
        )
        self.assertEqual(candidate_a.owner_company_id, COMPANY_A)
        self.assertEqual(candidate_b.owner_company_id, COMPANY_B)


if __name__ == "__main__":
    unittest.main()
