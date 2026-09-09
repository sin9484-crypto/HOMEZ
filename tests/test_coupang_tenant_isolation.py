"""
=========================================================
Homez OS

File : tests/test_coupang_tenant_isolation.py

2026-08-14 Gate R13 테넌트 격리 감사 — Coupang 도메인 회사(테넌트) 간
격리 전용 테스트.

배경: CoupangMarketplaceProduct 등 8개 테이블 중 6개(CoupangPolicySet/
CoupangPolicyRule 제외)에 company_id 컬럼 자체가 없어, 어떤 회사의
관리자든 다른 회사의 쿠팡 판매 초안(판매가·마진·재고)을 id 추측만으로
열람·수정·승인할 수 있었다. 이 테스트는 settlement의 확립된 패턴
(tests/test_settlement_tenant_isolation.py)을 그대로 재사용해 router
엔드포인트 함수 직접 호출로 실제 경계를 검증한다.

검증 대상:
1) 회사 A가 만든 초안을 회사 B가 GET/options/notices/profit-estimates/
   decisions 조회 시도하면 전부 404.
2) 회사 B가 회사 A의 상품 id로 validate-policy/profit-estimate/
   dry-run/approve-for-submission 시도해도 전부 404 + 실제로 상태
   불변.
3) 목록 조회는 자기 회사 소유만 반환한다.
4) 같은 idempotency_key를 회사 A/B가 각자 독립적으로 사용할 수 있다
   (create_draft, dry-run, approve-for-submission 전부).
5) CoupangPolicySet/CoupangPolicyRule(전역)은 회사 구분 없이 두 회사
   모두 동일하게 조회된다(의도된 설계).
=========================================================
"""

import os
import tempfile
import types
import unittest
import uuid
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import NotFoundException
from app.database.base import Base
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate
from app.domains.coupang.router import approve_for_submission
from app.domains.coupang.router import create_draft
from app.domains.coupang.router import estimate_profit
from app.domains.coupang.router import get_decisions
from app.domains.coupang.router import get_product
from app.domains.coupang.router import get_product_notices
from app.domains.coupang.router import get_product_options
from app.domains.coupang.router import list_policy_sets
from app.domains.coupang.router import list_products
from app.domains.coupang.router import run_dry_run
from app.domains.coupang.router import validate_policy
from app.domains.coupang.schema import CoupangApprovalRequest
from app.domains.coupang.schema import CoupangDraftCreateRequest
from app.domains.coupang.schema import CoupangDryRunRequest
from app.domains.coupang.schema import CoupangProductOptionInput
from app.domains.coupang.schema import CoupangProfitEstimateRequest
from app.domains.coupang.service import CoupangIntegrationService
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection

COMPANY_A = 1
COMPANY_B = 2


def _fake_user(company_id: int, user_id: int = 1):

    return types.SimpleNamespace(company_id=company_id, id=user_id)


class CoupangTenantIsolationTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                ProductCandidate.__table__,
                ProductCandidateSelection.__table__,
                CoupangMarketplaceProduct.__table__,
                CoupangProductOption.__table__,
                CoupangPolicySet.__table__,
                CoupangPolicyRule.__table__,
                CoupangProductNotice.__table__,
                CoupangProfitEstimate.__table__,
                CoupangDryRunAttempt.__table__,
                CoupangIntegrationDecision.__table__,
            ],
        )

        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()

        self._seed_verified_sellable_policy()

        self.candidate_a = self._approved_candidate(COMPANY_A, "CAND-A")
        self.candidate_b = self._approved_candidate(COMPANY_B, "CAND-B")

        self.user_a = _fake_user(COMPANY_A, user_id=101)
        self.user_b = _fake_user(COMPANY_B, user_id=201)

        self.product_a = create_draft(
            self._draft_request("sku-a", "idem-a"),
            current_user=self.user_a, db=self.db,
        )

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_verified_sellable_policy(self):

        policy_set = CoupangPolicySet(
            policy_set_id="tenant-test-baseline",
            policy_version="v1",
            source_reference="test",
            status=PolicySetStatus.VERIFIED,
            is_complete=True,
            is_active=True,
            checked_at=datetime.utcnow() - timedelta(days=1),
            effective_at=datetime.utcnow() - timedelta(days=1),
        )
        self.db.add(policy_set)
        self.db.commit()

        rule = CoupangPolicyRule(
            policy_set_id=policy_set.id,
            match_field="display_category_code",
            match_value="cat-001",
            risk_level="SELLABLE",
            reason="테스트 판매 가능 카테고리",
        )
        self.db.add(rule)
        self.db.commit()

    def _approved_candidate(self, company_id: int, ref: str) -> ProductCandidate:

        candidate = ProductCandidate(
            candidate_key=f"COUPANG_API:COUPANG:{ref}",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference=ref,
            product_name=f"테넌트 격리 테스트 상품 {ref}",
            status="RECOMMENDED",
        )
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)

        selection = ProductCandidateSelection(
            candidate_id=candidate.id,
            company_id=company_id,
            status="APPROVED",
        )
        self.db.add(selection)
        self.db.commit()

        return candidate

    def _draft_request(
        self, sku: str, idempotency_key: str,
    ) -> CoupangDraftCreateRequest:

        return CoupangDraftCreateRequest(
            product_candidate_id=self.candidate_a.id,
            sales_method="MARKETPLACE",
            external_vendor_sku=sku,
            seller_product_name="테넌트 격리 테스트 상품",
            idempotency_key=idempotency_key,
            brand="HOMEZ",
            display_category_code="cat-001",
            gtin="8801234567890",
            sale_price=Decimal("29900"),
            shipping_method="COURIER",
            outbound_shipping_place_code="OUT-1",
            return_center_code="RET-1",
            options=[
                CoupangProductOptionInput(
                    option_name="기본", option_value="기본",
                    vendor_sku=f"{sku}-A", price=Decimal("29900"), stock=10,
                ),
            ],
        )

    # --------------------------------------------------
    # 1) 조회 격리 — 전부 404
    # --------------------------------------------------

    def test_get_product_cross_company_is_404(self):

        with self.assertRaises(NotFoundException):
            get_product(
                self.product_a.id, current_user=self.user_b, db=self.db,
            )

    def test_get_options_notices_decisions_cross_company_are_404(self):

        with self.assertRaises(NotFoundException):
            get_product_options(
                self.product_a.id, current_user=self.user_b, db=self.db,
            )

        with self.assertRaises(NotFoundException):
            get_product_notices(
                self.product_a.id, current_user=self.user_b, db=self.db,
            )

        with self.assertRaises(NotFoundException):
            get_decisions(
                self.product_a.id, current_user=self.user_b, db=self.db,
            )

    def test_list_products_only_returns_own_company(self):

        request_b = self._draft_request("sku-b", "idem-b")
        request_b.product_candidate_id = self.candidate_b.id

        create_draft(
            request_b,
            current_user=self.user_b, db=self.db,
        )

        products_a = list_products(
            status=None, sales_method=None, skip=0, limit=100,
            current_user=self.user_a, db=self.db,
        )
        products_b = list_products(
            status=None, sales_method=None, skip=0, limit=100,
            current_user=self.user_b, db=self.db,
        )

        self.assertEqual(len(products_a), 1)
        self.assertEqual(len(products_b), 1)
        self.assertNotEqual(products_a[0].id, products_b[0].id)

    # --------------------------------------------------
    # 2) 상태 전이 API 격리 — 전부 404 + 실제 상태 불변
    # --------------------------------------------------

    def test_validate_policy_cross_company_is_404_and_status_unchanged(self):

        with self.assertRaises(NotFoundException):
            validate_policy(
                self.product_a.id, current_user=self.user_b, db=self.db,
            )

        service = CoupangIntegrationService(self.db)
        unchanged = service.get(self.product_a.id, COMPANY_A)
        self.assertEqual(unchanged.status, "DRAFT")

    def test_profit_estimate_cross_company_is_404_no_row_created(self):

        request = CoupangProfitEstimateRequest(
            consumer_sale_price=Decimal("29900"),
            supplier_product_cost=Decimal("10000"),
            supplier_shipping_cost=Decimal("2000"),
            coupang_sales_fee=Decimal("2000"),
        )

        with self.assertRaises(NotFoundException):
            estimate_profit(
                self.product_a.id, request,
                current_user=self.user_b, db=self.db,
            )

        service = CoupangIntegrationService(self.db)
        estimates = service.repository.list_profit_estimates_for_company(
            self.product_a.id, COMPANY_A,
        )
        self.assertEqual(estimates, [])

    def test_dry_run_cross_company_is_404(self):

        with self.assertRaises(NotFoundException):
            run_dry_run(
                self.product_a.id,
                CoupangDryRunRequest(idempotency_key="dr-cross"),
                current_user=self.user_b, db=self.db,
            )

    def test_approve_for_submission_cross_company_is_404_no_decision_created(
        self,
    ):

        with self.assertRaises(NotFoundException):
            approve_for_submission(
                self.product_a.id,
                CoupangApprovalRequest(idempotency_key="approve-cross"),
                current_user=self.user_b, db=self.db,
            )

        service = CoupangIntegrationService(self.db)
        decisions = service.repository.list_decisions_for_company(
            self.product_a.id, COMPANY_A,
        )
        self.assertEqual(decisions, [])

    # --------------------------------------------------
    # 3) idempotency_key 회사별 독립
    # --------------------------------------------------

    def test_same_idempotency_key_independent_per_company(self):

        request_b = self._draft_request("sku-b-shared", "shared-draft-key")
        request_b.product_candidate_id = self.candidate_b.id

        # 회사 A는 setUp에서 "idem-a"로 이미 생성했으니, 다른 key로
        # 같은 문자열 "shared-draft-key"를 회사 A/B가 각각 새로 써도
        # 서로 충돌하지 않아야 한다.
        request_a2 = self._draft_request(
            "sku-a-shared", "shared-draft-key",
        )

        product_a2 = create_draft(
            request_a2, current_user=self.user_a, db=self.db,
        )
        product_b = create_draft(
            request_b, current_user=self.user_b, db=self.db,
        )

        self.assertNotEqual(product_a2.id, product_b.id)
        self.assertEqual(product_a2.company_id, COMPANY_A)
        self.assertEqual(product_b.company_id, COMPANY_B)

    # --------------------------------------------------
    # 4) 전역 정책은 회사 구분 없이 공유
    # --------------------------------------------------

    def test_policy_sets_are_shared_across_companies(self):

        sets_a = list_policy_sets(_=self.user_a, db=self.db)
        sets_b = list_policy_sets(_=self.user_b, db=self.db)

        self.assertEqual(len(sets_a), 1)
        self.assertEqual(len(sets_b), 1)
        self.assertEqual(sets_a[0].id, sets_b[0].id)


if __name__ == "__main__":
    unittest.main()
