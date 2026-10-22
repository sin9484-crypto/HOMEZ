"""
=========================================================
Homez OS

File : tests/test_batch_adjust_ui.py

2026-10-23 — 이미지 AI R2: 위저드 이미지 단계의 "일괄 보정" 화면(`console.js`의 `lwBindBatchAdjust` 등)을 고정한다. 실제 화면 함수를 Node에서 실행하고 소스 배선·문구 짝을 확인한다. 네트워크·DB 0.

  - 실행 전 막기: 고른 사진 없음, 20장 초과, 범위(−50~+50) 밖·정수 아님, 둘 다 0. 서버도 같은 규칙으로 다시 막는다.
  - 요청 본문: 사진 번호 목록·밝기·색온도(숫자).
  - 결과 안내: 새로 만든 장수·이미 있던 장수·건너뜀·실패, 새로 만든 것이 아닌 사진마다 이유를 사람 말로. 서버가 준 글은 이스케이프하고 모르는 코드는 일반 문구로.
  - 문구는 한국어·영어가 짝을 이루고 자리표시자가 맞다. 사진 카드마다 보정 대상 체크가 있다. 이미지에 글자·워터마크를 넣지 않는다는 안내가 보인다.
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
            if ch == "/" and JS[i + 1] != "/" and JS[:i].rstrip()[-1:] in "(,=:[!&|?{};":
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
    return "\n".join(m.group(0) for m in re.finditer(r"^  (?:const LW_ADJ_[A-Z_]+ = [^\n]+|let lwAdjNotice = [^\n]+)$", JS, re.M))


def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.adj_[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

FUNCTIONS = ("lwAdjProblem", "lwAdjBody", "lwAdjReasonText", "lwAdjSummaryHtml", "lwAdjNoticeHtml")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BehaviourTests(unittest.TestCase):

    def run_js(self, expression):
        sources = "\n".join([constants_source()] + [function_source(n) for n in FUNCTIONS])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def problem(self, state, ids):
        return self.run_js(f"lwAdjProblem({json.dumps(state)}, {json.dumps(ids)})")

    def test_constants_are_pinned(self):
        self.assertEqual(self.run_js("[LW_ADJ_MAX_PHOTOS, LW_ADJ_LIMIT]"), [20, 50])

    def test_a_valid_selection_has_no_problem_including_the_boundaries(self):
        for state in ({"brightness": 10, "warmth": 0}, {"brightness": 0, "warmth": -5}, {"brightness": 50, "warmth": 50}, {"brightness": -50, "warmth": -50}):
            self.assertIsNone(self.problem(state, [1]), state)
        self.assertIsNone(self.problem({"brightness": 5, "warmth": 5}, list(range(1, 21))))

    def test_each_blocking_rule(self):
        ok = {"brightness": 10, "warmth": 0}
        self.assertEqual(self.problem(ok, []), "adj_err_none")
        self.assertEqual(self.problem(ok, None), "adj_err_none")
        self.assertEqual(self.problem(ok, list(range(1, 22))), "adj_err_many")
        for state in ({"brightness": 51, "warmth": 0}, {"brightness": 0, "warmth": -51}, {"brightness": 1.5, "warmth": 0}, {"brightness": "5", "warmth": 0}, {"brightness": None, "warmth": 0}):
            self.assertEqual(self.problem(state, [1]), "adj_err_range", state)
        self.assertEqual(self.problem({"brightness": 0, "warmth": 0}, [1]), "adj_err_zero")

    def test_no_photos_is_reported_before_the_value_problems(self):
        self.assertEqual(self.problem({"brightness": 0, "warmth": 0}, []), "adj_err_none")

    def test_request_body_uses_numbers(self):
        self.assertEqual(self.run_js('lwAdjBody({"brightness": "10", "warmth": -5}, ["3", 4])'), {"asset_ids": [3, 4], "brightness": 10, "warmth": -5})

    def test_reason_texts(self):
        self.assertEqual(self.run_js('lwAdjReasonText("NOT_FOUND")'), "사진을 찾을 수 없어요")
        self.assertEqual(self.run_js('lwAdjReasonText("RIGHTS_DENIED")'), "사용이 금지된 사진이에요")
        self.assertIn("그대로 썼어요", self.run_js('lwAdjReasonText("EXISTING_ASSET_WITH_SAME_CONTENT")'))
        other = self.run_js('lwAdjReasonText("<img onerror=x>")')
        self.assertEqual(other, "자세한 이유는 사진 상태를 확인하세요")     # 모르는 코드는 글을 그대로 보여 주지 않는다

    def test_summary_counts_and_lists_only_the_non_created_photos(self):
        result = {"created": 2, "reused": 1, "skipped": 1, "failed": 1, "results": [
            {"asset_id": 1, "status": "CREATED", "reason": None},
            {"asset_id": 2, "status": "CREATED", "reason": None},
            {"asset_id": 3, "status": "REUSED", "reason": "EXISTING_ASSET_WITH_SAME_CONTENT"},
            {"asset_id": 4, "status": "SKIPPED", "reason": "NOT_FOUND"},
            {"asset_id": 5, "status": "FAILED", "reason": "<script>x</script>"},
        ]}
        html = self.run_js(f"lwAdjSummaryHtml({json.dumps(result)})")
        self.assertIn("새로 만든 사진 2장 · 이미 있던 사진 1장 · 건너뜀 1장 · 실패 1장", html)
        self.assertEqual(html.count("<li>"), 3)
        self.assertNotIn("#1 ·", html)
        self.assertIn("#4 · 건너뜀 — 사진을 찾을 수 없어요", html)
        self.assertIn("#5 · 실패 — 자세한 이유는 사진 상태를 확인하세요", html)
        self.assertNotIn("<script>", html)

    def test_summary_never_prints_undefined_and_empty_results_show_no_list(self):
        html = self.run_js("lwAdjSummaryHtml({})")
        self.assertNotIn("undefined", html)
        self.assertIn("새로 만든 사진 …장", html)
        self.assertNotIn("<ul>", html)
        self.assertEqual(self.run_js("lwAdjSummaryHtml(null)"), "")

    def test_the_notice_is_shown_once(self):
        shown = self.run_js('[lwAdjNoticeHtml(), (lwAdjNotice = {created: 1, reused: 0, skipped: 0, failed: 0, results: []}, lwAdjNoticeHtml()), lwAdjNoticeHtml()]')
        self.assertEqual(shown[0], "")
        self.assertIn("새로 만든 사진 1장", shown[1])
        self.assertEqual(shown[2], "")


class WiringTests(unittest.TestCase):

    BIND = function_source("lwBindBatchAdjust")

    def test_every_photo_card_has_a_pick_box_and_the_toolbar_has_the_button_and_panel(self):
        self.assertIn('class="lw-adj-check" value="${a.id}"', JS)
        self.assertIn('id="lw-adj-toggle-btn"', JS)
        self.assertIn('<div id="lw-adj-panel"></div>', JS)
        self.assertIn("lwBindBatchAdjust(content);", JS)
        self.assertIn("${lwAdjNoticeHtml()}", JS)

    def test_the_panel_calls_only_the_batch_route_and_checks_before_sending(self):
        self.assertEqual(set(re.findall(r'apiFetch\("(/media-assets/[^"?]+)"', self.BIND)), {"/media-assets/batch-adjust"})
        run = self.BIND[self.BIND.index("lw-adj-run-btn\").addEventListener"):]
        self.assertLess(run.index("lwAdjProblem(state, chosen)"), run.index('apiFetch("/media-assets/batch-adjust"'))
        self.assertIn("withButtonGuard", run)

    def test_the_sliders_match_the_server_range(self):
        self.assertEqual(len(re.findall(r'type="range"[^>]*min="-50" max="50" step="5"', self.BIND)), 2)

    def test_the_server_text_is_escaped_and_no_hidden_attribute(self):
        self.assertIn("${escapeHtml(errorKey ? t(errorKey) : (errorText || \"\"))}", self.BIND)
        self.assertNotRegex(self.BIND, r"\shidden[\s>]")

    def test_the_no_marks_note_is_shown(self):
        self.assertIn('t("adj_no_marks_note")', self.BIND)
        self.assertIn("워터마크", table(KO)["lw.adj_no_marks_note"])


class TextTests(unittest.TestCase):

    def test_every_text_exists_in_both_languages_and_none_is_unused(self):
        ko, en = table(KO), table(EN)
        self.assertEqual(set(ko), set(en))
        used = set(re.findall(r'\bt\("(adj_[a-z_0-9]+)"', JS)) | set(re.findall(r'"(adj_[a-z_0-9]+)"', JS)) | set(re.findall(r'HomezI18n\.t\("lw\.(adj_[a-z_0-9]+)"', JS))
        used |= {f"adj_status_{s}" for s in ("reused", "skipped", "failed")}   # 상태 이름은 키를 조립해서 쓴다
        for key in ko:
            self.assertIn(key.replace("lw.", ""), used, key)
        for key in used:
            if key.startswith("adj_"):
                self.assertIn(f"lw.{key}", ko, key)

    def test_placeholders_match_and_are_not_repeated(self):
        ko, en = table(KO), table(EN)
        for key in ko:
            a, b = sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key]))
            self.assertEqual(a, b, key)
            self.assertEqual(len(a), len(set(a)), key)


if __name__ == "__main__":
    unittest.main()
