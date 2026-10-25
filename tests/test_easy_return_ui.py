"""
=========================================================
Homez OS

File : tests/test_easy_return_ui.py

2026-10-26 — 쉬운 보기 승인 화면의 "쿠팡 반품 처리"(쿠팡 쓰기 5번 — 입고 확인·반품 승인)를 고정한다. 실제 `easy_view.js`의 함수를 Node에서 실행하고,
서버 호출 순서·문구 짝은 소스에서 확인한다. 네트워크·DB 없음.

  - 반품접수번호가 숫자가 아니면 쿠팡을 읽지도 않는다. 동작은 입고 확인(RECEIVE)·승인(APPROVE) 둘뿐.
  - 상품 수령 확인 칸(승인은 환불 확인 칸도)을 모두 체크해야만 보내고, 보내기 전에 한 번 더 묻는다. 빠른환불 대상이면 입고 확인만으로 환불된다고 알린다.
  - 승인 수량은 쿠팡이 준 접수 수량(cp.cancel_count)을 그대로 보낸다. 끝나면 결과를 화면에 남긴다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

NODE = shutil.which("node")
PREVIEW = function_source("returnPreview")


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:ret\.[a-z_]+|purpose\.ret|stock\.[a-z_]+|rs\.[a-z_]+|fault\.[a-z_]+))": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.split(`{${k}}`).join(String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class FunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join(function_source(n) for n in ("tryT", "returnStatusLabel", "returnResultHtml"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_status_labels(self):
        self.assertEqual(self.run_js('["RETURNS_UNCHECKED", "VENDOR_WAREHOUSE_CONFIRM", "RETURNS_COMPLETED"].map(returnStatusLabel)'),
                         ["반품 접수(확인 전)", "입고 확인", "반품 완료"])                 # 반품 알림과 같은 문구(easy.rs.*)
        self.assertEqual(self.run_js('[returnStatusLabel("NEW_THING"), returnStatusLabel(null)]'), ["NEW_THING", "—"])

    def test_result_wording(self):
        def html(result, action="APPROVE"):
            return self.run_js(f"returnResultHtml({json.dumps(result)}, {json.dumps(action)})")

        self.assertIn("반품 승인을 마치고", html({"outcome": "SUCCEEDED", "verified": True, "after_status": "RETURNS_COMPLETED"}))
        self.assertIn("반품 완료", html({"outcome": "SUCCEEDED", "verified": True, "after_status": "RETURNS_COMPLETED"}))
        self.assertIn("입고 확인을 마치고", html({"outcome": "SUCCEEDED", "verified": True, "after_status": "VENDOR_WAREHOUSE_CONFIRM"}, "RECEIVE"))
        self.assertIn("다시 읽은 상태가 반품 접수(확인 전)", html({"outcome": "SUCCEEDED", "verified": False, "after_status": "RETURNS_UNCHECKED"}, "RECEIVE"))
        self.assertIn("확인하지는 못했어요", html({"outcome": "SUCCEEDED", "verified": None}))
        self.assertIn("이미 처리됐어요(지금 상태: 반품 완료)", html({"outcome": "ALREADY_DONE", "plan": {"coupang": {"status": "RETURNS_COMPLETED"}}}))
        self.assertIn("다시 보내지 말고", html({"outcome": "UNKNOWN"}))
        rejected = html({"outcome": "ACTION_REQUIRED", "detail": "<b>수량 불일치</b>"})
        self.assertIn("&lt;b&gt;수량 불일치", rejected)
        self.assertNotIn("<b>", rejected)


class SourceTests(unittest.TestCase):

    def test_the_section_and_the_allowance_purpose_exist(self):
        for marker in ('id="ez-ret-anchor"', 'id="ez-ret-id"', 'id="ez-ret-action"', '<option value="RECEIVE">', '<option value="APPROVE">',
                       '["RETURN_PROCESS", "easy.purpose.ret"]'):
            self.assertIn(marker, JS, marker)
        self.assertLess(JS.index('id="ez-inq-anchor"'), JS.index('id="ez-ret-anchor"'))

    def test_the_flow_checks_input_then_previews_then_needs_all_boxes_then_asks_then_sends(self):
        self.assertLess(PREVIEW.index("/^\\d{1,20}$/.test(id)"), PREVIEW.index("/preview?action="))
        self.assertIn('if (action !== "RECEIVE" && action !== "APPROVE") return;', PREVIEW)
        send = PREVIEW[PREVIEW.index('send.addEventListener("click"'):]
        self.assertLess(send.index('(action === "APPROVE" && !(refund && refund.checked))'), send.index("B.askConfirm("))
        self.assertLess(send.index("B.askConfirm("), send.index("/send`"))
        self.assertIn('expected_status: cp.status, expected_cancel_count: action === "APPROVE" ? cp.cancel_count : null', send)
        self.assertIn('confirm_received: !!(received && received.checked), confirm_refund: action === "APPROVE" && !!(refund && refund.checked)', send)
        self.assertIn("send.disabled = true", send)
        self.assertIn("/reconcile?action=${action}", send)

    def test_the_fast_refund_warning_is_shown_for_receipt(self):
        self.assertIn('action === "RECEIVE" && cp.pre_refund === true', PREVIEW)

    def test_status_and_fault_names_reuse_the_return_alert_texts(self):
        self.assertIn("tryT(`easy.rs.${String(status).toLowerCase()}`, String(status))", function_source("returnStatusLabel"))
        self.assertIn("tryT(`easy.fault.${String(cp.fault_by).toLowerCase()}`, String(cp.fault_by))", PREVIEW)
        self.assertNotIn("easy.ret.status.", JS + TEXTS)

    def test_server_text_is_escaped(self):
        self.assertIn("reasons.map((b) => esc(b.message))", PREVIEW)

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(easy\.ret\.[a-z_]+)"', JS)) | {"easy.purpose.ret"}
        for key in sorted(used | set(re.findall(r'"(easy\.stock\.[a-z_]+)"', PREVIEW + function_source("returnResultHtml")))):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
        own = {k for k in ko if k.startswith("easy.ret.") or k == "easy.purpose.ret"}
        self.assertEqual(own, {k for k in en if k.startswith("easy.ret.") or k == "easy.purpose.ret"})
        self.assertEqual(own - used, set(), "쓰지 않는 문구가 있다")

    def test_the_fixed_wording_says_what_matters(self):
        ko = table("ko-KR")
        self.assertIn("환불", ko["easy.ret.refund_confirm"])
        self.assertIn("되돌릴 수 없어요", ko["easy.ret.confirm_approve"])
        self.assertIn("바로 고객에게 환불", ko["easy.ret.prerefund_warn"])
        self.assertIn("공급처", ko["easy.ret.where"])
        self.assertIn("허용이 필요", ko["easy.ret.where"])
        self.assertEqual(ko["easy.purpose.ret"], "쿠팡 반품 처리")


if __name__ == "__main__":
    unittest.main()
