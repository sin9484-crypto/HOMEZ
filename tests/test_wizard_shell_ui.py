"""
=========================================================
Homez OS

File : tests/test_wizard_shell_ui.py

등록 화면 껍데기(2026-10-15, UI 개선 4단계)를 고정한다 — 상품 머리글, 왼쪽 단계 목록(완료 표시·이동), 오른쪽 "이번 단계 확인",
"초안 저장", 5단계 탭(배송·반품 / 채널 상품정보 / 옵션·고시), 입력 정보 출처 칩. 모두 화면 표시용이며 저장·승인·전송 동작은 바꾸지
않는다. 실제 `console.js`의 순수 함수를 Node에서 실행하고, DOM이 필요한 부분은 소스로 확인한다. 네트워크·DB 없음.
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
HTML = (ROOT / "app/web/console.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
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
    for m in re.finditer(r'^\s*"(lw\.[A-Za-z0-9_.]+)":\s*(".*"),?\s*$', KO, re.M):
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
const LW_STEPS = ["SOURCE", "DRAFT", "MEDIA", "CHANNELS", "FULFILLMENT", "ECONOMICS", "PRECHECK", "APPROVAL", "EXECUTION", "RESULTS"];
const lwState = JSON.parse(process.env.STATE_JSON);
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

SOURCES = ("lwFurthestStepIndex", "lwStepPlainLabel", "lwStepperHtml", "lwCheckPanelHtml")


def state(**overrides):
    base = {"wizard": {"id": 21, "status": "DRAFT", "current_step": "FULFILLMENT", "validation_result": None}, "viewingStep": "FULFILLMENT"}
    wizard_overrides = overrides.pop("wizard", {})
    base["wizard"].update(wizard_overrides)
    base.update(overrides)
    return base


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class PureFunctionTests(unittest.TestCase):

    def run_js(self, expression, st=None):
        sources = "\n".join(function_source(n) for n in SOURCES)
        env = {**os.environ, "I18N_JSON": json.dumps(i18n_table()), "STATE_JSON": json.dumps(st or state())}
        proc = subprocess.run(
            [NODE, "-e", HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_steps_before_the_furthest_are_done_and_clickable_and_later_ones_are_not(self):
        html = self.run_js("lwStepperHtml()")
        self.assertEqual(html.count("is-done"), 4)
        self.assertEqual(html.count("is-current"), 1)
        # 이동 버튼은 이미 도달한 단계(1~4)에만 있다 — 아직 오지 않은 단계로는 건너뛰지 못한다.
        self.assertEqual(re.findall(r'data-lw-stepper-go="([A-Z]+)"', html), ["SOURCE", "DRAFT", "MEDIA", "CHANNELS"])
        self.assertIn('aria-current="step"', html)

    def test_viewing_an_earlier_step_keeps_later_reached_steps_clickable(self):
        html = self.run_js("lwStepperHtml()", state(viewingStep="MEDIA"))
        self.assertIn('data-lw-stepper-go="FULFILLMENT"', html)
        self.assertNotIn('data-lw-stepper-go="MEDIA"', html)

    def test_a_finished_registration_shows_every_step_done(self):
        html = self.run_js("lwStepperHtml()", state(wizard={"status": "SUCCEEDED", "current_step": "RESULTS"}, viewingStep="RESULTS"))
        self.assertEqual(html.count("is-done"), 10)

    def test_the_panel_shows_only_this_steps_issues_and_marks_blocking_ones(self):
        issues = [
            {"step": "FULFILLMENT", "blocking": True, "code": "A", "localized_message_key": "lw.tab_logistics"},
            {"step": "ECONOMICS", "blocking": True, "code": "B", "localized_message_key": "lw.tab_product"},
            {"step": "FULFILLMENT", "blocking": False, "code": "C", "localized_message_key": "lw.tab_options"},
        ]
        html = self.run_js("lwCheckPanelHtml()", state(wizard={"validation_result": {"status": "NEEDS_CORRECTION", "issues": issues}}))
        self.assertIn("is-blocking", html)
        self.assertIn("is-note", html)
        self.assertIn("배송·반품", html)
        self.assertIn("옵션·고시", html)
        self.assertNotIn("채널 상품정보", html)  # 다른 단계의 이슈는 보이지 않는다
        self.assertIn("마지막 사전검사 결과 기준", html)

    def test_no_precheck_yet_is_stated_honestly_not_as_all_clear(self):
        html = self.run_js("lwCheckPanelHtml()")
        self.assertIn("아직 사전검사 전", html)
        self.assertNotIn("확인할 항목이 없습니다", html)

    def test_a_precheck_with_nothing_for_this_step_says_so_with_its_basis(self):
        html = self.run_js("lwCheckPanelHtml()", state(wizard={"validation_result": {"status": "READY_FOR_APPROVAL", "issues": []}}))
        self.assertIn("확인할 항목이 없습니다(마지막 사전검사 기준)", html)

    def test_the_source_legend_appears_only_in_the_sales_method_step(self):
        self.assertIn("입력 정보 출처", self.run_js("lwCheckPanelHtml()"))
        self.assertNotIn("입력 정보 출처", self.run_js("lwCheckPanelHtml()", state(viewingStep="MEDIA")))

    def test_the_next_step_is_named_except_after_the_last(self):
        self.assertIn("가격·마진", self.run_js("lwCheckPanelHtml()").replace("가격 · 마진", "가격·마진"))
        self.assertNotIn("다음 단계", self.run_js("lwCheckPanelHtml()", state(viewingStep="RESULTS")))

    def test_server_text_is_escaped(self):
        issues = [{"step": "FULFILLMENT", "blocking": True, "code": "<img src=x onerror=1>", "localized_message_key": ""}]
        html = self.run_js("lwCheckPanelHtml()", state(wizard={"validation_result": {"issues": issues}}))
        self.assertNotIn("<img", html)


class WiringTests(unittest.TestCase):

    def test_the_existing_controls_and_ids_are_still_the_ones_that_save_and_move(self):
        for needle in ('id="lw-step-content"', 'id="lw-prev-btn"', 'id="lw-next-btn"', 'id="lw-close-btn"', 'id="lw-save-status"',
                       'id="lw-progress-fill"', 'id="lw-step-indicator"', 'id="lw-status-pill"'):
            self.assertIn(needle, HTML)
        for needle in ('id="lw-product-bar"', 'id="lw-stepper"', 'id="lw-check-panel"', 'id="lw-save-draft-btn"'):
            self.assertIn(needle, HTML)

    def test_the_shell_refreshes_on_every_step_change_and_never_saves_by_itself(self):
        self.assertIn("lwRenderShellChrome();", function_source("lwUpdateStepIndicator"))
        chrome = function_source("lwRenderShellChrome")
        for forbidden in ("apiFetch", "lwSaveCurrentStep(", "PATCH", "POST"):
            self.assertNotIn(forbidden, chrome)
        self.assertIn("lwJumpToStep(btn.dataset.lwStepperGo)", chrome)

    def test_save_draft_uses_the_same_silent_save_as_autosave_and_only_when_the_step_has_one(self):
        body = function_source("lwHandleSaveDraft")
        self.assertIn("lwSaveCurrentStep({ silent: true })", body)
        self.assertIn('typeof lwSaveCurrentStep !== "function"', body)
        self.assertIn("lwNextInFlight", body)
        self.assertIn('hidden = typeof lwSaveCurrentStep !== "function"', function_source("lwRenderShellChrome"))

    def test_a_failed_save_reveals_the_tab_that_holds_the_error(self):
        nxt = function_source("lwHandleNext")
        self.assertLess(nxt.index("if (!ok) {"), nxt.index("lwSyncFulfillmentTabErrors("))
        sync = function_source("lwSyncFulfillmentTabErrors")
        self.assertIn(".field-error", sync)
        self.assertIn("lwSetFulfillmentTab(block", sync)

    def test_tabs_only_hide_never_move_or_remove_fields(self):
        apply_ = function_source("lwApplyFulfillmentTabs") + function_source("lwSetFulfillmentTab")
        for forbidden in ("removeChild", ".remove()", "appendChild", "innerHTML = \"\"", ".value ="):
            self.assertNotIn(forbidden, apply_)
        self.assertIn("lw-tab-off", apply_)
        self.assertIn(".lw-tab-off { display: none !important; }", CSS)

    def test_every_tab_target_selector_exists_in_the_fulfillment_markup(self):
        render = function_source("lwRenderFulfillmentStep") + function_source("lwRenderBrandStatePanel")
        targets = function_source("lwFulfillmentTabTargets")
        for selector in (".lw-logistics-panel", ".lw-coupang-charges-panel", ".lw-live-image-panel", ".lw-contents-panel",
                         ".lw-brand-state-panel", ".lw-channel-policy-panel", ".lw-policy-notice-confirmed"):
            self.assertIn(selector, targets)
            self.assertIn(selector.lstrip("."), render, f"{selector}가 5단계 마크업에 없다")
        for attr in ("data-lw-purchase-option-fields", "data-lw-item-combo-builder", "data-lw-notice-fields"):
            self.assertIn(attr, targets)
            self.assertIn(attr, render)

    def test_the_tabs_are_applied_after_the_step_renders_and_once_per_block(self):
        self.assertIn("lwApplyFulfillmentTabs(content);", function_source("lwRenderFulfillmentStep"))
        self.assertIn("block.dataset.lwTabsApplied", function_source("lwApplyFulfillmentTabs"))

    def test_the_source_chip_compares_with_the_saved_default_and_stays_hidden_without_one(self):
        body = function_source("lwWireSellerFixedDefaults")
        self.assertIn("input.dataset.lwFixedValue", body)
        self.assertIn("sourceChip.hidden = !fixed || !value;", body)
        self.assertIn("value === fixed", body)

    def test_the_next_button_now_says_it_saves(self):
        self.assertIn('"lw.next_btn": "저장 후 다음"', KO)
        self.assertIn('"lw.next_btn": "Save and next"', EN)

    def test_translations_exist_in_both_languages_and_are_all_used(self):
        pattern = r'"(lw\.(?:stepper_aria|save_draft_btn|check_panel_[a-z]+|source_[a-z_]+|tab_[a-z]+))"'
        ko, en = set(re.findall(pattern, KO)), set(re.findall(pattern, EN))
        self.assertEqual(ko, en)
        used = set(re.findall(pattern, JS)) | set(re.findall(r'data-i18n(?:-attr)?="(?:[a-z-]+:)?(lw\.(?:stepper_aria|save_draft_btn|check_panel_[a-z]+))"', HTML))
        self.assertEqual(ko - used, set(), "번역만 있고 쓰이지 않는 키")
        self.assertEqual(used - ko, set(), "쓰는데 번역이 없는 키")

    def test_responsive_and_focus_styles_exist(self):
        for needle in (".lw-shell-body", "@media (max-width: 760px)", ".lw-stepper-item > button:focus-visible", ".lw-wizard-nav { position: sticky"):
            self.assertIn(needle, CSS)


if __name__ == "__main__":
    unittest.main()
