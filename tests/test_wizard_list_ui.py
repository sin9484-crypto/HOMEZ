"""
=========================================================
Homez OS

File : tests/test_wizard_list_ui.py

등록 목록 화면(2026-10-15, UI 개선 3단계)을 고정한다 — 사진·상품명·공급처·판매채널·단계(n/10)·확인사항, 행을 고르면 열리는
오른쪽 등록 진행 패널. 실제 `console.js`의 순수 함수를 Node에서 실행하고, 배선은 소스로 확인한다. 네트워크·DB 없음.
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
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
HTML = (ROOT / "app/web/console.html").read_text(encoding="utf-8")
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
    for text in (KO,):
        for m in re.finditer(r'^\s*"(lw\.[A-Za-z0-9_.]+)":\s*(".*"),?\s*$', text, re.M):
            table[m.group(1)] = json.loads(m.group(2))
    return table


HARNESS = r"""
const HomezI18n = { t(key, params) {
  const table = JSON.parse(process.env.I18N_JSON);
  let text = table[key] !== undefined ? table[key] : key;
  Object.keys(params || {}).forEach((k) => { text = text.split("{" + k + "}").join(String(params[k])); });
  return text;
} };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const lwStatusLabel = (s) => HomezI18n.t("lw.status." + String(s).toLowerCase());
const LW_ATTENTION_WARN = new Set(["NEEDS_CORRECTION", "SUBMIT_FAILED", "PARTIALLY_SUCCEEDED", "NO_IMAGE_SELECTED", "NO_CHANNEL", "NO_ECONOMICS"]);
const LW_STEP_KEYS = ["source", "draft", "media", "channels", "fulfillment", "economics", "precheck", "approval", "execution", "results"];
__SOURCES__
const row = JSON.parse(process.env.ROW_JSON);
console.log(JSON.stringify(__EXPR__));
"""

SOURCES = ("lwThumbHtml", "lwAttentionLabel", "lwAttentionChipsHtml", "lwRowProductName", "lwChannelLabel", "lwStepPlainLabel", "lwProgressPanelHtml")


def row(**overrides):
    base = {
        "id": 14, "status": "DRAFT", "current_step": "FULFILLMENT", "product_name": "방수 생리팬티", "supplier_label": "온채널",
        "channels": [{"code": "COUPANG", "account_name": "에브리홈즈"}], "step_number": 5, "step_total": 10,
        "thumbnail_asset_id": 136, "attention": [],
    }
    base.update(overrides)
    return base


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class PureFunctionTests(unittest.TestCase):

    def run_js(self, expression, data=None):
        sources = "\n".join(function_source(n) for n in SOURCES)
        env = {**os.environ, "I18N_JSON": json.dumps(i18n_table()), "ROW_JSON": json.dumps(data or row())}
        proc = subprocess.run(
            [NODE, "-e", HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_product_name_falls_back_to_the_id_and_never_to_a_guess(self):
        self.assertEqual(self.run_js("lwRowProductName(row)"), "방수 생리팬티")
        self.assertEqual(self.run_js("lwRowProductName(row)", row(product_name=None)), "#14")

    def test_channels_show_codes_or_a_dash(self):
        self.assertEqual(self.run_js("lwChannelLabel(row)"), "COUPANG")
        self.assertEqual(self.run_js("lwChannelLabel(row)", row(channels=[])), "—")

    def test_the_step_label_drops_its_number_prefix(self):
        self.assertEqual(self.run_js("lwStepPlainLabel('fulfillment')"), "판매 방식")

    def test_warnings_and_info_chips_are_styled_differently_and_nothing_gets_a_calm_message(self):
        html = self.run_js("lwAttentionChipsHtml(row)", row(attention=["NO_CHANNEL", "AWAITING_APPROVAL"]))
        self.assertIn("lw-attn-warn", html)
        self.assertIn("lw-attn-info", html)
        self.assertIn("판매채널 선택 필요", html)
        self.assertIn("확인할 항목 없음", self.run_js("lwAttentionChipsHtml(row)"))

    def test_an_unknown_attention_code_is_shown_as_is_not_hidden(self):
        self.assertIn("SOMETHING_NEW", self.run_js("lwAttentionChipsHtml(row)", row(attention=["SOMETHING_NEW"])))

    def test_the_progress_panel_marks_done_current_and_todo(self):
        html = self.run_js("lwProgressPanelHtml(row)")
        self.assertEqual(html.count("lw-prog-done"), 4)
        self.assertEqual(html.count('aria-current="step"'), 1)
        self.assertEqual(html.count("lw-prog-todo"), 5)
        self.assertIn("5 / 10", html)
        self.assertIn('data-lw-panel-open="14"', html)

    def test_a_finished_registration_shows_every_step_done(self):
        html = self.run_js("lwProgressPanelHtml(row)", row(status="SUCCEEDED", current_step="RESULTS", step_number=10))
        self.assertEqual(html.count("lw-prog-done"), 10)
        self.assertIn("10 / 10", html)

    def test_an_archived_item_offers_no_open_button(self):
        self.assertNotIn("data-lw-panel-open", self.run_js("lwProgressPanelHtml(row)", row(status="ARCHIVED")))

    def test_server_text_is_escaped(self):
        html = self.run_js("lwProgressPanelHtml(row)", row(product_name="<img src=x onerror=alert(1)>", supplier_label="<b>x</b>"))
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>", html)

    def test_a_missing_picture_gets_an_empty_placeholder_not_a_broken_image(self):
        html = self.run_js("lwThumbHtml(row)", row(thumbnail_asset_id=None))
        self.assertIn("lw-thumb-empty", html)
        self.assertNotIn("data-lw-thumb=", html)


class WiringTests(unittest.TestCase):

    def test_the_list_keeps_selection_delete_restore_and_open_controls(self):
        body = function_source("lwRenderList")
        for needle in ("lw-row-checkbox", "data-lw-restore", "data-lw-delete", "data-lw-open", "lw-select-all-checkbox"):
            self.assertIn(needle, body)

    def test_rows_and_product_names_open_the_panel_but_buttons_do_not_double_fire(self):
        body = function_source("lwRenderList")
        self.assertIn("data-lw-select", body)
        self.assertIn('event.target.closest("button, input, a, label, select")', body)
        self.assertIn("lwShowProgressPanel(", body)

    def test_pictures_load_only_when_visible_and_use_the_authenticated_download(self):
        body = function_source("lwHydrateThumbs")
        self.assertIn("IntersectionObserver", body)
        self.assertIn("apiFetchBlob(`/media-assets/${assetId}/file`)", body)
        self.assertIn("catch", body)  # 권한 없음·사진 없음은 빈 칸으로 둔다

    def test_each_area_has_its_own_observer_so_redrawing_one_does_not_stop_the_others(self):
        # 패널을 열 때 목록의 사진 로딩이 끊기던 결함(2026-10-15 실화면에서 발견)을 고정한다.
        self.assertIn("const lwThumbObservers = new WeakMap();", JS)
        body = function_source("lwHydrateThumbs")
        self.assertIn("lwThumbObservers.get(root)", body)
        self.assertIn("lwThumbObservers.set(root, observer)", body)
        self.assertNotIn("lwThumbObserver ", JS.replace("lwThumbObservers", ""))

    def test_the_picture_cache_is_bounded_and_released(self):
        body = function_source("lwThumbCacheSet")
        self.assertIn("LW_THUMB_CACHE_MAX", body)
        self.assertIn("URL.revokeObjectURL", body)

    def test_the_layout_slot_and_panel_exist_in_the_page(self):
        self.assertIn('id="lw-list-layout"', HTML)
        self.assertIn('id="lw-progress-panel"', HTML)
        self.assertIn(".lw-list-layout.panel-open", CSS)
        self.assertIn(":focus-visible", CSS)

    def test_translations_exist_in_both_languages_and_are_all_used(self):
        pattern = r'"(lw\.(?:list_col_product|list_col_supplier|list_col_channel|list_col_attention|list_none_attention|list_progress_title|list_open_wizard|list_close_panel|attention\.[a-z_]+))"'
        ko, en = set(re.findall(pattern, KO)), set(re.findall(pattern, EN))
        self.assertEqual(ko, en)
        used = set(re.findall(pattern, JS))
        used |= {f"lw.attention.{c.lower()}" for c in (
            "NEEDS_CORRECTION", "SUBMIT_FAILED", "PARTIALLY_SUCCEEDED", "NO_IMAGE_SELECTED", "NO_CHANNEL", "NO_ECONOMICS",
            "AWAITING_APPROVAL", "READY_TO_SUBMIT",
        )}
        self.assertEqual(used - ko, set(), "쓰는데 번역이 없는 키")
        self.assertEqual(ko - used, set(), "번역만 있고 쓰이지 않는 키")

    def test_every_attention_code_the_server_can_send_has_a_label(self):
        server = (ROOT / "app/domains/marketplace_listing/listing_wizard_list_enrichment.py").read_text(encoding="utf-8")
        codes = re.findall(r'^ATTENTION_[A-Z_]+ = "([A-Z_]+)"', server, re.M)
        self.assertGreaterEqual(len(codes), 8)
        for code in codes:
            self.assertIn(f'"lw.attention.{code.lower()}"', KO, code)
            self.assertIn(f'"lw.attention.{code.lower()}"', EN, code)


if __name__ == "__main__":
    unittest.main()
