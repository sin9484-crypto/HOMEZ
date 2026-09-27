"""
=========================================================
Homez OS

File : tests/test_coupang_brand_search_endpoint.py

2026-08-30 V7 후속 안정화 Phase 3 — GET /listing-wizards/{id}/coupang/
brand-search 라우터 계약 검증.

2026-09-27 후속 — 실제 쿠팡 브랜드 검색 API가 공식 문서화돼 있음을
확인해 CoupangLiveBrandProvider로 실제 연동했다(coupang_brand_
provider.py 참고). 이 파일은 다른 Provider 테스트와 동일한 관례대로
HOMEZ_TEST_FAKE_COUPANG_PROVIDER=1을 명시적으로 설정해 Fake 경로만
검증한다(연결된 판매계정 없이도 라우터 계약을 확인하기 위함) — 실제
Provider의 파싱 로직은 test_coupang_live_brand_provider.py가 별도로
검증한다.
=========================================================
"""

import os
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
    """이 클래스는 ListingWizardServiceTestCase를 상속해 공용 fixture만
    재사용한다 — unittest가 부모의 test_* 메서드까지 이 서브클래스
    아래에서 함께 discover하므로, HOMEZ_TEST_FAKE_COUPANG_PROVIDER를
    setUp()에 두면 이 파일과 무관한 상속된 테스트에까지 영향을 준다
    (실제로 한 건이 멈추는 것을 확인함). 그래서 이 env var는 아래처럼
    브랜드 검색 호출 각각을 감싸는 범위로만 좁힌다."""

    def _search_with_fake_provider(self, wizard_id, query, current_user):
        os.environ["HOMEZ_TEST_FAKE_COUPANG_PROVIDER"] = "1"
        try:
            return search_coupang_brand(
                wizard_id, query=query, current_user=current_user, db=self.db,
            )
        finally:
            os.environ.pop("HOMEZ_TEST_FAKE_COUPANG_PROVIDER", None)

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

        response = self._search_with_fake_provider(
            wizard.id, "홈즈", self._admin_user(),
        )

        self.assertFalse(response["connected"])
        self.assertEqual(len(response["results"]), 1)
        result = response["results"][0]
        self.assertEqual(result["official_brand_name"], "HOMEZ")
        self.assertEqual(result["enrollment_status"], "ENROLLED")
        self.assertTrue(result["lookup_fingerprint"])

    def test_unknown_query_returns_empty_results_not_error(self):
        wizard = self._wizard()

        response = self._search_with_fake_provider(
            wizard.id, "존재하지않는브랜드검색어", self._admin_user(),
        )

        self.assertFalse(response["connected"])
        self.assertEqual(response["results"], [])

    def test_not_enrolled_seed_is_flagged(self):
        wizard = self._wizard()

        response = self._search_with_fake_provider(
            wizard.id, "에브리홈즈", self._admin_user(),
        )

        self.assertEqual(response["results"][0]["enrollment_status"], "NOT_ENROLLED")

    def test_fingerprint_changes_with_query(self):
        wizard = self._wizard()

        a = self._search_with_fake_provider(wizard.id, "홈즈", self._admin_user())
        b = self._search_with_fake_provider(wizard.id, "homez", self._admin_user())

        self.assertNotEqual(
            a["results"][0]["lookup_fingerprint"], b["results"][0]["lookup_fingerprint"],
        )


if __name__ == "__main__":
    unittest.main()
