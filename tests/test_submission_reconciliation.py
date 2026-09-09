"""
=========================================================
Homez OS

File : tests/test_submission_reconciliation.py

2026-08-30 V7 후속 안정화 Phase 5 — 성공 제출 정합화 서비스 검증.
Fake Provider만 사용한다(실제 쿠팡 상태 조회 API를 호출하는 코드
자체가 없음). marketplace_submissions 원본 행을 절대 UPDATE하지
않는다는 계약을 직접 확인한다(before/after 비교).

2026-08-31 V7 필수 작업 2번(제출 장부 정합화 완성) — preview/apply
분리, 상태 자격(PENDING/SUBMITTING/UNKNOWN만), 중복 sellerProductId
차단, 다중 식별값 대조, 동시 적용 멱등성을 추가로 검증한다.
=========================================================
"""

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing.coupang_live_provider import ProductStatusResult
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
    MarketplaceListing,
    MarketplaceSubmission,
    MarketplaceSubmissionReconciliation,
)
from app.domains.marketplace_listing.submission_reconciliation_service import (
    MarketplaceSubmissionReconciliationRepository,
    apply_reconciliation,
    preview_reconciliation,
)
from app.domains.product_candidate.model import ProductCandidate
from app.domains.user.model import User  # noqa: F401 — Company.relationship("User") 등록용


class _FakeStatusProvider:

    def __init__(self, result: ProductStatusResult):
        self.result = result
        self.calls = []

    def get_product_status(self, seller_product_id):
        self.calls.append(seller_product_id)
        return self.result

    def create_product(self, payload):  # pragma: no cover - 이 테스트에서 쓰지 않음
        raise AssertionError("정합화 테스트는 create_product를 호출하면 안 된다")


class _ReconciliationTestBase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__, MarketplaceSubmission.__table__,
                MarketplaceSubmissionReconciliation.__table__,
                MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                ProductCandidate.__table__,
            ],
        )

        self.db_conn = self.engine.connect()
        self.db_conn.execute(text(
            "CREATE TABLE audit_logs ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "company_id INTEGER, user_id INTEGER, "
            "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
            "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
            "ip_address VARCHAR(50)"
            ")",
        ))
        self.db_conn.commit()

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        self.company_a = Company(name="회사 A")
        self.company_b = Company(name="회사 B")
        self.db.add_all([self.company_a, self.company_b])
        self.db.commit()

    def tearDown(self):

        self.db.close()
        self.db_conn.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_submission(
        self, company, *, status="UNKNOWN", external_ref=None, key="key-1",
    ):

        submission = MarketplaceSubmission(
            company_id=company.id, listing_id=1, selection_id=1,
            marketplace_account_id=1, status=status,
            safety_decision="ALLOW", external_submission_ref=external_ref,
            error_reason="INVALID_RESPONSE: 원본 오류(보존돼야 함)" if status == "UNKNOWN" else None,
            idempotency_key=key, attempted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        self.db.add(submission)
        self.db.commit()
        self.db.refresh(submission)
        return submission

    def _seed_submission_with_known_identifiers(
        self, company, *, product_name, vendor_user_id, display_category_code,
        key="key-with-identifiers",
    ):
        """listing/selection/candidate까지 실제로 만들어, 정합화
        서비스가 기대 식별값을 실제로 조회해 매칭 엔진에 넘기는
        경로(단위 테스트가 아니라 서비스 통합 경로)를 검증한다."""

        candidate = ProductCandidate(
            candidate_key=f"candidate-{key}", source_type="MANUAL",
            source_reference=key, market="COUPANG", product_name=product_name,
        )
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)

        listing = MarketplaceListing(
            company_id=company.id, product_candidate_id=candidate.id,
            marketplace_account_id=1,
        )
        self.db.add(listing)
        self.db.commit()
        self.db.refresh(listing)

        selection = MarketplaceFulfillmentSelection(
            company_id=company.id, listing_id=listing.id, capability_id=1,
            fulfillment_mode="SELLER_FULFILLED",
            required_fields_json=(
                '{"vendorUserId": "%s", "displayCategoryCode": "%s"}'
                % (vendor_user_id, display_category_code)
            ),
            required_fields_schema_name="coupang.seller_fulfilled",
            required_fields_schema_version="1",
            required_fields_fingerprint="fingerprint",
            selected_by=1, idempotency_key=f"selection-{key}",
        )
        self.db.add(selection)
        self.db.commit()
        self.db.refresh(selection)

        submission = MarketplaceSubmission(
            company_id=company.id, listing_id=listing.id, selection_id=selection.id,
            marketplace_account_id=1, status="UNKNOWN",
            safety_decision="ALLOW", error_reason="TIMEOUT",
            idempotency_key=key, attempted_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        self.db.add(submission)
        self.db.commit()
        self.db.refresh(submission)
        return submission


class SubmissionReconciliationPreviewTestCase(_ReconciliationTestBase):
    """preview_reconciliation()은 순수 조회다 — 아무 것도 쓰지 않는다."""

    def test_preview_ready_manual_review_when_no_expected_identifiers(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider,
        )

        # 이 테스트 DB에는 listing/selection이 실제로 없으므로(대조할
        # 기대값 자체가 없음) MANUAL_REVIEW_REQUIRED로 판정돼야 한다.
        self.assertEqual(assessment.outcome, "READY_MANUAL_REVIEW")
        self.assertEqual(assessment.match_tier, "MANUAL_REVIEW_REQUIRED")

        # preview는 아무것도 쓰지 않는다.
        self.assertIsNone(
            MarketplaceSubmissionReconciliationRepository(self.db)
            .get_by_submission_id(submission.id),
        )
        self.db.refresh(submission)
        self.assertIsNone(submission.external_submission_ref)

    def test_preview_blocks_ineligible_status(self):

        submission = self._seed_submission(self.company_a, status="SUBMITTED")
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider,
        )

        self.assertEqual(assessment.outcome, "INELIGIBLE_SUBMISSION_STATUS")
        self.assertEqual(provider.calls, [])  # 상태 조회조차 하지 않는다

    def test_preview_blocks_duplicate_external_ref_on_another_submission(self):

        other = self._seed_submission(
            self.company_a, status="SUBMITTED", external_ref="90000000001", key="key-other",
        )
        submission = self._seed_submission(self.company_a, key="key-target")
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider,
        )

        self.assertEqual(assessment.outcome, "DUPLICATE_EXTERNAL_REF")
        self.assertIn(str(other.id), assessment.detail)
        self.assertEqual(provider.calls, [])  # 중복이면 상태 조회도 하지 않는다

    def test_preview_status_not_found(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(ProductStatusResult(outcome="NOT_FOUND"))

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="99999999",
            provider=provider,
        )

        self.assertEqual(assessment.outcome, "STATUS_NOT_CONFIRMED")

    def test_preview_denied_status_is_not_success(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="DENIED"),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="12345",
            provider=provider,
        )

        self.assertEqual(assessment.outcome, "STATUS_NOT_SUCCESS")

    def test_preview_already_resolved_original_record(self):

        submission = self._seed_submission(
            self.company_a, status="SUBMITTED", external_ref="ALREADY-SET",
        )
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="ALREADY-SET",
            provider=provider,
        )

        self.assertEqual(assessment.outcome, "ALREADY_RESOLVED_ON_ORIGINAL_RECORD")
        self.assertEqual(provider.calls, [])

    def test_preview_company_isolation(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        with self.assertRaises(Exception):
            preview_reconciliation(
                self.db, submission_id=submission.id, company_id=self.company_b.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider,
            )

        self.assertEqual(provider.calls, [])

    def test_preview_identifier_mismatch_blocks_even_with_matching_status(self):
        # listing/selection이 없어 기대 식별값이 전혀 없는 이 테스트
        # DB 환경에서는 mismatch를 직접 재현할 수 없으므로(대조 자체가
        # UNAVAILABLE), 매칭 엔진 단위 테스트에서 별도로 검증한다.
        # 여기서는 매칭 엔진이 서비스에 실제로 연결돼 있다는 것만
        # match_tier 필드 존재로 확인한다.
        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider,
        )

        self.assertIn(
            assessment.match_tier, ("MANUAL_REVIEW_REQUIRED", "AUTO_ELIGIBLE"),
        )

    def test_preview_auto_eligible_when_identifiers_match(self):

        submission = self._seed_submission_with_known_identifiers(
            self.company_a, product_name="정합화 테스트 상품",
            vendor_user_id="VENDOR-001", display_category_code="80754",
        )
        provider = _FakeStatusProvider(
            ProductStatusResult(
                outcome="FOUND", status_name="APPROVED",
                seller_product_name="정합화 테스트 상품",
                vendor_user_id="VENDOR-001", display_category_code="80754",
            ),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider,
        )

        self.assertEqual(assessment.outcome, "READY_AUTO_ELIGIBLE")
        self.assertEqual(assessment.match_tier, "AUTO_ELIGIBLE")
        self.assertIn("product_name", assessment.matched_fields)
        self.assertIn("vendor_user_id", assessment.matched_fields)
        self.assertIn("display_category_code", assessment.matched_fields)

    def test_preview_blocks_on_identifier_mismatch(self):

        submission = self._seed_submission_with_known_identifiers(
            self.company_a, product_name="정합화 테스트 상품",
            vendor_user_id="VENDOR-001", display_category_code="80754",
        )
        provider = _FakeStatusProvider(
            ProductStatusResult(
                outcome="FOUND", status_name="APPROVED",
                seller_product_name="완전히 다른 상품",
                vendor_user_id="OTHER-VENDOR", display_category_code="99999",
            ),
        )

        assessment = preview_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider,
        )

        self.assertEqual(assessment.outcome, "IDENTIFIER_MISMATCH")
        self.assertEqual(assessment.match_tier, "BLOCKED")
        self.assertGreaterEqual(len(assessment.mismatched_fields), 1)


class SubmissionReconciliationApplyTestCase(_ReconciliationTestBase):

    def test_successful_apply_appends_row_without_touching_original(self):

        submission = self._seed_submission(self.company_a)
        original_error_reason = submission.error_reason
        original_status = submission.status

        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        result = apply_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider, actor_user_id=1, reason="WING 화면에서 직접 확인",
        )

        self.assertEqual(result.outcome, "RECONCILED")
        self.assertEqual(result.external_submission_ref, "90000000001")
        self.assertEqual(result.observed_status_name, "APPROVED")
        self.assertEqual(provider.calls, ["90000000001"])

        # 원본 행은 절대 건드리지 않는다(append-only 계약).
        self.db.refresh(submission)
        self.assertEqual(submission.error_reason, original_error_reason)
        self.assertEqual(submission.status, original_status)
        self.assertIsNone(submission.external_submission_ref)

        # 별도 행이 실제로 append됐는지 확인.
        recon_repo = MarketplaceSubmissionReconciliationRepository(self.db)
        recon = recon_repo.get_by_submission_id(submission.id)
        self.assertIsNotNone(recon)
        self.assertEqual(recon.external_submission_ref, "90000000001")
        self.assertEqual(recon.reconciled_by_user_id, 1)

    def test_apply_requires_non_empty_reason(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        with self.assertRaises(Exception):
            apply_reconciliation(
                self.db, submission_id=submission.id, company_id=self.company_a.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider, actor_user_id=1, reason="   ",
            )
        self.assertEqual(provider.calls, [])

    def test_apply_is_idempotent(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        first = apply_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider, actor_user_id=1, reason="최초 정합화",
        )
        second = apply_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider, actor_user_id=2, reason="다시 시도",
        )

        self.assertEqual(first.outcome, "RECONCILED")
        self.assertEqual(second.outcome, "ALREADY_RECONCILED")
        self.assertEqual(provider.calls, ["90000000001"])

        rows = self.db.query(MarketplaceSubmissionReconciliation).filter(
            MarketplaceSubmissionReconciliation.submission_id == submission.id,
        ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].reconciled_by_user_id, 1)  # 두 번째 호출로 덮이지 않음

    def test_apply_blocks_ineligible_status(self):

        submission = self._seed_submission(self.company_a, status="FAILED")
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        with self.assertRaises(Exception):
            apply_reconciliation(
                self.db, submission_id=submission.id, company_id=self.company_a.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider, actor_user_id=1, reason="확인",
            )
        self.assertEqual(provider.calls, [])

    def test_apply_blocks_duplicate_external_ref_across_submissions(self):

        self._seed_submission(
            self.company_a, status="SUBMITTED", external_ref="90000000001", key="key-other",
        )
        submission = self._seed_submission(self.company_a, key="key-target")
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        with self.assertRaises(Exception):
            apply_reconciliation(
                self.db, submission_id=submission.id, company_id=self.company_a.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider, actor_user_id=1, reason="확인",
            )
        self.assertEqual(provider.calls, [])

    def test_apply_blocks_duplicate_external_ref_across_reconciliations(self):

        first_submission = self._seed_submission(self.company_a, key="key-first")
        second_submission = self._seed_submission(self.company_a, key="key-second")
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        apply_reconciliation(
            self.db, submission_id=first_submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider, actor_user_id=1, reason="최초 정합화",
        )

        with self.assertRaises(Exception):
            apply_reconciliation(
                self.db, submission_id=second_submission.id, company_id=self.company_a.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider, actor_user_id=1, reason="같은 ID 재사용 시도",
            )

    def test_apply_status_not_found_is_not_reconciled(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(ProductStatusResult(outcome="NOT_FOUND"))

        result = apply_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="99999999",
            provider=provider, actor_user_id=1, reason="확인 시도",
        )

        self.assertEqual(result.outcome, "STATUS_NOT_CONFIRMED")
        self.assertIsNone(
            MarketplaceSubmissionReconciliationRepository(self.db)
            .get_by_submission_id(submission.id),
        )

    def test_apply_denied_status_is_not_treated_as_success(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="DENIED"),
        )

        result = apply_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="12345",
            provider=provider, actor_user_id=1, reason="확인 시도",
        )

        self.assertEqual(result.outcome, "STATUS_NOT_SUCCESS")
        self.assertIsNone(
            MarketplaceSubmissionReconciliationRepository(self.db)
            .get_by_submission_id(submission.id),
        )

    def test_apply_already_resolved_original_record_is_not_reconciled_again(self):

        submission = self._seed_submission(
            self.company_a, status="SUBMITTED", external_ref="ALREADY-SET",
        )
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        result = apply_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="ALREADY-SET",
            provider=provider, actor_user_id=1, reason="확인 시도",
        )

        self.assertEqual(result.outcome, "ALREADY_RESOLVED_ON_ORIGINAL_RECORD")
        self.assertEqual(provider.calls, [])

    def test_company_isolation_blocks_cross_company_apply(self):

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        with self.assertRaises(Exception):
            apply_reconciliation(
                self.db, submission_id=submission.id, company_id=self.company_b.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider, actor_user_id=1, reason="다른 회사 시도",
            )

        self.assertEqual(provider.calls, [])

    def test_apply_blocks_on_identifier_mismatch(self):

        submission = self._seed_submission_with_known_identifiers(
            self.company_a, product_name="정합화 테스트 상품",
            vendor_user_id="VENDOR-001", display_category_code="80754",
            key="mismatch-apply",
        )
        provider = _FakeStatusProvider(
            ProductStatusResult(
                outcome="FOUND", status_name="APPROVED",
                seller_product_name="완전히 다른 상품",
                vendor_user_id="OTHER-VENDOR", display_category_code="99999",
            ),
        )

        with self.assertRaises(Exception):
            apply_reconciliation(
                self.db, submission_id=submission.id, company_id=self.company_a.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider, actor_user_id=1, reason="불일치 강행 시도",
            )

        self.assertIsNone(
            MarketplaceSubmissionReconciliationRepository(self.db)
            .get_by_submission_id(submission.id),
        )

    def test_apply_succeeds_when_identifiers_match(self):

        submission = self._seed_submission_with_known_identifiers(
            self.company_a, product_name="정합화 테스트 상품",
            vendor_user_id="VENDOR-001", display_category_code="80754",
            key="match-apply",
        )
        provider = _FakeStatusProvider(
            ProductStatusResult(
                outcome="FOUND", status_name="APPROVED",
                seller_product_name="정합화 테스트 상품",
                vendor_user_id="VENDOR-001", display_category_code="80754",
            ),
        )

        result = apply_reconciliation(
            self.db, submission_id=submission.id, company_id=self.company_a.id,
            operator_confirmed_seller_product_id="90000000001",
            provider=provider, actor_user_id=1, reason="다중 식별값 일치 확인",
        )

        self.assertEqual(result.outcome, "RECONCILED")

    def test_concurrent_apply_only_commits_once(self):
        """
        2026-08-31 V7 필수 작업 2번 — 두 요청이 동시에 같은 submission_id
        를 정합화하려는 TOCTOU 경쟁을 결정적으로 재현한다: "이미
        정합화됐는지" 확인은 통과했지만(existing is None) 그 직후
        다른 커넥션이 먼저 커밋해버린 상황을, 실제 스레드 타이밍에
        기대지 않고 create_no_commit() 호출 시점에 강제로 주입한다.
        DB UNIQUE 제약이 이 세션의 커밋을 막아야 하고,
        apply_reconciliation()은 그 IntegrityError를 500으로 흘리지
        않고 ALREADY_RECONCILED로 정상 반환해야 한다.
        """

        submission = self._seed_submission(self.company_a)
        provider = _FakeStatusProvider(
            ProductStatusResult(outcome="FOUND", status_name="APPROVED"),
        )

        original_create = MarketplaceSubmissionReconciliationRepository.create_no_commit

        def _racing_create(repo_self, **kwargs):
            racing_session = self.SessionLocal()
            racing_session.add(MarketplaceSubmissionReconciliation(
                company_id=self.company_a.id, submission_id=submission.id,
                external_submission_ref="RACED-IN-FIRST",
                observed_status_name="APPROVED", reconciled_by_user_id=999,
                reconciled_at=datetime.utcnow(), reason="경쟁 커넥션",
                created_at=datetime.utcnow(),
            ))
            racing_session.commit()
            racing_session.close()
            return original_create(repo_self, **kwargs)

        with patch.object(
            MarketplaceSubmissionReconciliationRepository,
            "create_no_commit", _racing_create,
        ):
            result = apply_reconciliation(
                self.db, submission_id=submission.id, company_id=self.company_a.id,
                operator_confirmed_seller_product_id="90000000001",
                provider=provider, actor_user_id=1, reason="경쟁 상황 재현",
            )

        self.assertEqual(result.outcome, "ALREADY_RECONCILED")
        self.assertEqual(result.external_submission_ref, "RACED-IN-FIRST")

        rows = self.db.query(MarketplaceSubmissionReconciliation).filter(
            MarketplaceSubmissionReconciliation.submission_id == submission.id,
        ).all()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
