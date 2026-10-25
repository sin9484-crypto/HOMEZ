"""
=========================================================
Homez OS

File : tests/test_easy_price_ui.py

2026-10-25 — 쉬운 보기 승인 화면의 "쿠팡 판매가 바꾸기"(쿠팡 쓰기 2번)를 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고, 서버 호출 순서·문구 짝은 소스에서 확인한다.
네트워크·DB 없음.

  - 입력 검사: 옵션 ID는 숫자, 판매가는 10원 단위(10~10,000,000), 매입가 1원 이상, 배송비 0원 이상 — 틀리면 쿠팡을 읽지도 않는다.
  - 매입가·배송비는 사용자가 넣고 그대로 서버에 보낸다(화면이 추측하지 않는다). 최저 판매가·마진·가정 수수료를 보여 준다.
  - 20% 이상 변경은 확인 칸을 체크해야만 보내고, 보내기 전에 한 번 더 묻는다. 끝나면 결과를 화면에 남긴다.
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
PREVIEW = function_source("pricePreview")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:price\.[a-z_]+|purpose\.price|stock\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const PRICE_MAX = 10000000;
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join(function_source(n) for n in ("priceInputProblem", "priceNum", "priceResultHtml"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_input_rules(self):
        self.assertIsNone(self.run_js('priceInputProblem(" 70012345678 ", "16000", "6000", "3000")'))
        self.assertIsNone(self.run_js('priceInputProblem("1", "10", "1", "0")'))
        self.assertIsNone(self.run_js('priceInputProblem("1", 10000000, 10000000, 10000000)'))
        for item in ('""', '"abc"', '"1".repeat(21)', "null"):
            self.assertEqual(self.run_js(f'priceInputProblem({item}, "16000", "6000", "0")'), "easy.stock.err_item", item)
        for price in ('""', '"0"', '"5"', '"16005"', '"-100"', '"1.5"', '"10000010"', '"abc"', "null"):
            self.assertEqual(self.run_js(f'priceInputProblem("1", {price}, "6000", "0")'), "easy.price.err_price", price)
        for cost in ('""', '"0"', '"-1"', '"1.5"', '"10000001"', "null"):
            self.assertEqual(self.run_js(f'priceInputProblem("1", "16000", {cost}, "0")'), "easy.price.err_cost", cost)
        for ship in ('""', '"-1"', '"1.5"', '"10000001"', "null"):
            self.assertEqual(self.run_js(f'priceInputProblem("1", "16000", "6000", {ship})'), "easy.price.err_ship", ship)

    def test_numbers_are_shown_with_separators_or_a_dash(self):
        self.assertEqual(self.run_js("[priceNum(16000), priceNum(null), priceNum(1.5), priceNum(undefined)]"), ["16,000", "—", "—", "—"])

    def test_result_wording(self):
        def html(result, p=16000):
            return self.run_js(f"priceResultHtml({json.dumps(result)}, {p})")

        self.assertIn("16,000원으로 바꾸고 다시 읽어 확인했어요", html({"outcome": "SUCCEEDED", "verified": True, "after_price": 16000}))
        diff = html({"outcome": "SUCCEEDED", "verified": False, "after_price": 15000})
        self.assertIn("다시 읽은 판매가는 15,000원", diff)
        self.assertIn("ez-note warn", diff)
        self.assertIn("확인하지는 못했어요", html({"outcome": "SUCCEEDED", "verified": None}))
        self.assertIn("다시 보내지 않았어요", html({"outcome": "ALREADY_DONE"}))
        self.assertIn("판매가를 읽어 확인", html({"outcome": "UNKNOWN"}))
        rejected = html({"outcome": "ACTION_REQUIRED", "detail": "<b>범위 초과</b>"})
        self.assertIn("&lt;b&gt;범위 초과", rejected)
        self.assertNotIn("<b>", rejected)
        self.assertIn("ez-note bad", rejected)
        self.assertIn("&lt;i&gt;WEIRD&lt;/i&gt;: x", html({"outcome": "<i>WEIRD</i>", "detail": "x"}))


class SourceTests(unittest.TestCase):

    def test_the_section_and_the_allowance_purpose_exist(self):
        for marker in ('id="ez-price-anchor"', 'id="ez-price-check"', 'id="ez-price-cost"', 'id="ez-price-ship"', 'step="10"',
                       '["PRICE_UPDATE", "easy.purpose.price"]', "const PRICE_MAX = 10000000;"):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-stock-anchor"'), JS.index('id="ez-price-anchor"'))

    def test_the_flow_checks_input_previews_with_a_reference_and_sends_once_with_the_same_reference(self):
        self.assertLess(PREVIEW.index("priceInputProblem(itemId, priceText, costText, shipText)"), PREVIEW.index("/price/preview?"))
        self.assertIn("price=${price}&unit_cost=${unitCost}&shipping_cost=${shippingCost}&request_ref=${encodeURIComponent(ref)}&live=true", PREVIEW)
        send = PREVIEW[PREVIEW.index('send.addEventListener("click"'):]
        self.assertLess(send.index("bigBox && bigBox.checked"), send.index("B.askConfirm("))
        self.assertLess(send.index("B.askConfirm("), send.index("/price/send"))
        self.assertIn("price, unit_cost: unitCost, shipping_cost: shippingCost, expected_current_price: cp.current_price, request_ref: ref", send)
        self.assertIn("retry_action_required: false", send)
        self.assertIn("send.disabled = true", send)
        self.assertIn("/price/reconcile?price=${price}&request_ref=${encodeURIComponent(ref)}", send)
        self.assertIn('$("ez-price-result").innerHTML = priceResultHtml(result, price)', send)

    def test_a_big_change_is_only_confirmed_by_the_checkbox(self):
        self.assertIn("confirm_big_change: plan.big_change === true && !!(bigBox && bigBox.checked)", PREVIEW)
        self.assertIn('id="ez-price-big"', PREVIEW)

    def test_the_margin_assumptions_are_shown(self):
        self.assertIn("floor: priceNum(m.floor_price), margin: pctDown(m.margin_rate), fee: pct(m.fee_rate)", PREVIEW)
        self.assertIn("Math.floor(r * 1000) / 10", PREVIEW)                     # 마진은 내림(기준을 지킨 것처럼 올려 보이지 않는다)

    def test_server_text_is_escaped(self):
        self.assertIn("reasons.map((b) => esc(b.message))", PREVIEW)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.price\.[a-z_]+)"', JS)) | {"easy.purpose.price"}
        for key in sorted(used | set(re.findall(r'"(easy\.stock\.[a-z_]+)"', PREVIEW))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        price_keys = {k for k in ko if k.startswith("easy.price.") or k == "easy.purpose.price"}
        self.assertEqual(price_keys, {k for k in en if k.startswith("easy.price.") or k == "easy.purpose.price"})
        self.assertEqual(price_keys - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("바로 판매에 반영", ko["easy.price.confirm"])
        self.assertIn("짐작하지 않아요", ko["easy.price.where"])
        self.assertIn("최저 판매가보다 낮으면 보내지 않아요", ko["easy.price.where"])
        self.assertIn("허용이 필요", ko["easy.price.where"])
        self.assertIn("카테고리마다 다를 수", ko["easy.price.margin_line"])
        self.assertEqual(ko["easy.purpose.price"], "쿠팡 판매가 변경")


if __name__ == "__main__":
    unittest.main()
