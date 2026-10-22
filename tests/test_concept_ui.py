"""
=========================================================
Homez OS

File : tests/test_concept_ui.py

2026-10-23 — "컨셉으로 만들기" I2·I3: 위저드 이미지 단계의 화면(`console.js`의 `lwOpenConceptPanel` 등)을 고정한다. 실제 화면 함수를 Node에서 실행하고, 서버 호출 위치·문구 짝은 소스에서 확인한다. 네트워크·DB 0.

  - 실행 전 막기: 컨셉을 하나도 안 골랐을 때, 6개를 넘을 때. 요청 본문: 누끼 번호·컨셉 코드·용도.
  - 결과 표시: 컨셉마다 상태·이유·검사 내용(알 수 없는 코드는 서버 글을 그대로 보여 주지 않는다), 서버가 준 글은 이스케이프, 쿠팡 정책은 자동 확인하지 못한다는 안내가 항상 보인다.
  - 끝나면 목록을 처음 상태로 다시 그리고 맨 위에 결과를 남긴다(한 번만 보인다). 쓰는 모든 문구가 한국어·영어로 짝을 이루고 자리표시자가 맞다.
=========================================================
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_reference_generation_ui import function_source

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def constants_source():
    return "\n".join(m.group(0) for m in re.finditer(r"^  (?:const LW_CONCEPT_[A-Z_]+ = [^\n]+|let lwConceptNotice = [^\n]+)$", JS, re.M))


def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.concept_[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

FUNCTIONS = ("lwConceptProblem", "lwConceptBody", "lwConceptCheckText", "lwConceptSummaryHtml", "lwConceptNoticeHtml")
PANEL = function_source("lwOpenConceptPanel")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BehaviourTests(unittest.TestCase):

    def run_js(self, expression, pre=""):
        sources = "\n".join([constants_source()] + [function_source(n) for n in FUNCTIONS] + [pre])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_problem_rules(self):
        self.assertEqual(self.run_js('lwConceptProblem({codes: []})'), "concept_err_none")
        self.assertEqual(self.run_js('lwConceptProblem({})'), "concept_err_none")
        self.assertEqual(self.run_js('lwConceptProblem({codes: "A"})'), "concept_err_none")
        self.assertIsNone(self.run_js('lwConceptProblem({codes: ["A"]})'))
        self.assertIsNone(self.run_js('lwConceptProblem({codes: ["A","B","C","D","E","F"]})'))
        self.assertEqual(self.run_js('lwConceptProblem({codes: ["A","B","C","D","E","F","G"]})'), "concept_err_max")

    def test_body_sends_the_cutout_number_codes_and_purpose_and_copies_the_list(self):
        self.assertEqual(self.run_js('lwConceptBody({codes: ["SOFT_BEIGE", "WOOD_WARM"], purpose: "DETAIL"}, "17")'),
                         {"cutout_asset_id": 17, "concept_codes": ["SOFT_BEIGE", "WOOD_WARM"], "purpose": "DETAIL"})
        self.assertEqual(self.run_js('(() => { const s = {codes: ["A"], purpose: "ADDITIONAL"}; const b = lwConceptBody(s, 1); b.concept_codes.push("X"); return s.codes; })()'), ["A"])

    def test_check_text_uses_korean_names_and_never_shows_unknown_server_text(self):
        self.assertEqual(self.run_js('lwConceptCheckText({code: "PRODUCT_PRESERVATION", status: "PASS"})'), "제품 보존: 통과")
        self.assertEqual(self.run_js('lwConceptCheckText({code: "POLICY_FLAGS", status: "NOT_CHECKED"})'), "쿠팡 정책 적합: 확인하지 못함(직접 확인 필요)")
        self.assertIn("사진을 들여다본 검사는 아님", self.run_js('lwConceptCheckText({code: "NO_TEXT_OVERLAY", status: "BY_CONSTRUCTION"})'))
        text = self.run_js('lwConceptCheckText({code: "<b>EVIL</b>", status: "<i>hack</i>"})')
        self.assertEqual(text, "기타 검사: 알 수 없음")
        self.assertNotIn("EVIL", text)

    def result(self, **kw):
        base = {"created": 1, "reused": 1, "skipped": 1, "check_failed": 1, "failed": 0, "results": [
            {"concept_code": "SOFT_BEIGE", "status": "CREATED", "reason": None, "checks": [{"code": "PRODUCT_PRESERVATION", "status": "PASS", "detail": {}}], "asset": {"id": 55}},
            {"concept_code": "CALM_GRAY", "status": "REUSED", "reason": "EXISTING_ASSET_WITH_SAME_CONTENT", "checks": [], "asset": {"id": 40}},
            {"concept_code": "NEON", "status": "SKIPPED", "reason": "UNKNOWN_CONCEPT", "checks": [], "asset": None},
            {"concept_code": "WOOD_WARM", "status": "CHECK_FAILED", "reason": "INSPECTION_FAILED", "checks": [{"code": "BACKGROUND_CONTRAST", "status": "FAIL", "detail": {}}], "asset": None},
        ]}
        base.update(kw)
        return base

    def html(self, result, labels=None):
        return self.run_js(f"lwConceptSummaryHtml({json.dumps(result)}, {json.dumps(labels)})")

    def test_the_summary_lists_each_concept_with_status_reason_checks_and_the_policy_note(self):
        html = self.html(self.result(), {"SOFT_BEIGE": "부드러운 베이지", "WOOD_WARM": "따뜻한 나무결"})
        self.assertIn("컨셉 사진 1장을 만들었습니다(이미 있던 1장, 건너뜀 1개, 검사 실패 1개, 오류 0개).", html)
        self.assertIn("부드러운 베이지</strong> · 만듦 (#55)", html)
        self.assertIn("제품 보존: 통과", html)
        self.assertIn("CALM_GRAY</strong> · 이미 있음 (#40) — 같은 내용의 기존 사진을 그대로 썼습니다", html)        # 이름을 모르면 코드를 보인다
        self.assertIn("건너뜀 — 알 수 없는 컨셉", html)
        self.assertIn("따뜻한 나무결</strong> · 검사 실패(저장 안 함) — 검사를 통과하지 못해 저장하지 않았습니다", html)
        self.assertIn("배경 대비: 실패", html)
        self.assertIn("쿠팡 이미지 정책에 맞는지는 자동으로 확인하지 못합니다", html)
        self.assertIn('role="status"', html)
        self.assertNotIn("(#null)", html)

    def test_server_text_is_escaped_and_unknown_codes_become_generic_text(self):
        evil = self.result(results=[{"concept_code": "<script>x</script>", "status": "<img onerror=1>", "reason": "<b>why</b>", "checks": [{"code": "X", "status": "Y"}], "asset": {"id": "<i>"}}])
        html = self.html(evil, {"<script>x</script>": "<u>라벨</u>"})
        for raw in ("<script>", "<img", "<b>why", "<u>", "<i>"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;u&gt;라벨&lt;/u&gt;", html)
        self.assertIn("알 수 없음", html)
        self.assertIn("자세한 이유를 알 수 없음", html)

    def test_non_numeric_counts_show_an_ellipsis_not_garbage(self):
        html = self.html(self.result(created="<b>", reused=None, skipped=1.5, check_failed="9", failed=-1))
        self.assertIn("컨셉 사진 …장을 만들었습니다(이미 있던 …장, 건너뜀 …개, 검사 실패 …개, 오류 -1개).", html)
        self.assertNotIn("<b>", html)

    def test_empty_result_is_empty_text_and_notice_shows_once(self):
        self.assertEqual(self.run_js("lwConceptSummaryHtml(null, null)"), "")
        notice = self.run_js('(() => { lwConceptNotice = {result: ' + json.dumps(self.result()) + ', labels: {}}; const first = lwConceptNoticeHtml(); const second = lwConceptNoticeHtml(); return [first.length > 0, second]; })()')
        self.assertEqual(notice, [True, ""])


class SourceTests(unittest.TestCase):

    def test_the_button_exists_only_for_generated_photos_and_has_its_panel_and_binding(self):
        self.assertRegex(JS, r'a\.asset_role === "GENERATED" \? `<button type="button" class="btn btn-ghost btn-sm lw-concept-btn"')
        self.assertIn('id="lw-concept-panel-${a.id}"', JS)
        self.assertRegex(JS, r'querySelectorAll\("\.lw-concept-btn"\)\.forEach\(\(btn\) => \{\s*btn\.addEventListener\("click", \(\) => lwOpenConceptPanel\(btn\.dataset\.assetId, content\)\)')
        self.assertIn("${lwConceptNoticeHtml()}", JS)

    def test_the_panel_uses_the_right_routes_and_returns_to_the_start_screen_after_running(self):
        self.assertIn('"/media-assets/concept-runs"', PANEL)
        self.assertIn("/media-assets/concept-recommendations?purpose=", PANEL)
        run = PANEL[PANEL.index("lw-concept-run-btn-${assetId}`).addEventListener"):]
        self.assertIn("lwConceptProblem(state)", run)
        self.assertLess(run.index("lwConceptProblem(state)"), run.index("/media-assets/concept-runs"))     # 막을 일은 서버 호출 전에
        self.assertLess(run.index("lwConceptNotice = {"), run.index("await lwRenderMediaStep(content)"))
        self.assertIn("window.scrollTo", run)
        self.assertIn("withButtonGuard", run)
        self.assertIn("encodeURIComponent(state.category)", PANEL)                                      # 분류 글은 주소에 그대로 붙이지 않는다

    def test_picking_uses_the_shared_limit_and_unknown_picks_are_dropped_after_a_reload(self):
        self.assertIn("lwRefToggle(state.codes, box.dataset.conceptCode, LW_CONCEPT_MAX_PICK)", PANEL)
        self.assertIn("state.codes = state.codes.filter((code) => known.has(code));", PANEL)
        self.assertIn("render(overLimit ? \"concept_err_max\" : null)", PANEL)

    def test_changing_the_purpose_clears_the_picks_and_reloads(self):
        self.assertRegex(PANEL, r"state\.purpose = radio\.value;\s*state\.codes = \[\];\s*load\(\);")

    def test_server_text_in_rows_is_escaped(self):
        row = PANEL[PANEL.index("function rowHtml"): PANEL.index("function render(")]
        for expr in ("row.code", "row.label_ko", "r)}</span>"):
            self.assertIn(f"escapeHtml({expr}" if not expr.endswith("</span>") else "escapeHtml(r)", row)

    def test_every_used_key_exists_in_both_languages_with_matching_placeholders(self):
        ko, en = table(KO), table(EN)
        used = set(re.findall(r'"lw\.(concept_[a-z_0-9]+)"', JS)) | set(re.findall(r'\bt\("(concept_[a-z_0-9]+)"', JS)) | set(re.findall(r'\bt\(\s*"(concept_[a-z_0-9]+)"', JS))
        used |= set(re.findall(r'"(concept_[a-z_0-9]+)"', PANEL + function_source("lwConceptSummaryHtml") + function_source("lwConceptProblem") + function_source("lwConceptCheckText")))
        used |= set(re.findall(r'\[\["ADDITIONAL", "(concept_[a-z_]+)"\]', PANEL))
        for group in re.findall(r'\["(?:ADDITIONAL|DETAIL|MAIN)", "(concept_purpose_[a-z]+)"\]', PANEL):
            used.add(group)
        # 동적으로 조립되는 키 묶음: 코드 목록에서 이끌어 낸다.
        for const, prefix in (("LW_CONCEPT_KNOWN_CHECKS", "concept_check_"), ("LW_CONCEPT_KNOWN_CHECK_STATUSES", "concept_cstatus_"),
                              ("LW_CONCEPT_KNOWN_STATUSES", "concept_status_"), ("LW_CONCEPT_KNOWN_REASONS", "concept_reason_")):
            codes = re.search(const + r" = \[([^\]]+)\]", JS).group(1).replace('"', "").replace(" ", "").split(",")
            used |= {prefix + c.lower() for c in codes}
            used.add(prefix + "other")
        used |= {"concept_level_l0", "concept_level_l1", "concept_level_other"}
        self.assertTrue(used)
        for key in sorted(used):
            self.assertIn(f"lw.{key}", ko, f"한국어 문구 없음: {key}")
            self.assertIn(f"lw.{key}", en, f"영어 문구 없음: {key}")
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ko[f"lw.{key}"])), sorted(re.findall(r"\{(\w+)\}", en[f"lw.{key}"])), key)
        self.assertEqual(set(ko), set(en))
        self.assertEqual(len(ko), len(en))
        unused = {k for k in ko if k[3:] not in used}
        self.assertEqual(unused, set(), "쓰지 않는 컨셉 문구가 있다")

    def test_the_fixed_notices_say_what_matters(self):
        ko = table(KO)
        self.assertIn("자동으로 확인하지 못합니다", ko["lw.concept_policy_note"])
        self.assertIn("워터마크", ko["lw.concept_local_note"])
        self.assertIn("비용", ko["lw.concept_local_note"])
        self.assertIn("흰 배경", ko["lw.concept_purpose_main"])
        self.assertIn("직접 확인", ko["lw.concept_cstatus_not_checked"])
        self.assertEqual(ko["lw.concept_btn"], "컨셉으로 여러 장 만들기")
        for text in (PANEL,):
            self.assertIn('t("concept_policy_note")', text)
            self.assertIn('t("concept_local_note")', text)


if __name__ == "__main__":
    unittest.main()
