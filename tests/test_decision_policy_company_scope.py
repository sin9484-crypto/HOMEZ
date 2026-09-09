"""
=========================================================
Homez OS

File : tests/test_decision_policy_company_scope.py

V7 Gate 2(2026-08-15) — DecisionPolicy 전역/회사 분리(요구사항 1)
전용 회귀 테스트.

배경: 회사가 전역 시스템 기본 템플릿(DecisionPolicy)을 그대로 쓸 수도,
자기 회사용 커스텀 정책(CompanyDecisionPolicy)을 만들 수도 있어야
한다. 기존 DecisionEvaluation이 어느 정책 버전을 근거로 평가됐는지
추적 가능해야 한다(policy_source + policy_id/company_policy_id).

검증 대상:
1) 회사 전용 정책이 있으면 전역보다 우선한다(policy_source="COMPANY").
2) 회사 전용 정책이 없으면 전역으로 폴백한다(policy_source="GLOBAL").
3) 둘 다 없으면 fail-closed(BadRequestException).
4) 회사 정책이 VERIFIED/활성/완전/유효기간 조건을 만족하지 못하면
   전역으로 폴백한다(사용 불가능한 회사 정책을 억지로 쓰지 않는다).
5) 회사 정책 생성은 (company_id, policy_set_id) UNIQUE — 같은 회사
   안에서는 중복 거부, 다른 회사는 동일 policy_set_id를 독립적으로
   쓸 수 있다.
6) 회사 정책 목록 조회는 회사별로 스코프된다.
7) 회사 정책도 axis_weights 검증(12축 전부 포함, 합계 1.0000)을 그대로
   적용한다.
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
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.automation_safety.model import AutomationModeState
from app.domains.automation_safety.model import EmergencyStop
from app.domains.automation_safety.model import ExecutionLimit
from app.domains.automation_safety.model import ExecutionPeriodUsage
from app.domains.automation_safety.model import ExecutionUsage
from app.domains.decision.constants import DecisionPolicyStatus
from app.domains.decision.constants import PolicySource
from app.domains.decision.constants import ScoreAxis
from app.domains.decision.model import CompanyDecisionPolicy
from app.domains.decision.model import DecisionAuditLog
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionPolicy
from app.domains.decision.model import DecisionReview
from app.domains.decision.model import DecisionScore
from app.domains.decision.schema import DecisionCompanyPolicyCreateRequest
from app.domains.decision.schema import DecisionPolicyCreateRequest
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


class DecisionPolicyCompanyScopeTestCase(unittest.TestCase):

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
        self.service = DecisionService(self.db)

        self.candidate = ProductCandidate(
            candidate_key="TREND:COUPANG:POLICY-SCOPE-1",
            source_type="TREND",
            source_reference="POLICY-SCOPE-1",
            market="COUPANG",
            product_name="정책 분리 테스트 상품",
            status="RECOMMENDED",
            trend_score=80.0, novelty_score=70.0, demand_score=85.0,
            competition_score=60.0, margin_score=75.0, risk_score=10.0,
            confidence=0.9,
        )
        self.db.add(self.candidate)
        self.db.commit()
        self.db.refresh(self.candidate)

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

    def _create_global_policy(self, policy_set_id="global-default", **kw):

        defaults = dict(
            policy_set_id=policy_set_id,
            policy_version="v1",
            source_reference="global-doc",
            status=DecisionPolicyStatus.VERIFIED,
            is_complete=True,
            axis_weights=FULL_WEIGHTS,
            min_approve_total_score=Decimal("70.0000"),
            min_confidence_for_recommendation=Decimal("0.5000"),
            checked_at=datetime.utcnow() - timedelta(days=1),
            effective_at=datetime.utcnow() - timedelta(days=1),
        )
        defaults.update(kw)

        return self.service.create_policy(
            DecisionPolicyCreateRequest(**defaults),
        )

    def _create_company_policy(
        self, company_id, policy_set_id="company-custom", **kw,
    ):

        defaults = dict(
            policy_set_id=policy_set_id,
            policy_version="v1-company",
            source_reference="company-doc",
            status=DecisionPolicyStatus.VERIFIED,
            is_complete=True,
            axis_weights=FULL_WEIGHTS,
            min_approve_total_score=Decimal("60.0000"),
            min_confidence_for_recommendation=Decimal("0.4000"),
            checked_at=datetime.utcnow() - timedelta(days=1),
            effective_at=datetime.utcnow() - timedelta(days=1),
        )
        defaults.update(kw)

        return self.service.create_company_policy(
            DecisionCompanyPolicyCreateRequest(**defaults), company_id,
        )

    # --------------------------------------------------
    # 1) 우선순위 — 회사 정책이 있으면 전역보다 우선
    # --------------------------------------------------

    def test_company_policy_takes_priority_over_global(self):

        global_policy = self._create_global_policy()
        company_policy = self._create_company_policy(COMPANY_A)

        evaluation, _scores, _dup = self.service.evaluate_candidate(
            self.candidate.id, COMPANY_A,
            idempotency_key="eval-company-priority",
            supplementary_inputs=self._full_supplementary(),
        )

        self.assertEqual(evaluation.policy_source, PolicySource.COMPANY)
        self.assertEqual(evaluation.company_policy_id, company_policy.id)
        self.assertIsNone(evaluation.policy_id)
        self.assertEqual(evaluation.policy_version, "v1-company")
        # 회사 정책이 우선 채택됐다는 것을 policy_version으로 재확인
        # (전역은 "v1", 회사는 "v1-company" — 값 자체가 다른 정책임을
        # 보장한다. id는 서로 다른 테이블의 PK라 비교 대상이 아니다).
        self.assertNotEqual(
            evaluation.policy_version, global_policy.policy_version,
        )

    def test_no_company_policy_falls_back_to_global(self):

        global_policy = self._create_global_policy()
        # 회사 A만 커스텀 정책을 만든다 — 회사 B는 전역을 그대로 쓴다.
        self._create_company_policy(COMPANY_A)

        evaluation, _scores, _dup = self.service.evaluate_candidate(
            self.candidate.id, COMPANY_B,
            idempotency_key="eval-fallback-global",
            supplementary_inputs=self._full_supplementary(),
        )

        self.assertEqual(evaluation.policy_source, PolicySource.GLOBAL)
        self.assertEqual(evaluation.policy_id, global_policy.id)
        self.assertIsNone(evaluation.company_policy_id)

    def test_neither_policy_available_fails_closed(self):

        with self.assertRaises(BadRequestException):
            self.service.evaluate_candidate(
                self.candidate.id, COMPANY_A,
                idempotency_key="eval-no-policy",
                supplementary_inputs=self._full_supplementary(),
            )

    def test_unusable_company_policy_falls_back_to_global(self):
        """
        회사 정책이 DRAFT(VERIFIED 아님)라 사용 불가능하면, 억지로 쓰지
        않고 전역으로 폴백해야 한다.
        """

        global_policy = self._create_global_policy()
        self._create_company_policy(
            COMPANY_A, status=DecisionPolicyStatus.DRAFT,
        )

        evaluation, _scores, _dup = self.service.evaluate_candidate(
            self.candidate.id, COMPANY_A,
            idempotency_key="eval-unusable-company-policy",
            supplementary_inputs=self._full_supplementary(),
        )

        self.assertEqual(evaluation.policy_source, PolicySource.GLOBAL)
        self.assertEqual(evaluation.policy_id, global_policy.id)

    # --------------------------------------------------
    # 2) 회사 정책 생성/조회 스코프
    # --------------------------------------------------

    def test_duplicate_policy_set_id_within_same_company_conflicts(self):

        self._create_company_policy(COMPANY_A, policy_set_id="dup-set")

        with self.assertRaises(ConflictException):
            self._create_company_policy(COMPANY_A, policy_set_id="dup-set")

    def test_different_companies_can_reuse_same_policy_set_id(self):

        policy_a = self._create_company_policy(
            COMPANY_A, policy_set_id="shared-name",
        )
        policy_b = self._create_company_policy(
            COMPANY_B, policy_set_id="shared-name",
        )

        self.assertNotEqual(policy_a.id, policy_b.id)
        self.assertEqual(policy_a.policy_set_id, policy_b.policy_set_id)

    def test_list_company_policies_scoped_per_company(self):

        self._create_company_policy(COMPANY_A, policy_set_id="a-policy")
        self._create_company_policy(COMPANY_B, policy_set_id="b-policy")

        list_a = self.service.list_company_policies(COMPANY_A)
        list_b = self.service.list_company_policies(COMPANY_B)

        self.assertEqual(len(list_a), 1)
        self.assertEqual(len(list_b), 1)
        self.assertEqual(list_a[0].policy_set_id, "a-policy")
        self.assertEqual(list_b[0].policy_set_id, "b-policy")

    def test_company_policy_requires_all_axes_and_weights_sum_to_one(self):

        incomplete_weights = dict(FULL_WEIGHTS)
        incomplete_weights.pop(ScoreAxis.BRAND_IP_RISK)

        with self.assertRaises(BadRequestException):
            self._create_company_policy(
                COMPANY_A, axis_weights=incomplete_weights,
            )

    def test_company_policy_base_policy_id_lineage_optional(self):
        """
        base_policy_id는 순수 계보 정보 — 지정하지 않아도 되고,
        지정하면 그 전역 템플릿의 존재를 확인한다.
        """

        global_policy = self._create_global_policy()

        company_policy = self._create_company_policy(
            COMPANY_A, base_policy_id=global_policy.id,
        )
        self.assertEqual(company_policy.base_policy_id, global_policy.id)

        from app.core.exceptions import NotFoundException

        with self.assertRaises(NotFoundException):
            self._create_company_policy(
                COMPANY_B, base_policy_id=999999,
            )


if __name__ == "__main__":
    unittest.main()
