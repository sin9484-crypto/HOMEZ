"""
=========================================================
Homez OS

File : tests/test_sales_performance_ui.py

2026-10-18 — 마케팅 AI M4(lite): 쉬운 보기 "분석 > 판매 성과" 탭(`easy_view.js`)을 고정한다. 실제 화면 함수를 Node에서 실행한다. 네트워크·DB 없음.

  - 읽기는 사용자가 "쿠팡에서 읽기"를 누르고 확인 창에서 승인했을 때만 나간다. 탭을 열거나 새로고침할 때는 읽지 않는다. 요청은 `POST /orders/sales-performance?days=` 하나다.
  - 결과: 판매 주문·수량·판매액·반품률·수수료율, 옵션별 표(이름·번호·판매·반품·수수료·표본 칩), 표본 부족은 "표본 부족"으로 보인다.
  - 안전: 서버가 준 모든 글은 이스케이프한다. 없는 값은 "undefined"가 아니라 "—"로 보인다. 어떤 제안이 맞았는지는 말하지 않는다.
  - 문구: 한국어·영어 짝, 자리표시자 일치·중복 없음, 쓰지 않는 문구 없음.
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
from tests.test_easy_briefing_ui import NODE
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

ROOT = Path(__file__).resolve().parents[1]


def table():
    block = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:perf|an\.tab_perf)[a-z_.]*)": ("(?:[^"\\]|\\.)*")', block)}


def constants():
    return "\n".join(m.group(0) for m in re.finditer(r"^  const PERF_[A-Z_]+ = [^\n]+$", JS, re.M))


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

SUMMARY = {
    "period": {"from": "2026-09-20", "to": "2026-10-16"},
    "totals": {"orders": 61, "sample": "ENOUGH", "items": 2, "sale_units": 70, "sale_amount": "903000", "refund_units": 7, "return_rate": "0.1000", "fee_ratio": "0.1080"},
    "items": [
        {"vendor_item_id": "9001", "name": "바디워시 3개입", "orders": 55, "sample": "ENOUGH", "sale_units": 60, "sale_amount": "774000", "refund_units": 6, "return_rate": "0.1000",
         "high_return": True, "fee_ratio": "0.1080", "refund_without_sale": False},
        {"vendor_item_id": "9002", "name": "샘플 상품", "orders": 6, "sample": "INSUFFICIENT", "sale_units": 10, "sale_amount": "129000", "refund_units": 1, "return_rate": "0.1667",
         "high_return": False, "fee_ratio": "0.1080", "refund_without_sale": False},
    ],
    "omitted": 0, "duplicates": 0, "skipped": 0, "notes": ["노트 하나", "노트 둘"],
}


def ok_data(**summary_overrides):
    return {"ok": True, "blocked": False, "no_connection": False, "detail": "", "days": 30, "calls": 2, "truncated": False,
            "summary": {**SUMMARY, **summary_overrides}, "checked_at": "2026-10-18T06:30:00"}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenTests(unittest.TestCase):

    def html(self, state):
        sources = "\n".join([constants()] + [function_source(n) for n in ("perfPercent", "perfWon", "perfHtml")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", f"perfHtml({json.dumps(state)})")
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_before_reading_it_says_nothing_has_been_read(self):
        html = self.html({"days": 30, "data": None, "error": ""})
        self.assertIn("아직 읽지 않았어요", html)
        self.assertNotIn("<table", html)

    def test_the_cards_and_table_show_the_numbers(self):
        html = self.html({"days": 30, "data": ok_data(), "error": ""})
        for text in ("판매 주문", "61", "판매 수량", "70", "903,000원", "실현 수수료율 10.8%", "10.0%", "반품 7개", "표본 충분", "2026-09-20 ~ 2026-10-16 매출 인식 기준"):
            self.assertIn(text, html)
        self.assertIn("<b>바디워시 3개입</b>", html)
        self.assertIn("774,000원", html)
        self.assertIn("6 · 10.0%", html)
        self.assertIn("16.7%", html)           # 0.1667
        self.assertIn("반품 많음(참고)", html)  # 9001만
        self.assertEqual(html.count("반품 많음(참고)"), 1)
        self.assertIn("표본 부족", html)       # 9002
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)

    def test_server_texts_are_escaped(self):
        evil = ok_data(items=[{**SUMMARY["items"][0], "name": "<img src=x onerror=alert(1)>", "vendor_item_id": "<b>1</b>"}], notes=["<script>x</script>"])
        html = self.html({"days": 30, "data": evil, "error": ""})
        for raw in ("<img", "<script", "<b>1</b>"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;img", html)
        failed = self.html({"days": 30, "data": {"ok": False, "detail": "<i>오류</i>"}, "error": ""})
        self.assertNotIn("<i>", failed)
        error = self.html({"days": 30, "data": None, "error": "<u>x</u>"})
        self.assertNotIn("<u>", error)

    def test_each_failure_kind_has_its_own_message(self):
        base = {"ok": False, "blocked": False, "no_connection": False, "detail": "code=E message=서버 오류"}
        self.assertIn("연결된 쿠팡 상점이 없어요", self.html({"data": {**base, "no_connection": True}, "error": ""}))
        blocked = self.html({"data": {**base, "blocked": True}, "error": ""})
        self.assertIn("승인 센터", blocked)
        self.assertIn("반품·민원·정산 확인", blocked)
        failed = self.html({"data": base, "error": ""})
        self.assertIn("읽지 못했어요: code=E message=서버 오류", failed)
        self.assertNotIn("<table", failed)

    def test_no_sales_a_cut_read_and_skipped_lines_are_explained(self):
        empty = self.html({"data": ok_data(items=[], totals={**SUMMARY["totals"], "orders": 0, "items": 0, "sale_units": 0, "sale_amount": "0", "refund_units": 0, "return_rate": None, "fee_ratio": None}), "error": ""})
        self.assertIn("매출로 인정한 판매가 없어요", empty)
        self.assertIn("—", empty)
        cut = ok_data(omitted=7, duplicates=2, skipped=3)
        cut["truncated"] = True
        html = self.html({"data": cut, "error": ""})
        for text in ("일부만 읽었어요", "나머지 7개", "겹친 줄 2개와 읽지 못한 줄 3개"):
            self.assertIn(text, html)

    def test_missing_fields_never_print_undefined(self):
        html = self.html({"data": {"ok": True, "summary": {"totals": {}, "items": [{"vendor_item_id": "1"}], "notes": []}}, "error": ""})
        self.assertNotIn("undefined", html)
        self.assertNotIn("NaN", html)
        self.assertIn("이름 없음", html)

    def test_a_refund_only_option_is_marked(self):
        item = {**SUMMARY["items"][1], "sale_units": 0, "refund_without_sale": True, "return_rate": None}
        self.assertIn("판매 없이 반품만 있음", self.html({"data": ok_data(items=[item]), "error": ""}))

    def test_it_never_says_whether_a_suggestion_was_right(self):
        html = self.html({"data": ok_data(), "error": ""})
        for word in ("적중", "맞았", "성공했", "효과가 있었"):
            self.assertNotIn(word, html)


class WiringTests(unittest.TestCase):

    def test_reading_needs_a_click_and_a_confirmation_and_is_one_post(self):
        src = JS[JS.index("  function renderPerf(host)"):JS.index("  // ---- /판매 성과 ----")]
        self.assertEqual(re.findall(r"api\(", src), ["api("])
        self.assertIn("`/orders/sales-performance?days=${encodeURIComponent(perfState.days)}`, { method: \"POST\" }", src)
        click = src[src.index('$("ez-perf-read").addEventListener("click"'):]
        self.assertLess(click.index("ezDialog("), click.index("api("))
        self.assertIn("if (!ok) return;", click)

    def test_a_second_click_while_a_dialog_is_open_or_a_read_is_running_does_nothing(self):
        src = JS[JS.index("  function renderPerf(host)"):JS.index("  // ---- /판매 성과 ----")]
        click = src[src.index('$("ez-perf-read").addEventListener("click"'):]
        self.assertIn("if (perfState.busy) return;", click)
        self.assertLess(click.index("if (perfState.busy) return;"), click.index("perfState.busy = true;"))
        self.assertLess(click.index("perfState.busy = true;"), click.index("ezDialog("))
        self.assertIn("} finally {\n        perfState.busy = false;", click)   # 어떤 경우에도(취소·오류 포함) 다시 풀린다
        self.assertIn('busy: false };', JS)

    def test_opening_the_tab_never_reads(self):
        self.assertEqual(JS.count("/orders/sales-performance"), 1)
        for name in ("perfPercent", "perfWon", "perfHtml"):
            self.assertNotIn("api(", function_source(name), name)
        analysis = JS[JS.index("  async function loadAnalysis()"):]
        self.assertNotIn("sales-performance", analysis[:analysis.index("\n  // ====")] if "\n  // ====" in analysis else analysis)
        self.assertIn('} else if (tab === "PERF") {\n      renderPerf(host);', analysis)

    def test_the_tab_is_listed_and_the_period_choices_fit_the_servers_limit(self):
        self.assertIn('["PERF", "easy.an.tab_perf", perfBadge]', JS)
        self.assertIn("const PERF_DAYS = [7, 14, 30];", JS)
        from app.domains.purchase_task.coupang_after_sales_provider import MAX_REVENUE_RANGE_DAYS

        self.assertLessEqual(max([7, 14, 30]), MAX_REVENUE_RANGE_DAYS)

    def test_the_view_code_stores_nothing_in_the_browser(self):
        src = JS[JS.index("  // ---- 판매 성과 (2026-10-18"):JS.index("  // ---- /판매 성과 ----")]
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "eval(", "fetch("):
            self.assertNotIn(forbidden, src, forbidden)


class TextTests(unittest.TestCase):

    def defined(self, source):
        return set(re.findall(r'"(easy\.perf\.[a-z_]+|easy\.an\.tab_perf)":', source))

    def sections(self):
        ko = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        en = re.search(r'"en-US": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        return ko, en

    def test_both_languages_define_the_same_keys_with_matching_placeholders_and_no_repeats(self):
        ko, en = self.sections()
        self.assertEqual(self.defined(ko), self.defined(en))
        self.assertGreaterEqual(len(self.defined(ko)), 35)
        for key in self.defined(ko):
            kv = re.search(rf'"{re.escape(key)}": "((?:[^"\\]|\\.)*)"', ko)
            ev = re.search(rf'"{re.escape(key)}": "((?:[^"\\]|\\.)*)"', en)
            names_ko, names_en = re.findall(r"\{(\w+)\}", kv.group(1)), re.findall(r"\{(\w+)\}", ev.group(1))
            self.assertEqual(set(names_ko), set(names_en), key)
            self.assertEqual(len(names_ko), len(set(names_ko)), key)   # 같은 자리표시자를 두 번 쓰면 첫 번째만 바뀐다

    def test_every_defined_text_is_used_in_the_view_code_and_every_used_text_is_defined(self):
        ko, _ = self.sections()
        src = JS[JS.index("  // ---- 판매 성과 (2026-10-18"):JS.index("  // ---- /판매 성과 ----")]
        used = set(re.findall(r'"(easy\.perf\.[a-z_]+)"', src)) | set(re.findall(r'"(easy\.an\.tab_perf)"', JS))
        self.assertEqual(self.defined(ko), used)


if __name__ == "__main__":
    unittest.main()
