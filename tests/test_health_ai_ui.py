"""
=========================================================
Homez OS

File : tests/test_health_ai_ui.py

2026-10-18 — 서버 관리 AI T2: 쉬운 보기 "AI로 원인 설명 받기"(`easy_view.js`)를 고정한다. 실제 화면 함수를 Node에서 실행한다. 네트워크·DB 없음.

  - 버튼은 오늘 브리핑에 위험·주의 점검 줄이 있을 때만 보인다. 누르면 먼저 무엇이 전송되는지 묻고, 취소하면 서버 요청이 없다.
  - 요청은 `POST /diagnostics/health-advice` 하나이고 본문이 없다(점검 결과는 서버가 모은다).
  - 결과 화면: 점검 줄(숫자 포함) 아래에 AI의 원인·조치, 요약은 있을 때만, 알 수 없는 코드는 건너뛴다. 모든 글은 이스케이프한다.
  - 실패·검증 탈락은 알림/안내로 보이고 화면이 깨지지 않는다. 문구는 ko/en 짝이 맞고 자리표시자가 겹치지 않는다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_easy_briefing_ui import JS
from tests.test_easy_briefing_ui import NODE
from tests.test_easy_briefing_ui import TEXTS
from tests.test_easy_briefing_ui import constants_source
from tests.test_easy_briefing_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "app/domains/diagnostics/health_ai_service.py").read_text(encoding="utf-8")


def ko_table():
    block = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.(?:brief|sysadv)\.[a-z_]+)": ("(?:[^"\\]|\\.)*")', block)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const LOG = { api: [], dialogs: [], toasts: [] };
let DIALOG_ANSWERS = [];
let API_RESULT = null;
const api = async (path, opts) => { LOG.api.push({ path, opts: opts || null }); if (API_RESULT instanceof Error) throw API_RESULT; return API_RESULT; };
const ezDialog = async (opts) => { LOG.dialogs.push(opts); return DIALOG_ANSWERS.length ? DIALOG_ANSWERS.shift() : true; };
const B = { toast: (message, kind) => LOG.toasts.push({ message, kind }) };
__SOURCES__
(async () => {
__BODY__
})().then((out) => console.log(JSON.stringify(out))).catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""

SOURCE_NAMES = ("parseUtcMs", "briefSysText", "briefingHtml", "systemAdviceHtml", "systemAdviceAsk")
SYSTEM = [
    {"code": "BACKUP_STALE", "level": "WARN", "params": {"days": 9}},
    {"code": "DISK_LOW", "level": "WARN", "params": {"free_mb": 512}},
    {"code": "SCHEDULER_NOT_RUNNING", "level": "FAIL", "params": {}},
]
ADVICE = {
    "summary": "백업이 오래됐어요.",
    "items": [
        {"code": "BACKUP_STALE", "cause": "한동안 백업을 만들지 않았어요.", "steps": ["백업을 새로 만들어 두세요."]},
        {"code": "DISK_LOW", "cause": "공간이 부족해지고 있어요.", "steps": ["큰 파일을 옮기세요.", "상태를 다시 확인하세요."]},
    ],
}


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class ScreenTests(unittest.TestCase):

    def run_js(self, body, *, names=SOURCE_NAMES):
        sources = "\n".join([constants_source()] + [function_source(n) for n in names])
        script = HARNESS.replace("__SOURCES__", sources).replace("__BODY__", body)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(ko_table())}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def ask(self, *, api_result, answers=None, system=SYSTEM):
        return self.run_js(
            f"API_RESULT = {json.dumps(api_result)}; DIALOG_ANSWERS = {json.dumps(answers or [True, True])};\n"
            f"await systemAdviceAsk({json.dumps(system)}); return LOG;")

    # ---- 버튼 ----

    def test_the_button_shows_only_when_there_are_system_lines(self):
        base = {"mine": 0, "urgent": [], "upcoming": [], "stalled": []}
        with_lines = self.run_js(f"return briefingHtml({json.dumps({**base, 'system': SYSTEM})});")
        self.assertIn("data-ez-sysadv", with_lines)
        self.assertIn("AI로 원인 설명 받기", with_lines)
        for system in ([], None):
            html = self.run_js(f"return briefingHtml({json.dumps({**base, 'system': system})});")
            self.assertNotIn("data-ez-sysadv", html)
        only_stalled = self.run_js(f"return briefingHtml({json.dumps({**base, 'stalled': [{'kind': 'detect', 'minutes': 45}], 'system': []})});")
        self.assertNotIn("data-ez-sysadv", only_stalled)  # 주문 감지 정체는 이 설명 대상이 아니다

    # ---- 묻기·요청 ----

    def test_cancelling_the_confirmation_sends_nothing(self):
        log = self.ask(api_result=None, answers=[False])
        self.assertEqual(log["api"], [])
        self.assertEqual(len(log["dialogs"]), 1)
        body = log["dialogs"][0]["bodyHtml"]
        for text in ("외부 AI 서비스(Claude)", "사용료", "로그·파일 경로·설정·개인정보는 보내지 않아요", "승인 센터", "4개"):
            self.assertIn(text, body)

    def test_the_request_is_one_post_without_a_body(self):
        log = self.ask(api_result={"advice": ADVICE, "cached": False, "usage": None})
        self.assertEqual(len(log["api"]), 1)
        self.assertEqual(log["api"][0]["path"], "/diagnostics/health-advice")
        self.assertEqual(log["api"][0]["opts"], {"method": "POST"})

    def test_the_result_shows_each_item_under_its_check_line_and_escapes_text(self):
        evil = {"summary": "<b>요약</b>", "items": [{"code": "DISK_LOW", "cause": "<img src=x onerror=alert(1)>", "steps": ["<script>x</script>"]}]}
        log = self.ask(api_result={"advice": evil, "cached": False, "usage": {"cost_krw": 20, "month_used_krw": 40.4, "monthly_limit_krw": 5000}})
        html = log["dialogs"][1]["bodyHtml"]
        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<b>요약", html)
        self.assertIn("&lt;img", html)
        self.assertIn("저장 공간이 512MB밖에 남지 않았어요", html)  # 점검 줄(숫자 포함)이 제목
        self.assertIn("이번 설명 약 20원 · 이번 달 40원 / 한도 5000원", html)

    def test_items_summary_note_and_steps(self):
        html = self.ask(api_result={"advice": ADVICE, "cached": False})["dialogs"][1]["bodyHtml"]
        for text in ("백업이 오래됐어요.", "마지막 백업이 9일 전이에요", "한동안 백업을 만들지 않았어요.", "백업을 새로 만들어 두세요.", "큰 파일을 옮기세요.", "이렇게 해 보세요", "직접 해 주세요"):
            self.assertIn(text, html)
        self.assertLess(html.index("마지막 백업이 9일 전이에요"), html.index("저장 공간이 512MB"))
        no_summary = self.ask(api_result={"advice": {"items": ADVICE["items"]}, "cached": False})["dialogs"][1]["bodyHtml"]
        self.assertNotIn("undefined", no_summary)

    def test_unknown_codes_and_empty_items_do_not_break_the_screen(self):
        advice = {"items": [{"code": "NOT_A_CODE", "cause": "x", "steps": ["y"]}, ADVICE["items"][0], None, {}]}
        html = self.ask(api_result={"advice": advice, "cached": False})["dialogs"][1]["bodyHtml"]
        self.assertIn("마지막 백업이 9일 전이에요", html)
        self.assertNotIn("NOT_A_CODE", html)
        self.assertNotIn("undefined", html)

    def test_an_item_for_a_check_that_is_no_longer_shown_still_gets_its_line(self):
        html = self.ask(api_result={"advice": ADVICE, "cached": False}, system=[])["dialogs"][1]["bodyHtml"]
        self.assertIn("저장 공간이", html)
        self.assertIn("마지막 백업이 …일 전이에요", html)  # 값이 없으면 "undefined" 대신 "…"
        self.assertNotIn("undefined", html)

    def test_a_cached_advice_says_so_with_the_time(self):
        html = self.ask(api_result={"advice": ADVICE, "cached": True, "at": "2026-10-18T03:00:00", "usage": None})["dialogs"][1]["bodyHtml"]
        self.assertIn("이미 받아 둔 설명이에요", html)
        self.assertNotIn("이번 설명 약", html)

    def test_a_rejected_advice_shows_the_notice_and_reasons_escaped(self):
        log = self.ask(api_result={"advice": None, "rejected": {"all": "<i>통과한 항목이 없어</i>"}, "cached": False, "usage": {"cost_krw": 20, "month_used_krw": 20, "monthly_limit_krw": 5000}})
        html = log["dialogs"][1]["bodyHtml"]
        self.assertIn("검증을 통과하지 못해", html)
        self.assertNotIn("<i>", html)
        self.assertIn("&lt;i&gt;", html)

    def test_a_failed_request_shows_a_toast_and_no_result_window(self):
        log = self.run_js(
            "API_RESULT = new Error('이번 달 AI 사용 한도를 넘을 수 있어 호출하지 않았습니다.'); await systemAdviceAsk([]); return LOG;")
        self.assertEqual(len(log["dialogs"]), 1)
        self.assertEqual(log["toasts"], [{"message": "이번 달 AI 사용 한도를 넘을 수 있어 호출하지 않았습니다.", "kind": "error"}])
        blank = self.run_js("API_RESULT = Object.assign(new Error(''), {message: ''}); await systemAdviceAsk([]); return LOG;")
        self.assertEqual(blank["toasts"][0]["message"], "AI 설명을 받지 못했어요")


class WiringTests(unittest.TestCase):

    def test_the_button_is_bound_to_the_briefing_system_lines(self):
        body = JS.split("async function loadToday()")[1]
        self.assertIn('root.querySelector("[data-ez-sysadv]")', body)
        self.assertIn("systemAdviceAsk(brief.system)", body)

    def test_the_ai_screen_code_makes_no_other_request(self):
        code = "\n".join(function_source(n) for n in ("briefSysText", "systemAdviceHtml", "systemAdviceAsk"))
        self.assertEqual(re.findall(r"api\(", code), ["api("])
        self.assertIn('api("/diagnostics/health-advice", { method: "POST" })', code)
        for forbidden in ("fetch(", "XMLHttpRequest", "localStorage", "body:", "innerHTML"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_every_checked_code_is_explainable_and_shown(self):
        mapped = set(re.findall(r"([A-Z_]+): \"easy\.brief\.sys_", re.search(r"const BRIEF_SYS_KEYS = [^\n]+", JS).group(0)))
        meanings = set(re.findall(r'^    "([A-Z_]+)": "', SERVICE.split("CODE_MEANING = {")[1].split("\n}")[0], re.M))
        self.assertEqual(mapped, meanings)

    def test_every_text_exists_in_both_languages_with_matching_placeholders_and_no_repeats(self):
        used = set(re.findall(r'T\(\s*"(easy\.sysadv\.[a-z_]+)"', JS))
        self.assertTrue({"easy.sysadv.btn", "easy.sysadv.ask_title", "easy.sysadv.ask_body", "easy.sysadv.title", "easy.sysadv.steps", "easy.sysadv.note",
                         "easy.sysadv.none", "easy.sysadv.usage", "easy.sysadv.cached", "easy.sysadv.failed", "easy.sysadv.close"} <= used)
        ko = re.search(r'"ko-KR": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        en = re.search(r'"en-US": \{(.*?)\n  \}', TEXTS, re.S).group(1)
        for key in used:
            kv = re.search(rf'"{re.escape(key)}": "((?:[^"\\]|\\.)*)"', ko)
            ev = re.search(rf'"{re.escape(key)}": "((?:[^"\\]|\\.)*)"', en)
            self.assertTrue(kv and ev, key)
            names_ko, names_en = re.findall(r"\{(\w+)\}", kv.group(1)), re.findall(r"\{(\w+)\}", ev.group(1))
            self.assertEqual(set(names_ko), set(names_en), key)
            self.assertEqual(len(names_ko), len(set(names_ko)), key)  # 같은 자리표시자를 두 번 쓰면 첫 번째만 바뀐다


if __name__ == "__main__":
    unittest.main()
