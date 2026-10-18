"""
=========================================================
Homez OS

File : tests/test_competitor_price_ui.py

2026-10-18 — 마케팅 AI M2(경쟁 가격 비교) 화면을 소스 수준에서 고정한다(바깥 호출 0). 실제 클릭 동작은 격리 서버 화면 시험으로 따로 확인한다.

  - 위치: URL로 가져오기 화면의 AI 분석 블록 바로 뒤에 붙고, 그 블록·기존 URL 가져오기 검사 범위는 건드리지 않는다.
  - 요청: 읽기는 확인 창을 먼저 거치고 주소는 중복 제거 후 1~5개, 비교는 가격이 1원 이상일 때만 보낸다. 서버 주소는 두 개뿐이고 둘 다 POST다.
  - 확인 표시: AI가 읽은 줄에만 "직접 확인했어요" 칸이 있고, 비교 요청에 그 값이 그대로 실린다. 직접 입력한 줄은 항상 확인 안 함으로 보낸다(서버가 직접 입력은 바로 센다).
  - 안전: 서버가 준 모든 글은 이스케이프한다. 저장소(localStorage 등)에 아무것도 남기지 않는다.
  - 문구: 한국어·영어 짝, 자리표시자 일치·중복 없음, 쓰지 않는 문구 없음. "표시가일 뿐 실제 판매 실적이 아니다"를 화면과 서버 둘 다 고정한다.
=========================================================
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")

START = "// ---- 경쟁 가격 비교 (2026-10-18, 마케팅 AI M2) ----"
END = "// ---- /경쟁 가격 비교 ----"


def block():
    start = CONSOLE.index(START)
    return CONSOLE[start:CONSOLE.index(END, start)]


class PlacementTests(unittest.TestCase):

    def test_the_block_follows_the_ai_analysis_block_and_hangs_off_the_url_import_view(self):
        self.assertLess(CONSOLE.index("// ---- /AI 분석 · 단가 · 이익 계산 ----"), CONSOLE.index(START))
        view = CONSOLE[CONSOLE.index("// ---- URL로 상품 가져오기 (2026-10-14) ----"):CONSOLE.index("  async function lwCreateNew() {")]
        self.assertIn('<div id="lw-aia-host"></div>\n      <div id="lw-cmp-host"></div>', view)
        self.assertIn('lwAiaAttach(el("lw-aia-host"), data);\n    lwCmpAttach(el("lw-cmp-host"));', view)
        self.assertNotIn("lwCmpAttach", block().split("function lwCmpAttach")[0].replace("// ---- 경쟁", ""))

    def test_the_legacy_url_import_window_stays_free_of_the_new_code(self):
        view = CONSOLE[CONSOLE.index("// ---- URL로 상품 가져오기 (2026-10-14) ----"):CONSOLE.index("  async function lwCreateNew() {")]
        self.assertNotIn("competitors", view)
        self.assertNotIn("cmpT", view)


class RequestTests(unittest.TestCase):

    def test_only_two_post_endpoints_are_used(self):
        src = block()
        self.assertEqual(sorted(set(re.findall(r'apiFetch\("(/[^"]+)"', src))), ["/product-import/competitors/compare", "/product-import/competitors/read"])
        self.assertEqual(src.count("apiFetch("), 2)
        self.assertEqual(src.count('method: "POST"'), 2)

    def test_reading_asks_first_after_validating_and_deduplicating_the_addresses(self):
        src = block()
        read = src[src.index('q("read").addEventListener'):src.index("const reportHtml")]
        self.assertLess(read.index("new Set("), read.index("askConfirm("))
        self.assertLess(read.index("urls.length > CMP_MAX_URLS"), read.index("askConfirm("))
        self.assertLess(read.index("askConfirm("), read.index("apiFetch("))
        self.assertIn("if (!ok) return;", read)
        self.assertIn("const CMP_MAX_URLS = 5;", src)
        self.assertIn("{ n: urls.length }", read)  # 확인 창에 주소 수(=AI 호출 수)를 알린다

    def test_comparison_needs_a_price_of_at_least_one_won_and_sends_the_confirmation_as_it_is(self):
        src = block()
        compare = src[src.index("compareBtn.addEventListener"):]
        self.assertLess(compare.index("price < 1"), compare.index("apiFetch("))
        self.assertIn("Number.isInteger(price)", compare)
        self.assertIn('confirmed: row.source === "AI_PAGE" ? !!row.confirmed : false', compare)
        self.assertIn("source: row.source", compare)
        self.assertIn("body: JSON.stringify({ entries })", compare)

    def test_the_confirmation_box_exists_only_on_ai_rows(self):
        src = block()
        self.assertIn('row.source === "AI_PAGE" ? `<label class="lw-checklist-item"><input type="checkbox" data-cmp-confirm', src)
        self.assertIn("source: \"AI_PAGE\", confirmed: false", src)  # 읽은 직후에는 항상 확인 전
        self.assertIn('source: "MANUAL", confirmed: false', src)

    def test_nothing_is_stored_in_the_browser_and_no_dynamic_code_runs(self):
        src = block()
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "eval(", "Function(", "window.open", "location."):
            self.assertNotIn(forbidden, src, forbidden)


class EscapingTests(unittest.TestCase):

    def test_every_server_text_is_escaped(self):
        src = block()
        for expr in (
            "escapeHtml(row.label || cmpT(\"manual_label\"))", "escapeHtml(row.url)", "escapeHtml(text)", "escapeHtml(row.label)", "escapeHtml(row.reason || \"\")",
            "escapeHtml(cmpWon(row.compared_price))", "escapeHtml(String(row.rank))", "escapeHtml(cmpT(\"per_volume\"", "escapeHtml(value === undefined", "escapeHtml(cmpT(`ev_${key}`))",
        ):
            self.assertIn(expr, src, expr)
        # 보간 중에서 서버/입력 값으로 시작하는 것은 모두 escapeHtml 안에 있다
        # 읽기 실패 줄(notes.push)은 글로 모았다가 화면에 그릴 때 한 번에 이스케이프한다
        self.assertIn('q("notes").innerHTML = notes.map((text) => `<p class="stat-sub">${escapeHtml(text)}</p>`).join("");', src)
        src = re.sub(r"notes\.push\(`[^`]*`\);", "", src)
        raw = re.findall(r"\$\{(?!escapeHtml|cmpT|cmpWon|index|row\.included|row\.source|row\.url|row\.confirmed|reportHtml|head|piece|perVolume|entries|report\.|\(row\.notes|Object\.entries|fieldHtml|attrs|notes|rows)([^}]*)\}", src)
        self.assertEqual([r for r in raw if re.match(r"\s*(row|result|data|err|text|report|market)\b", r)], [])

    def test_the_comparison_notes_come_from_the_server_and_are_escaped(self):
        src = block()
        self.assertIn("(report.notes || []).map((text) => `<p class=\"stat-sub\">${escapeHtml(text)}</p>`)", src)
        self.assertIn("(report.warnings || []).map((text) => `<p class=\"stat-sub\">${escapeHtml(text)}</p>`)", src)


class TextTests(unittest.TestCase):

    LITERAL = None

    def used(self):
        found = set(re.findall(r'cmpT\("([a-z_]+)"', block()))
        # 조건식 안의 글자 그대로의 열쇠(cmpT(조건 ? "a" : "b"))도 쓰는 문구로 센다
        for first, second in re.findall(r'cmpT\([^()]*?\?\s*"([a-z_]+)"\s*:\s*"([a-z_]+)"\)', block()):
            found |= {first, second}
        return sorted(found)

    def defined(self, source):
        return set(re.findall(r'"lw\.cmp_([a-z_]+)":', source))

    def test_every_used_text_exists_in_both_languages_with_matching_placeholders_and_no_repeats(self):
        keys = set(self.used()) | {f"ev_{k}" for k in ("price", "shipping_fee", "pack_count", "unit_volume")}
        for key in keys:
            kv = re.search(rf'"lw\.cmp_{key}": "((?:[^"\\]|\\.)*)"', KO)
            ev = re.search(rf'"lw\.cmp_{key}": "((?:[^"\\]|\\.)*)"', EN)
            self.assertTrue(kv and ev, key)
            names_ko, names_en = re.findall(r"\{(\w+)\}", kv.group(1)), re.findall(r"\{(\w+)\}", ev.group(1))
            self.assertEqual(set(names_ko), set(names_en), key)
            self.assertEqual(len(names_ko), len(set(names_ko)), key)  # 같은 자리표시자를 두 번 쓰면 첫 번째만 바뀐다

    def test_no_defined_text_is_unused(self):
        evidence_keys = {f"ev_{k}" for k in ("price", "shipping_fee", "pack_count", "unit_volume")}
        used = set(self.used()) | evidence_keys
        self.assertEqual(self.defined(KO), used)
        self.assertEqual(self.defined(EN), used)
        evidence_in_js = re.search(r'\["price", "shipping_fee", "pack_count", "unit_volume"\]\.forEach', block())
        self.assertIsNotNone(evidence_in_js)  # 동적 문구 열쇠(ev_*)가 가리키는 항목과 일치

    def test_the_screen_and_the_server_both_say_these_are_displayed_prices_not_sales(self):
        self.assertIn("실제 판매 실적이 아니에요", KO)
        self.assertIn("not actual sales results", EN)
        from app.domains.product_import.competitor_price_stats import NOTES

        self.assertIn("실제 판매 실적이 아니에요", NOTES[0])

    def test_the_money_unit_and_the_search_page_warning_are_in_both_languages(self):
        self.assertIn('"lw.cmp_won": "원"', KO)
        self.assertIn('"lw.cmp_won": " KRW"', EN)
        self.assertIn("검색 결과 페이지는 넣지 마세요", KO)
        self.assertIn("Do not enter search result pages", EN)


if __name__ == "__main__":
    unittest.main()
