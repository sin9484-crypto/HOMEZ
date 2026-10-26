"""
=========================================================
Homez OS

File : tests/test_easy_call_center_confirm_ui.py

2026-10-27 — 쉬운 보기 고객센터 구역의 "상담이 끝난 이관 문의 확인"(쿠팡 쓰기 9번)을 고정한다. 서버 호출 순서·문구 짝은 소스에서 확인한다.

  - 목록에서 막는 이유(모드·비상 정지)가 있으면 확인 버튼을 보이지 않고 이유를 보인다.
  - 내용 확인 칸을 체크해야만 보내고, 보내기 전에 한 번 더 묻는다. 문의 글은 이스케이프한다. 결과는 화면에 남긴다.
=========================================================
"""

import json
import re
import unittest

from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

LIST = function_source("callCenterConfirmList")
SEND = function_source("callCenterConfirmSend")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:ccc\.[a-z_]+|cc\.[a-z_]+|inq\.[a-z_]+|ret\.[a-z_]+|stock\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


class SourceTests(unittest.TestCase):

    def test_the_block_lives_in_the_call_center_section(self):
        for marker in ('id="ez-ccc-anchor"', 'id="ez-ccc-load"'):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-cc-anchor"'), JS.index('id="ez-ccc-anchor"'))
        self.assertLess(JS.index('id="ez-ccc-anchor"'), JS.index('id="ez-ret-anchor"'))

    def test_gate_reasons_hide_the_button(self):
        self.assertIn('${(listed.gate_blockers || []).length ? "" : `<label', LIST)
        self.assertIn("listed.gate_blockers.map((b) => esc(b.message))", LIST)

    def test_text_is_escaped(self):
        self.assertIn('esc(r.question || "")', LIST)
        self.assertIn('esc(r.agent_text || T("easy.ccc.no_agent_text"))', LIST)    # 확인 칸 문구대로 쿠팡 상담 내용을 보여 준다
        self.assertIn("(listed.blockers || []).map((b) => esc(b.message))", LIST)
        self.assertIn('`<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`', SEND)

    def test_the_send_needs_the_box_then_asks_then_posts(self):
        self.assertLess(SEND.index("seen && seen.checked"), SEND.index("B.askConfirm("))
        self.assertLess(SEND.index("B.askConfirm("), SEND.index("/confirm`"))
        self.assertIn("JSON.stringify({ confirm_seen: !!(seen && seen.checked), retry_action_required: false })", SEND)
        self.assertIn("btn.disabled = true", SEND)
        self.assertIn('result.outcome === "NOT_PENDING"', SEND)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.ccc\.[a-z_]+)"', JS))
        for key in sorted(used | set(re.findall(r'"(easy\.(?:cc|inq|ret|stock)\.[a-z_]+)"', LIST + SEND))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        own = {k for k in ko if k.startswith("easy.ccc.")}
        self.assertEqual(own, {k for k in en if k.startswith("easy.ccc.")})
        self.assertEqual(own - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("24시간", ko["easy.ccc.sub"])
        self.assertIn("다시 보낼 수 없어요", ko["easy.ccc.ask"])


if __name__ == "__main__":
    unittest.main()
