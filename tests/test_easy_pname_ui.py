"""
=========================================================
Homez OS

File : tests/test_easy_pname_ui.py

2026-10-25 — 쉬운 보기 승인 화면의 "쿠팡 상품명 바꾸기"(쿠팡 쓰기 3번)를 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고, 서버 호출 순서·문구 짝은 소스에서 확인한다.
네트워크·DB 없음.

  - 입력 검사: 등록상품번호는 숫자, 노출상품명 1~100자(줄바꿈 없음), 등록상품명은 비우거나 100자 이하 — 틀리면 쿠팡을 읽지도 않는다.
  - 화면은 상품 본문을 만들지 않는다(서버가 방금 읽은 상품에서 이름만 바꾼다). 승인 뒤 반영 확인 칸을 체크해야만 보내고, 보내기 전에 한 번 더 묻는다.
  - 결과 문구: 다시 읽어 확인됨·승인 대기(이름 아직 그대로)·확인 못 함·이미 처리됨·결과불명(다시 보내지 말 것)·거부.
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
PREVIEW = function_source("pnamePreview")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:pname\.[a-z_]+|purpose\.pname|stock\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const PNAME_MAX = 100;
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join(function_source(n) for n in ("pnameInputProblem", "pnameResultHtml"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_input_rules(self):
        self.assertIsNone(self.run_js('pnameInputProblem(" 15000123456 ", "  새 이름 ", "")'))
        self.assertIsNone(self.run_js('pnameInputProblem("1", "가".repeat(100), "나".repeat(100))'))
        self.assertIsNone(self.run_js('pnameInputProblem("1", "이름", null)'))
        for pid in ('""', '"abc"', '"1".repeat(21)', "null"):
            self.assertEqual(self.run_js(f'pnameInputProblem({pid}, "이름", "")'), "easy.pname.err_id", pid)
        for name in ('""', '"   "', '"가".repeat(101)', '"줄\\n바꿈"', '"탭\\t문자"', "null"):
            self.assertEqual(self.run_js(f'pnameInputProblem("1", {name}, "")'), "easy.pname.err_display", name)
        for seller in ('"가".repeat(101)', '"줄\\n바꿈"'):
            self.assertEqual(self.run_js(f'pnameInputProblem("1", "이름", {seller})'), "easy.pname.err_seller", seller)

    def test_result_wording(self):
        def html(result, name="새 이름"):
            return self.run_js(f"pnameResultHtml({json.dumps(result)}, {json.dumps(name)})")

        self.assertIn("'새 이름'(으)로 바뀐 것을 다시 읽어 확인", html({"outcome": "SUCCEEDED", "verified": True, "after": {"status_name": "승인완료"}}))
        waiting = html({"outcome": "SUCCEEDED", "verified": None, "after": {"display_name": "옛 이름", "status_name": "승인대기"}})
        self.assertIn("'승인대기'", waiting)
        self.assertIn("쿠팡 승인 뒤에 바뀌어요", waiting)
        self.assertIn("ez-note warn", waiting)
        self.assertIn("확인하지는 못했어요", html({"outcome": "SUCCEEDED", "verified": None, "after": None}))
        self.assertIn("다시 보내지 않았어요", html({"outcome": "ALREADY_DONE"}))
        self.assertIn("다시 보내지 말고", html({"outcome": "UNKNOWN"}))
        rejected = html({"outcome": "ACTION_REQUIRED", "detail": "<b>필수 속성 누락</b>"})
        self.assertIn("&lt;b&gt;필수 속성 누락", rejected)
        self.assertNotIn("<b>", rejected)
        self.assertIn("&lt;i&gt;X&lt;/i&gt;: y", html({"outcome": "<i>X</i>", "detail": "y"}))
        self.assertIn("&lt;script&gt;", html({"outcome": "SUCCEEDED", "verified": True}, "<script>"))


class SourceTests(unittest.TestCase):

    def test_the_section_and_the_allowance_purpose_exist(self):
        for marker in ('id="ez-pname-anchor"', 'id="ez-pname-id"', 'id="ez-pname-display"', 'id="ez-pname-seller"', 'id="ez-pname-check"',
                       '["PRODUCT_NAME_UPDATE", "easy.purpose.pname"]', "const PNAME_MAX = 100;"):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-price-anchor"'), JS.index('id="ez-pname-anchor"'))

    def test_the_flow_checks_input_previews_and_sends_once_with_the_same_reference(self):
        self.assertLess(PREVIEW.index("pnameInputProblem(productId, displayText, sellerText)"), PREVIEW.index("/name/preview?"))
        self.assertIn("display_name=${encodeURIComponent(displayName)}", PREVIEW)
        self.assertIn("request_ref=${encodeURIComponent(ref)}&live=true", PREVIEW)
        send = PREVIEW[PREVIEW.index('send.addEventListener("click"'):]
        self.assertLess(send.index("reviewBox && reviewBox.checked"), send.index("B.askConfirm("))
        self.assertLess(send.index("B.askConfirm("), send.index("/name/send"))
        self.assertIn("display_name: displayName, seller_name: sellerName || null, expected_display_name: cp.display_name", send)
        self.assertIn("request_ref: ref, confirm_review: !!(reviewBox && reviewBox.checked), retry_action_required: false", send)
        self.assertIn("send.disabled = true", send)
        self.assertIn("/name/reconcile?display_name=${encodeURIComponent(displayName)}&request_ref=${encodeURIComponent(ref)}", send)
        self.assertNotIn("items", PREVIEW)                                     # 화면은 상품 본문을 만들지 않는다

    def test_server_text_is_escaped(self):
        self.assertIn("reasons.map((b) => esc(b.message))", PREVIEW)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.pname\.[a-z_]+)"', JS)) | {"easy.purpose.pname"}
        for key in sorted(used | set(re.findall(r'"(easy\.stock\.[a-z_]+)"', PREVIEW + function_source("pnameResultHtml")))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        own = {k for k in ko if k.startswith("easy.pname.") or k == "easy.purpose.pname"}
        self.assertEqual(own, {k for k in en if k.startswith("easy.pname.") or k == "easy.purpose.pname"})
        self.assertEqual(own - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("승인 뒤에 반영", ko["easy.pname.review_warn"])
        self.assertIn("반려될 수", ko["easy.pname.review_warn"])
        self.assertIn("이름만 바꿔", ko["easy.pname.confirm"])
        self.assertIn("허용이 필요", ko["easy.pname.where"])
        self.assertIn("승인완료·승인반려", ko["easy.pname.where"])
        self.assertEqual(ko["easy.purpose.pname"], "쿠팡 상품명 수정")


if __name__ == "__main__":
    unittest.main()
