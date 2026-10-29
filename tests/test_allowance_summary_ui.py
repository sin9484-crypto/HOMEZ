"""
=========================================================
Homez OS

File : tests/test_allowance_summary_ui.py

2026-10-29 — 승인 센터 허용 만들기의 "무엇을 허용하는지" 다시 보여 주기를 고정한다.
실제 첫 사용에서 허용 창의 기본값(신규 주문 자동 감지·조회·1회)이 내용 확인 없이 그대로 승인된 일이 있었다.

  - 직접 허용 만들기의 목적 칸은 미리 골라 두지 않는다(빈 칸 + required). 고르지 않으면 비밀번호를 묻지 않고 다시 연다.
  - 비밀번호 재확인 창 위에 허용할 일·종류·보내는 곳·범위를 보여 준다. 변경(쓰기)과 '모든 목적'은 눈에 띄게 한다.
  - 요약은 텍스트로만 넣는다(innerHTML 아님). 요약 없이 부르는 다른 화면에서는 숨긴다.
=========================================================
"""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from tests.test_easy_briefing_ui import HARNESS
from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import NODE
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
HTML = (ROOT / "app/web/console.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
GRANT = function_source("grantDialog")
APPROVE = function_source("approveDialog")
KEYS = r'easy\.(?:ap\.[a-z_]+|purpose\.[a-z_]+|kind\.[a-z]+|sys\.[a-z]+)'


def table(locale):
    block = re.search(r'"' + locale + r'": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(' + KEYS + r')": ("(?:[^"\\]|\\.)*")', block)}


def console_function(name):
    start = CONSOLE.index(f"function {name}(")
    return CONSOLE[start:CONSOLE.index("\n  }\n", start)]


class DialogSourceTests(unittest.TestCase):

    def test_the_purpose_is_not_preselected_and_is_required(self):
        self.assertIn('<select name="purpose" required><option value="" ${picked ? "" : "selected"}>${esc(T("easy.ap.d_pick"))}</option>', GRANT)
        self.assertIn('${picked === v ? "selected" : ""}', GRANT)
        self.assertNotIn('const purposes = [', GRANT)                       # 목록은 요약과 같은 GRANT_PURPOSES 하나만 쓴다

    def test_no_purpose_reopens_without_asking_the_password(self):
        check = GRANT.index('if (!result.get("purpose"))')
        self.assertLess(check, GRANT.index("B.promptRecentAuthToken("))
        reopen = GRANT[check:GRANT.index("B.promptRecentAuthToken(")]
        self.assertIn('B.toast(T("easy.ap.d_pick_needed"), "error")', reopen)
        self.assertIn("return grantDialog(", reopen)

    def test_both_dialogs_show_the_summary_before_the_password_and_send_after(self):
        for name, body in (("grantDialog", GRANT), ("approveDialog", APPROVE)):
            self.assertLess(body.index("allowanceSummaryRows("), body.index("B.promptRecentAuthToken({ summary })"), name)
            self.assertLess(body.index("B.promptRecentAuthToken({ summary })"), body.index('"X-Recent-Auth-Token": token'), name)
            self.assertIn("if (token === null) return;", body, name)

    def test_the_summary_uses_what_is_actually_sent(self):
        self.assertIn('allowanceSummaryRows({ purpose: result.get("purpose"), kind: result.get("kind"), system: result.get("system"), count, per_hour_limit: perHour, hours })', GRANT)
        self.assertIn('body: JSON.stringify({ system: result.get("system"), kind: result.get("kind"), purpose: result.get("purpose"), mode: standing ? "STANDING" : "COUNTED", count: standing ? null : count, per_hour_limit: standing ? perHour : null, hours: standing ? hours : (hours || null) })', GRANT)
        self.assertLess(APPROVE.index("const payload ="), APPROVE.index("allowanceSummaryRows("))
        self.assertIn("JSON.stringify(payload)", APPROVE)


class PasswordDialogTests(unittest.TestCase):

    def test_the_summary_slot_sits_above_the_password(self):
        dialog = HTML[HTML.index('<dialog id="recent-auth-dialog"'):HTML.index("</dialog>", HTML.index('<dialog id="recent-auth-dialog"'))]
        self.assertIn('<dl id="ra-summary" class="ra-summary" hidden></dl>', dialog)
        self.assertLess(dialog.index('id="ra-summary"'), dialog.index('id="ra-password"'))
        self.assertIn(".ra-summary[hidden] { display: none; }", CSS)
        self.assertIn(".ra-summary dd.is-strong { color: var(--danger); }", CSS)

    def test_the_summary_is_text_only_and_hidden_without_rows(self):
        render = console_function("renderRecentAuthSummary")
        self.assertIn("dt.textContent = String(row.label);", render)
        self.assertIn('dd.textContent = String(row.value == null ? "" : row.value);', render)
        self.assertNotIn("innerHTML", render)
        self.assertIn("summaryEl.replaceChildren();", render)                 # 이전 요약이 남지 않는다
        self.assertIn("summaryEl.hidden = list.length === 0;", render)
        self.assertIn('if (row.strong) dd.classList.add("is-strong");', render)   # 변경·모든 목적은 빨갛게 보인다
        prompt = console_function("promptRecentAuthToken")
        self.assertIn("function promptRecentAuthToken(options)", prompt)
        self.assertIn("if (summaryEl) renderRecentAuthSummary(summaryEl, options && options.summary);", prompt)
        self.assertLess(prompt.index("renderRecentAuthSummary("), prompt.index("dialog.showModal()"))


class TextTests(unittest.TestCase):

    def test_every_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table("ko-KR"), table("en-US")
        used = set(re.findall(r'"(' + KEYS + r')"', GRANT + APPROVE + function_source("allowanceSummaryRows")))
        used |= {k for _, k in re.findall(r'\["([A-Z_]+)", "(easy\.purpose\.[a-z_]+)"\]', JS)}
        self.assertTrue({"easy.ap.sum_write", "easy.ap.sum_standing", "easy.ap.d_pick_needed"} <= used)
        for key in sorted(used):
            self.assertIn(key, ko, key)
            self.assertIn(key, en, key)
            self.assertEqual(sorted(re.findall(r"\{[a-z]+\}", ko[key])), sorted(re.findall(r"\{[a-z]+\}", en[key])), key)


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class SummaryFunctionTests(unittest.TestCase):

    def rows(self, allowance):
        purposes = re.search(r"  const GRANT_PURPOSES = \[.*?\];\n", JS, re.S).group(0)
        script = (HARNESS.replace("__SOURCES__", purposes + function_source("allowanceSummaryRows"))
                  .replace("__NOW__", "0").replace("__EXPR__", f"allowanceSummaryRows({json.dumps(allowance)})"))
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table("ko-KR"))},
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_a_counted_write_is_spelled_out_and_marked(self):
        rows = self.rows({"purpose": "STOCK_UPDATE", "kind": "WRITE", "system": "COUPANG", "count": 2, "per_hour_limit": None, "hours": None})
        self.assertEqual([r["label"] for r in rows], ["허용할 일", "종류", "보내는 곳", "범위"])
        self.assertEqual([r["value"] for r in rows], ["쿠팡 재고 수량 변경", "변경 (실제 값을 바꿔요)", "쿠팡", "2회"])
        self.assertEqual([bool(r.get("strong")) for r in rows], [False, True, False, False])

    def test_a_standing_read_shows_the_hourly_limit_and_period(self):
        rows = self.rows({"purpose": "AFTER_SALES_CHECK", "kind": "READ", "system": "COUPANG", "count": None, "per_hour_limit": 30, "hours": 24})
        self.assertEqual([r["value"] for r in rows], ["반품·민원·정산 확인", "조회", "쿠팡", "시간당 30회 · 24시간 동안"])
        self.assertFalse(any(r.get("strong") for r in rows))

    def test_any_purpose_and_onchannel_are_shown_as_such(self):
        rows = self.rows({"purpose": "ANY", "kind": "READ", "system": "ONCH", "count": 1})
        self.assertEqual(rows[0]["value"], table("ko-KR")["easy.purpose.any"])
        self.assertTrue(rows[0]["strong"])
        self.assertEqual(rows[2]["value"], "온채널")

    def test_a_pending_request_uses_its_server_labels(self):
        rows = self.rows({"purpose": "ORDER_DETECTION", "purpose_label": "신규 주문 자동 감지", "system_label": "쿠팡", "kind": "READ",
                          "per_hour_limit": 14, "hours": 24})
        self.assertEqual([r["value"] for r in rows], ["신규 주문 자동 감지", "조회", "쿠팡", "시간당 14회 · 24시간 동안"])
        rows = self.rows({"purpose": "ORDER_DETECTION", "purpose_label": "서버가 붙인 이름", "kind": "READ", "per_hour_limit": 14, "hours": 48})
        self.assertEqual((rows[0]["value"], rows[3]["value"]), ("서버가 붙인 이름", "시간당 14회 · 48시간 동안"))   # 서버 이름·입력한 기간 그대로

    def test_a_missing_count_is_not_shown_as_a_number(self):
        rows = self.rows({"purpose": "STOCK_UPDATE", "kind": "READ", "system": "COUPANG", "count": None, "per_hour_limit": None})
        self.assertEqual(rows[3]["value"], "횟수 없음")


if __name__ == "__main__":
    unittest.main()
