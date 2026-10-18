"""
=========================================================
Homez OS

File : tests/test_easy_briefing_ui.py

2026-10-18 — 상품 관리 AI P4 "오늘 브리핑"(규칙 기반, AI 없음·새 호출 없음)을 고정한다. 실제 `easy_view.js`의 순수 함수를 Node에서 실행한다. 네트워크·DB 없음.

  - 가장 먼저 볼 것: "내가 할 일"(진행 중이 아닌 것) 앞의 3개. 없으면 "급한 일이 없다".
  - 24시간 안에 있을 일: 허용이 이미 만료됐거나 24시간 안에 만료되는 것만(그 밖은 나오지 않는다).
  - 멈춘 건 아닌지: 주문 감지는 성공 기록이 (주기의 3배, 최소 30분)보다 오래되면, 상품 상태는 아직 승인 안 된 상품이 있고 90분 넘게 못 읽었으면,
    정산은 36시간 넘게 못 읽었으면. 꺼 둔 기능(OFF/PAUSED)이나 읽은 기록이 없는 항목은 멈춤으로 단정하지 않는다.
  - 시간대 표기가 없는 서버 시각은 UTC로 읽는다. 화면 글은 이스케이프한다.
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
JS = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

NOW = 1_760_000_000_000  # 고정 시각(ms)


def iso_before(minutes):
    from datetime import datetime, timedelta, timezone

    return (datetime.fromtimestamp(NOW / 1000, timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


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
                # 정규식 리터럴: 닫는 /까지 건너뛴다
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


def constants_source():
    return "\n".join(m.group(0) for m in re.finditer(r"^  const BRIEF_[A-Z_]+ = [^\n]+$", JS, re.M))


def ko_table():
    block = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.brief\.[a-z_]+)": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
const NOW = __NOW__;
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BriefingFunctionTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join([constants_source()] + [function_source(n) for n in ("parseUtcMs", "briefWeight", "briefSysText", "briefingFrom", "briefingHtml")])
        script = HARNESS.replace("__SOURCES__", sources).replace("__NOW__", str(NOW)).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(ko_table())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def brief(self, **parts):
        data = {"rows": [], "ext": None, "collect": None, "today": None, "nowMs": NOW, **parts}
        return self.run_js(f"briefingFrom({json.dumps(data)})")

    # ---- 가장 먼저 볼 것 ----

    def test_the_first_three_things_that_need_the_user_come_first_and_progress_rows_are_not_counted(self):
        rows = [
            {"key": "a", "group": "CHECK", "title": "반려", "product": {"name": "상품1"}},
            {"key": "b", "group": "CHECK", "title": "송장", "product": {"name": "상품2"}},
            {"key": "c", "group": "APPROVAL", "title": "허용", "product": {"name": "허용 요청"}},
            {"key": "d", "group": "APPROVAL", "title": "넷째", "product": {"name": "x"}},
            {"key": "e", "group": "PROGRESS", "title": "진행", "product": {"name": "y"}},
        ]
        result = self.brief(rows=rows)
        self.assertEqual([u["key"] for u in result["urgent"]], ["a", "b", "c"])
        self.assertEqual(result["mine"], 4)

    def test_sales_and_customer_facing_work_comes_before_housekeeping(self):
        rows = [
            {"key": "days:1", "group": "CHECK", "title": "출고일", "product": {"name": "a"}},
            {"key": "wizard:9", "group": "CHECK", "title": "등록", "product": {"name": "b"}},
            {"key": "allowances", "group": "APPROVAL", "title": "허용", "product": {"name": "c"}},
            {"key": "inq:5", "group": "CHECK", "title": "문의", "product": {"name": "d"}},
            {"key": "invoice:3", "group": "CHECK", "title": "송장", "product": {"name": "e"}},
            {"key": "pstatus:7", "group": "CHECK", "title": "반려", "product": {"name": "f"}},
        ]
        keys = [u["key"] for u in self.brief(rows=rows)["urgent"]]
        self.assertEqual(keys, ["pstatus:7", "invoice:3", "inq:5"])
        self.assertEqual(self.brief(rows=rows)["mine"], 6)

    def test_equal_weight_rows_keep_their_order_and_checks_stay_ahead_of_approvals(self):
        rows = [
            {"key": "allowances", "group": "APPROVAL", "title": "허용", "product": None},
            {"key": "x:1", "group": "CHECK", "title": "하나", "product": None},
            {"key": "x:2", "group": "CHECK", "title": "둘", "product": None},
        ]
        self.assertEqual([u["key"] for u in self.brief(rows=rows)["urgent"]], ["x:1", "x:2", "allowances"])

    def test_nothing_to_do_gives_no_urgent_items(self):
        result = self.brief(rows=[{"key": "e", "group": "PROGRESS", "title": "진행", "product": None}])
        self.assertEqual((result["urgent"], result["mine"]), ([], 0))
        self.assertEqual(self.brief()["mine"], 0)

    # ---- 24시간 안에 있을 일 ----

    def test_only_expired_or_soon_to_expire_allowances_are_listed(self):
        ext = {"renewable": [
            {"state": "EXPIRED", "purpose_label": "주문 감지"}, {"state": "ACTIVE", "hours_left": 5, "purpose_label": "상품 상태"},
            {"state": "ACTIVE", "hours_left": 24, "purpose_label": "경계"}, {"state": "ACTIVE", "hours_left": 25, "purpose_label": "먼 것"},
            {"state": "ACTIVE", "hours_left": None, "purpose_label": "값 없음"},
        ]}
        up = self.brief(ext=ext)["upcoming"]
        self.assertEqual([(u["kind"], u["what"]) for u in up], [("expired", "주문 감지"), ("soon", "상품 상태"), ("soon", "경계")])
        self.assertEqual(up[1]["hours"], 5)

    # ---- 멈춘 건 아닌지 ----

    def test_order_detection_is_flagged_after_three_intervals_but_not_before(self):
        collect = lambda minutes, **kw: {"health": "OK", "interval_minutes": 5, "last_succeeded_at": iso_before(minutes), **kw}
        self.assertEqual(self.brief(collect=collect(29))["stalled"], [])
        self.assertEqual(self.brief(collect=collect(31))["stalled"], [{"kind": "detect", "minutes": 31}])
        # 주기가 길면 주기의 3배까지는 정상
        self.assertEqual(self.brief(collect=collect(100, interval_minutes=60))["stalled"], [])
        self.assertEqual(self.brief(collect=collect(200, interval_minutes=60))["stalled"][0]["kind"], "detect")

    def test_switched_off_or_unrecorded_detection_is_not_called_stalled(self):
        for health in ("OFF", "PAUSED", "ERROR", "BLOCKED"):  # 이 상태는 다른 줄(감지 상태)에서 따로 알린다
            self.assertEqual(self.brief(collect={"health": health, "interval_minutes": 5, "last_succeeded_at": iso_before(500)})["stalled"], [], health)
        self.assertEqual(self.brief(collect={"health": "OK", "interval_minutes": 5, "last_succeeded_at": None})["stalled"], [])
        self.assertEqual(self.brief(collect=None)["stalled"], [])

    def test_product_status_is_flagged_only_when_an_unapproved_product_exists(self):
        old = iso_before(120)
        review = {"product_status": [{"status": "REVIEW", "checked_at": old}, {"status": "APPROVED", "checked_at": iso_before(1)}]}
        self.assertEqual(self.brief(today=review)["stalled"], [{"kind": "pstatus", "minutes": 120}])
        self.assertEqual(self.brief(today={"product_status": [{"status": "REVIEW", "checked_at": iso_before(89)}]})["stalled"], [])
        approved_only = {"product_status": [{"status": "APPROVED", "checked_at": old}, {"status": "DELETED", "checked_at": old}]}
        self.assertEqual(self.brief(today=approved_only)["stalled"], [])  # 승인된 상품은 하루 한 번만 읽는다
        self.assertEqual(self.brief(today={"product_status": [{"status": "DENIED", "checked_at": None}]})["stalled"], [])  # 읽은 기록 없음은 단정하지 않는다

    def test_the_freshest_product_read_counts(self):
        today = {"product_status": [{"status": "REVIEW", "checked_at": iso_before(500)}, {"status": "DENIED", "checked_at": iso_before(10)}]}
        self.assertEqual(self.brief(today=today)["stalled"], [])

    def test_settlement_is_flagged_after_36_hours_only_when_it_was_ever_read(self):
        self.assertEqual(self.brief(today={"settlement_checked_at": iso_before(35 * 60)})["stalled"], [])
        self.assertEqual(self.brief(today={"settlement_checked_at": iso_before(40 * 60)})["stalled"], [{"kind": "settle", "hours": 40}])
        self.assertEqual(self.brief(today={"settlement_checked_at": None})["stalled"], [])

    def test_times_without_a_timezone_are_read_as_utc(self):
        self.assertEqual(self.run_js('parseUtcMs("2026-10-18T00:00:00")'), self.run_js('parseUtcMs("2026-10-18T00:00:00Z")'))
        self.assertEqual(self.run_js('parseUtcMs("2026-10-18T09:00:00+09:00")'), self.run_js('parseUtcMs("2026-10-18T00:00:00Z")'))
        for bad in ('""', "null", '"가짜"'):
            self.assertIsNone(self.run_js(f"parseUtcMs({bad})"))

    # ---- 앱 상태 점검(T1) ----

    def health(self, *codes):
        return {"overall": "FAIL", "checks": [{"code": c, "level": lv, "params": p} for c, lv, p in codes], "passed": [], "unknown": []}

    def test_system_checks_from_the_server_are_listed_and_unknown_or_info_codes_are_skipped(self):
        health = self.health(("SCHEDULER_NOT_RUNNING", "FAIL", {}), ("BACKUP_STALE", "WARN", {"days": 9}), ("REHEARSAL_NONE", "INFO", {}),
                             ("SOMETHING_NEW", "FAIL", {}), ("DISK_LOW", "OK", {"free_mb": 10}))
        system = self.brief(health=health)["system"]
        self.assertEqual([(c["code"], c["level"]) for c in system], [("SCHEDULER_NOT_RUNNING", "FAIL"), ("BACKUP_STALE", "WARN")])

    def test_missing_or_malformed_health_gives_no_system_lines_and_does_not_break_the_briefing(self):
        for health in (None, {}, {"checks": None}, {"checks": "x"}, {"checks": [None, 3, {}]}):
            self.assertEqual(self.brief(health=health)["system"], [])
        self.assertEqual(self.brief()["system"], [])

    def test_system_lines_show_in_the_stalled_card_with_numbers_and_colour(self):
        data = {"mine": 0, "urgent": [], "upcoming": [], "stalled": [], "system": [
            {"code": "BACKUP_OLD", "level": "FAIL", "params": {"days": 41}},
            {"code": "DISK_LOW", "level": "WARN", "params": {"free_mb": 512}},
            {"code": "JOBS_OVERDUE", "level": "WARN", "params": {"jobs": ["a", "b"]}}]}
        html = self.run_js(f"briefingHtml({json.dumps(data)})")
        for text in ("마지막 백업이 41일 전이에요", "512MB", "자동 작업 2개가 제때 돌지 않고"):
            self.assertIn(text, html)
        self.assertNotIn("자동 확인이 모두 최근에 돌았어요", html)
        self.assertRegex(html, r"ez-c-red[^>]*>[^<]*41일")
        self.assertRegex(html, r"ez-c-orange[^>]*>[^<]*512MB")

    def test_stalled_items_and_system_lines_appear_together_and_nothing_hides_the_all_clear_when_empty(self):
        data = {"mine": 0, "urgent": [], "upcoming": [], "stalled": [{"kind": "detect", "minutes": 45}], "system": [{"code": "ESTOP_ACTIVE", "level": "WARN", "params": {}}]}
        html = self.run_js(f"briefingHtml({json.dumps(data)})")
        self.assertIn("45분째", html)
        self.assertIn("긴급 중지가 켜져 있어", html)
        empty = self.run_js('briefingHtml({"mine": 0, "urgent": [], "upcoming": [], "stalled": [], "system": []})')
        self.assertIn("자동 확인이 모두 최근에 돌았어요", empty)

    # ---- 화면 글 ----

    def test_the_html_shows_each_section_and_escapes_text(self):
        data = {
            "mine": 2, "urgent": [{"key": "a", "title": "<b>반려</b>", "name": "<i>상품</i>"}],
            "upcoming": [{"kind": "soon", "what": "<u>감지</u>", "hours": 3}, {"kind": "expired", "what": "상태"}],
            "stalled": [{"kind": "detect", "minutes": 45}, {"kind": "settle", "hours": 40}],
        }
        html = self.run_js(f"briefingHtml({json.dumps(data)})")
        self.assertNotIn("<b>반려", html)
        self.assertNotIn("<i>상품", html)
        self.assertNotIn("<u>감지", html)
        for text in ("오늘 브리핑", "지금 직접 할 일이 2건", "3시간 뒤 만료", "만료됐어요", "45분째", "40시간째"):
            self.assertIn(text, html)

    def test_empty_sections_say_so_instead_of_staying_blank(self):
        html = self.run_js('briefingHtml({"mine": 0, "urgent": [], "upcoming": [], "stalled": []})')
        for text in ("지금 직접 할 일은 없어요", "급한 일이 없어요", "만료되는 허용이 없어요", "자동 확인이 모두 최근에 돌았어요"):
            self.assertIn(text, html)


class WiringTests(unittest.TestCase):

    def test_the_briefing_is_built_from_data_already_loaded_and_rendered_above_the_flow(self):
        body = JS.split("async function loadToday()")[1]
        self.assertIn("const brief = briefingFrom({ rows, ext, collect, today, health: sysHealth, nowMs: Date.now() });", body)
        self.assertLess(body.index("${briefingHtml(brief)}"), body.index('class="ez-flow"'))

    def test_the_briefing_code_makes_no_request(self):
        code = "\n".join(function_source(n) for n in ("parseUtcMs", "briefWeight", "briefSysText", "briefingFrom", "briefingHtml"))
        for forbidden in ("api(", "apiFetch", "fetch(", "XMLHttpRequest", "localStorage", "method:"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_every_text_exists_in_both_languages_with_matching_placeholders(self):
        used = set(re.findall(r'T\(\s*"(easy\.brief\.[a-z_]+)"', JS)) | {f"easy.brief.st_{k}" for k in ("detect", "pstatus", "settle")}
        used |= {"easy.brief.exp_soon", "easy.brief.exp_done", "easy.brief.headline", "easy.brief.headline_none"}
        used |= set(re.findall(r'"(easy\.brief\.sys_[a-z_]+)"', re.search(r"const BRIEF_SYS_KEYS = [^\n]+", JS).group(0)))
        ko = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        en = re.search(r'"en-US": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        for key in used:
            kv = re.search(rf'"{re.escape(key)}": "([^"]*)"', ko)
            ev = re.search(rf'"{re.escape(key)}": "([^"]*)"', en)
            self.assertTrue(kv and ev, key)
            self.assertEqual(set(re.findall(r"\{(\w+)\}", kv.group(1))), set(re.findall(r"\{(\w+)\}", ev.group(1))), key)

    def test_every_server_check_code_has_a_screen_text_except_the_info_only_ones(self):
        rules = (ROOT / "app/domains/diagnostics/health_summary.py").read_text(encoding="utf-8")
        codes = set(re.findall(r'_check\("([A-Z_]+)", (FAIL|WARN)', rules))
        mapped = set(re.findall(r"([A-Z_]+): \"easy\.brief\.sys_", re.search(r"const BRIEF_SYS_KEYS = [^\n]+", JS).group(0)))
        self.assertEqual({c for c, _ in codes}, mapped)
        # 같은 문구 열쇠를 두 코드가 나눠 쓰지 않는다
        keys = re.findall(r'easy\.brief\.sys_[a-z_]+', re.search(r"const BRIEF_SYS_KEYS = [^\n]+", JS).group(0))
        self.assertEqual(len(keys), len(set(keys)))

    def test_texts_do_not_repeat_a_placeholder(self):
        for lang in ("ko-KR", "en-US"):
            block = re.search(rf'"{lang}": \{{(.*?)\n  \}}', TEXTS, re.S).group(1)
            for key, value in re.findall(r'"(easy\.brief\.sys_[a-z_]+)": "([^"]*)"', block):
                names = re.findall(r"\{(\w+)\}", value)
                self.assertEqual(len(names), len(set(names)), key)


if __name__ == "__main__":
    unittest.main()
