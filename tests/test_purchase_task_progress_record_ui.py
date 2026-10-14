"""
=========================================================
Homez OS

File : tests/test_purchase_task_progress_record_ui.py

2026-10-14 — 구매 작업 상세 화면의 "진행 기록"(조작이 끝났는지 확인할 수 있게 하는 기록 카드)과
발주 전송 성공 뒤 "처음 대기 화면으로 되돌리기"를 고정한다.

실제 `app/web/console.js`에서 해당 함수를 꺼내 Node에서 가짜 데이터로 실행해 결과(체크 표시·완료
배너·강조·XSS 이스케이프)를 확인하고, 배선(성공 처리 순서, 입력칸 미리 채움)은 소스로 확인한다.
네트워크·DB 없음.
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
CONSOLE = (ROOT / "app" / "web" / "console.js").read_text(encoding="utf-8")
KO = (ROOT / "app" / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app" / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    """`function name(`부터 짝이 맞는 닫는 중괄호까지. 문자열·템플릿(`${}` 중첩)·`//`·`/* */` 주석 안의
    중괄호와 따옴표는 건너뛴다."""

    match = re.search(r"(?:async )?function " + re.escape(name) + r"\(", CONSOLE)
    assert match, name
    start = match.start()
    i = CONSOLE.index("{", match.end())
    depth, mode, template_stack = 0, None, []
    while i < len(CONSOLE):
        ch = CONSOLE[i]
        if mode is None:
            if ch == "/" and CONSOLE[i + 1] == "/":
                i = CONSOLE.index("\n", i)
                continue
            if ch == "/" and CONSOLE[i + 1] == "*":
                i = CONSOLE.index("*/", i) + 2
                continue
            if ch in "\"'`":
                mode = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if template_stack and depth == template_stack[-1]:
                    template_stack.pop()
                    mode = "`"
                elif depth == 0:
                    return CONSOLE[start : i + 1]
        else:
            if ch == "\\":
                i += 2
                continue
            if mode == "`" and ch == "$" and CONSOLE[i + 1] == "{":
                template_stack.append(depth)
                depth += 1
                mode = None
                i += 2
                continue
            if ch == mode:
                mode = None
        i += 1
    raise AssertionError("unbalanced: " + name)


HARNESS = r"""
const HomezI18n = { t(key, params) {
  const table = JSON.parse(process.env.I18N_JSON);
  let text = table[key] !== undefined ? table[key] : key;
  Object.keys(params || {}).forEach((k) => { text = text.split("{" + k + "}").join(String(params[k])); });
  return text;
} };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fmtMoney = (v) => Number(v).toLocaleString("en-US") + "원";
const fmtDate = (v) => (v ? String(v).slice(0, 16).replace("T", " ") : "—");
const host = { innerHTML: "" };
const el = (id) => (id === "pt-progress-body" ? host : null);
let ptProgressNotice = null;
let ptProgressLastTracking = null;
__SOURCES__
const task = JSON.parse(process.env.TASK_JSON);
const approval = JSON.parse(process.env.APPROVAL_JSON);
const attempts = JSON.parse(process.env.ATTEMPTS_JSON);
const tracking = JSON.parse(process.env.TRACKING_JSON);
const notice = JSON.parse(process.env.NOTICE_JSON);
if (notice) ptProgressNotice = notice;
const rows = ptProgressRows(task, approval, attempts, tracking);
ptRenderProgressRecord(task, approval, attempts, tracking, process.env.HIGHLIGHT || null);
console.log(JSON.stringify({ rows, html: host.innerHTML, noticeAfter: ptProgressNotice }));
"""


def i18n_table() -> dict:
    table = {}
    for line in KO.splitlines():
        m = re.match(r'\s*"(purchase_task\.[a-z_.]+)":\s*(".*"),?\s*$', line)
        if m:
            table[m.group(1)] = json.loads(m.group(2))
    return table


TASK = {
    "id": 2, "source_order_id": 1, "coupang_sale_amount": 12050, "coupang_fee_amount": 1180.9,
    "channel_connection_id": 4, "status": "TRACKING_REQUIRED",
}
APPROVAL = {
    "status": "CONSUMED", "shipping_cost_amount": 3000, "shipping_cost_source": "ONCHANNEL_PRODUCT_PAGE",
    "shipping_cost_confirmed_at": "2026-10-14T10:58:08", "required_points_snapshot": 8310,
    "margin_amount_snapshot": 2559, "margin_rate_snapshot": 0.2123734, "expires_at": "2026-10-14T11:18:19",
}
ATTEMPT_OK = {
    "status": "SUCCEEDED", "external_order_code": "GO_1018547475",
    "started_at": "2026-10-14T11:11:34", "finished_at": "2026-10-14T11:11:35",
}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ProgressRecordBehaviorTests(unittest.TestCase):

    def _run(self, *, task=TASK, approval=APPROVAL, attempts=(ATTEMPT_OK,), tracking=None,
             notice=None, highlight=None):
        sources = "\n".join(function_source(n) for n in ("ptProgressRows", "ptRenderProgressRecord"))
        env = {
            **os.environ,
            "I18N_JSON": json.dumps(i18n_table()), "TASK_JSON": json.dumps(task),
            "APPROVAL_JSON": json.dumps(approval), "ATTEMPTS_JSON": json.dumps(list(attempts)),
            "TRACKING_JSON": json.dumps(tracking), "NOTICE_JSON": json.dumps(notice),
            "HIGHLIGHT": highlight or "",
        }
        proc = subprocess.run(
            [NODE, "-e", HARNESS.replace("__SOURCES__", sources)], env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_every_finished_step_is_checked_and_shows_what_was_done(self):

        result = self._run(tracking={"courier": "롯데택배", "tracking_number": "123456"})
        rows = {r["key"]: r for r in result["rows"]}
        for key in ("order", "fee", "connection", "shipping", "approval", "submit", "tracking"):
            self.assertTrue(rows[key]["done"], key)
        self.assertIn("1,180.9 KRW", rows["fee"]["detail"])  # 소수 둘째 자리까지(반올림해 보이지 않게)
        self.assertIn("3,000원", rows["shipping"]["detail"])
        self.assertIn("ONCHANNEL_PRODUCT_PAGE", rows["shipping"]["detail"])
        self.assertIn("8,310원", rows["approval"]["detail"])
        self.assertIn("2,559원", rows["approval"]["detail"])
        self.assertIn("21.2%", rows["approval"]["detail"])
        self.assertIn("GO_1018547475", rows["submit"]["detail"])
        self.assertIn("롯데택배 123456", rows["tracking"]["detail"])
        self.assertEqual(result["html"].count("✔"), 7)

    def test_unfinished_steps_stay_pending_and_are_not_claimed_as_done(self):

        task = dict(TASK, coupang_fee_amount=None, channel_connection_id=None)
        result = self._run(task=task, approval=None, attempts=())
        rows = {r["key"]: r for r in result["rows"]}
        for key in ("fee", "connection", "shipping", "approval", "submit", "tracking"):
            self.assertFalse(rows[key]["done"], key)
        self.assertTrue(rows["order"]["done"])
        self.assertIn("○", result["html"])
        self.assertEqual(result["html"].count("✔"), 1)

    def test_a_pending_approval_is_not_shown_as_final_approval(self):

        approval = dict(APPROVAL, status="PENDING_SHIPPING_COST")
        result = self._run(approval=approval, attempts=())
        rows = {r["key"]: r for r in result["rows"]}
        self.assertTrue(rows["shipping"]["done"])
        self.assertFalse(rows["approval"]["done"])

    def test_a_successful_order_shows_the_completion_banner_once(self):

        result = self._run(notice={"taskId": 2, "attempt": ATTEMPT_OK})
        banner = result["html"].split("pt-progress-list")[0]
        self.assertIn("pt-progress-banner ok", banner)
        self.assertIn("GO_1018547475", banner)
        self.assertIn("8,310원", banner)
        self.assertTrue(result["noticeAfter"]["shown"])

    def test_the_stale_completion_banner_is_dropped_when_the_screen_is_reopened(self):

        result = self._run(notice={"taskId": 2, "attempt": ATTEMPT_OK, "shown": True})
        self.assertNotIn("pt-progress-banner", result["html"])
        self.assertIsNone(result["noticeAfter"])

    def test_a_banner_for_another_task_is_never_shown(self):

        result = self._run(notice={"taskId": 99, "attempt": ATTEMPT_OK})
        self.assertNotIn("pt-progress-banner", result["html"])

    def test_a_non_success_result_is_a_warning_not_a_completion(self):

        attempt = {"status": "RESULT_UNKNOWN", "external_order_code": None, "started_at": "2026-10-14T11:11:34"}
        result = self._run(attempts=(attempt,), notice={"taskId": 2, "attempt": attempt})
        rows = {r["key"]: r for r in result["rows"]}
        self.assertFalse(rows["submit"]["done"])
        self.assertTrue(rows["submit"]["warn"])
        self.assertIn("pt-progress-banner warn", result["html"])
        self.assertNotIn("pt-progress-banner ok", result["html"])
        self.assertIn("⚠", result["html"])

    def test_the_just_changed_row_is_highlighted(self):

        result = self._run(highlight="shipping")
        self.assertEqual(result["html"].count("pt-progress-flash"), 1)
        before = result["html"].split('data-progress-key="shipping"')[0]
        self.assertIn("pt-progress-flash", before.rsplit("<li", 1)[1] + 'data-progress-key="shipping"')

    def test_values_from_the_server_are_escaped(self):

        attempt = dict(ATTEMPT_OK, external_order_code="<img src=x onerror=alert(1)>")
        result = self._run(attempts=(attempt,))
        self.assertNotIn("<img", result["html"])
        self.assertIn("&lt;img", result["html"])


class ProgressRecordWiringTests(unittest.TestCase):

    def test_the_card_is_the_first_card_of_the_task_detail(self):

        head = CONSOLE.index('<div class="dash-card" id="pt-progress-card">')
        source_order = CONSOLE.index("purchase_task.field_source_order_id", head)
        self.assertLess(head, source_order)
        self.assertIn("ptLoadProgressRecord(task, null, tracking);", CONSOLE)

    def test_a_successful_send_returns_to_the_waiting_screen_and_a_failed_one_does_not(self):

        source = function_source("ptWireOrderApprovalHandlers")
        marker = 'if (attempt.status === "SUCCEEDED") {'
        self.assertEqual(source.count(marker), 1)
        succeeded, failed = source.split(marker)[1].split("} else {", 1)
        self.assertIn("await loadPurchaseTaskDetail({ id: task.id });", succeeded)
        self.assertIn("window.scrollTo", succeeded)
        failed_branch = failed.split("}")[0]
        self.assertIn('await ptLoadProgressRecord(task, "submit");', failed_branch)
        self.assertNotIn("loadPurchaseTaskDetail", failed_branch)

    def test_intermediate_steps_update_the_record_without_resetting_the_review(self):

        source = function_source("ptWireOrderApprovalHandlers")
        for key in ("shipping", "approval"):
            self.assertEqual(source.count(f'await ptLoadProgressRecord(task, "{key}");'), 1)
        # 상세 화면 전체를 다시 불러오는 곳은 "발주 전송 성공" 한 곳뿐이다(중간 조작에서 검토 내용이
        # 사라지면 다음 단계를 이어갈 수 없다).
        self.assertEqual(source.count("loadPurchaseTaskDetail("), 1)

    def test_the_shipping_input_is_prefilled_and_warns_before_re_saving(self):

        self.assertIn('id="pt-shipping-amount" min="0" step="1" required value="${a && a.shipping_cost_amount', CONSOLE)
        self.assertIn('id="pt-shipping-reentry-hint"', CONSOLE)

    def test_a_new_review_clears_the_previous_completion_notice(self):

        source = function_source("ptRunOrderSubmissionReview")
        self.assertIn("if (!recentAuthToken) ptProgressNotice = null;", source)

    def test_every_progress_key_exists_in_both_languages_and_is_used(self):

        keys_ko = set(re.findall(r'"(purchase_task\.progress_[a-z_]+)"', KO))
        keys_en = set(re.findall(r'"(purchase_task\.progress_[a-z_]+)"', EN))
        self.assertEqual(keys_ko, keys_en)
        used = {f"purchase_task.{k}" for k in re.findall(r"purchase_task\.(progress_[a-z_]+)", CONSOLE)}
        used |= {f"purchase_task.{k}" for k in re.findall(r'\bt\("(progress_[a-z_]+)"', CONSOLE)}
        self.assertEqual(used - keys_ko, set(), "화면이 쓰는데 번역이 없는 키")
        self.assertEqual(keys_ko - used, set(), "번역만 있고 화면이 쓰지 않는 키")


if __name__ == "__main__":
    unittest.main()
