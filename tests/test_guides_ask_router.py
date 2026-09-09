"""
=========================================================
Homez OS

File : tests/test_guides_ask_router.py

Gate AI-F2(2026-08-22 CTO 지시) 검증 — POST /guides/ask 라우터 함수.
DB를 쓰지 않는다(가이드는 정적 파일이다). httpx 미설치로 라우터
함수를 직접 호출한다.
=========================================================
"""

import unittest

from app.domains.guides.router import ask_guidance


class _FakeUser:

    def __init__(self, user_id=1, company_id=1):
        self.id = user_id
        self.company_id = company_id


class GuidesAskRouterTestCase(unittest.TestCase):

    def test_ask_returns_matched_guides_and_envelope(self):

        result = ask_guidance(
            query="상품 등록 방법", locale="ko-KR", _=_FakeUser(),
        )

        self.assertTrue(
            any(g.id == "product-registration" for g in result.suggested_guides),
        )
        self.assertEqual(result.ai_result["decision"], "GUIDE_MATCHED")

    def test_ask_no_match_returns_empty_list(self):

        result = ask_guidance(
            query="완전히 무관한 질문 xyzabc123", locale="ko-KR", _=_FakeUser(),
        )

        self.assertEqual(result.suggested_guides, [])
        self.assertEqual(result.ai_result["decision"], "NO_MATCH")


if __name__ == "__main__":
    unittest.main()
