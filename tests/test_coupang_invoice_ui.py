"""
=========================================================
Homez OS

File : tests/test_coupang_invoice_ui.py

2026-10-15 — 구매 작업 상세의 "쿠팡에 송장 등록" 패널 규칙을 고정한다(정적 검사 + Node 구문 검사).
 - 패널은 배송완료 전 "발송됨(SHIPPED)"이고 송장 기록이 있을 때만 보인다.
 - 쿠팡 조회는 버튼을 눌러야만 나가고(자동 조회 없음), 등록은 확인창 → 비밀번호 재확인 → POST 순서다.
 - 보내는 값은 미리보기에서 받은 값 그대로이며 화면에서 새로 만들지 않는다.
=========================================================
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")


def func_body(name):
    start = JS.index(f"function {name}(")
    nxt = re.search(r"\n  (?:async )?function \w+\(", JS[start + 10:])
    return JS[start:start + 10 + nxt.start()] if nxt else JS[start:]


class InvoicePanelTests(unittest.TestCase):

    def test_the_panel_is_only_for_shipped_tasks_with_a_tracking_record(self):
        body = func_body("ptInvoiceCardHtml")
        self.assertIn('task.status !== "SHIPPED"', body)
        self.assertIn("!tracking", body)
        self.assertIn("${ptInvoiceCardHtml(task, tracking)}", JS)

    def test_nothing_is_read_from_coupang_until_the_button_is_pressed(self):
        # 정의 1 + 버튼 연결 1 + 결과 확인 뒤 재조회 1 — 화면을 그릴 때 부르는 곳은 없다.
        self.assertEqual(JS.count("ptInvoicePreview("), 3)
        self.assertEqual(JS.count("() => ptInvoicePreview(task)"), 1)
        self.assertIn("await ptInvoicePreview(task);", func_body("ptInvoiceReconcile"))
        self.assertIn('wireIfExists("pt-invoice-preview-btn", () => ptInvoicePreview(task));', JS)
        self.assertIn("/coupang-invoice/preview?live=true", func_body("ptInvoicePreview"))

    def test_sending_confirms_then_asks_for_the_password_then_posts_the_previewed_values(self):
        body = func_body("ptInvoiceSend")
        self.assertLess(body.index("askConfirm("), body.index("promptRecentAuthToken()"))
        self.assertLess(body.index("promptRecentAuthToken()"), body.index("/coupang-invoice/send"))
        self.assertIn("if (!confirmed) return;", body)
        self.assertIn("if (recentAuthToken === null) return;", body)
        self.assertIn('"X-Recent-Auth-Token": recentAuthToken', body)
        for field in ("v.delivery_company_code", "v.invoice_number", "v.shipment_box_id", "v.vendor_item_ids"):
            self.assertIn(field, body)

    def test_the_screen_never_invents_the_values_it_sends(self):
        body = func_body("ptInvoiceSend")
        self.assertNotIn("el(", body.split("/coupang-invoice/send")[1].split("});")[0])
        self.assertNotIn("tracking.", body)

    def test_the_send_button_exists_only_for_a_sendable_plan(self):
        body = func_body("ptInvoiceRenderPlan")
        self.assertIn("plan.sendable ?", body)
        self.assertIn("pt-invoice-send-btn", body)
        self.assertIn('plan.ledger.status === "UNKNOWN"', body)
        self.assertIn('plan.ledger.status === "ACTION_REQUIRED"', body)

    def test_the_resend_flag_is_only_set_after_a_recorded_rejection(self):
        self.assertIn('retry_action_required: Boolean(plan.ledger && plan.ledger.status === "ACTION_REQUIRED")', func_body("ptInvoiceSend"))

    def test_server_messages_are_escaped_before_rendering(self):
        body = func_body("ptInvoiceRenderPlan")
        self.assertIn("escapeHtml(b.message)", body)
        self.assertIn("escapeHtml(value === null", body)

    def test_a_failed_request_shows_the_error_instead_of_claiming_success(self):
        for name in ("ptInvoicePreview", "ptInvoiceSend", "ptInvoiceReconcile"):
            body = func_body(name)
            self.assertIn("catch (err)", body, name)
            self.assertIn('"error"', body, name)

    def test_every_invoice_text_key_used_exists_in_both_languages(self):
        used = set(re.findall(r'"(purchase_task\.invoice_[a-z_.]+)"', "".join(func_body(n) for n in (
            "ptInvoiceCardHtml", "ptInvoiceRenderPlan", "ptInvoiceSend", "ptInvoiceReconcile", "ptInvoicePreview"))))
        used |= {f"purchase_task.invoice_outcome.{o}" for o in ("succeeded", "already_registered", "action_required", "retryable", "unknown", "requesting")}
        self.assertGreater(len(used), 15)
        for key in used:
            self.assertIn(f'"{key}":', KO, key)
            self.assertIn(f'"{key}":', EN, key)
        ko_keys = set(re.findall(r'"(purchase_task\.invoice_[a-z_.]+)":', KO))
        en_keys = set(re.findall(r'"(purchase_task\.invoice_[a-z_.]+)":', EN))
        self.assertEqual(ko_keys, en_keys)

    def test_the_outcome_labels_cover_every_ledger_status(self):
        for status in ("SUCCEEDED", "RETRYABLE", "ACTION_REQUIRED", "UNKNOWN", "REQUESTING"):
            self.assertIn(f'"purchase_task.invoice_outcome.{status.lower()}":', KO)

    def test_the_javascript_files_pass_a_node_syntax_check(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 없음")
        for rel in ("app/web/console.js", "app/web/i18n/ko-KR.js", "app/web/i18n/en-US.js"):
            result = subprocess.run([node, "--check", str(ROOT / rel)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
