"""
=========================================================
Homez OS

File : tests/test_image_ai_settings_screen.py

2026-11-22 — 쉬운 보기 'AI 이미지 설정 (Gemini)' 칸(사용자 결정 "가 나 다 진행해라", "매월 제공하는 크레딧 100%까지만").
실제 화면 함수를 Node에서 실행하고, 서버 호출 위치·문구 짝은 소스에서 확인한다. 네트워크·DB·실제 호출 0.

  - 저장은 확인 창 → 비밀번호 재확인(1회용 토큰) 뒤 PUT. 키는 비밀번호 칸으로만 받고 화면에 다시 그리지 않는다.
  - 화면 쪽 검사: 모델(비우면 기본 모델)·크기(1K/2K/4K)·월 크레딧(0 초과 100 이하)·장당 가격(비우거나 0 초과 1 이하), 처음 저장에는 키가 있어야 한다.
  - 보여 주는 숫자(장당 추정·최근 31일 사용·남은 장수·80/100% 알림)는 서버가 계산한 값을 그대로 쓴다. 서버 글은 이스케이프한다.
=========================================================
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EASY = (ROOT / "app/web/easy/easy_view.js").read_text(encoding="utf-8")
TEXTS = (ROOT / "app/web/easy/easy_texts.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def function_source(name: str) -> str:
    match = re.search(r"\n  (?:async )?function " + re.escape(name) + r"\(", EASY)
    assert match, name
    end = EASY.index("\n  }\n", match.end()) if "\n  }\n" in EASY[match.end():] else EASY.index("\n  }\r\n", match.end())
    return EASY[match.start() + 1: end + 4]


def ko_table():
    start = TEXTS.index('"easy.imgai.title"')
    ko_line = TEXTS[TEXTS.rindex("\n", 0, start): TEXTS.index("\n", start)]
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.[a-z_.0-9]+)": ("(?:[^"\\]|\\.)*")', ko_line)}


class WiringTests(unittest.TestCase):

    def test_the_card_is_on_the_more_page_and_loads_with_the_other_ai_cards(self):
        self.assertIn('id="ez-imgai-box"', EASY)
        self.assertLess(EASY.index('id="ez-ai-box"'), EASY.index('id="ez-imgai-box"'))
        self.assertLess(EASY.index('id="ez-imgai-box"'), EASY.index('id="ez-aibg-box"'))
        self.assertIn("imageAiSettingsLoad();", function_source("loadMore"))
        self.assertIn('api("/media-assets/image-ai/settings")', function_source("imageAiSettingsLoad"))

    def test_saving_uses_the_password_recheck_and_a_password_field(self):
        edit = function_source("imageAiSettingsEdit")
        self.assertIn('api("/media-assets/image-ai/settings", { method: "PUT", headers: { "X-Recent-Auth-Token": token }', edit)
        self.assertLess(edit.index("imageAiSettingsForm(picked.get, s)"), edit.index("B.promptRecentAuthToken()"))
        self.assertLess(edit.index("B.promptRecentAuthToken()"), edit.index('method: "PUT"'))
        self.assertIn("if (token === null) return;", edit)
        self.assertIn('field("api_key", s.key_saved ? "easy.imgai.f_key_keep" : "easy.imgai.f_key", "", `type="password" autocomplete="new-password"`)', edit)

    def test_the_card_never_draws_the_key_and_escapes_server_text(self):
        html = function_source("imageAiSettingsHtml")
        self.assertNotIn("api_key", html)
        self.assertIn("s.key_saved ?", html)
        for needle in ('esc(r.ref || "—")', 'esc(r.image_size || "")', "<b>${esc(v)}</b>", "esc(when(r.at))"):
            self.assertIn(needle, html)

    def test_every_text_exists_in_both_languages_with_the_same_placeholders(self):
        used = set(re.findall(r'"(easy\.imgai\.[a-z_0-9]+)"', EASY))
        self.assertTrue(used)
        ko = ko_table()
        for key in used:
            self.assertEqual(TEXTS.count(f'"{key}":'), 2, key)                     # 한국어·영어 한 번씩
        defined = set(re.findall(r'"(easy\.imgai\.[a-z_0-9]+)":', TEXTS))
        self.assertEqual(defined - used, set(), "쓰지 않는 문구가 있다")
        en_start = TEXTS.rindex("\n", 0, TEXTS.index('"easy.imgai.title": "AI image settings (Gemini)"'))
        en_line = TEXTS[en_start: TEXTS.index("\n", en_start + 1)]
        en = {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(easy\.imgai\.[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', en_line)}
        for key in used:
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key])), key)
            self.assertEqual(len(re.findall(r"\{(\w+)\}", ko[key])), len(set(re.findall(r"\{(\w+)\}", ko[key]))), key)   # 자리는 한 번씩만 바뀐다
        self.assertIn("추가 결제 없이", ko["easy.imgai.limit_rule"])
        self.assertIn("추정", ko["easy.imgai.estimate_note"])


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const T = (key, params) => { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; };
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const chip = (kind, label) => `<span class="chip ${kind}">${esc(label)}</span>`;
__SOURCES__
const get = (o) => (n) => (n in o ? o[n] : "");
console.log(JSON.stringify(__EXPR__));
"""


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join(function_source(n) for n in ("imageAiSettingsHtml", "imageAiSettingsForm"))
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        table = {**ko_table(), "easy.ai.ready": "사용 준비됨", "easy.ai.not_ready": "설정 필요", "easy.ai.key_saved": "저장됨", "easy.ai.key_none": "없음",
                 "easy.ai.unknown": "알 수 없음"}
        proc = subprocess.run([NODE, "-e", script], env={"TABLE_JSON": json.dumps(table), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def form(self, values, saved=None):
        return self.run_js(f"imageAiSettingsForm(get({json.dumps(values)}), {json.dumps(saved)})")

    def test_the_form_checks_and_builds_the_body(self):
        ok = self.form({"model": "", "image_size": " 2k ", "credit": "10", "price": "", "api_key": " AIza-key "}, {"default_model": "gemini-nano-banana-2.1"})
        self.assertEqual(ok, {"body": {"model": "gemini-nano-banana-2.1", "image_size": "2K", "monthly_credit_usd": 10, "price_per_image_usd": None, "api_key": "AIza-key"}})
        kept = self.form({"model": "gemini-9-image", "image_size": "4K", "credit": "40", "price": "0.2"}, {"key_saved": True})
        self.assertEqual(kept, {"body": {"model": "gemini-9-image", "image_size": "4K", "monthly_credit_usd": 40, "price_per_image_usd": 0.2}})
        cases = [
            ({"model": "bad model", "image_size": "2K", "credit": "10", "api_key": "k"}, "easy.imgai.err_model"),
            ({"image_size": "3K", "credit": "10", "api_key": "k"}, "easy.imgai.err_size"),
            ({"image_size": "2K", "credit": "", "api_key": "k"}, "easy.imgai.err_credit"),
            ({"image_size": "2K", "credit": "0", "api_key": "k"}, "easy.imgai.err_credit"),
            ({"image_size": "2K", "credit": "100.5", "api_key": "k"}, "easy.imgai.err_credit"),
            ({"image_size": "2K", "credit": "abc", "api_key": "k"}, "easy.imgai.err_credit"),
            ({"image_size": "2K", "credit": "10", "price": "0", "api_key": "k"}, "easy.imgai.err_price"),
            ({"image_size": "2K", "credit": "10", "price": "2", "api_key": "k"}, "easy.imgai.err_price"),
            ({"image_size": "2K", "credit": "10", "price": "x", "api_key": "k"}, "easy.imgai.err_price"),
            ({"image_size": "2K", "credit": "10", "api_key": "   "}, "easy.imgai.err_key"),          # 처음 저장에는 키가 있어야 한다
        ]
        for values, error in cases:
            self.assertEqual(self.form(values, {}), {"error": error}, values)

    def test_the_card_shows_the_server_numbers(self):
        status = {"configured": True, "key_saved": True, "model": "gemini-nano-banana-2.1", "image_size": "2K", "monthly_credit_usd": 10, "estimated_cost_per_image_usd": 0.0554,
                  "window_days": 31, "window_used_usd": 8.31, "window_calls": 150, "images_left": 30, "window_remaining_usd": 1.69, "credit_alert_pct": 80,
                  "records": [{"ref": "REF<b>7#1", "image_size": "2K", "cost_usd": 0.0554, "ok": True, "at": "2026-11-22T03:00:00"}]}
        html = self.run_js(f"imageAiSettingsHtml({json.dumps(status)})")
        for part in ("$10.00", "$0.0554", "$8.31 · 150회", "30장 (남은 크레딧 약 $1.69)", "최근 31일", "80%를 넘게", "REF&lt;b&gt;7#1", "11. 22. 오후 12:00", "사용 준비됨", "저장됨"):
            self.assertIn(part, html)
        self.assertNotIn("undefined", html)
        self.assertNotIn("{", re.sub(r"<[^>]+>", "", html).replace("{ ", ""))      # 바뀌지 않은 자리표시자가 없다
        empty = self.run_js('imageAiSettingsHtml({"configured": false, "key_saved": false})')
        self.assertIn("설정 필요", empty)
        self.assertIn("아직 사용 기록이 없어요", empty)
        self.assertNotIn("undefined", empty)
        self.assertNotIn("NaN", empty)
        full = self.run_js('imageAiSettingsHtml({"configured": true, "credit_alert_pct": 100, "window_days": 31})')
        self.assertIn("31일 창에서 빠질 때까지", full)


if __name__ == "__main__":
    unittest.main()
