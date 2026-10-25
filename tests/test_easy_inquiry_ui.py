"""
=========================================================
Homez OS

File : tests/test_easy_inquiry_ui.py

2026-10-25 — 쉬운 보기 승인 화면의 "쿠팡 상품 문의 답변"(쿠팡 쓰기 4번)을 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고, 서버 호출 순서·문구 짝은 소스에서 확인한다.
네트워크·DB 없음.

  - 입력 검사: 답변 1~1,000자, 초안 가림 표시([전화번호] 등)·초안 안내 문구가 남으면 보내지 않는다(서버와 같은 규칙).
  - 고객에게 바로 보인다는 확인 칸을 체크해야만 보내고, 보내기 전에 한 번 더 묻는다. 문의 글·서버 글은 이스케이프한다.
  - 결과 문구: 다시 읽어 확인됨·보이지 않음·확인 못 함·이미 답이 있음·결과불명(다시 보내지 말 것)·거부.
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
LIST = function_source("inquiryList")
SEND = function_source("inquirySend")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:inq\.[a-z_]+|purpose\.inq|stock\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const INQ_MAX = 1000;
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join(function_source(n) for n in ("inquiryReplyProblem", "inquiryResultHtml", "inquiryTime"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_input_rules_match_the_server(self):
        self.assertIsNone(self.run_js('inquiryReplyProblem("  안녕하세요\\r\\n내일 출고됩니다 ")'))
        self.assertIsNone(self.run_js('inquiryReplyProblem("가".repeat(1000))'))
        for bad in ('""', '"   "', '"가".repeat(1001)', "null"):
            self.assertEqual(self.run_js(f"inquiryReplyProblem({bad})"), "easy.inq.err_content", bad)
        for left in ('"[전화번호]로 연락 주세요"', '"[이름]님"', '"[주소창] 확인"', '"답\\n※ 초안입니다. 사람이"'):
            self.assertEqual(self.run_js(f"inquiryReplyProblem({left})"), "easy.inq.err_draft", left)

    def test_coupang_time_is_shown_as_received(self):
        self.assertEqual(self.run_js('inquiryTime("2026-10-25T09:00:00")'), "2026-10-25 09:00")
        self.assertEqual(self.run_js('[inquiryTime(null), inquiryTime(""), inquiryTime("어제")]'), ["—", "—", "어제"])

    def test_result_wording(self):
        def html(result):
            return self.run_js(f"inquiryResultHtml({json.dumps(result)})")

        self.assertIn("다시 읽어 확인했어요", html({"outcome": "SUCCEEDED", "verified": True}))
        self.assertIn("아직 보이지 않아요", html({"outcome": "SUCCEEDED", "verified": False}))
        self.assertIn("확인하지는 못했어요", html({"outcome": "SUCCEEDED", "verified": None}))
        self.assertIn("이미 답변이 달려 있어", html({"outcome": "ALREADY_ANSWERED"}))
        self.assertIn("다시 보내지 말고", html({"outcome": "UNKNOWN"}))
        rejected = html({"outcome": "ACTION_REQUIRED", "detail": "<b>replyBy</b>"})
        self.assertIn("&lt;b&gt;replyBy", rejected)
        self.assertNotIn("<b>", rejected)
        self.assertIn("&lt;i&gt;X&lt;/i&gt;: y", html({"outcome": "<i>X</i>", "detail": "y"}))


class SourceTests(unittest.TestCase):

    def test_the_section_and_the_allowance_purpose_exist(self):
        for marker in ('id="ez-inq-anchor"', 'id="ez-inq-load"', '["INQUIRY_REPLY", "easy.purpose.inq"]', "const INQ_MAX = 1000;"):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-pname-anchor"'), JS.index('id="ez-inq-anchor"'))

    def test_the_list_escapes_customer_text(self):
        self.assertIn("inquiryTime(r.inquiry_at)", LIST)
        self.assertIn('esc(r.question || "")', LIST)
        self.assertIn("(listed.blockers || []).map((b) => esc(b.message))", LIST)
        self.assertIn('api("/purchase-tasks/coupang-inquiries/unanswered")', LIST)

    def test_the_send_checks_text_then_the_box_then_asks_then_posts_once(self):
        self.assertLess(SEND.index("inquiryReplyProblem(content)"), SEND.index("publicEl && publicEl.checked"))
        self.assertLess(SEND.index("publicEl && publicEl.checked"), SEND.index("B.askConfirm("))
        self.assertLess(SEND.index("B.askConfirm("), SEND.index("/reply/send"))
        self.assertIn("JSON.stringify({ content, confirm_public: !!(publicEl && publicEl.checked), retry_action_required: false })", SEND)
        self.assertIn("btn.disabled = true", SEND)
        self.assertIn("/reply/reconcile", SEND)
        self.assertIn('if (result.outcome !== "ACTION_REQUIRED") textEl.readOnly = true;', SEND)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.inq\.[a-z_]+)"', JS)) | {"easy.purpose.inq"}
        for key in sorted(used | set(re.findall(r'"(easy\.stock\.[a-z_]+)"', SEND + function_source("inquiryResultHtml")))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        own = {k for k in ko if k.startswith("easy.inq.") or k == "easy.purpose.inq"}
        self.assertEqual(own, {k for k in en if k.startswith("easy.inq.") or k == "easy.purpose.inq"})
        self.assertEqual(own - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("고객에게 바로 보이", ko["easy.inq.public_confirm"])
        self.assertIn("고칠 수 있는지 알 수 없다", ko["easy.inq.public_confirm"])
        self.assertIn("다시 보낼 수 없어요", ko["easy.inq.confirm"])
        self.assertIn("허용이 필요", ko["easy.inq.where"])
        self.assertEqual(ko["easy.purpose.inq"], "쿠팡 문의 답변")


if __name__ == "__main__":
    unittest.main()
