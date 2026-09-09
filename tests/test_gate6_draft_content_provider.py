"""
=========================================================
Homez OS

File : tests/test_gate6_draft_content_provider.py

V7 Gate 6 — 상품 설명 AI-Provider 인터페이스(Fake만 실제 동작) 검증.
실제 네트워크 호출이 없다는 것과, 동일 입력 → 동일 출력(결정적)임을
확인한다. content_source가 항상 "AI_FAKE_DRAFT"로 명시돼 실제 AI로
위장하지 않는지도 확인한다.
=========================================================
"""

import unittest

from app.domains.marketplace_listing.draft_content_provider import (
    DRAFT_CONTENT_PROVIDERS_BY_CODE,
    DisabledDraftContentProvider,
    DraftContentProviderError,
    DraftContentRequest,
    FakeDraftContentProvider,
    get_draft_content_provider,
)


class DraftContentProviderTestCase(unittest.TestCase):

    def test_fake_provider_is_deterministic(self):

        request = DraftContentRequest(
            candidate_id=1, product_name="테스트 상품",
            category_hint="생활용품", brand_hint="HOMEZ",
        )

        first = FakeDraftContentProvider().generate(request)
        second = FakeDraftContentProvider().generate(request)

        self.assertEqual(first.description, second.description)
        self.assertEqual(first.keywords, second.keywords)

    def test_fake_provider_marks_content_source_honestly(self):

        result = FakeDraftContentProvider().generate(DraftContentRequest(
            candidate_id=1, product_name="테스트 상품",
            category_hint=None, brand_hint=None,
        ))

        self.assertEqual(result.content_source, "AI_FAKE_DRAFT")
        self.assertEqual(result.provider_code, "FAKE")
        self.assertIn("Fake Provider", result.description)

    def test_different_candidates_produce_different_description(self):

        a = FakeDraftContentProvider().generate(DraftContentRequest(
            candidate_id=1, product_name="상품A", category_hint=None,
            brand_hint=None,
        ))
        b = FakeDraftContentProvider().generate(DraftContentRequest(
            candidate_id=2, product_name="상품B", category_hint=None,
            brand_hint=None,
        ))

        self.assertNotEqual(a.description, b.description)

    def test_keywords_include_only_present_hints(self):

        result = FakeDraftContentProvider().generate(DraftContentRequest(
            candidate_id=1, product_name="상품", category_hint="카테고리",
            brand_hint=None,
        ))

        self.assertEqual(result.keywords, ("카테고리",))

    def test_disabled_provider_fails_closed(self):

        with self.assertRaises(DraftContentProviderError):
            DisabledDraftContentProvider().generate(DraftContentRequest(
                candidate_id=1, product_name="상품", category_hint=None,
                brand_hint=None,
            ))

    def test_get_provider_unknown_code_raises(self):

        with self.assertRaises(DraftContentProviderError):
            get_draft_content_provider("REAL_LLM_NOT_CONNECTED")

    def test_registry_only_has_fake_and_disabled(self):
        """실제 유료 AI Provider는 이번 Phase에 등록돼 있지 않다."""

        self.assertEqual(
            set(DRAFT_CONTENT_PROVIDERS_BY_CODE.keys()), {"FAKE", "DISABLED"},
        )


if __name__ == "__main__":
    unittest.main()
