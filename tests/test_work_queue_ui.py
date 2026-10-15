"""
=========================================================
Homez OS

File : tests/test_work_queue_ui.py

오늘 할 일 > 전체 업무(2026-10-15, UI 개선 2단계)를 고정한다. 주문 예외·정산 차이·AI 제안·운영 우선순위·진행 중인 등록 작업을
"확인 필요 / 승인 대기 / 처리 중"으로 한 목록에 모은다. 새 판정을 만들지 않고 원본의 값을 옮길 뿐이다.
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


def const_source(name: str) -> str:
    match = re.search(r"  const " + re.escape(name) + r"\s*=", JS)
    assert match, name
    start = match.start()
    depth, i = 0, match.end()
    while True:
        ch = JS[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif ch == ";" and depth == 0:
            return JS[start: i + 1]
        i += 1


def i18n_table() -> dict:
    table = {}
    for m in re.finditer(r'^\s*"((?:wq|lw|nav|ai_proposals)\.[A-Za-z0-9_.]+)":\s*(".*"),?\s*$', KO, re.M):
        table[m.group(1)] = json.loads(m.group(2))
    return table


HARNESS = r"""
const HomezI18n = { t(key, params) {
  const table = JSON.parse(process.env.I18N_JSON);
  let text = table[key] !== undefined ? table[key] : key;
  Object.keys(params || {}).forEach((k) => { text = text.split("{" + k + "}").join(String(params[k])); });
  return text;
} };
const AIP_OPEN_STATUSES = new Set(["DRAFT", "REVIEW_REQUIRED", "APPROVED"]);
const aipStatusLabel = (s) => "상태:" + s;
const lwAttentionLabel = (c) => "확인:" + c;
__SOURCES__
const src = JSON.parse(process.env.SRC_JSON);
console.log(JSON.stringify(__EXPR__));
"""

CONSTS = ("WQ_RESULT_STATUS", "WQ_STATE_ORDER", "WQ_URGENCY_ORDER", "WQ_PRIORITY_DUPLICATES", "WQ_PRIORITY_VIEWS", "WQ_OPEN_WIZARD_STATUS", "WQ_WIZARD_WARN")
FUNCS = ("wqUrgency", "wqBuildItems", "wqCounts", "wqTitle")


def exception(**o):
    base = {"exception_type": "OUT_OF_STOCK", "urgency": "HIGH", "target_entity": "order_item:1", "evidence": "재고 부족",
            "elapsed_hours": 3, "recommended_action": "ORDER_EXCEPTION_REVIEW"}
    base.update(o)
    return base


def wizard(**o):
    base = {"id": 14, "status": "DRAFT", "current_step": "MEDIA", "product_name": "방수 생리팬티", "supplier_label": "온채널",
            "step_number": 3, "step_total": 10, "thumbnail_asset_id": 136, "attention": [], "deleted_at": None}
    base.update(o)
    return base


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BuildTests(unittest.TestCase):

    def run_js(self, expression, src):
        sources = "\n".join(const_source(n) for n in CONSTS) + "\n" + "\n".join(function_source(n) for n in FUNCS)
        env = {**os.environ, "I18N_JSON": json.dumps(i18n_table()), "SRC_JSON": json.dumps(src)}
        proc = subprocess.run(
            [NODE, "-e", HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def items(self, src):
        return self.run_js("wqBuildItems(src)", src)

    def test_exceptions_and_differences_need_a_check(self):
        items = self.items({"exceptions": {"exceptions": [exception()]}, "differences": {"differences": [exception(exception_type="AMOUNT_MISMATCH")]}})
        self.assertEqual([(i["kind"], i["state"]) for i in items], [("exception", "CHECK"), ("difference", "CHECK")])
        self.assertEqual(items[0]["next"]["view"], "order-exceptions")
        self.assertEqual(items[1]["next"]["view"], "settlement-differences")

    def test_open_ai_proposals_are_awaiting_approval_and_approved_ones_are_in_progress(self):
        rows = [{"id": 1, "status": "DRAFT", "action_type": "A"}, {"id": 2, "status": "REVIEW_REQUIRED", "action_type": "B"},
                {"id": 3, "status": "APPROVED", "action_type": "C"}, {"id": 4, "status": "EXECUTED", "action_type": "D"},
                {"id": 5, "status": "REJECTED", "action_type": "E"}]
        items = self.items({"proposals": rows})
        self.assertEqual({i["key"]: i["state"] for i in items}, {"proposal:1": "APPROVAL", "proposal:2": "APPROVAL", "proposal:3": "PROGRESS"})
        self.assertEqual(items[0]["next"], {"view": "ai-proposal-detail", "opts": {"id": 1}, "labelKey": "wq.next_proposal"})

    def test_priority_lines_that_repeat_the_itemized_sources_are_dropped(self):
        priorities = {"items": [
            {"category": "ORDER_SHIPMENT_RETURN_EXCEPTIONS", "urgency": "HIGH", "label": "주문 예외 1건"},
            {"category": "SETTLEMENT_DIFFERENCES", "urgency": "HIGH", "label": "정산 차이 1건"},
            {"category": "PRODUCT_INPUT_REQUIRED", "urgency": "MEDIUM", "label": "판매 판단 대기 상품 6건"},
        ]}
        items = self.items({"priorities": priorities})
        self.assertEqual([i["key"] for i in items], ["priority:PRODUCT_INPUT_REQUIRED"])
        self.assertEqual(items[0]["next"]["view"], "candidates")

    def test_wizard_states_map_to_the_three_groups(self):
        rows = [wizard(id=1, status="DRAFT"), wizard(id=2, status="NEEDS_CORRECTION"), wizard(id=3, status="READY_FOR_APPROVAL"),
                wizard(id=4, status="APPROVED"), wizard(id=5, status="FAILED"), wizard(id=6, status="SUCCEEDED"),
                wizard(id=7, status="ARCHIVED"), wizard(id=8, status="DRAFT", deleted_at="2026-10-01T00:00:00")]
        states = {i["key"]: i["state"] for i in self.items({"wizards": rows})}
        self.assertEqual(states, {"wizard:1": "PROGRESS", "wizard:2": "CHECK", "wizard:3": "APPROVAL", "wizard:4": "PROGRESS", "wizard:5": "CHECK"})

    def test_a_draft_with_a_warning_needs_a_check_and_shows_its_product(self):
        item = self.items({"wizards": [wizard(attention=["NO_IMAGE_SELECTED"])]})[0]
        self.assertEqual((item["state"], item["urgency"]), ("CHECK", "MEDIUM"))
        self.assertEqual(item["product"], {"name": "방수 생리팬티", "thumbAssetId": 136, "supplier": "온채널"})
        self.assertEqual(item["stepText"], "3 / 10")
        self.assertEqual(item["next"]["wizardId"], 14)

    def test_a_failed_or_partial_registration_asks_for_the_result_not_for_more_editing(self):
        items = self.items({"wizards": [wizard(id=1, status="FAILED", current_step="RESULTS", step_number=10),
                                        wizard(id=2, status="PARTIALLY_SUCCEEDED", current_step="RESULTS", step_number=10),
                                        wizard(id=3, status="DRAFT")]})
        by_key = {i["key"]: i for i in items}
        self.assertEqual(by_key["wizard:1"]["next"]["labelKey"], "wq.next_result")
        self.assertEqual(by_key["wizard:2"]["next"]["labelKey"], "wq.next_result")
        self.assertEqual(by_key["wizard:3"]["next"]["labelKey"], "wq.next_wizard")
        titles = self.run_js("wqBuildItems(src).map(wqTitle)", {"wizards": [wizard(id=1, status="FAILED", current_step="RESULTS", step_number=10)]})
        self.assertEqual(titles, ["상품 등록 결과 확인 필요 · 10 / 10"])
        self.assertNotIn("진행 중", titles[0])

    def test_a_product_without_a_name_does_not_repeat_its_id_as_the_subtitle(self):
        body = function_source("wqTargetHtml")
        self.assertIn("item.target === item.product.name", body)

    def test_items_are_sorted_check_first_then_urgency(self):
        src = {"exceptions": {"exceptions": [exception(urgency="LOW", target_entity="a"), exception(urgency="HIGH", target_entity="b")]},
               "proposals": [{"id": 9, "status": "DRAFT", "action_type": "X"}], "wizards": [wizard(id=3)]}
        order = [(i["state"], i["urgency"]) for i in self.items(src)]
        self.assertEqual(order, [("CHECK", "HIGH"), ("CHECK", "LOW"), ("APPROVAL", "MEDIUM"), ("PROGRESS", "LOW")])

    def test_a_source_that_failed_to_load_simply_contributes_nothing(self):
        self.assertEqual(self.items({"exceptions": None, "differences": None, "proposals": None, "priorities": None, "wizards": None}), [])
        self.assertEqual(self.items({}), [])

    def test_an_unknown_urgency_is_treated_as_low_not_dropped(self):
        item = self.items({"exceptions": {"exceptions": [exception(urgency="WEIRD")]}})[0]
        self.assertEqual(item["urgency"], "LOW")

    def test_counts_and_titles(self):
        src = {"exceptions": {"exceptions": [exception()]}, "proposals": [{"id": 1, "status": "DRAFT", "action_type": "X"}], "wizards": [wizard()]}
        self.assertEqual(self.run_js("wqCounts(wqBuildItems(src))", src), {"CHECK": 1, "APPROVAL": 1, "PROGRESS": 1})
        self.assertEqual(self.run_js("wqBuildItems(src).map(wqTitle)", src), ["OUT_OF_STOCK", "X", "상품 등록 진행 중 · 3 / 10"])


class WiringTests(unittest.TestCase):

    def test_the_screen_is_registered_and_reachable(self):
        self.assertIn('"work-queue": loadWorkQueue,', JS)
        self.assertIn('id="view-work-queue"', HTML)
        self.assertIn('data-view="work-queue" data-permission="__admin_only__"', HTML)
        self.assertRegex(JS, r'tabs: \[\s*\["work-queue", "nav\.work_queue"\]')

    def test_each_source_is_read_only_and_a_failure_in_one_does_not_hide_the_rest(self):
        body = function_source("loadWorkQueue")
        self.assertIn("Promise.allSettled", body)
        for path in ("/orchestration/priorities", "/orders/exception-analysis", "/settlements/difference-analysis",
                     "/ai-governance/proposed-actions", "/listing-wizards"):
            self.assertIn(f'"{path}"', body)
        self.assertNotIn('method: "POST"', body)
        self.assertNotIn('method: "PUT"', body)
        self.assertIn("wqState.failed.push", body)

    def test_when_everything_fails_it_shows_an_error_not_an_empty_success(self):
        body = function_source("loadWorkQueue")
        self.assertIn("wqState.failed.length === keys.length", body)
        self.assertIn("renderErrorState(", body)

    def test_the_panel_open_button_reuses_the_existing_wizard_opening_path(self):
        self.assertIn("lwPendingOpenWizardId = item.next.wizardId", function_source("wqOpenItem"))
        loader = function_source("loadListingWizardView")
        self.assertIn("lwPendingOpenWizardId", loader)
        self.assertLess(loader.index("lwRenderList()"), loader.index("lwOpenWizard(pendingId)"))

    def test_rows_work_with_keyboard_and_text_is_escaped(self):
        body = function_source("wqRender")
        self.assertIn('tabindex="0"', body)
        self.assertIn('event.key === "Enter"', body)
        self.assertNotIn("${item.detail}", body)
        self.assertNotIn("${item.title}", body)

    def test_translations_exist_in_both_languages_and_are_all_used(self):
        pattern = r'"((?:wq\.[a-z_.]+)|nav\.work_queue)"'
        ko, en = set(re.findall(pattern, KO)), set(re.findall(pattern, EN))
        self.assertEqual(ko, en)
        used = set(re.findall(pattern, JS)) | set(re.findall(pattern, HTML.replace("data-i18n=", '"').replace('"', '"')))
        # 동적으로 조립하는 키(상태·종류·원본 이름)는 구성 요소로 확인한다.
        dynamic = {f"wq.state_{s}" for s in ("check", "approval", "progress")} | {f"wq.chip_{s}" for s in ("check", "approval", "progress")}
        dynamic |= {f"wq.kind.{k}" for k in ("exception", "difference", "proposal", "priority", "wizard")}
        dynamic |= {f"wq.src.{k}" for k in ("priorities", "exceptions", "differences", "proposals", "wizards")}
        html_keys = set(re.findall(r'data-i18n(?:-attr)?="(?:[a-z-]+:)?((?:wq\.[a-z_.]+)|nav\.work_queue)"', HTML))
        self.assertEqual((ko - used - dynamic - html_keys), set(), "번역만 있고 쓰이지 않는 키")
        self.assertEqual((used | html_keys) - ko, set(), "쓰는데 번역이 없는 키")
        self.assertTrue(dynamic <= ko)

    def test_focus_and_responsive_styles_exist(self):
        self.assertIn(".wq-layout.panel-open", CSS)
        self.assertIn(".wq-chip:focus-visible", CSS)


if __name__ == "__main__":
    unittest.main()
