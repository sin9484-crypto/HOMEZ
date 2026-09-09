"""
=========================================================
Homez OS

File : tests/test_product_candidate_tenant_isolation.py

2026-08-14 Gate R13 테넌트 격리 감사 — ProductCandidate 승인 상태
회사 간 격리 전용 테스트.

배경: 이전에는 승인/보류/거절이 ProductCandidate.status라는 전역
단일 필드에 반영되어, 회사 A가 approve()하면 그 즉시 회사 B도 같은
candidate_id로 coupang 초안/marketplace_listing/listing_package를
만들 수 있었다(회사 A의 승인에 무임승차). 수정 후에는
ProductCandidateSelection(candidate_id, company_id) UNIQUE 투영
테이블이 회사별 현재 상태를 담당하고, ProductCandidate 자체(발견
카탈로그)는 계속 전역으로 유지된다.

검증 대상:
1) ProductCandidate 자체는 회사 A/B 모두 동일하게 조회 가능(전역).
2) 회사 A가 approve()해도 회사 B 관점에서는 여전히 미승인 상태.
3) 회사 B는 독립적으로 같은 후보를 reject()할 수 있다(서로 간섭 없음).
4) 같은 회사가 이미 최종 결정(APPROVED/REJECTED)했으면 재결정 불가.
5) require_approved_for_company() — 다른 회사의 승인만으로는 통과할
   수 없다(coupang/marketplace_listing/listing_package가 공유하는
   게이트).
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.schema import ProductCandidateDiscover
from app.domains.product_candidate.service import ProductCandidateService

COMPANY_A = 1
COMPANY_B = 2


class ProductCandidateTenantIsolationTestCase(unittest.TestCase):

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
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.service = ProductCandidateService(self.db)

        candidate, _events = self.service.discover(
            ProductCandidateDiscover(
                source_type="COUPANG_API",
                source_reference="TENANT-REF-1",
                market="COUPANG",
                product_name="테넌트 격리 테스트 상품",
            ),
            correlation_id="setup",
        )
        self.service.apply_trend_analysis(
            candidate.id, company_id=COMPANY_A, trend_score=0.7, confidence=0.8,
            evidence_text="상승", correlation_id="setup-trend",
        )
        self.service.recommend(candidate.id, company_id=COMPANY_A, correlation_id="setup-rec")
        self.candidate_id = candidate.id

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_candidate_itself_is_global_visible_to_both_companies(self):

        seen_by_a = self.service.get(self.candidate_id)
        seen_by_b = self.service.get(self.candidate_id)

        self.assertEqual(seen_by_a.id, seen_by_b.id)
        self.assertEqual(seen_by_a.product_name, seen_by_b.product_name)

    def test_company_a_approval_does_not_leak_to_company_b(self):

        self.service.approve(
            self.candidate_id, company_id=COMPANY_A,
            operator_id=1, is_admin=True, memo="A 승인",
            correlation_id="c-a",
        )

        status_for_a = self.service.get_effective_status_for_company(
            self.candidate_id, COMPANY_A,
        )
        status_for_b = self.service.get_effective_status_for_company(
            self.candidate_id, COMPANY_B,
        )

        self.assertEqual(status_for_a, CandidateStatus.APPROVED)
        # 회사 B 관점에서는 여전히 "아직 결정하지 않음" — 전역
        # 워크플로우 상태(RECOMMENDED) 그대로 보인다.
        self.assertEqual(status_for_b, CandidateStatus.RECOMMENDED)

        # 전역 필드 자체도 APPROVED로 오염되지 않는다.
        candidate = self.service.get(self.candidate_id)
        self.assertEqual(candidate.status, CandidateStatus.RECOMMENDED)

    def test_company_b_can_independently_reject_same_candidate(self):

        self.service.approve(
            self.candidate_id, company_id=COMPANY_A,
            operator_id=1, is_admin=True, memo="A 승인",
            correlation_id="c-a",
        )
        new_status, _events = self.service.reject(
            self.candidate_id, company_id=COMPANY_B,
            operator_id=2, is_admin=True, memo="B 거절",
            correlation_id="c-b",
        )

        self.assertEqual(new_status, CandidateStatus.REJECTED)
        self.assertEqual(
            self.service.get_effective_status_for_company(
                self.candidate_id, COMPANY_A,
            ),
            CandidateStatus.APPROVED,
        )
        self.assertEqual(
            self.service.get_effective_status_for_company(
                self.candidate_id, COMPANY_B,
            ),
            CandidateStatus.REJECTED,
        )

        decisions_a = self.service.list_decisions(
            self.candidate_id, COMPANY_A,
        )
        decisions_b = self.service.list_decisions(
            self.candidate_id, COMPANY_B,
        )
        self.assertEqual(len(decisions_a), 1)
        self.assertEqual(len(decisions_b), 1)
        self.assertEqual(decisions_a[0].action, "APPROVE")
        self.assertEqual(decisions_b[0].action, "REJECT")

    def test_company_cannot_redecide_after_own_terminal_decision(self):

        self.service.approve(
            self.candidate_id, company_id=COMPANY_A,
            operator_id=1, is_admin=True, memo=None,
            correlation_id="c-a-1",
        )

        with self.assertRaises(BadRequestException):
            self.service.reject(
                self.candidate_id, company_id=COMPANY_A,
                operator_id=1, is_admin=True, memo=None,
                correlation_id="c-a-2",
            )

        # 다른 회사(B)는 여전히 영향받지 않고 독립적으로 결정 가능.
        new_status, _events = self.service.approve(
            self.candidate_id, company_id=COMPANY_B,
            operator_id=2, is_admin=True, memo=None,
            correlation_id="c-b-1",
        )
        self.assertEqual(new_status, CandidateStatus.APPROVED)

    def test_require_approved_for_company_blocks_on_other_companys_approval(
        self,
    ):
        """
        coupang/marketplace_listing/listing_package가 공유하는 게이트 —
        회사 A만 승인했다면 회사 B는 절대 통과할 수 없다(다른 회사의
        승인에 무임승차 불가, 이번 감사가 지적한 핵심 결함).
        """

        self.service.approve(
            self.candidate_id, company_id=COMPANY_A,
            operator_id=1, is_admin=True, memo=None,
            correlation_id="c-a",
        )

        # 회사 A는 통과.
        candidate = self.service.require_approved_for_company(
            self.candidate_id, COMPANY_A,
        )
        self.assertEqual(candidate.id, self.candidate_id)

        # 회사 B는 차단 — 404가 아니라 400(상태 불충족)이다. candidate
        # 존재 자체는 전역이라 회사 B도 알 수 있다는 것이 이 Domain의
        # 의도된 설계다(원본 발견 카탈로그는 전역 유지).
        with self.assertRaises(BadRequestException):
            self.service.require_approved_for_company(
                self.candidate_id, COMPANY_B,
            )

    def test_require_approved_for_company_raises_not_found_for_missing_candidate(
        self,
    ):

        with self.assertRaises(NotFoundException):
            self.service.require_approved_for_company(999999, COMPANY_A)


if __name__ == "__main__":
    unittest.main()
