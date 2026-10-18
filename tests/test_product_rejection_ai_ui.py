"""
=========================================================
Homez OS

File : tests/test_product_rejection_ai_ui.py

2026-10-18 — 반려 사유 AI 설명(상품 관리 AI P1b)이 쉬운 보기에 붙는 방식을 고정한다. 외부 호출 0.

  - 쉬운 보기 payload: 같은 사유로 이미 받아 둔 AI 설명만 `ai_advice`로 붙는다(사유가 바뀌었거나 없으면 None).
  - 화면: 사유가 있고 저장된 설명이 없을 때만 "AI 설명 받기"가 주 버튼이다. 서버 요청은 확인창을 거친 뒤에만 나가고, 글은 전부 이스케이프한다.
    설명에는 항상 "AI가 쓴 참고용·쿠팡 공식 안내 아님" 문구가 붙는다. 쿠팡 쓰기·재제출 코드는 없다.
  - 문구: 쓰는 키가 한국어·영어에 모두 있고 자리표시가 같다.
=========================================================
"""

import re
import unittest
from pathlib import Path
from unittest import mock

from app.domains.easy_view.service import EasyViewService

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/domains/easy_view/service.py").read_text(encoding="utf-8")


def block(lang):
    return re.search(r'"%s": \{(.*?)\n  \}' % lang, TEXTS, re.S).group(1)


DENIED = {"status": "DENIED", "raw": "승인반려", "checked_at": "T1", "reason_for": "T1", "reason_state": "OK", "reason": "카테고리가 적합하지 않습니다."}
CACHED = {"for": "T1", "at": "2026-10-18T05:00:00", "ai": {"summary": "요약", "steps": ["단계"]}}


def product_status(latest, cached):
    service = object.__new__(EasyViewService)
    service.db = None
    submitted = [{"seller_product_id": "111", "product_name": "상품", "wizard_id": 1}]
    with mock.patch("app.domains.easy_view.service.ProductStatusMonitorService.latest_by_product", return_value=latest), \
            mock.patch("app.domains.easy_view.service.ProductRejectionAiService.latest_cached", return_value=cached):
        return service._product_status(submitted)


class PayloadTests(unittest.TestCase):

    def test_a_stored_explanation_for_the_current_reason_is_attached(self):
        self.assertEqual(product_status({"111": DENIED}, {"111": CACHED})[0]["ai_advice"], CACHED["ai"])

    def test_a_stale_missing_or_unrelated_explanation_is_not(self):
        self.assertIsNone(product_status({"111": DENIED}, {"111": {**CACHED, "for": "OLD"}})[0]["ai_advice"])  # 사유를 읽은 시각이 다름
        self.assertIsNone(product_status({"111": DENIED}, {})[0]["ai_advice"])
        self.assertIsNone(product_status({"111": DENIED}, {"222": CACHED})[0]["ai_advice"])
        self.assertIsNone(product_status({"111": {**DENIED, "reason_for": "OLD"}}, {"111": CACHED})[0]["ai_advice"])  # 지난 사유
        self.assertIsNone(product_status({"111": {**DENIED, "status": "APPROVED"}}, {"111": CACHED})[0]["ai_advice"])

    def test_the_easy_view_service_stays_read_only(self):
        self.assertNotIn("db.execute(", SERVICE)
        self.assertNotIn(".commit(", SERVICE)


class DialogTests(unittest.TestCase):

    def _dialog(self):
        return JS.split("async function productReasonDialog(")[1].split("// 공급처(온채널) 반품 요청 내용:")[0]

    def _ask(self):
        return JS.split("async function productAdviceAsk(")[1].split("// 0~1 점수를 막대와 숫자로")[0]

    def _html(self):
        return JS.split("function productAdviceHtml(")[1].split("async function productAdviceAsk(")[0]

    def test_the_ask_button_only_shows_with_a_reason_and_no_stored_explanation(self):
        body = self._dialog()
        self.assertIn("const canAsk = !!p.reason && !p.ai_advice;", body)
        self.assertIn("if (canAsk && asked) await productAdviceAsk(p);", body)

    def test_the_request_goes_out_only_after_a_confirmation_dialog_and_to_one_endpoint(self):
        body = self._ask()
        self.assertLess(body.index("const ok = await ezDialog("), body.index('api("/orders/product-rejection-advice"'))
        self.assertIn("if (!ok) return;", body)
        self.assertEqual(re.findall(r'api\("([^"]+)"', body), ["/orders/product-rejection-advice"])
        self.assertIn("seller_product_id: p.seller_product_id", body)
        self.assertNotIn("reason", body.split('api("/orders/product-rejection-advice"')[1].split("}")[0])  # 사유 글은 서버가 찾는다

    def test_the_legacy_dialog_part_still_makes_no_call(self):
        body = self._dialog()
        for forbidden in ("api(", "apiFetch", "fetch(", "submit", "resubmit", "method:"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_ai_text_is_escaped_and_always_carries_the_reference_only_note(self):
        html = self._html()
        self.assertIn("esc(ai.summary", html)
        self.assertIn("esc(t)", html)
        self.assertIn('T("easy.advice.ai_note")', html)
        ask = self._ask()
        self.assertIn("esc(w)", ask)  # 검증에서 버린 이유도 이스케이프

    def test_no_coupang_write_or_resubmit_code_was_added(self):
        for part in (self._ask(), self._html()):
            for forbidden in ("coupang", "resubmit", "send-live", "/listing-wizards"):
                self.assertNotIn(forbidden, part.lower(), forbidden)


class TextTests(unittest.TestCase):

    KEYS = ("ask_btn", "close", "ask_title", "ask_body", "ai_title", "ai_steps", "ai_note", "ai_none", "ai_usage", "ai_failed")

    def test_every_text_exists_in_both_languages_with_the_same_placeholders(self):
        for key in self.KEYS:
            ko = re.search(rf'"easy\.advice\.{key}": "([^"]*)"', block("ko-KR"))
            en = re.search(rf'"easy\.advice\.{key}": "([^"]*)"', block("en-US"))
            self.assertTrue(ko and en, key)
            self.assertEqual(set(re.findall(r"\{(\w+)\}", ko.group(1))), set(re.findall(r"\{(\w+)\}", en.group(1))), key)
            names = re.findall(r"\{(\w+)\}", ko.group(1))
            self.assertEqual(len(names), len(set(names)), key)  # 같은 자리표시 두 번 금지(치환은 첫 번째만 바꾼다)

    def test_every_text_the_screen_uses_exists(self):
        used = set(re.findall(r'T\("easy\.advice\.([a-z_]+)"', JS))
        for key in used:
            self.assertIn(f'"easy.advice.{key}":', block("ko-KR"), key)
            self.assertIn(f'"easy.advice.{key}":', block("en-US"), key)

    def test_the_confirmation_names_what_is_sent_and_what_is_not(self):
        ko = re.search(r'"easy\.advice\.ask_body": "([^"]*)"', block("ko-KR")).group(1)
        for needle in ("상품명", "반려 사유", "사용료", "가격", "한 번만"):
            self.assertIn(needle, ko, needle)


if __name__ == "__main__":
    unittest.main()
