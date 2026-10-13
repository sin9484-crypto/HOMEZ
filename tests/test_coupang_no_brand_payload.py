"""
=========================================================
Homez OS

File : tests/test_coupang_no_brand_payload.py

무브랜드(NO_BRAND) 상품의 쿠팡 전송 값을 고정한다.

2026-10-13 러닝 무릎보호대 등록에서 쿠팡이 "브랜드 ID가 필요합니다. 브랜드 ID 없이 브랜드명을
사용하려면 WING의 브랜드 관리 페이지에서 브랜드를 등록해 주세요"로 거절했다. 무브랜드 확인 문구를
브랜드명으로 보낸 것이 원인이어서, NO_BRAND는 브랜드를 비워 보내고 공식 브랜드(OFFICIAL_BRAND)는
브랜드명과 brandId를 그대로 보내도록 바꿨다. 외부 호출 없이 순수 함수만 검사한다.
=========================================================
"""

import unittest

from app.domains.marketplace_listing.coupang_live_payload import build_coupang_live_payload
from tests.test_coupang_submission_contract import (
    DRAFT,
    NOTICE_ATTRIBUTES,
    _valid_required_fields,
)


class NoBrandPayloadTests(unittest.TestCase):

    def _build(self, **overrides):
        required = _valid_required_fields(**overrides)
        payload, blockers = build_coupang_live_payload(
            draft=DRAFT, required_fields=required,
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertEqual(blockers, [])
        return payload

    def test_no_brand_confirmation_text_is_not_sent_as_a_brand_name(self):
        payload = self._build(brandState="NO_BRAND", brand="브랜드 없음")
        self.assertEqual(payload["brand"], "")
        self.assertIsNone(payload.get("brandId"))

    def test_no_brand_still_requires_the_users_confirmation_text(self):
        from app.domains.marketplace_listing.coupang_submission_contract import (
            validate_coupang_submission_contract,
        )
        result = validate_coupang_submission_contract(
            draft=DRAFT,
            required_fields=_valid_required_fields(brandState="NO_BRAND", brand=""),
            channel_policy_attributes=NOTICE_ATTRIBUTES,
        )
        self.assertFalse(result.ready)
        self.assertIn("BRAND_NO_BRAND_VALUE_MISSING", result.blocking_codes)

    def test_official_brand_keeps_name_and_id(self):
        from app.domains.marketplace_listing.coupang_brand_provider import (
            BrandSearchResult,
            brand_lookup_fingerprint,
        )
        chosen = BrandSearchResult(
            brand_id="KR-1", official_brand_name="공식 브랜드", enrollment_status="ENROLLED",
        )
        payload = self._build(
            brandState="OFFICIAL_BRAND", brandId="KR-1", officialBrandName="공식 브랜드",
            brandEnrollmentStatus="ENROLLED",
            brandLookupFingerprint=brand_lookup_fingerprint("공식", chosen),
        )
        self.assertEqual(payload["brand"], "공식 브랜드")
        self.assertEqual(payload["brandId"], "KR-1")


if __name__ == "__main__":
    unittest.main()
