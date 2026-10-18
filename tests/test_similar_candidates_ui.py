"""
=========================================================
Homez OS

File : tests/test_similar_candidates_ui.py

2026-10-18 — 상품 서칭 AI S1: URL 가져오기 결과의 "이미 비슷한 후보가 있어요" 알림 화면을 고정한다. 실제 `console.js`의 함수를 Node에서 실행한다. 네트워크·DB 없음.

  - 같은 상품/구성만 다른 상품/비슷한 상품과 이유(수량·용량·모델 번호 등)를 사람 말로 보여 주고, 글은 모두 이스케이프한다.
  - 알림은 읽기 전용이다 — 요청을 보내거나 후보를 바꾸지 않는다. 비어 있으면 아무것도 그리지 않는다.
  - 판정이 낼 수 있는 모든 이유·규격 이름이 한국어·영어 문구를 가진다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from app.domains.product_candidate import product_similarity as ps

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


def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.urlimp\.[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
function lwUiT(key, vars) { return HomezI18n.t(`lw.urlimp.${key}`, vars); }
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class RenderTests(unittest.TestCase):

    def run_js(self, expression):
        script = HARNESS.replace("__SOURCES__", function_source("lwUrlImportSimilarHtml")).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def html(self, items):
        return self.run_js(f"lwUrlImportSimilarHtml({json.dumps(items)})")

    def test_empty_or_missing_lists_draw_nothing(self):
        for value in ("[]", "null", "undefined", '"x"', "{}"):
            self.assertEqual(self.run_js(f"lwUrlImportSimilarHtml({value})"), "")

    def test_each_relation_status_and_reason_is_shown_in_plain_words(self):
        items = [
            {"candidate_id": 4, "product_name": "레이펄스 바디워시", "status": "APPROVED", "relation": "DUPLICATE", "core_score": 0.98, "reasons": ["NAME_NEAR_SAME", "SAME_SPECS"]},
            {"candidate_id": 5, "product_name": "레이펄스 바디워시 500ml", "status": "REJECTED", "relation": "VARIANT", "core_score": 1.0, "reasons": ["NAME_NEAR_SAME", "SPEC_DIFFERS:volume_ml", "SPEC_DIFFERS:count:piece"]},
            {"candidate_id": 6, "product_name": "비슷한 상품", "status": "", "relation": "SIMILAR", "core_score": 0.7, "reasons": ["NAME_PARTLY_SAME"]},
        ]
        html = self.html(items)
        for text in ("이미 비슷한 후보가 있어요", "같은 상품", "구성만 다른 상품", "비슷한 상품", "승인한 후보", "거절한 후보", "검토 중인 후보", "98%",
                     "이름이 거의 같아요", "구성·용량이 같아요", "용량이(가) 달라요", "수량이(가) 달라요", "이름이 일부 같아요", "#4", "#5", "#6"):
            self.assertIn(text, html, text)

    def test_every_text_from_the_candidate_is_escaped(self):
        html = self.html([{"candidate_id": "<i>1</i>", "product_name": "<script>x()</script>", "status": "<em>", "relation": "DUPLICATE", "core_score": 1, "reasons": ["<u>", "SPEC_DIFFERS:<s>"]}])
        for raw in ("<script>", "<i>1", "<em>", "<u>", "<s>"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;script&gt;x()&lt;/script&gt;", html)

    def test_unknown_relations_statuses_and_reason_codes_do_not_break_the_card(self):
        html = self.html([{"candidate_id": 1, "product_name": "x", "status": "NEW_STATUS", "relation": "WEIRD", "core_score": None, "reasons": ["FUTURE_CODE"]}])
        self.assertIn("비슷한 상품", html)       # 알 수 없는 관계는 가장 약한 표현으로
        self.assertIn("검토 중인 후보", html)    # 알 수 없는 상태는 일반 표현으로
        self.assertIn("0%", html)
        self.assertNotIn("lw.urlimp.", html)     # 문구가 없는 이유 코드의 키 이름을 화면에 내보내지 않는다

    def test_the_notice_says_it_changes_nothing(self):
        self.assertIn("후보는 바뀌지 않아요", table(KO)["lw.urlimp.sim_hint"])


class WiringTests(unittest.TestCase):

    def test_the_card_is_part_of_the_url_search_result_and_makes_no_request(self):
        self.assertIn("${lwUrlImportSimilarHtml(data.similar_candidates)}", function_source("lwUrlImportRender"))
        code = function_source("lwUrlImportSimilarHtml")
        for forbidden in ("api(", "apiFetch", "fetch(", "method:", "localStorage", "/product-candidates"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_the_card_is_inside_the_same_page_scoped_window_the_existing_import_tests_guard(self):
        window = JS.split("// ---- URL로 상품 가져오기 (2026-10-14) ----")[1].split("  async function lwCreateNew() {")[0]
        self.assertIn("function lwUrlImportSimilarHtml(", window)
        for token in ("confirm-rights", "brand", "notice_confirmed", "rights_status"):
            self.assertNotIn(token, window.lower().replace("brandstate", ""))


class TextTests(unittest.TestCase):

    def test_every_reason_and_spec_the_judgement_can_produce_has_a_text_in_both_languages(self):
        codes = ["NAME_NEAR_SAME", "NAME_PARTLY_SAME", "SAME_MODEL", "SAME_SPECS", "NO_SPEC_INFO"]
        sample = ps.normalize("a 1개 2매 3롤 4켤레 5병 6캔 7박스 8ml 9g 10cm 블랙 xl ab1234")
        specs = [k.replace(":", "_") for k in sample.specs] + ["model"]
        for lang, source in (("ko", KO), ("en", EN)):
            t = table(source)
            for code in codes:
                self.assertIn(f"lw.urlimp.sim_why_{code.lower()}", t, (lang, code))
            self.assertIn("lw.urlimp.sim_why_differs", t)
            for spec in specs:
                self.assertIn(f"lw.urlimp.sim_spec_{spec}", t, (lang, spec))
            for key in ("sim_title", "sim_hint", "sim_rel_duplicate", "sim_rel_variant", "sim_rel_similar", "sim_st_accepted", "sim_st_held", "sim_st_rejected", "sim_st_other"):
                self.assertIn(f"lw.urlimp.{key}", t, (lang, key))

    def test_the_two_languages_have_the_same_similarity_keys_and_placeholders(self):
        ko, en = table(KO), table(EN)
        keys_ko = {k for k in ko if ".sim_" in k}
        self.assertEqual(keys_ko, {k for k in en if ".sim_" in k})
        for key in keys_ko:
            self.assertEqual(set(re.findall(r"\{(\w+)\}", ko[key])), set(re.findall(r"\{(\w+)\}", en[key])), key)

    def test_every_spec_key_the_normalizer_can_emit_is_covered(self):
        emitted = {f"count:{unit}" for unit in ("piece", "sheet", "roll", "pair", "bottle", "can", "box")} | {"volume_ml", "weight_g", "length_mm", "colors", "size", "model"}
        sample = ps.normalize("a 1개 2매 3롤 4켤레 5병 6캔 7박스 8ml 9g 10cm 블랙 xl ab1234")
        self.assertEqual(set(sample.specs) | {"model"}, emitted)


if __name__ == "__main__":
    unittest.main()
