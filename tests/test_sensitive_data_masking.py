"""
=========================================================
Homez OS

File : tests/test_sensitive_data_masking.py

2026-08-30 V7 후속 안정화 Phase 4 — 중앙 마스킹 유틸리티(app/core/
sensitive_data.py) 및 raw_response_snippet 방어선 검증.
=========================================================
"""

import unittest

from app.core.sensitive_data import (
    mask_address,
    mask_card_number,
    mask_phone,
    mask_secret,
    redact_dict,
    redact_free_text,
)
from app.domains.marketplace_listing.coupang_live_provider import (
    CoupangLiveProductProvider,
)


class _FakeResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


class SensitiveDataMaskingTestCase(unittest.TestCase):

    def test_mask_phone_keeps_only_last_four_digits(self):

        self.assertEqual(mask_phone("02-1234-5678"), "******5678")
        self.assertIsNone(mask_phone(None))
        self.assertEqual(mask_phone(""), "")

    def test_mask_address_keeps_only_prefix(self):

        masked = mask_address("서울특별시 강남구 테헤란로 123, 4층")
        self.assertTrue(masked.startswith("서울특별시"))
        self.assertNotIn("테헤란로 123", masked)

    def test_mask_secret_never_partially_exposes(self):

        self.assertEqual(mask_secret("sk_live_abcdef1234567890"), "***REDACTED***")
        self.assertNotIn("abcdef", mask_secret("sk_live_abcdef1234567890"))

    def test_mask_card_number_keeps_only_last_four_digits(self):
        """2026-09-10 Phase 11 — 카드정보 마스킹(이전에는 이 저장소에
        전혀 없던 함수, 카드 저장 코드 자체가 없었기 때문)."""

        self.assertEqual(
            mask_card_number("4111 1111 1111 1234"), "************1234",
        )
        self.assertIsNone(mask_card_number(None))
        self.assertEqual(mask_card_number(""), "")
        self.assertEqual(mask_card_number("123"), "***")

    def test_redact_free_text_masks_phone_and_jwt_shaped_strings(self):

        text = (
            "문의사항은 02-1234-5678로 연락주세요. "
            "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        masked = redact_free_text(text)

        self.assertNotIn("02-1234-5678", masked)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", masked)
        self.assertIn("***REDACTED_TOKEN***", masked)

    def test_redact_dict_drops_keys_not_on_allowlist(self):

        data = {
            "correlation_id": "abc-123", "error_code": "FAILED",
            "companyContactNumber": "02-1234-5678", "returnAddress": "서울시 ...",
        }
        redacted = redact_dict(data, frozenset({"correlation_id", "error_code"}))

        self.assertEqual(
            redacted, {"correlation_id": "abc-123", "error_code": "FAILED"},
        )
        self.assertNotIn("companyContactNumber", redacted)
        self.assertNotIn("returnAddress", redacted)

    def test_provider_snippet_masks_phone_shaped_text_in_raw_response(self):

        provider = CoupangLiveProductProvider.__new__(CoupangLiveProductProvider)
        response = _FakeResponse(
            '{"message":"반품지 연락처 02-1234-5678 확인 필요"}',
        )

        snippet = provider._snippet(response)

        self.assertNotIn("02-1234-5678", snippet)


if __name__ == "__main__":
    unittest.main()
