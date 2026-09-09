"""
=========================================================
Homez OS

File : tests/test_listing_wizard_reconciliation_router.py

2026-08-31 V7 필수 작업 2번(제출 장부 정합화 완성) — 정합화 라우터
배선(누가 preview/apply를 호출할 수 있는지)과 실제 라우터 함수 호출
경로(FakeCoupangLiveProductProvider, 실제 API 호출 없음)를 검증한다.
이 저장소 관례대로 TestClient가 아니라 라우터 함수를 실제 Session·
User로 직접 호출한다(tests/test_listing_wizard_router.py와 동일 패턴).
=========================================================
"""

import os
import tempfile
import types
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.guard import SuperAdminGuard
from app.core.guard import admin_guard
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.marketplace_listing import listing_wizard_router as wr
from app.domains.marketplace_listing.listing_wizard_permission_guard import (
    ListingWizardPermissionGuard,
)
from app.domains.marketplace_listing.model import (
    MarketplaceFulfillmentSelection,
    MarketplaceListing,
    MarketplaceSubmission,
    MarketplaceSubmissionReconciliation,
)
from app.domains.marketplace_listing.schema import (
    MarketplaceSubmissionReconciliationApplyRequest,
    MarketplaceSubmissionReconciliationPreviewRequest,
)
from app.domains.product_candidate.model import ProductCandidate


class ReconciliationRouterWiringTestCase(unittest.TestCase):
    """apply만 SuperAdminGuard, 나머지는 ListingWizardPermissionGuard."""

    def _route(self, path: str, method: str):

        return next(
            r for r in wr.router.routes
            if r.path == path and method in r.methods
        )

    def test_apply_requires_super_admin_guard(self):

        route = self._route(
            "/listing-wizards/reconciliation/submissions/{submission_id}/apply",
            "POST",
        )
        calls = [d.call for d in route.dependant.dependencies]

        self.assertIn(SuperAdminGuard, calls)
        self.assertNotIn(admin_guard, calls)

    def test_preview_uses_permission_guard_not_role_guards(self):

        route = self._route(
            "/listing-wizards/reconciliation/submissions/{submission_id}/preview",
            "POST",
        )
        calls = [d.call for d in route.dependant.dependencies]

        self.assertNotIn(admin_guard, calls)
        self.assertNotIn(SuperAdminGuard, calls)
        self.assertTrue(any(
            getattr(c, "__qualname__", "").startswith(
                "ListingWizardPermissionGuard.",
            )
            for c in calls
        ))

    def test_unresolved_list_and_get_use_permission_guard(self):

        for path, method in [
            ("/listing-wizards/reconciliation/unresolved-submissions", "GET"),
            (
                "/listing-wizards/reconciliation/submissions/{submission_id}",
                "GET",
            ),
        ]:
            route = self._route(path, method)
            calls = [d.call for d in route.dependant.dependencies]
            self.assertNotIn(admin_guard, calls, path)
            self.assertNotIn(SuperAdminGuard, calls, path)
            self.assertTrue(
                any(
                    getattr(c, "__qualname__", "").startswith(
                        "ListingWizardPermissionGuard.",
                    )
                    for c in calls
                ),
                path,
            )


class ReconciliationRouterFunctionalTestCase(unittest.TestCase):

    def setUp(self):

        os.environ["HOMEZ_TEST_FAKE_COUPANG_PROVIDER"] = "1"

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                Company.__table__,
                ProductCandidate.__table__,
                MarketplaceListing.__table__,
                MarketplaceFulfillmentSelection.__table__,
                MarketplaceSubmission.__table__,
                MarketplaceSubmissionReconciliation.__table__,
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

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()

        company_a = Company(
            name="A", business_number="1", ceo="t", phone="t",
            email="a@example.com", address="t",
        )
        company_b = Company(
            name="B", business_number="2", ceo="t", phone="t",
            email="b@example.com", address="t",
        )
        self.db.add_all([company_a, company_b])
        self.db.commit()

        self.admin_a = types.SimpleNamespace(
            id=1, company_id=company_a.id, role="ADMIN",
        )
        self.super_admin_a = types.SimpleNamespace(
            id=2, company_id=company_a.id, role="SUPER_ADMIN",
        )
        self.admin_b = types.SimpleNamespace(
            id=3, company_id=company_b.id, role="ADMIN",
        )

        # FakeCoupangLiveProductProvider의 기본(마커 없음) 응답값과
        # 정확히 일치하는 기대 식별값을 심어 둔다 — FAKE-RECONCILE-
        # MISMATCH 마커가 실제로 대조 실패를 일으키는지(빈 기대값이라
        # 항상 UNAVAILABLE로만 판정되는 상황을 피해) 검증할 수 있게
        # 한다.
        candidate = ProductCandidate(
            candidate_key="router-test-candidate", source_type="MANUAL",
            source_reference="router-test", market="COUPANG",
            product_name="[FAKE PACKAGED TEST] 정합화 대상 상품",
        )
        self.db.add(candidate)
        self.db.commit()
        self.db.refresh(candidate)

        listing = MarketplaceListing(
            company_id=company_a.id, product_candidate_id=candidate.id,
            marketplace_account_id=1,
        )
        self.db.add(listing)
        self.db.commit()
        self.db.refresh(listing)

        selection = MarketplaceFulfillmentSelection(
            company_id=company_a.id, listing_id=listing.id, capability_id=1,
            fulfillment_mode="SELLER_FULFILLED",
            required_fields_json=(
                '{"vendorUserId": "FAKE-VENDOR-USER-001", '
                '"displayCategoryCode": "80754"}'
            ),
            required_fields_schema_name="coupang.seller_fulfilled",
            required_fields_schema_version="1",
            required_fields_fingerprint="fingerprint",
            selected_by=1, idempotency_key="router-test-selection",
        )
        self.db.add(selection)
        self.db.commit()
        self.db.refresh(selection)

        submission = MarketplaceSubmission(
            company_id=company_a.id, listing_id=listing.id,
            selection_id=selection.id,
            marketplace_account_id=1, status="UNKNOWN",
            safety_decision="ALLOW", error_reason="TIMEOUT",
            idempotency_key="router-test-key",
        )
        self.db.add(submission)
        self.db.commit()
        self.db.refresh(submission)
        self.submission = submission

    def tearDown(self):

        self.db.close()
        self.db_conn.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.environ.pop("HOMEZ_TEST_FAKE_COUPANG_PROVIDER", None)

    def test_list_unresolved_submissions_scoped_to_company(self):

        rows = wr.list_unresolved_submissions(
            current_user=self.admin_a, db=self.db,
        )
        self.assertEqual([r.id for r in rows], [self.submission.id])

        rows_b = wr.list_unresolved_submissions(
            current_user=self.admin_b, db=self.db,
        )
        self.assertEqual(rows_b, [])

    def test_get_reconciliation_returns_none_when_absent(self):

        result = wr.get_submission_reconciliation(
            self.submission.id, current_user=self.admin_a, db=self.db,
        )
        self.assertIsNone(result)

    def test_preview_via_admin_guard_is_allowed_and_writes_nothing(self):

        assessment = wr.preview_submission_reconciliation(
            self.submission.id,
            MarketplaceSubmissionReconciliationPreviewRequest(
                operator_confirmed_seller_product_id="FAKE-RECONCILE-NO-EVIDENCE",
            ),
            current_user=self.admin_a, db=self.db,
        )

        self.assertIn(
            assessment.outcome, ("READY_MANUAL_REVIEW", "READY_AUTO_ELIGIBLE"),
        )
        self.assertIsNone(
            wr.get_submission_reconciliation(
                self.submission.id, current_user=self.admin_a, db=self.db,
            ),
        )

    def test_preview_cross_company_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            wr.preview_submission_reconciliation(
                self.submission.id,
                MarketplaceSubmissionReconciliationPreviewRequest(
                    operator_confirmed_seller_product_id="FAKE-RECONCILE-NO-EVIDENCE",
                ),
                current_user=self.admin_b, db=self.db,
            )

    def test_apply_by_super_admin_persists_and_is_visible_via_get(self):

        result = wr.apply_submission_reconciliation(
            self.submission.id,
            MarketplaceSubmissionReconciliationApplyRequest(
                operator_confirmed_seller_product_id="FAKE-RECONCILE-NO-EVIDENCE",
                reason="라우터 테스트 — WING 화면 직접 확인",
            ),
            current_user=self.super_admin_a, db=self.db,
        )
        self.assertEqual(result.outcome, "RECONCILED")

        fetched = wr.get_submission_reconciliation(
            self.submission.id, current_user=self.admin_a, db=self.db,
        )
        self.assertEqual(fetched.outcome, "RECONCILED")
        self.assertEqual(
            fetched.external_submission_ref, "FAKE-RECONCILE-NO-EVIDENCE",
        )

    def test_apply_blocks_on_fake_identifier_mismatch_marker(self):

        with self.assertRaises(BadRequestException):
            wr.apply_submission_reconciliation(
                self.submission.id,
                MarketplaceSubmissionReconciliationApplyRequest(
                    operator_confirmed_seller_product_id="FAKE-RECONCILE-MISMATCH",
                    reason="불일치 마커 강행 시도",
                ),
                current_user=self.super_admin_a, db=self.db,
            )


if __name__ == "__main__":
    unittest.main()
