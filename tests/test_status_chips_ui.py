"""
=========================================================
Homez OS

File : tests/test_status_chips_ui.py

목록 화면 공통 "상태별 건수 칩"(2026-10-15, UI 개선 5단계)을 고정한다 — 주문·배송·반품·환불·정산 대조. 칩은 기존 상태 필터와 같은
값으로 목록을 다시 불러오고, 건수는 필터와 무관하게 전체 기준이며, 데이터에 있는 상태만 보여 준다(없는 상태를 만들지 않는다).
실제 `console.js`의 순수 함수를 Node에서 실행한다. 네트워크·DB 없음.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    match = re.search(r"(?:async )?function " + re.escape(name) + r"\(", JS)
    assert match, name
    start = match.start()
    paren, j = 1, match.end()
    while paren:
        paren += {"(": 1, ")": -1}.get(JS[j], 0)
        j += 1
    i = JS.index("{", j)
    depth, mode, stack = 0, None, []
    while i < len(JS):
        ch = JS[i]
        if mode is None:
            if ch == "/" and JS[i + 1] == "/":
                i = JS.index("\n", i)
                continue
            if ch in "\"'`":
                mode = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if stack and depth == stack[-1]:
                    stack.pop()
                    mode = "`"
                elif depth == 0:
                    return JS[start: i + 1]
        else:
            if ch == "\\":
                i += 2
                continue
            if mode == "`" and ch == "$" and JS[i + 1] == "{":
                stack.append(depth)
                depth += 1
                mode = None
                i += 2
                continue
            if ch == mode:
                mode = None
        i += 1
    raise AssertionError("unbalanced: " + name)


def i18n_table() -> dict:
    table = {}
    for m in re.finditer(r'^\s*"((?:ord|common)\.[A-Za-z0-9_.]+)":\s*(".*"),?\s*$', KO, re.M):
        table[m.group(1)] = json.loads(m.group(2))
    return table


HARNESS = r"""
const HomezI18n = { t(key, params) {
  const table = JSON.parse(process.env.I18N_JSON);
  return table[key] !== undefined ? table[key] : key;
} };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
let apiResult = JSON.parse(process.env.API_JSON);
let apiCalls = [];
const apiFetch = async (path) => { apiCalls.push(path); if (apiResult === "FAIL") throw new Error("x"); return apiResult; };
__SOURCES__
(async () => {
  const out = await (async () => { return __EXPR__; })();
  console.log(JSON.stringify({ out, apiCalls }));
})();
"""
SOURCES = ("countByStatus", "statusCountsFor", "statusChipsHtml")
OPTIONS = '["PENDING","SHIPPED","DELIVERED","CANCELLED"]'


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class PureFunctionTests(unittest.TestCase):

    def run_js(self, expression, api=None):
        sources = "\n".join(function_source(n) for n in SOURCES)
        env = {**os.environ, "I18N_JSON": json.dumps(i18n_table()), "API_JSON": json.dumps(api if api is not None else [])}
        proc = subprocess.run(
            [NODE, "-e", HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_counts_group_rows_by_status_and_ignore_rows_without_one(self):
        rows = [{"status": "PENDING"}, {"status": "PENDING"}, {"status": "SHIPPED"}, {}, None]
        self.assertEqual(self.run_js(f"countByStatus({json.dumps(rows)})")["out"], {"PENDING": 2, "SHIPPED": 1})

    def test_without_a_filter_the_loaded_rows_are_counted_without_another_request(self):
        result = self.run_js('await statusCountsFor("/orders?limit=200", "", [{"status":"PENDING"}])')
        self.assertEqual(result["out"], {"PENDING": 1})
        self.assertEqual(result["apiCalls"], [])

    def test_with_a_filter_the_counts_come_from_an_unfiltered_read(self):
        api = [{"status": "PENDING"}, {"status": "SHIPPED"}, {"status": "SHIPPED"}]
        result = self.run_js('await statusCountsFor("/orders?limit=200", "SHIPPED", [{"status":"SHIPPED"}])', api)
        self.assertEqual(result["out"], {"PENDING": 1, "SHIPPED": 2})
        self.assertEqual(result["apiCalls"], ["/orders?limit=200"])

    def test_if_the_full_read_fails_there_are_no_counts_and_no_chips(self):
        result = self.run_js('await statusCountsFor("/orders?limit=200", "SHIPPED", [{"status":"SHIPPED"}])', "FAIL")
        self.assertIsNone(result["out"])
        self.assertEqual(self.run_js(f'statusChipsHtml({OPTIONS}, null, "", "ord.status.")')["out"], "")

    def test_only_statuses_that_exist_get_a_chip_plus_an_all_chip_with_the_total(self):
        html = self.run_js(f'statusChipsHtml({OPTIONS}, {{"PENDING": 2, "SHIPPED": 1}}, "", "ord.status.")')["out"]
        self.assertIn('data-status-chip=""', html)
        self.assertIn('data-status-chip="PENDING"', html)
        self.assertIn('data-status-chip="SHIPPED"', html)
        self.assertNotIn('data-status-chip="DELIVERED"', html)
        self.assertIn("전체 <strong>3</strong>", html)
        self.assertIn("접수 대기 <strong>2</strong>", html)

    def test_the_selected_status_stays_visible_even_when_its_count_is_zero(self):
        html = self.run_js(f'statusChipsHtml({OPTIONS}, {{"PENDING": 2}}, "DELIVERED", "ord.status.")')["out"]
        self.assertIn('data-status-chip="DELIVERED"', html)
        self.assertIn('aria-pressed="true"', html)

    def test_an_unknown_label_falls_back_to_the_raw_status_not_an_empty_chip(self):
        html = self.run_js('statusChipsHtml(["WEIRD"], {"WEIRD": 1}, "", "ord.status.")')["out"]
        self.assertIn("WEIRD <strong>1</strong>", html)

    def test_status_text_is_escaped(self):
        html = self.run_js('statusChipsHtml(["<b>x</b>"], {"<b>x</b>": 1}, "", "ord.status.")')["out"]
        self.assertNotIn("<b>x</b>", html)


class WiringTests(unittest.TestCase):

    SCREENS = (
        ("ordRenderList", "ordState", "ORDER_STATUS_OPTIONS", '"ord.status."'),
        ("shipRenderList", "shipState", "SHIPMENT_STATUS_OPTIONS", '"ship.status."'),
        ("retRenderList", "retState", "RETURN_STATUS_OPTIONS", '"ret.status."'),
        ("rfRenderList", "rfState", "REFUND_STATUS_OPTIONS", '"refund.status."'),
    )

    def test_each_list_shows_chips_and_chips_drive_the_same_filter_as_the_select(self):
        for fn, state, options, prefix in self.SCREENS:
            body = function_source(fn)
            self.assertIn(f"statusChipsHtml({options}", body, fn)
            self.assertIn(prefix, body, fn)
            self.assertIn(f"{state}.statusFilter = value;", body, fn)
            self.assertIn("wireStatusChips(container,", body, fn)

    def test_the_settlement_reconciliation_list_uses_the_same_chips(self):
        body = function_source("loadChannelSettlement")
        self.assertIn("statusChipsHtml(RECONCILIATION_STATUS_OPTIONS", body)
        self.assertIn("stlState.reconStatusFilter = value;", body)
        self.assertIn("${reconChipsHtml}", body)

    def test_order_statuses_are_translated_in_the_table_instead_of_shown_raw(self):
        self.assertIn('statusPillHtmlLabeled(o.status, "ord.status.")', function_source("ordRenderList"))

    def test_chips_never_change_anything_on_the_server(self):
        for name in ("countByStatus", "statusCountsFor", "statusChipsHtml", "wireStatusChips"):
            body = function_source(name)
            self.assertNotIn("POST", body)
            self.assertNotIn("PUT", body)
            self.assertNotIn("PATCH", body)

    def test_order_status_translations_cover_every_status_in_both_languages(self):
        options = re.search(r"const ORDER_STATUS_OPTIONS = \[(.*?)\];", JS, re.S).group(1)
        statuses = re.findall(r'"([A-Z_]+)"', options)
        self.assertGreaterEqual(len(statuses), 10)
        for status in statuses:
            for text in (KO, EN):
                self.assertIn(f'"ord.status.{status.lower()}"', text, status)
        self.assertIn('"common.status_chips_aria"', KO)
        self.assertIn('"common.status_chips_aria"', EN)


if __name__ == "__main__":
    unittest.main()
