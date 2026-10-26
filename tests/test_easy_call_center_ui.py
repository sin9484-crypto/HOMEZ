"""
=========================================================
Homez OS

File : tests/test_easy_call_center_ui.py

2026-10-26 — 쉬운 보기 승인 화면의 "쿠팡 고객센터 문의 답변"(쿠팡 쓰기 6번)을 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고,
서버 호출 순서·문구 짝은 소스에서 확인한다. 네트워크·DB 없음.

  - 답변 2~1,000자(쿠팡 공식), 초안 가림 표시가 남으면 보내지 않는다(상품 문의 답변과 같은 규칙).
  - 상담사 이관글이 정확히 하나일 때만 답변 칸을 보이고, 그 이관글 번호를 그대로 보낸다. 여럿이면 윙에서 답하라고 안내한다.
  - 공개 확인 칸을 체크해야만 보내고 보내기 전에 한 번 더 묻는다. 문의 글·상담사 글은 이스케이프한다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

NODE = shutil.which("node")
LIST = function_source("callCenterList")
SEND = function_source("callCenterSend")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:cc\.[a-z_]+|inq\.[a-z_]+|stock\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); }); return s; };
const INQ_MAX = 1000;
const CC_MIN = 2;
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join(function_source(n) for n in ("inquiryReplyProblem", "callCenterReplyProblem"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_input_rules_match_the_server(self):
        self.assertIsNone(self.run_js('callCenterReplyProblem("네.")'))
        self.assertIsNone(self.run_js('callCenterReplyProblem("가".repeat(1000))'))
        for bad in ('""', '"가"', '" 가 "', '"가".repeat(1001)', "null"):
            self.assertEqual(self.run_js(f"callCenterReplyProblem({bad})"), "easy.cc.err_content", bad)
        for left in ('"[전화번호]로 연락드릴게요"', '"답\\n※ 초안입니다. 사람이"'):
            self.assertEqual(self.run_js(f"callCenterReplyProblem({left})"), "easy.inq.err_draft", left)


class SourceTests(unittest.TestCase):

    def test_the_section_exists_below_product_inquiries(self):
        for marker in ('id="ez-cc-anchor"', 'id="ez-cc-load"', "const CC_MIN = 2;"):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-inq-anchor"'), JS.index('id="ez-cc-anchor"'))
        self.assertLess(JS.index('id="ez-cc-anchor"'), JS.index('id="ez-ret-anchor"'))

    def test_the_list_escapes_text_and_only_offers_a_reply_for_exactly_one_transfer(self):
        self.assertIn('api("/purchase-tasks/coupang-call-center-inquiries/unanswered")', LIST)
        self.assertIn('esc(r.question || "")', LIST)
        self.assertIn('esc(r.pending_text || "")', LIST)
        self.assertIn("const answerable = r.pending_count === 1 && r.pending_answer_id;", LIST)
        self.assertIn('data-ez-cc-parent="${esc(r.pending_answer_id)}"', LIST)
        self.assertIn('T("easy.cc.multiple")', LIST)
        self.assertIn("inquiryTime(r.inquiry_at)", LIST)

    def test_the_send_checks_text_then_the_box_then_asks_then_posts_once_with_the_parent(self):
        self.assertLess(SEND.index("callCenterReplyProblem(content)"), SEND.index("publicEl && publicEl.checked"))
        self.assertLess(SEND.index("publicEl && publicEl.checked"), SEND.index("B.askConfirm("))
        self.assertLess(SEND.index("B.askConfirm("), SEND.index("/reply/send"))
        self.assertIn("JSON.stringify({ content, expected_parent_answer_id: parent, confirm_public: !!(publicEl && publicEl.checked), retry_action_required: false })", SEND)
        self.assertIn("btn.disabled = true", SEND)
        self.assertIn("/reply/reconcile", SEND)
        self.assertIn("inquiryResultHtml(result)", SEND)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.cc\.[a-z_]+)"', JS))
        for key in sorted(used | set(re.findall(r'"(easy\.(?:inq|stock)\.[a-z_]+)"', LIST + SEND))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        own = {k for k in ko if k.startswith("easy.cc.")}
        self.assertEqual(own, {k for k in en if k.startswith("easy.cc.")})
        self.assertEqual(own - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("24시간", ko["easy.cc.sub"])
        self.assertIn("전화번호", ko["easy.cc.where"])
        self.assertIn("같은 허용", ko["easy.cc.where"])
        self.assertIn("고칠 수 있는지 알 수 없다", ko["easy.cc.public_confirm"])
        self.assertIn("다시 보낼 수 없어요", ko["easy.cc.confirm"])


if __name__ == "__main__":
    unittest.main()
