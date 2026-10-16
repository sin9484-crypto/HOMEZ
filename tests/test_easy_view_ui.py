"""
=========================================================
Homez OS

File : tests/test_easy_view_ui.py

2026-10-16 — "쉬운 보기"(app/web/easy/*)의 화면 규칙을 정적으로 고정한다(소스 검사 + Node 구문 검사).
  - 한국어·영어 문구가 짝이 맞고, 화면 코드가 쓰는 모든 문구가 있다(없으면 화면에 빈 글자가 나온다).
  - 쉬운 보기는 console.js의 연결부(HomezEasyBridge)에 있는 것만 쓴다.
  - 화면을 여는 것만으로 쿠팡·온채널을 부르지 않는다: 바깥을 읽는 호출은 버튼 핸들러 안에서만, 변경은 확인→재인증 순서 뒤에만.
  - 스타일은 모두 `body.easy-view` 아래라 끄면 기존 화면이 그대로다. 정적 파일은 허용 목록으로만 서빙된다.
=========================================================
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EASY = ROOT / "app/web/easy"
JS = (EASY / "easy_view.js").read_text(encoding="utf-8")
TEXTS = (EASY / "easy_texts.js").read_text(encoding="utf-8")
CSS = (EASY / "easy_view.css").read_text(encoding="utf-8")
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
HTML = (ROOT / "app/web/console.html").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
ROUTER = (ROOT / "app/web/router.py").read_text(encoding="utf-8")


def text_block(lang):
    match = re.search(r'"%s": \{(.*?)\n  \}' % lang, TEXTS, re.S)
    return match.group(1)


def keys_of(block):
    return set(re.findall(r'"(easy\.[a-z_.0-9]+)":', block))


# 동적으로 만드는 키(`T(`easy.xxx.${값}`)`)의 가능한 값
DYNAMIC = {
    "easy.flow.": ["prepare", "selling", "new_orders", "shipping", "settlement"],
    "easy.kind.": ["read", "write"], "easy.sys.": ["coupang", "onch"],
    "easy.step.": ["ordered", "supplier_order", "supplier_shipped", "invoice", "delivered"],
    "easy.next.": ["register_invoice", "order_from_supplier", "wait_supplier_shipment", "check_coupang_order", "wait_delivery", "return_in_progress", "done"],
    "easy.outcome.": ["allowed", "succeeded", "failed", "logged"],
    "easy.cs.": ["accept", "instruct", "departure", "delivering", "final_delivery", "none_tracking"],
    "easy.rs.": ["release_stop_unchecked", "returns_unchecked", "vendor_warehouse_confirm", "request_coupang_check", "returns_completed"],
    "easy.fault.": ["customer", "vendor", "coupang", "wms", "general"],
    "easy.settle.type_": ["monthly", "weekly", "additional", "reserve"],
    "easy.settle.st_": ["subject", "done"],
}


def function_body(name):
    match = re.search(r"\n  (?:async )?function %s\(" % re.escape(name), JS)
    start = match.start()
    nxt = re.search(r"\n  (?:async )?function \w+\(|\n  // =====", JS[start + 20:])
    return JS[start:start + 20 + nxt.start()] if nxt else JS[start:]


class TextTests(unittest.TestCase):

    def test_korean_and_english_have_exactly_the_same_keys(self):
        self.assertEqual(keys_of(text_block("ko-KR")), keys_of(text_block("en-US")))

    def test_every_key_the_screens_use_exists(self):
        ko = keys_of(text_block("ko-KR"))
        used = set(re.findall(r'"(easy\.[a-z_.0-9]+)"', JS))
        for prefix, values in DYNAMIC.items():
            for value in values:
                used.add(prefix + value)
                if prefix == "easy.flow.":
                    used.add(prefix + value + "_d")
        for key in ("easy.ap.mode_rec", "easy.ap.mode_op", "easy.ap.mode_auto", "easy.ap.mode_off"):
            used.add(key + "_s")
        self.assertEqual(sorted(used - ko), [])

    def test_no_text_is_left_unused(self):
        ko = keys_of(text_block("ko-KR"))
        used = set(re.findall(r'"(easy\.[a-z_.0-9]+)"', JS))
        for prefix, values in DYNAMIC.items():
            for value in values:
                used.add(prefix + value)
                if prefix == "easy.flow.":
                    used.add(prefix + value + "_d")
        for key in ("easy.ap.mode_rec", "easy.ap.mode_op", "easy.ap.mode_auto", "easy.ap.mode_off"):
            used.add(key + "_s")
        self.assertEqual(sorted(ko - used), [], "쓰지 않는 문구는 지운다")

    def test_the_existing_keys_the_screens_borrow_exist_in_both_languages(self):
        borrowed = set(re.findall(r'T\(\s*"((?:common|safety|wq|dialog)\.[a-z_.0-9]+)"', JS))
        borrowed |= {f"odo.health.{c}" for c in ("ok", "waiting", "off", "paused", "error", "failing", "blocked", "needs_allowance", "env_blocked", "unknown")}
        for key in sorted(borrowed):
            self.assertIn(f'"{key}":', KO, key)
            self.assertIn(f'"{key}":', EN, key)
        for kind in ("exception", "difference", "proposal", "priority", "wizard"):
            self.assertIn(f'"wq.kind.{kind}":', KO)
        for src in ("priorities", "exceptions", "differences", "proposals", "wizards"):
            self.assertIn(f'"wq.src.{src}":', KO)

    def test_texts_never_promise_what_the_app_does_not_do(self):
        ko = text_block("ko-KR")
        for risky in ("자동으로 결제", "자동 발주", "무조건"):
            self.assertNotIn(risky, ko)


class BridgeTests(unittest.TestCase):

    def test_the_easy_script_only_uses_what_the_bridge_provides(self):
        bridge = CONSOLE.split("window.HomezEasyBridge = Object.freeze({")[1].split("\n  });")[0]
        provided = set(re.findall(r"^\s{4}(?:get )?([A-Za-z_]+)\s*[:,(]", bridge, re.M))
        provided |= set(re.findall(r"([A-Za-z_]+)(?:,|\s*:)", bridge.split("\n", 6)[0] + "\n" + bridge.split("\n", 6)[1] + "\n" + bridge.split("\n", 6)[2]))
        provided |= {"apiFetch", "toast", "el", "escapeHtml", "fmtDate", "fmtMoney", "confirmDialog", "askConfirm", "promptRecentAuthToken",
                     "navigateTo", "renderErrorState", "getCurrentUser", "userSettingFetch", "userSettingSave", "safetyModeLabel",
                     "lwThumbHtml", "lwHydrateThumbs", "taskStatusLabel"}
        used = set(re.findall(r"\bB\.([A-Za-z_]+)", JS))
        self.assertEqual(sorted(used - provided), [], "연결부에 없는 기능을 쓰고 있다")

    def test_console_calls_the_hooks_and_the_home_view_override(self):
        shell = CONSOLE.split("function showShell()")[1].split("\n  }\n")[0]
        self.assertIn("runEasyHooks(easyHooks.shellShown)", shell)
        nav = CONSOLE.split("function navigateTo(")[1].split("\n  }\n")[0]
        self.assertIn("runEasyHooks(easyHooks.navigated, viewName)", nav)
        first = CONSOLE.split("function firstVisibleNavView()")[1].split("\n  }\n")[0]
        self.assertIn("easyState.homeView", first)

    def test_a_failing_hook_cannot_break_the_console(self):
        runner = CONSOLE.split("function runEasyHooks(")[1].split("\n  }\n")[0]
        self.assertIn("try {", runner)
        self.assertIn("catch (err)", runner)

    def test_the_bridge_is_frozen_and_exposes_no_raw_internals(self):
        self.assertIn("window.HomezEasyBridge = Object.freeze({", CONSOLE)
        bridge = CONSOLE.split("window.HomezEasyBridge = Object.freeze({")[1].split("\n  });")[0]
        for forbidden in ("TOKEN_KEY", "inMemoryRefreshToken", "localStorage", "inMemoryDesktopAccessToken"):
            self.assertNotIn(forbidden, bridge)


class ExternalCallTimingTests(unittest.TestCase):

    def test_opening_a_screen_never_reads_coupang_or_onchannel(self):
        for name in ("loadToday", "loadProducts", "loadProductDetail", "loadOrders", "loadMoney", "loadApprovals", "loadMore", "renderOrderPanel"):
            body = function_body(name)
            for forbidden in ("coupang-invoice/preview", "outbound-days/preview", "coupang-invoice/send", "outbound-days/send", "/collection-ops/trigger", "/delivery/refresh"):
                self.assertNotIn(forbidden, body, f"{name}: {forbidden}")

    def test_the_outside_reads_only_run_from_button_handlers(self):
        self.assertEqual(JS.count("invoicePreview(order)"), 2)  # 정의 1 + 버튼 연결 1
        self.assertIn('previewBtn.addEventListener("click", () => invoicePreview(order))', JS)
        self.assertEqual(JS.count("daysPreview("), 2)
        self.assertIn('btn.addEventListener("click", () => daysPreview(btn.dataset.ezDays))', JS)

    def test_sending_an_invoice_confirms_then_asks_for_the_password_then_posts(self):
        body = function_body("invoicePreview")
        send = body.split('send.addEventListener("click"')[1]
        self.assertLess(send.index("B.askConfirm("), send.index("B.promptRecentAuthToken()"))
        self.assertLess(send.index("B.promptRecentAuthToken()"), send.index("/coupang-invoice/send"))
        self.assertIn('"X-Recent-Auth-Token": token', send)
        self.assertIn("if (token === null) return;", send)

    def test_the_values_sent_come_from_the_preview_not_from_the_screen(self):
        send = function_body("invoicePreview").split('send.addEventListener("click"')[1]
        for field in ("v.delivery_company_code", "v.invoice_number", "v.shipment_box_id", "v.vendor_item_ids"):
            self.assertIn(field, send)

    def test_changing_dispatch_days_confirms_first_and_sends_the_values_that_were_read(self):
        body = function_body("daysPreview")
        send = body.split('send.addEventListener("click"')[1]
        self.assertLess(send.index("B.askConfirm("), send.index("/outbound-days/send"))
        self.assertIn("expected_current_days: cur", send)

    def test_granting_an_allowance_always_re_checks_the_password(self):
        for name in ("approveDialog", "grantDialog"):
            body = function_body(name)
            self.assertLess(body.index("B.promptRecentAuthToken()"), body.index('"X-Recent-Auth-Token": token'), name)
            self.assertIn("if (token === null) return;", body, name)

    def test_the_manual_registration_record_asks_before_recording(self):
        body = function_body("invoiceMarkDone")
        self.assertLess(body.index("B.askConfirm("), body.index("mark-registered"))

    def test_rejecting_and_revoking_need_no_password(self):
        approvals = function_body("loadApprovals")
        self.assertIn("/reject", approvals)
        self.assertIn("/revoke", approvals)


class ShellTests(unittest.TestCase):

    def test_only_admins_get_the_easy_view_and_the_default_is_on(self):
        shown = function_body("onShellShown")
        self.assertLess(shown.index("if (!isAdmin())"), shown.index("setEnabled(true)"))
        self.assertIn("easy_view === false", shown)

    def test_turning_it_off_restores_the_classic_home(self):
        self.assertIn('B.state.homeView = on ? "easy-today" : null;', function_body("setEnabled"))
        self.assertIn('B.navigateTo(on ? "easy-today" : "overview")', function_body("setEasyEnabled"))

    def test_the_preference_is_saved_without_losing_other_ui_settings(self):
        body = function_body("savePreference")
        self.assertIn("{ ...current.value }", body)
        self.assertIn('base.easy_view = on;', body)

    def test_there_is_a_way_back_from_the_classic_screens(self):
        self.assertIn("ez-legacy-bar", JS)
        self.assertIn("ez-toggle-menu", JS)
        self.assertIn("ez-on-legacy", CSS)

    def test_classic_menu_buttons_stay_in_the_page_for_permissions(self):
        # 기존 사이드바를 지우지 않고 숨기기만 한다(권한 숨김·가이드가 그 버튼 상태를 기준으로 삼는다).
        self.assertNotIn(".remove()", function_body("ensureShell").replace("dialog.remove()", ""))
        self.assertIn("display: none !important", CSS)

    def test_every_screen_has_a_container_and_a_loader(self):
        views = ["easy-today", "easy-products", "easy-product-detail", "easy-orders", "easy-money", "easy-analysis", "easy-approvals", "easy-more"]
        for view in views:
            self.assertIn(f'"{view}"', JS)
            self.assertIn(f'B.registerView("{view}"', JS)

    def test_the_badge_counts_what_needs_a_decision(self):
        body = function_body("refreshBadge")
        self.assertIn("ext.pending", body)
        self.assertIn("outbound_days_todo", body)


class AnalysisScreenTests(unittest.TestCase):

    def test_the_analysis_screen_only_reads_and_never_calls_outside(self):
        body = function_body("loadAnalysis")
        for forbidden in ('method: "POST"', 'method: "PUT"', 'method: "DELETE"', "coupang", "onch", "/send", "/trigger"):
            self.assertNotIn(forbidden, body, forbidden)
        for path in ("/product-candidates?limit=200", "/decisions/pending-review?limit=200", "/ai-learning/model-candidates"):
            self.assertIn(f'"{path}"', body)

    def test_one_failing_source_does_not_hide_the_rest(self):
        body = function_body("loadAnalysis")
        self.assertIn("Promise.allSettled", body)
        self.assertIn("easy.an.partial", body)

    def test_the_labels_it_borrows_from_the_classic_screens_exist_in_both_languages(self):
        keys = set(re.findall(r'"(decision\.recommendation\.[a-z_]+)"', JS))
        keys |= {f"candidates.status.{s}" for s in ("discovered", "analyzed", "recommended", "approved", "held", "rejected", "expired")}
        keys |= {f"ail.status.{s}" for s in ("draft", "offline_evaluated", "regression_compared", "approved", "rejected")}
        self.assertGreaterEqual(len(keys), 17)
        for key in sorted(keys):
            self.assertIn(f'"{key}":', KO, key)
            self.assertIn(f'"{key}":', EN, key)

    def test_rows_open_the_existing_detail_screens(self):
        body = function_body("loadAnalysis")
        for view in ('"candidate-detail"', '"decision-detail"', '"ai-learning"'):
            self.assertIn(view, body)

    def test_the_menu_highlights_analysis_for_the_classic_analysis_screens(self):
        self.assertIn('analysis: "analysis"', JS)
        self.assertIn('id: "analysis", view: "easy-analysis"', JS)

    def test_a_missing_score_is_shown_as_no_score_not_zero(self):
        body = function_body("scoreCell")
        self.assertIn("easy.an.no_score", body)
        self.assertLess(body.index("easy.an.no_score"), body.index("toFixed"))


class UnresolvedOrderRowTests(unittest.TestCase):

    def test_today_lists_unlinked_new_orders_as_things_for_me_to_do(self):
        body = function_body("loadToday")
        self.assertIn("today.unresolved_orders", body)
        self.assertIn('group: "CHECK"', body.split("today.unresolved_orders")[1].split("// 3)")[0])
        self.assertIn('B.navigateTo("order-fulfillment")', body)

    def test_the_row_only_navigates_and_never_calls_outside(self):
        block = function_body("loadToday").split("today.unresolved_orders")[1].split("// 3)")[0]
        for forbidden in ("api(", 'method: "POST"', "coupang-invoice", "/trigger"):
            self.assertNotIn(forbidden, block)


class DeliveryTrackingRowTests(unittest.TestCase):

    def test_the_coupang_status_check_only_runs_from_its_button(self):
        self.assertIn('trackBtn.addEventListener("click", () => trackNow(order))', JS)
        for name in ("trackHtml", "renderOrderPanel", "loadOrders"):
            self.assertNotIn("/delivery/refresh", function_body(name), name)
        self.assertIn("/delivery/refresh", function_body("trackNow"))
        self.assertIn('method: "POST"', function_body("trackNow"))

    def test_a_blocked_check_points_to_the_approval_center_instead_of_retrying(self):
        body = function_body("trackNow")
        self.assertIn("r.blocked", body)
        self.assertIn('B.navigateTo("easy-approvals")', body)
        self.assertNotIn("setInterval", body)

    def test_the_tracking_allowance_can_be_granted_from_the_approval_center_with_a_password(self):
        self.assertIn('purpose: "ORDER_TRACKING", mode: "STANDING", per_hour_limit: 30, hours: 24', JS)
        self.assertIn('["ORDER_TRACKING", "easy.purpose.tracking"]', JS)
        self.assertLess(function_body("grantDialog").index("B.promptRecentAuthToken()"), function_body("grantDialog").index('"X-Recent-Auth-Token": token'))

    def test_a_finished_delivery_has_no_check_button(self):
        self.assertIn('const done = s === "FINAL_DELIVERY";', function_body("trackHtml"))


class AfterSalesRowTests(unittest.TestCase):

    def test_today_lists_open_returns_and_unanswered_inquiries_as_things_to_check(self):
        body = function_body("loadToday")
        block = body.split("today.after_sales")[1].split("// 3)")[0]
        self.assertIn("returns_open", block)
        self.assertIn("inquiries_open", block)
        self.assertEqual(block.count('group: "CHECK"'), 2)

    def test_the_rows_only_navigate_and_the_screens_never_call_outside_on_open(self):
        block = function_body("loadToday").split("today.after_sales")[1].split("// 3)")[0]
        for forbidden in ("api(", 'method: "POST"', "/after-sales/refresh"):
            self.assertNotIn(forbidden, block)
        for name in ("afterHtml", "settlementBlock", "loadMoney", "loadOrders", "renderOrderPanel"):
            self.assertNotIn("/after-sales/refresh", function_body(name), name)

    def test_the_texts_say_that_handling_happens_in_wing_and_nothing_is_done_automatically(self):
        ko = text_block("ko-KR")
        self.assertIn("승인·수령 확인·환불은 하지 않아요", ko)
        self.assertIn("개인정보라 HOMEZ에 저장하지 않아요", ko)

    def test_the_settlement_block_shows_coupang_values_with_a_caveat_and_money_loads_it_in_parallel(self):
        self.assertIn("settlementBlock(today)", function_body("loadMoney"))
        self.assertIn('api("/easy-view/today").catch(() => null)', function_body("loadMoney"))
        self.assertIn("easy.settle.sub", function_body("settlementBlock"))

    def test_the_after_sales_allowance_is_granted_from_the_approval_center_with_a_password(self):
        self.assertIn('purpose: "AFTER_SALES_CHECK", mode: "STANDING", per_hour_limit: 30, hours: 24', JS)
        self.assertIn('["AFTER_SALES_CHECK", "easy.purpose.after"]', JS)


class ReturnInProgressRowTests(unittest.TestCase):

    def test_an_order_with_an_open_return_is_something_for_me_to_do_and_explains_itself(self):
        self.assertIn('"RETURN_IN_PROGRESS"].includes(o.next_action.code)', JS)
        self.assertIn('code === "RETURN_IN_PROGRESS"', function_body("renderOrderPanel"))
        self.assertIn("정산을 기다리는 주문이 아니에요", text_block("ko-KR"))


class StyleAndServingTests(unittest.TestCase):

    def test_every_rule_is_scoped_so_turning_the_view_off_restores_the_classic_look(self):
        stripped = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        allowed = ("body.easy-view", "#ez-", ".ez-", "dialog.ez-dialog", ".ez-dialog", "@media", "}", "@")
        depth = 0
        for chunk in re.finditer(r"([^{}]+)\{", stripped):
            selector = chunk.group(1).strip()
            if not selector or selector.startswith("@"):
                continue
            for part in [p.strip() for p in selector.split(",")]:
                self.assertTrue(part.startswith(allowed), f"범위 밖 선택자: {part}")

    def test_the_stylesheet_never_hides_the_login_or_dialogs_globally(self):
        for forbidden in ("login-gate", "confirm-dialog", "#shell {"):
            self.assertNotIn(forbidden, CSS)

    def test_the_detail_panel_never_overlaps_the_list(self):
        # 2026-10-16 실제 화면 확인: 1280px 폭에서 표(720px)가 오른쪽 패널 위로 넘쳤다.
        self.assertIn("@media (max-width: 1320px)", CSS)
        self.assertIn("overflow-x: auto", re.search(r"\.ez-main \{[^}]*\}", CSS).group(0))
        self.assertIn("function revealPanel(", JS)

    def test_the_classic_drawer_stays_reachable_on_phones(self):
        # 2026-10-16: 모바일에서 사이드바를 숨기면 더보기·설정으로 갈 길이 없었다. 기존 서랍 메뉴를 막지 않는다.
        mobile = CSS.split("@media (max-width: 860px) {")[1].split("\n}\n")[0]
        self.assertNotIn(".sidenav", mobile)

    def test_the_top_bar_may_wrap_on_narrow_screens(self):
        self.assertNotRegex(CSS, r"body\.easy-view \.topbar \{[^}]*[^-]height: 66px")

    def test_the_order_step_date_is_cut_by_pattern_not_by_position(self):
        self.assertNotIn("fmtDate(step.detail).slice(", JS)

    def test_motion_is_reduced_for_people_who_ask(self):
        self.assertIn("prefers-reduced-motion", CSS)

    def test_the_mobile_layout_has_bottom_tabs(self):
        self.assertIn("max-width: 860px", CSS)
        self.assertIn("#ez-bottom", CSS)

    def test_the_files_are_served_only_through_an_allowlist(self):
        self.assertIn('_EASY_FILES = {"easy_view.js"', ROUTER)
        route = ROUTER.split("def console_easy_asset(")[1].split("\n\n\n")[0]
        self.assertIn("_EASY_FILES.get(filename)", route)
        self.assertIn("raise HTTPException(status_code=404", route)
        self.assertNotIn("..", route.split("media_type = ")[0])
        for name in ("easy_view.js", "easy_texts.js", "easy_view.css"):
            self.assertTrue((EASY / name).exists())

    def test_the_html_loads_the_easy_files_after_the_console_script(self):
        order = [HTML.index(s) for s in ("/console/static/console.js", "/console/static/easy/easy_texts.js", "/console/static/easy/easy_view.js")]
        self.assertEqual(order, sorted(order))
        self.assertIn('/console/static/easy/easy_view.css', HTML)

    def test_the_cache_busting_covers_the_new_files(self):
        for name in ("easy_texts.js", "easy_view.js", "easy_view.css"):
            self.assertIn(f'"/console/static/easy/{name}"', ROUTER)

    def test_no_secret_or_token_handling_in_the_easy_script(self):
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "homez_console_token", "Authorization"):
            self.assertNotIn(forbidden, JS)

    def test_the_easy_script_has_no_unescaped_server_messages(self):
        for risky in ("${b.message}", "${err.message}", "${reasons.map((b) => b.message)", "${a.reason}", "${x.product_title}", "${o.product_name}"):
            self.assertNotIn(risky, JS, risky)

    def test_the_files_pass_a_node_syntax_check(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 없음")
        for name in ("easy_view.js", "easy_texts.js"):
            result = subprocess.run([node, "--check", str(EASY / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([node, "--check", str(ROOT / "app/web/console.js")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
