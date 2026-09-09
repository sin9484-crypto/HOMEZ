"""
=========================================================
Homez OS

File : tests/test_coupang_policy.py

HOMEZ V3.1 Coupang Marketplace Integration Foundation
정책 검증 재감사(2026-07-29) 반영 — fail-closed 검증:

- 사용 가능한(VERIFIED·활성·완전·유효기간 내) 정책 세트가 없으면
  SELLABLE로 fail-open하지 않고 POLICY_DATA_UNAVAILABLE로 차단한다.
- DRAFT/EXPIRED/불완전/출처 없는 정책 세트만 있으면 마찬가지로 차단.
- 명시적인 VERIFIED 구조화 SELLABLE 규칙이 있을 때만 판매 가능 판정.
- 자유문자(KEYWORD) 매칭만으로는 SELLABLE을 확정하지 못한다.
- PROHIBITED는 SELLABLE보다 항상 우선한다.
- 정책 검증 실패 후에는 Dry Run·최종 승인으로 진행할 수 없다.
- 중간 실패 시 전체 rollback.
- AI가 판매 금지를 임의로 해제할 수 있는 메서드가 존재하지 않는다.

고시정보(CoupangProductNotice) 관련 Dry Run 게이트 테스트와 동시 정책
검증 경쟁 테스트는 각각 tests/test_coupang_dry_run.py,
tests/test_coupang_concurrency.py에 있다.
=========================================================
"""

import inspect
import os
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.base import Base
from app.domains.coupang.constants import IntegrationStatus
from app.domains.coupang.constants import PolicyMatchField
from app.domains.coupang.constants import PolicySetStatus
from app.domains.coupang.constants import RiskLevel
from app.domains.coupang.constants import ValidationStatus
from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate
from app.domains.coupang.repository import CoupangRepository
from app.domains.coupang.schema import CoupangDraftCreateRequest
from app.domains.coupang.schema import CoupangProductOptionInput
from app.domains.coupang.service import CoupangIntegrationService
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection



COMPANY_ID = 1
class CoupangPolicyTestCase(unittest.TestCase):
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

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = CoupangIntegrationService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    # --------------------------------------------------
    # 시드 헬퍼
    # --------------------------------------------------

    def _seed_policy_set(
        self,
        status=PolicySetStatus.VERIFIED,
        is_complete=True,
        is_active=True,
        effective_offset_days=-1,
        expires_offset_days=None,
        policy_version="test-v1",
        source_reference="test-source",
        policy_set_id=None,
    ) -> CoupangPolicySet:

        now = datetime.utcnow()

        policy_set = CoupangPolicySet(
            policy_set_id=policy_set_id or f"test-set-{id(object())}",
            policy_version=policy_version,
            source_reference=source_reference,
            status=status,
            is_complete=is_complete,
            is_active=is_active,
            checked_at=now + timedelta(days=effective_offset_days),
            effective_at=now + timedelta(days=effective_offset_days),
            expires_at=(
                now + timedelta(days=expires_offset_days)
                if expires_offset_days is not None
                else None
            ),
        )
        self.db.add(policy_set)
        self.db.commit()

        return policy_set

    def _seed_rule(
        self,
        policy_set: CoupangPolicySet,
        match_field: str,
        match_value: str,
        risk_level: str,
        reason: str = "테스트 규칙",
    ) -> CoupangPolicyRule:

        rule = CoupangPolicyRule(
            policy_set_id=policy_set.id,
            match_field=match_field,
            match_value=match_value,
            risk_level=risk_level,
            reason=reason,
        )
        self.db.add(rule)
        self.db.commit()

        return rule

    def _draft(
        self,
        product_name: str = "일반 생활용품",
        idempotency_key: str = "idem-1",
        display_category_code: str = "CAT-001",
    ) -> CoupangMarketplaceProduct:

        candidate = ProductCandidate(
            candidate_key=f"COUPANG_API:COUPANG:{idempotency_key}",
            source_type="COUPANG_API",
            market="COUPANG",
            source_reference=idempotency_key,
            product_name=product_name,
            status=CandidateStatus.APPROVED,
        )
        self.db.add(candidate)
        self.db.commit()

        request = CoupangDraftCreateRequest(
            product_candidate_id=candidate.id,
            sales_method="MARKETPLACE",
            external_vendor_sku=f"SKU-{idempotency_key}",
            seller_product_name=product_name,
            idempotency_key=idempotency_key,
            brand="HOMEZ",
            display_category_code=display_category_code,
            gtin="8801234567890",
            sale_price=Decimal("10000"),
            shipping_method="COURIER",
            outbound_shipping_place_code="OUT-1",
            return_center_code="RET-1",
            options=[
                CoupangProductOptionInput(
                    option_name="기본",
                    option_value="기본",
                    vendor_sku=f"SKU-{idempotency_key}-A",
                    price=Decimal("10000"),
                    stock=10,
                ),
            ],
        )

        product, _dup, _events = self.service.create_draft(
            request, company_id=COMPANY_ID,
                correlation_id="c1",
        )

        return product

    # --------------------------------------------------
    # 1) 정책 0건이면 검증 실패 (fail-closed)
    # --------------------------------------------------

    def test_zero_policy_sets_blocks_validation(self):

        product = self._draft()

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)
        self.assertEqual(validated.validation_status, ValidationStatus.FAILED)
        self.assertEqual(validated.risk_level, RiskLevel.POLICY_DATA_UNAVAILABLE)
        self.assertIn("POLICY_DATA_UNAVAILABLE", validated.validation_errors)

    # --------------------------------------------------
    # 2) DRAFT 정책만 있으면 검증 실패
    # --------------------------------------------------

    def test_draft_only_policy_set_blocks_validation(self):

        policy_set = self._seed_policy_set(status=PolicySetStatus.DRAFT)
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="draft-only")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)
        self.assertEqual(validated.risk_level, RiskLevel.POLICY_DATA_UNAVAILABLE)

    # --------------------------------------------------
    # 3) 만료 정책만 있으면 검증 실패
    # --------------------------------------------------

    def test_expired_policy_set_blocks_validation(self):

        policy_set = self._seed_policy_set(
            status=PolicySetStatus.VERIFIED,
            effective_offset_days=-10,
            expires_offset_days=-1,  # 이미 만료됨
        )
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="expired-only")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)
        self.assertEqual(validated.risk_level, RiskLevel.POLICY_DATA_UNAVAILABLE)

    def test_verified_but_not_yet_effective_policy_set_blocks_validation(self):
        """effective_at이 미래인 정책 세트도 아직 사용 불가로 취급한다."""

        policy_set = self._seed_policy_set(
            status=PolicySetStatus.VERIFIED,
            effective_offset_days=+10,
        )
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="not-yet-effective")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.risk_level, RiskLevel.POLICY_DATA_UNAVAILABLE)

    # --------------------------------------------------
    # 4) 불완전한 정책 세트면 검증 실패
    # --------------------------------------------------

    def test_incomplete_policy_set_blocks_validation(self):

        policy_set = self._seed_policy_set(is_complete=False)
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="incomplete-only")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)
        self.assertEqual(validated.risk_level, RiskLevel.POLICY_DATA_UNAVAILABLE)

    # --------------------------------------------------
    # 5) 출처 없는 정책이면 검증 실패
    # --------------------------------------------------

    def test_policy_set_without_source_reference_blocks_validation(self):

        policy_set = self._seed_policy_set(source_reference="")
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="no-source")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)
        self.assertEqual(validated.risk_level, RiskLevel.POLICY_DATA_UNAVAILABLE)

    def test_policy_set_without_version_blocks_validation(self):

        policy_set = self._seed_policy_set(policy_version="")
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="no-version")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.risk_level, RiskLevel.POLICY_DATA_UNAVAILABLE)

    # --------------------------------------------------
    # 6) 명시적인 VERIFIED SELLABLE 규칙이 있을 때만 통과
    # --------------------------------------------------

    def test_explicit_verified_structured_sellable_rule_passes(self):

        policy_set = self._seed_policy_set()
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE, "CAT-001은 판매 가능 카테고리로 확인됨",
        )

        product = self._draft(idempotency_key="explicit-sellable")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.READY_FOR_REVIEW)
        self.assertEqual(validated.risk_level, RiskLevel.SELLABLE)

    # --------------------------------------------------
    # 7) PROHIBITED가 SELLABLE보다 항상 우선
    # --------------------------------------------------

    def test_prohibited_always_wins_over_sellable(self):

        policy_set = self._seed_policy_set()
        # 카테고리 자체는 판매 가능으로 확인되어 있지만,
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE, "카테고리 자체는 판매 가능",
        )
        # 상품명에 위험물 키워드가 있으면 그래도 금지되어야 한다.
        self._seed_rule(
            policy_set, PolicyMatchField.KEYWORD, "고압가스",
            RiskLevel.PROHIBITED, "위험물 — 판매 금지",
        )

        product = self._draft(
            product_name="캠핑용 고압가스 버너세트",
            idempotency_key="prohibited-wins",
        )

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)
        self.assertEqual(validated.risk_level, RiskLevel.PROHIBITED)

    # --------------------------------------------------
    # 8) 문자열 미매칭 / 키워드 단독 매칭만으로 SELLABLE 처리되지 않음
    # --------------------------------------------------

    def test_no_matching_rule_does_not_default_to_sellable(self):

        policy_set = self._seed_policy_set()
        # 이 상품과 관련 없는 규칙만 존재.
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-999",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="no-match")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertNotEqual(validated.risk_level, RiskLevel.SELLABLE)
        self.assertEqual(validated.risk_level, RiskLevel.OPERATOR_REVIEW_REQUIRED)
        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)

    def test_keyword_match_alone_does_not_grant_sellable(self):
        """
        자유문자(KEYWORD) 규칙이 risk_level=SELLABLE로 매칭되더라도,
        그것만으로는 판매 가능을 확정하지 않는다(보조 위험 탐지 전용).
        """

        policy_set = self._seed_policy_set()
        self._seed_rule(
            policy_set, PolicyMatchField.KEYWORD, "생활용품",
            RiskLevel.SELLABLE, "키워드만으로 판매 가능 취급하면 안 됨",
        )

        product = self._draft(
            product_name="일반 생활용품 세트", idempotency_key="keyword-sellable",
        )

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertNotEqual(validated.risk_level, RiskLevel.SELLABLE)
        self.assertEqual(validated.risk_level, RiskLevel.OPERATOR_REVIEW_REQUIRED)

    def test_keyword_match_can_still_escalate_risk(self):
        """
        KEYWORD 규칙은 SELLABLE 확정에는 못 쓰이지만, PROHIBITED 등
        위험 등급 상향에는 계속 사용할 수 있다(보조 위험 탐지).
        """

        policy_set = self._seed_policy_set()
        self._seed_rule(
            policy_set, PolicyMatchField.KEYWORD, "고압가스",
            RiskLevel.PROHIBITED, "위험물 — 판매 금지",
        )

        product = self._draft(
            product_name="캠핑용 고압가스 버너세트",
            idempotency_key="keyword-prohibited",
        )

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )

        self.assertEqual(validated.risk_level, RiskLevel.PROHIBITED)
        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)

    # --------------------------------------------------
    # 11) 정책 검증 실패 후 Dry Run·최종 승인 차단
    # --------------------------------------------------

    def test_validation_failure_blocks_dry_run_and_approval(self):

        product = self._draft(idempotency_key="blocked-progress")

        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )
        self.assertEqual(validated.status, IntegrationStatus.VALIDATION_FAILED)

        with self.assertRaises(BadRequestException):
            self.service.run_dry_run(
                product.id, COMPANY_ID, idempotency_key="dr-blocked", correlation_id="c3",
            )

        with self.assertRaises(BadRequestException):
            self.service.approve_for_submission(
                product.id, COMPANY_ID, operator_id=1, is_admin=True,
                idempotency_key="approve-blocked", memo=None,
                correlation_id="c4",
            )

    # --------------------------------------------------
    # 12) 중간 실패 시 전체 rollback
    # --------------------------------------------------

    def test_mid_validation_failure_rolls_back(self):

        policy_set = self._seed_policy_set()
        self._seed_rule(
            policy_set, PolicyMatchField.DISPLAY_CATEGORY_CODE, "cat-001",
            RiskLevel.SELLABLE,
        )

        product = self._draft(idempotency_key="mid-failure")

        with mock.patch.object(
            CoupangRepository,
            "finalize_validation_conditional",
            side_effect=RuntimeError("강제 실패"),
        ):
            with self.assertRaises(RuntimeError):
                self.service.validate_policy(product.id, COMPANY_ID, correlation_id="c2")

        # 강제 실패 이후 DB에는 어떤 변경도 반영되지 않아야 한다
        # (rollback이 정확히 동작함).
        reloaded = self.service.get(product.id, COMPANY_ID)
        self.assertEqual(reloaded.status, IntegrationStatus.DRAFT)
        self.assertEqual(
            reloaded.validation_status, ValidationStatus.NOT_VALIDATED,
        )
        self.assertIsNone(reloaded.validation_errors)
        self.assertIsNone(reloaded.risk_level)

    # --------------------------------------------------
    # AI가 판매 금지를 임의 해제할 수 없음
    # --------------------------------------------------

    def test_service_has_no_method_to_override_or_relax_risk_level(self):
        """
        CoupangIntegrationService에는 risk_level을 낮추거나(완화) 지우는
        어떤 public 메서드도 존재하지 않는다 — 정책을 바꾸려면
        CoupangPolicySet/CoupangPolicyRule 데이터 자체를 admin_guard로
        보호된 API를 통해 별도 승인 하에 직접 갱신해야 한다.
        """

        forbidden_name_fragments = (
            "override", "relax", "bypass", "unblock", "clear_risk",
            "force_approve", "disable_policy",
        )

        public_methods = [
            name
            for name, _ in inspect.getmembers(
                CoupangIntegrationService, predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        ]

        for method_name in public_methods:
            lowered = method_name.lower()
            for fragment in forbidden_name_fragments:
                self.assertNotIn(
                    fragment, lowered,
                    f"위험 완화/해제성 메서드로 의심되는 이름 발견: "
                    f"{method_name}",
                )

    def test_prohibited_status_cannot_be_revalidated_past_failure(self):
        """
        VALIDATION_FAILED 상태에서 validate_policy를 다시 호출해도(DRAFT가
        아니므로) 차단된다 — 재시도로 금지 판정을 우회할 수 없다.
        """

        policy_set = self._seed_policy_set()
        self._seed_rule(
            policy_set, PolicyMatchField.KEYWORD, "고압가스",
            RiskLevel.PROHIBITED, "판매 금지",
        )

        product = self._draft(
            product_name="고압가스 캔", idempotency_key="revalidate-blocked",
        )

        self.service.validate_policy(product.id, COMPANY_ID, correlation_id="c2")

        with self.assertRaises(BadRequestException):
            self.service.validate_policy(product.id, COMPANY_ID, correlation_id="c3")

    # --------------------------------------------------
    # 정책 세트 관리 API (admin 전용, 별도 명시 작업)
    # --------------------------------------------------

    def test_create_policy_set_and_add_rule_via_service(self):

        from app.domains.coupang.schema import CoupangPolicyRuleCreateRequest
        from app.domains.coupang.schema import CoupangPolicySetCreateRequest

        now = datetime.utcnow()
        policy_set = self.service.create_policy_set(
            CoupangPolicySetCreateRequest(
                policy_set_id="admin-created-1",
                policy_version="v1",
                source_reference="관리자 확인 문서",
                status=PolicySetStatus.VERIFIED,
                is_complete=True,
                checked_at=now,
                effective_at=now - timedelta(hours=1),
            ),
        )
        self.assertEqual(policy_set.status, PolicySetStatus.VERIFIED)

        rule = self.service.add_policy_rule(
            policy_set.id,
            CoupangPolicyRuleCreateRequest(
                match_field=PolicyMatchField.DISPLAY_CATEGORY_CODE,
                match_value="cat-001",
                risk_level=RiskLevel.SELLABLE,
                reason="관리자가 확인한 판매 가능 카테고리",
            ),
        )
        self.assertEqual(rule.policy_set_id, policy_set.id)

        product = self._draft(idempotency_key="admin-created-flow")
        validated, _ = self.service.validate_policy(
            product.id, COMPANY_ID, correlation_id="c2",
        )
        self.assertEqual(validated.status, IntegrationStatus.READY_FOR_REVIEW)


if __name__ == "__main__":
    unittest.main()
