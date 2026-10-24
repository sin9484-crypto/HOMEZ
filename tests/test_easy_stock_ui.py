"""
=========================================================
Homez OS

File : tests/test_easy_stock_ui.py

2026-10-24 — 쉬운 보기 승인 화면의 "쿠팡 재고 수량 바꾸기"(쿠팡 쓰기 1번)를 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고, 서버 호출 순서·문구 짝은 소스에서 확인한다.
네트워크·DB 없음.

  - 입력 검사: 옵션 ID는 숫자(20자리까지), 수량은 0~99,999 정수 — 틀리면 쿠팡을 읽지도 않는다.
  - 요청 참조값: 미리보기마다 새로 만들고 서버 규칙(영문·숫자·하이픈 8~40자)에 맞는다. 결과 확인(결과불명)은 같은 참조값을 쓴다.
  - 0은 확인 칸을 체크해야만 보내고, 보내기 전에 한 번 더 묻는다. 끝나면 결과를 화면에 남긴다(토스트만으로 끝내지 않는다).
  - 결과 문구: 다시 읽어 확인됨·다른 값(그사이 주문 가능성)·확인 못 함·이미 처리됨·결과불명(다시 보내지 말고 조회)·거부. 서버 글은 이스케이프한다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

NODE = shutil.which("node")
PREVIEW = function_source("stockPreview")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:stock\.[a-z_]+|purpose\.stock))": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const STOCK_MAX = 99999;
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join(function_source(n) for n in ("stockInputProblem", "stockRef", "stockResultHtml"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_input_rules(self):
        self.assertIsNone(self.run_js('stockInputProblem(" 70012345678 ", "12")'))
        self.assertIsNone(self.run_js('stockInputProblem("1", "0")'))
        self.assertIsNone(self.run_js('stockInputProblem("1", 99999)'))
        for item in ('""', '"abc"', '"12a"', '"1".repeat(21)', "null"):
            self.assertEqual(self.run_js(f'stockInputProblem({item}, "1")'), "easy.stock.err_item", item)
        for qty in ('""', '"-1"', '"1.5"', '"100000"', '"abc"', "null", '" "'):
            self.assertEqual(self.run_js(f'stockInputProblem("1", {qty})'), "easy.stock.err_qty", qty)

    def test_each_reference_is_new_and_matches_the_server_rule(self):
        refs = self.run_js("[stockRef(), stockRef(), stockRef()]")
        self.assertEqual(len(set(refs)), 3)
        for ref in refs:
            self.assertRegex(ref, r"^[A-Za-z0-9-]{8,40}$")

    def test_result_wording(self):
        def html(result, q=12):
            return self.run_js(f"stockResultHtml({json.dumps(result)}, {q})")

        self.assertIn("12개로 바꾸고 다시 읽어 확인했어요", html({"outcome": "SUCCEEDED", "verified": True, "after_stock": 12}))
        diff = html({"outcome": "SUCCEEDED", "verified": False, "after_stock": 11})
        self.assertIn("다시 읽은 재고는 11개", diff)
        self.assertIn("그사이 주문", diff)
        self.assertIn("ez-note warn", diff)
        self.assertIn("확인하지는 못했어요", html({"outcome": "SUCCEEDED", "verified": None}))
        self.assertIn("다시 보내지 않았어요", html({"outcome": "ALREADY_DONE"}))
        unknown = html({"outcome": "UNKNOWN"})
        self.assertIn("다시 보내지 말고", unknown)
        rejected = html({"outcome": "ACTION_REQUIRED", "detail": "<b>유효하지 않은 재고수량</b>"})
        self.assertIn("&lt;b&gt;유효하지 않은 재고수량", rejected)
        self.assertNotIn("<b>", rejected)
        self.assertIn("ez-note bad", rejected)
        odd = html({"outcome": "<i>WEIRD</i>", "detail": "x"})                    # 모르는 결과는 결과 코드와 내용을 그대로(이스케이프) 보인다
        self.assertIn("&lt;i&gt;WEIRD&lt;/i&gt;: x", odd)


class SourceTests(unittest.TestCase):

    def test_the_section_and_the_allowance_purpose_exist(self):
        self.assertIn('id="ez-stock-anchor"', JS)
        self.assertIn('id="ez-stock-check"', JS)
        self.assertIn('["STOCK_UPDATE", "easy.purpose.stock"]', JS)
        self.assertIn("const STOCK_MAX = 99999;", JS)

    def test_the_flow_checks_input_previews_with_a_reference_and_sends_once_with_the_same_reference(self):
        self.assertLess(PREVIEW.index("stockInputProblem(itemId, qtyText)"), PREVIEW.index("/stock/preview?"))
        self.assertIn("request_ref=${encodeURIComponent(ref)}&live=true", PREVIEW)
        send = PREVIEW[PREVIEW.index('send.addEventListener("click"'):]
        self.assertLess(send.index("zeroBox && zeroBox.checked"), send.index("B.askConfirm("))
        self.assertLess(send.index("B.askConfirm("), send.index("/stock/send"))
        self.assertIn("expected_current_stock: cp.current_stock, request_ref: ref", send)
        self.assertIn("retry_action_required: false", send)
        self.assertIn("send.disabled = true", send)
        self.assertIn("/stock/reconcile?quantity=${quantity}&request_ref=${encodeURIComponent(ref)}", send)
        self.assertIn('$("ez-stock-result").innerHTML = stockResultHtml(result, quantity)', send)

    def test_zero_is_only_confirmed_by_the_checkbox(self):
        self.assertIn("confirm_zero: plan.zero_warning === true && !!(zeroBox && zeroBox.checked)", PREVIEW)
        self.assertIn('id="ez-stock-zero"', PREVIEW)

    def test_server_text_is_escaped(self):
        self.assertIn("reasons.map((b) => esc(b.message))", PREVIEW)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.stock\.[a-z_]+)"', JS)) | {"easy.purpose.stock"}
        for key in sorted(used):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        self.assertEqual(set(ko), set(en))
        self.assertEqual(set(ko) - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("바로 팔리지 않아요", ko["easy.stock.zero_warn"])
        self.assertIn("바로 판매에 반영", ko["easy.stock.confirm"])
        self.assertIn("허용이 필요", ko["easy.stock.where_id"])
        self.assertEqual(ko["easy.purpose.stock"], "쿠팡 재고 수량 변경")


if __name__ == "__main__":
    unittest.main()
