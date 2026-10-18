"""
=========================================================
Homez OS

File : tests/test_candidate_evaluation_ui.py

2026-10-18 — 상품 서칭 AI S2: 후보 목록의 "사전 평가" 칸을 고정한다. 실제 `console.js` 함수를 Node에서 실행한다. 네트워크·DB 없음.

  - 결정 칩(후보 가능/보류 권고/제외 권고)과 가장 중요한 이유 2개를 사람 말로 보여 주고 나머지는 "외 n건"으로 줄인다. 글은 모두 이스케이프한다.
  - 평가가 없으면 "—". 알 수 없는 이유 코드는 건너뛴다(키 이름을 노출하지 않는다). 사전 평가를 못 불러와도 후보 목록은 그대로다.
  - 쓰는 모든 문구(이유·규제 분류·누락 항목)가 한국어·영어로 짝을 이룬다. 이 칸은 읽기 전용이라 요청을 보내거나 후보를 바꾸지 않는다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.domains.product_candidate import candidate_evaluation as ev

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
            if ch == "/" and JS[i + 1] != "/" and JS[:i].rstrip()[-1:] in "(,=:[!&|?{};":
                k = i + 1
                while JS[k] != "/" or JS[k - 1] == "\\":
                    k += 1
                i = k + 1
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


def constants_source():
    return "\n".join(m.group(0) for m in re.finditer(r"^  const CAND_EVAL_[A-Z_]+ = [^\n]+$", JS, re.M))


def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(candidates\.[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class RenderTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join([constants_source()] + [function_source(n) for n in ("candidateEvalReasonText", "candidateEvalCellHtml")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def cell(self, item):
        return self.run_js(f"candidateEvalCellHtml({json.dumps(item)})")

    def reason(self, reason):
        return self.run_js(f"candidateEvalReasonText({json.dumps(reason)})")

    def test_no_evaluation_shows_a_dash(self):
        for value in ("null", "undefined"):
            self.assertEqual(self.run_js(f"candidateEvalCellHtml({value})"), "—")

    def test_each_decision_has_its_chip(self):
        for decision, tone, label in (("READY", "ok", "후보 가능"), ("HOLD", "warn", "보류 권고"), ("EXCLUDE", "danger", "제외 권고")):
            html = self.cell({"decision": decision, "reasons": []})
            self.assertIn(f'class="pill {tone}"', html)
            self.assertIn(label, html)

    def test_every_reason_code_the_rules_can_emit_reads_in_plain_words(self):
        samples = {
            "PROHIBITED_ITEM": ({"words": ["총기"]}, "금지 품목 표현이 있어요: 총기"),
            "BRAND_IP_WORD": ({"words": ["나이키"]}, "상표 권리 확인이 필요한 단어가 있어요: 나이키"),
            "CLAIM_WORDS": ({"words": ["연골", "예방"]}, "연골, 예방"),
            "CHILD_TERMS": ({"words": ["어린이"]}, "아동 대상 표현"),
            "REGULATED_CATEGORY": ({"kind": "HEALTH_FOOD", "value": "건강기능식품"}, "건강기능식품"),
            "KC_CERT": ({"value": "XU12345"}, "XU12345"),
            "MARGIN_NOT_ANALYZED": ({}, "마진 분석이 없어요"),
            "MARGIN_ANALYSIS_HOLD": ({"missing": ["VAT_STATE", "CHANNEL_FEE", "SUPPLY_PRICE"]}, "부가세, 수수료율, 공급가"),
            "MARGIN_ANALYSIS_UNFIT": ({}, "기준 마진율을 달성하기 어려워요"),
            "MARGIN_OK": ({"price": "12790", "rate": "0.18"}, "12,790원"),
            "DUPLICATE_OF": ({"candidate_id": 7}, "#7"),
            "VARIANT_EXISTS": ({}, "구성만 다른 후보"),
            "NO_NOTICE": ({}, "고시 근거가 없어요"),
        }
        for code, (params, expected) in samples.items():
            text = self.reason({"code": code, "level": "HOLD", "params": params})
            self.assertIn(expected, text, code)
            self.assertNotIn("candidates.", text, code)  # 키 이름이 그대로 나오지 않는다
        self.assertIn("18%", self.reason({"code": "MARGIN_OK", "level": "OK", "params": {"price": "12790", "rate": "0.18"}}))

    def test_only_the_two_most_important_reasons_show_and_the_rest_are_counted(self):
        reasons = [{"code": c, "level": "HOLD", "params": {}} for c in ("MARGIN_NOT_ANALYZED", "NO_NOTICE", "VARIANT_EXISTS", "MARGIN_ANALYSIS_UNFIT")]
        html = self.cell({"decision": "HOLD", "reasons": reasons})
        self.assertIn("마진 분석이 없어요", html)
        self.assertIn("고시 근거가 없어요", html)
        self.assertNotIn("구성만 다른 후보", html)
        self.assertIn("외 2건", html)

    def test_ok_reasons_are_shown_only_for_ready_candidates(self):
        ok = {"code": "MARGIN_OK", "level": "OK", "params": {"price": "12790", "rate": "0.18"}}
        self.assertIn("12,790원", self.cell({"decision": "READY", "reasons": [ok]}))
        self.assertNotIn("12,790원", self.cell({"decision": "HOLD", "reasons": [ok, {"code": "NO_NOTICE", "level": "INFO", "params": {}}]}))

    def test_unknown_codes_are_skipped_and_text_is_escaped(self):
        html = self.cell({"decision": "HOLD", "reasons": [
            {"code": "FUTURE_CODE", "level": "HOLD", "params": {}},
            {"code": "PROHIBITED_ITEM", "level": "BLOCK", "params": {"words": ["<script>x()</script>"]}},
        ]})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("FUTURE_CODE", html)
        self.assertNotIn("candidates.", html)

    def test_an_unknown_decision_still_draws_a_neutral_chip(self):
        self.assertIn('class="pill info"', self.cell({"decision": "WEIRD", "reasons": []}))


class WiringTests(unittest.TestCase):

    def test_the_list_loads_evaluations_but_a_failure_never_hides_the_candidates(self):
        body = function_source("loadCandidates")
        self.assertIn('apiFetch("/product-candidates/evaluations")', body)
        self.assertLess(body.index("candidatesCache = await fetchCandidates();"), body.index('apiFetch("/product-candidates/evaluations")'))
        evaluation = body.split('apiFetch("/product-candidates/evaluations")')[1].split("populateSourceFilter")[0]
        self.assertIn("catch", evaluation)
        self.assertIn("candidateEvalCache = new Map();", evaluation)

    def test_the_table_has_the_evaluation_column_and_the_empty_state_is_untouched(self):
        body = function_source("renderCandidatesTable")
        self.assertIn("<th>${colEval}</th>", body)
        self.assertIn("candidateEvalCellHtml(candidateEvalCache.get(r.id))", body)
        self.assertLess(body.index("candidates.empty"), body.index("colEval"))  # 기존 빈 화면 코드가 앞에 그대로 있다

    def test_the_evaluation_code_only_reads(self):
        code = function_source("candidateEvalReasonText") + function_source("candidateEvalCellHtml")
        for forbidden in ("api(", "apiFetch", "fetch(", "method:", "localStorage", "approve", "reject"):
            self.assertNotIn(forbidden, code, forbidden)


class TextTests(unittest.TestCase):

    def test_every_reason_kind_and_missing_item_the_rules_can_emit_has_a_text_in_both_languages(self):
        keys = ["col_eval", "eval_ready", "eval_hold", "eval_exclude", "eval_more"]
        keys += ["eval_r_" + k for k in ("prohibited", "brand_ip", "claim", "child", "regulated", "kc", "margin_none", "margin_hold", "margin_unfit", "margin_ok", "duplicate", "variant", "no_notice")]
        keys += ["eval_kind_" + kind.lower() for kind, _words in ev.REGULATED_WORDS]
        keys += ["eval_miss_supply", "eval_miss_vat", "eval_miss_shipping", "eval_miss_fee", "eval_miss_ai"]
        for source in (KO, EN):
            t = table(source)
            for key in keys:
                self.assertIn(f"candidates.{key}", t, key)

    def test_the_two_languages_have_the_same_evaluation_keys_and_placeholders(self):
        ko, en = table(KO), table(EN)
        keys = {k for k in ko if ".eval_" in k or k == "candidates.col_eval"}
        self.assertEqual(keys, {k for k in en if ".eval_" in k or k == "candidates.col_eval"})
        for key in keys:
            self.assertEqual(set(re.findall(r"\{(\w+)\}", ko[key])), set(re.findall(r"\{(\w+)\}", en[key])), key)
            names = re.findall(r"\{(\w+)\}", ko[key])
            self.assertEqual(len(names), len(set(names)), key)  # 같은 자리표시 두 번 금지(치환은 첫 번째만 바꾼다)

    def test_the_rule_modules_codes_are_all_handled_by_the_screen(self):
        handled = set(re.findall(r'case "([A-Z_]+)":', function_source("candidateEvalReasonText")))
        emitted = set(re.findall(r'_reason\("([A-Z_]+)"', (ROOT / "app/domains/product_candidate/candidate_evaluation.py").read_text(encoding="utf-8")))
        self.assertEqual(handled, emitted)

    def test_the_missing_item_names_cover_the_analysis_codes(self):
        self.assertEqual(set(re.findall(r"(\w+): \"eval_miss_", constants_source())), {"SUPPLY_PRICE", "VAT_STATE", "SHIPPING_COST", "CHANNEL_FEE", "AI_CONFIRM"})


if __name__ == "__main__":
    unittest.main()
