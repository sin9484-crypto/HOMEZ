"""
=========================================================
Homez OS

File : tests/test_easy_pickup_ui.py

2026-10-27 — 쉬운 보기 반품 처리 구역의 "회수 송장 등록"(쿠팡 쓰기 7번)을 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고,
서버 호출 순서·문구 짝은 소스에서 확인한다. 네트워크·DB 없음.

  - 접수번호 숫자, 택배사 이름 필수(코드 변환은 서버가 공식 목록으로만), 송장 6~30자 영문·숫자·하이픈, 회수번호는 비우거나 영문·숫자 30자.
  - 직접 회수 확인 칸을 체크해야만 보내고, 보내기 전에 한 번 더 묻고, 미리보기에서 본 상태를 함께 보낸다. 결과는 화면에 남긴다.
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
PREVIEW = function_source("pickupPreview")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:pick\.[a-z_]+|ret\.[a-z_]+|stock\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def run_js(self, expression):
        script = function_source("pickupInputProblem") + f"\nconsole.log(JSON.stringify({expression}));"
        proc = subprocess.run([NODE, "-e", script], env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_input_rules(self):
        self.assertIsNone(self.run_js('pickupInputProblem(" 363585 ", "CJ대한통운", "584712345678", "")'))
        self.assertIsNone(self.run_js('pickupInputProblem("1", "CJGLS", "AB-1234", "R1234")'))
        for args, code in ((('"abc"', '"CJ"', '"584712345678"', '""'), "easy.ret.err_id"),
                           (('"1"', '"  "', '"584712345678"', '""'), "easy.pick.err_courier"),
                           (('"1"', '"CJ"', '"12345"', '""'), "easy.pick.err_invoice"),
                           (('"1"', '"CJ"', '"1234 5678"', '""'), "easy.pick.err_invoice"),
                           (('"1"', '"CJ"', '"584712345678"', '"R-1"'), "easy.pick.err_reg"),
                           (('"1"', '"CJ"', '"584712345678"', '"1".repeat(31)'), "easy.pick.err_reg")):
            self.assertEqual(self.run_js(f"pickupInputProblem({', '.join(args)})"), code, args)


class SourceTests(unittest.TestCase):

    def test_the_block_lives_in_the_return_section(self):
        for marker in ('id="ez-pick-anchor"', 'id="ez-pick-id"', 'id="ez-pick-courier"', 'id="ez-pick-invoice"', 'id="ez-pick-reg"', 'id="ez-pick-check"'):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-ret-anchor"'), JS.index('id="ez-pick-anchor"'))

    def test_the_flow_checks_input_then_previews_then_needs_the_box_then_asks_then_sends(self):
        self.assertLess(PREVIEW.index("pickupInputProblem(receiptId, courier, invoice, reg)"), PREVIEW.index("/pickup-invoice/preview?"))
        send = PREVIEW[PREVIEW.index('send.addEventListener("click"'):]
        self.assertLess(send.index("box2 && box2.checked"), send.index("B.askConfirm("))
        self.assertLess(send.index("B.askConfirm("), send.index("/pickup-invoice/send"))
        self.assertIn("courier: c, invoice_number: n, reg_number: r || null, expected_status: cp.status, confirm_pickup: !!(box2 && box2.checked), retry_action_required: false", send)
        self.assertIn("send.disabled = true", send)
        self.assertIn("/pickup-invoice/reconcile?invoice_number=", send)

    def test_server_text_is_escaped(self):
        self.assertIn("reasons.map((b) => esc(b.message))", PREVIEW)
        self.assertIn('`<div class="ez-note ${tone}"><div>${esc(text)}</div></div>`', PREVIEW)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.pick\.[a-z_]+)"', JS))
        for key in sorted(used | set(re.findall(r'"(easy\.(?:ret|stock)\.[a-z_]+)"', PREVIEW + function_source("pickupInputProblem")))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        own = {k for k in ko if k.startswith("easy.pick.")}
        self.assertEqual(own, {k for k in en if k.startswith("easy.pick.")})
        self.assertEqual(own - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("철회할 수 있다", ko["easy.pick.confirm_box"])
        self.assertIn("굿스플로", ko["easy.pick.sub"])
        self.assertIn("반품접수 상태만", ko["easy.pick.sub"])


if __name__ == "__main__":
    unittest.main()
