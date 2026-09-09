"""
=========================================================
Homez OS

File : tests/test_interactive_guidance.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — InteractiveGuidanceService.
정적 키워드 매칭이 실제 존재하는 가이드만 추천하는지, 매칭 실패 시
정직하게 EVIDENCE_REQUIRED로 응답하는지, 응답 어디에도 비밀번호/
Credential류 필드가 없는지, capability 비활성 시 차단되는지 검증한다.
DB를 쓰지 않는다(가이드는 정적 파일이다).
=========================================================
"""

import unittest

from app.domains.ai_governance.service import InactiveCapabilityError
from app.domains.guides.interactive_guidance_service import (
    InteractiveGuidanceService,
)
from app.domains.guides.service import list_guides

from tests.ai_governance_test_helpers import deactivated_capability


class InteractiveGuidanceTestCase(unittest.TestCase):

    def setUp(self):

        self.service = InteractiveGuidanceService()

    def test_matches_product_registration_query(self):

        suggested, envelope = self.service.ask("상품 등록은 어떻게 하나요?")

        self.assertTrue(
            any(g.id == "product-registration" for g in suggested),
        )
        self.assertEqual(envelope.decision, "GUIDE_MATCHED")
        self.assertEqual(envelope.result_type, "CALCULATED_RESULT")

    def test_matches_troubleshooting_query_in_english(self):

        suggested, _envelope = self.service.ask("I got an error on login")

        self.assertTrue(
            any(g.id == "troubleshooting" for g in suggested),
        )

    def test_no_match_returns_evidence_required_honestly(self):

        suggested, envelope = self.service.ask("완전히 무관한 질문 xyzabc123")

        self.assertEqual(suggested, [])
        self.assertEqual(envelope.result_type, "EVIDENCE_REQUIRED")
        self.assertEqual(envelope.decision, "NO_MATCH")
        self.assertTrue(len(envelope.missing_evidence) > 0)

    def test_empty_query_returns_no_match(self):

        suggested, envelope = self.service.ask("")

        self.assertEqual(suggested, [])
        self.assertEqual(envelope.decision, "NO_MATCH")

    def test_suggested_guides_are_real_registry_entries(self):
        """추천된 가이드는 항상 list_guides()의 실제 결과 부분집합이다
        — 존재하지 않는 화면/가이드를 지어내지 않는다."""

        suggested, _envelope = self.service.ask("모바일 앱 사용법")

        all_ids = {g.id for g in list_guides()}
        for g in suggested:
            self.assertIn(g.id, all_ids)

    def test_response_never_contains_credential_fields(self):
        """응답 스키마 어디에도 비밀번호/Credential류 필드가 없다."""

        suggested, envelope = self.service.ask("로그인이 안 돼요")

        for g in suggested:
            dumped = g.model_dump()
            self.assertNotIn("password", str(dumped).lower())
            self.assertNotIn("credential", str(dumped).lower())

        envelope_dumped = str(envelope.model_dump()).lower()
        self.assertNotIn("password", envelope_dumped)
        self.assertNotIn("credential", envelope_dumped)

    def test_execution_allowed_is_always_false(self):
        """안내는 실행이 아니다 — execution_allowed는 항상 False."""

        _suggested, envelope = self.service.ask("상품 등록")

        self.assertFalse(envelope.execution_allowed)

    def test_blocks_when_user_guidance_capability_deactivated(self):

        with deactivated_capability("USER_GUIDANCE"):
            with self.assertRaises(InactiveCapabilityError):
                self.service.ask("상품 등록")


if __name__ == "__main__":
    unittest.main()
