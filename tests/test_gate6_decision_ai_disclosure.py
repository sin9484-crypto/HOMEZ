"""
=========================================================
Homez OS

File : tests/test_gate6_decision_ai_disclosure.py

V7 Gate 6 — "Decision AI가 규칙 기반 엔진인지 실제 AI Provider인지
UI/문서에 정직하게 공개하라"는 요구사항 검증.

두 가지를 함께 확인한다(둘 중 하나만 확인하면 "UI 문구는 정직하지만
실제 코드는 다르게 동작"하는 괴리를 놓칠 수 있다):
  1) 실제 DecisionService.evaluate_axes()가 정말 규칙 기반
     결정적(deterministic) 엔진이다(evaluator_kind 상수 값 확인).
  2) console.html/i18n에 그 사실을 정직하게 공개하는 문구가 실제로
     존재한다(정적 텍스트 검사 — 렌더링 자체는 브라우저 몫이라 이
     범위 밖).
=========================================================
"""

import re
import unittest
from pathlib import Path

from app.domains.decision.service import EVALUATOR_KIND

_REPO_ROOT = Path(__file__).resolve().parents[1]


class DecisionAiDisclosureTestCase(unittest.TestCase):

    def test_evaluator_kind_is_actually_rule_based_not_llm(self):
        """코드 사실 확인 — UI가 공개할 내용이 실제로 참인지."""

        self.assertEqual(EVALUATOR_KIND, "deterministic")
        self.assertNotIn("llm", EVALUATOR_KIND.lower())
        self.assertNotIn("ai", EVALUATOR_KIND.lower())

    def test_evaluator_protocol_for_real_ai_is_defined_but_unwired(self):
        """
        요구사항 2(AI-Provider 인터페이스와 Fake 분리) — 향후 실제
        LLM evaluator를 붙일 인터페이스가 이미 정의돼 있고, 지금은
        DecisionService가 이를 사용하지 않는다는 사실을 코드로 재확인.
        """

        from app.domains.decision import evaluator_protocol
        import app.domains.decision.service as decision_service_module

        self.assertTrue(
            hasattr(evaluator_protocol, "DecisionEvaluatorProtocol"),
        )
        source = Path(decision_service_module.__file__).read_text(
            encoding="utf-8",
        )
        self.assertNotIn("evaluator_protocol", source)

    def test_console_html_declares_decision_disclosure_banner(self):

        html = (_REPO_ROOT / "app" / "web" / "console.html").read_text(
            encoding="utf-8",
        )

        self.assertIn('id="decision-ai-disclosure-banner"', html)
        self.assertIn('data-i18n="decision.ai_disclosure"', html)

    def test_ko_and_en_i18n_disclosure_text_mentions_rule_based_engine(self):

        ko = (_REPO_ROOT / "app" / "web" / "i18n" / "ko-KR.js").read_text(
            encoding="utf-8",
        )
        en = (_REPO_ROOT / "app" / "web" / "i18n" / "en-US.js").read_text(
            encoding="utf-8",
        )

        pattern = r'"decision\.ai_disclosure":\s*"((?:[^"\\]|\\.)*)"'
        ko_match = re.search(pattern, ko)
        en_match = re.search(pattern, en)

        self.assertIsNotNone(ko_match)
        self.assertIsNotNone(en_match)

        self.assertIn("규칙", ko_match.group(1))
        self.assertIn("deterministic", ko_match.group(1))
        self.assertIn("rule-based", en_match.group(1))
        self.assertIn("deterministic", en_match.group(1))


if __name__ == "__main__":
    unittest.main()
