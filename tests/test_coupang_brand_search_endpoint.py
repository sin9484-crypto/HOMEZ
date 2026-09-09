"""
=========================================================
Homez OS

File : tests/test_coupang_brand_search_endpoint.py

2026-08-30 V7 후속 안정화 Phase 3 — GET /listing-wizards/{id}/coupang/
brand-search 라우터 계약 검증. Fake Provider만 사용한다(실제 쿠팡
브랜드 검색 API를 호출하는 코드 자체가 없음 — connected:false로
명시).
=========================================================
"""

import unittest

from app.domains.marketplace_listing.listing_wizard_router import (
    search_coupang_brand,
)
from app.domains.marketplace_listing.listing_wizard_schema import (
    WizardSourceUpdateRequest,
)
from tests.test_listing_wizard_service import ListingWizardServiceTestCase


class _StubUser:
    """라우터 함수를 FastAPI Depends 체인 없이 직접 호출할 때 쓰는
    최소 스텁 — 이 엔드포인트는 current_user.company_id만 읽는다."""

    def __init__(self, company_id):
        self.id = 1
        self.company_id = company_id


class CoupangBrandSearchEndpointTestCase(ListingWizardServiceTestCase):

    def _wizard(self):
        candidate, _channel, account, media = self._full_setup()
        wizard = self._create_wizard()
        self.service.update_source(
            wizard.id, self.company_id,
            WizardSourceUpdateRequest(
                expected_version=wizard.version, product_candidate_id=candidate.id,
            ),
        )
        return wizard

    def _admin_user(self):
        return _StubUser(self.company_id)

    def test_known_seed_query_returns_demo_flagged_results(self):
        wizard = self._wizard()

        response = search_coupang_brand(
            wizard.id, query="홈즈",
            current_user=self._admin_user(), db=self.db,
        )

        self.assertFalse(response["connected"])
        self.assertEqual(len(response["results"]), 1)
        result = response["results"][0]
        self.assertEqual(result["official_brand_name"], "HOMEZ")
        self.assertEqual(result["enrollment_status"], "ENROLLED")
        self.assertTrue(result["lookup_fingerprint"])

    def test_unknown_query_returns_empty_results_not_error(self):
        wizard = self._wizard()

        response = search_coupang_brand(
            wizard.id, query="존재하지않는브랜드검색어",
            current_user=self._admin_user(), db=self.db,
        )

        self.assertFalse(response["connected"])
        self.assertEqual(response["results"], [])

    def test_not_enrolled_seed_is_flagged(self):
        wizard = self._wizard()

        response = search_coupang_brand(
            wizard.id, query="에브리홈즈",
            current_user=self._admin_user(), db=self.db,
        )

        self.assertEqual(response["results"][0]["enrollment_status"], "NOT_ENROLLED")

    def test_fingerprint_changes_with_query(self):
        wizard = self._wizard()

        a = search_coupang_brand(
            wizard.id, query="홈즈", current_user=self._admin_user(), db=self.db,
        )
        b = search_coupang_brand(
            wizard.id, query="homez", current_user=self._admin_user(), db=self.db,
        )

        self.assertNotEqual(
            a["results"][0]["lookup_fingerprint"], b["results"][0]["lookup_fingerprint"],
        )


if __name__ == "__main__":
    unittest.main()
