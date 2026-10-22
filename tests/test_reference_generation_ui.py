"""
=========================================================
Homez OS

File : tests/test_reference_generation_ui.py

2026-10-22 — "참고 사진으로 만들기" R3b: 위저드 이미지 단계의 화면(`console.js`의 `lwOpenReferencePanel` 등)을 고정한다. 실제 화면 함수를 Node에서 실행하고, 서버 호출 위치·문구 짝은
소스에서 확인한다. 네트워크·DB·실제 생성 0.

  - 기본 선택: 대표는 흰 배경 단색 스튜디오 + 원본 포즈, 그 밖에는 카페 + 팔 내리기. 배경·포즈는 3개까지, 장수는 조합 수 이하(최대 6).
  - 실행 전 막기: 사용 금지 사진, 배경·포즈 없음, 장수 초과, 사용권 미확인(경고 확인 전), 대표(최종 확인 전). 서버도 같은 규칙으로 다시 막는다.
  - 요청 본문: 대표만 최종 확인이 True로 나가고, 미리보기에서는 대표 최종 확인을 보내지 않는다.
  - 문구: 쓰는 모든 문구가 한국어·영어로 짝을 이루고 자리표시자가 맞다. 서버가 준 글은 이스케이프한다. 이미지에 워터마크·글자를 넣지 않는다는 안내와 시연용 안내가 항상 보인다.
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
    return "\n".join(m.group(0) for m in re.finditer(r"^  (?:const LW_REF_[A-Z_]+ = [^\n]+|let lwRefNotice = [^\n]+)$", JS, re.M))


def table(source):
    return {m.group(1): json.loads(m.group(2)) for m in re.finditer(r'"(lw\.ref_[a-z_0-9]+)": ("(?:[^"\\]|\\.)*")', source)}


def ref_keys_in(source):
    return sorted(set(re.findall(r'"lw\.(ref_[a-z_0-9]+)"', source)))


HARNESS = r"""
const TABLE = JSON.parse(process.env.TABLE_JSON);
const HomezI18n = { t(key, params) { let s = TABLE[key] !== undefined ? TABLE[key] : key; Object.entries(params || {}).forEach(([k, v]) => { s = s.replace(`{${k}}`, String(v)); }); return s; } };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
__SOURCES__
console.log(JSON.stringify(__EXPR__));
"""

FUNCTIONS = ("lwRefDefaults", "lwRefToggle", "lwRefMaxCount", "lwRefProblem", "lwRefBody", "lwRefNoticeHtml", "lwRefPreviewHtml")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class BehaviourTests(unittest.TestCase):

    def run_js(self, expression, pre=""):
        sources = "\n".join([constants_source()] + [function_source(n) for n in FUNCTIONS] + [pre])
        script = HARNESS.replace("__SOURCES__", sources).replace("__EXPR__", expression)
        proc = subprocess.run([NODE, "-e", script], env={**os.environ, "TABLE_JSON": json.dumps(table(KO))}, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def problem(self, state, rights="VERIFIED"):
        return self.run_js(f"lwRefProblem({json.dumps(state)}, {json.dumps(rights)})")

    def ok_state(self, **kw):
        base = {"purpose": "ADDITIONAL", "backgrounds": ["CAFE"], "poses": ["ARMS_DOWN"], "count": 1, "rightsAck": False, "confirmMain": False}
        base.update(kw)
        return base

    def test_defaults(self):
        self.assertEqual(self.run_js('lwRefDefaults("MAIN")'), {"backgrounds": ["STUDIO_PLAIN"], "poses": ["KEEP_ORIGINAL"], "count": 1})
        for purpose in ("ADDITIONAL", "DETAIL"):
            self.assertEqual(self.run_js(f'lwRefDefaults("{purpose}")'), {"backgrounds": ["CAFE"], "poses": ["ARMS_DOWN"], "count": 1})

    def test_the_default_selection_has_no_problem(self):
        for purpose in ("ADDITIONAL", "DETAIL"):
            self.assertIsNone(self.problem(self.ok_state(purpose=purpose)))

    def test_toggle_adds_removes_and_stops_at_three(self):
        self.assertEqual(self.run_js('lwRefToggle(["A"], "B", 3)'), ["A", "B"])
        self.assertEqual(self.run_js('lwRefToggle(["A", "B"], "A", 3)'), ["B"])
        self.assertEqual(self.run_js('lwRefToggle(["A", "B", "C"], "D", 3)'), ["A", "B", "C"])
        self.assertEqual(self.run_js('lwRefToggle(["A", "B", "C"], "C", 3)'), ["A", "B"])
        self.assertEqual(self.run_js('lwRefToggle([], "A", 3)'), ["A"])

    def test_max_count_is_the_combination_count_capped_at_six(self):
        cases = [(1, 1, 1), (2, 1, 2), (2, 2, 4), (3, 2, 6), (3, 3, 6), (0, 3, 0), (3, 0, 0)]
        for backgrounds, poses, expected in cases:
            state = {"backgrounds": ["b"] * backgrounds, "poses": ["p"] * poses}
            self.assertEqual(self.run_js(f"lwRefMaxCount({json.dumps(state)})"), expected, (backgrounds, poses))

    def test_each_blocking_rule(self):
        self.assertEqual(self.problem(self.ok_state(), "RIGHTS_DENIED"), "ref_err_denied")
        self.assertEqual(self.problem(self.ok_state(backgrounds=[])), "ref_err_background")
        self.assertEqual(self.problem(self.ok_state(poses=[])), "ref_err_pose")
        for count in (0, -1, None, "x", 2):  # 조합이 1개뿐이라 2도 초과
            self.assertEqual(self.problem(self.ok_state(count=count)), "ref_err_count", repr(count))
        self.assertEqual(self.problem(self.ok_state(backgrounds=["a", "b", "c"], poses=["p", "q"], count=7)), "ref_err_count")
        self.assertIsNone(self.problem(self.ok_state(backgrounds=["a", "b", "c"], poses=["p", "q"], count=6)))

    def test_unverified_photos_need_the_warning_confirmed_first(self):
        self.assertEqual(self.problem(self.ok_state(), "RIGHTS_UNVERIFIED"), "ref_err_rights")
        self.assertEqual(self.problem(self.ok_state(rightsAck="true"), "RIGHTS_UNVERIFIED"), "ref_err_rights")  # 정확히 true만
        self.assertIsNone(self.problem(self.ok_state(rightsAck=True), "RIGHTS_UNVERIFIED"))
        self.assertIsNone(self.problem(self.ok_state(), "VERIFIED"))

    def test_a_main_image_needs_the_exact_confirmation(self):
        main = self.ok_state(purpose="MAIN", backgrounds=["STUDIO_PLAIN"], poses=["KEEP_ORIGINAL"])
        self.assertEqual(self.problem(main), "ref_err_main_confirm")
        self.assertEqual(self.problem({**main, "confirmMain": "yes"}), "ref_err_main_confirm")
        self.assertIsNone(self.problem({**main, "confirmMain": True}))
        for purpose in ("ADDITIONAL", "DETAIL"):
            self.assertIsNone(self.problem(self.ok_state(purpose=purpose)))  # 확인 없이도 된다

    def test_denied_wins_over_every_other_message(self):
        state = self.ok_state(purpose="MAIN", backgrounds=[], poses=[], count=0)
        self.assertEqual(self.problem(state, "RIGHTS_DENIED"), "ref_err_denied")

    def test_request_body(self):
        body = self.run_js('lwRefBody({"purpose": "DETAIL", "backgrounds": ["CAFE", "STREET"], "poses": ["ARMS_DOWN"], "count": "2", "confirmMain": true}, "17")')
        self.assertEqual(body, {"reference_asset_id": 17, "purpose": "DETAIL", "backgrounds": ["CAFE", "STREET"], "poses": ["ARMS_DOWN"], "count": 2, "confirmed_main": False})
        main = self.run_js('lwRefBody({"purpose": "MAIN", "backgrounds": ["STUDIO_PLAIN"], "poses": ["KEEP_ORIGINAL"], "count": 1, "confirmMain": true}, 5)')
        self.assertIs(main["confirmed_main"], True)
        for value in ("false", "0", "1", "undefined"):
            pre_main = self.run_js('lwRefBody({"purpose": "MAIN", "backgrounds": ["S"], "poses": ["K"], "count": 1, "confirmMain": ' + value + "}, 5)")
            self.assertIs(pre_main["confirmed_main"], False, value)

    def test_request_body_is_a_copy_not_the_live_lists(self):
        self.assertEqual(self.run_js('(() => { const s = {purpose: "DETAIL", backgrounds: ["A"], poses: ["B"], count: 1}; const b = lwRefBody(s, 1); b.backgrounds.push("Z"); return s.backgrounds; })()'), ["A"])

    def test_notice_banner_is_shown_once(self):
        shown = self.run_js("[lwRefNoticeHtml(), (lwRefNotice = { count: 3 }, lwRefNoticeHtml()), lwRefNoticeHtml()]")
        self.assertEqual(shown[0], "")
        self.assertIn("3장을 만들었습니다", shown[1])
        self.assertIn("시연용", shown[1])
        self.assertEqual(shown[2], "")

    def test_preview_lists_the_checks_the_warnings_and_escapes_server_text(self):
        plan = {"count": 3, "estimated_cost": 0.03, "required_checks": ["PRODUCT_MATCHES_REFERENCE", "NO_WATERMARK", "MAIN_IMAGE_SPEC", "SOMETHING_NEW"], "warnings": ["<script>alert(1)</script> 반려될 수 있습니다"]}
        html = self.run_js(f"lwRefPreviewHtml({json.dumps(plan)})")
        self.assertIn("3장 · 예상 비용 0.03", html)
        self.assertIn("워터마크·로고·서명이 없는가", html)
        self.assertIn("쿠팡 대표 이미지 규격", html)
        self.assertEqual(html.count("<li>"), 3)  # 모르는 검수 코드는 건너뛴다(키 이름을 노출하지 않는다)
        self.assertNotIn("SOMETHING_NEW", html)
        self.assertNotIn("ref_check_something_new", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_preview_never_prints_undefined(self):
        html = self.run_js("lwRefPreviewHtml({})")
        self.assertNotIn("undefined", html)
        self.assertIn("…장 · 예상 비용 …", html)
        self.assertEqual(self.run_js("lwRefPreviewHtml(null)"), "")


class WiringTests(unittest.TestCase):

    PANEL = function_source("lwOpenReferencePanel")

    def test_only_original_photos_get_the_button_and_every_card_has_a_panel(self):
        self.assertRegex(JS, r'a\.asset_role === "ORIGINAL" \? `<button type="button" class="btn btn-ghost btn-sm lw-ref-btn"')
        self.assertIn('id="lw-ref-panel-${a.id}"', JS)
        self.assertIn("lwOpenReferencePanel(btn.dataset.assetId, btn.dataset.rights, content)", JS)
        self.assertIn("${lwRefNoticeHtml()}", JS)

    def test_the_panel_uses_only_the_three_reference_routes_and_the_rights_acknowledgement(self):
        paths = set(re.findall(r'apiFetch\("(/media-assets/[^"?]+)"', self.PANEL))
        self.assertEqual(paths, {
            "/media-assets/reference-generation/presets", "/media-assets/reference-generation/preview", "/media-assets/reference-generation",
            "/media-assets/rights-acknowledgements",
        })
        self.assertIn('workflow_stage: "GENERATE_OR_EDIT_OR_EXPORT"', self.PANEL)
        self.assertIn('user_action: "CONTINUE"', self.PANEL)

    def test_running_is_blocked_by_the_same_rules_before_any_request(self):
        run = self.PANEL[self.PANEL.index("lw-ref-run-btn-${assetId}`).addEventListener"):]
        self.assertLess(run.index("lwRefProblem(state, rightsStatus)"), run.index('apiFetch("/media-assets/reference-generation"'))
        self.assertIn("withButtonGuard", run)

    def test_the_preview_never_sends_the_main_confirmation(self):
        preview = self.PANEL[self.PANEL.index("lw-ref-preview-btn-${assetId}`).addEventListener"): self.PANEL.index("lw-ref-run-btn-${assetId}`).addEventListener")]
        self.assertIn("lwRefBody({ ...state, confirmMain: false }, assetId)", preview)

    def test_every_dynamic_server_text_is_escaped(self):
        self.assertIn("${escapeHtml(problem)}", self.PANEL)
        self.assertIn("${escapeHtml(item.label_ko)}", self.PANEL)
        self.assertIn('${escapeHtml(presets.main_policy_warning || "")}', self.PANEL)
        self.assertNotRegex(self.PANEL, r"innerHTML\s*=\s*[^`]*err\.message")

    def test_the_always_visible_notes_are_present(self):
        for key in ("ref_demo_note", "ref_no_watermark_note", "ref_wing_note"):
            self.assertIn(f't("{key}")', self.PANEL)

    def test_no_hidden_attribute_buttons(self):
        # .btn이 hidden 속성을 이긴다(기존 결함) — 이 화면은 hidden으로 버튼을 숨기지 않는다.
        self.assertNotRegex(self.PANEL, r"\shidden[\s>]")


class TextTests(unittest.TestCase):

    def test_every_reference_text_exists_in_both_languages(self):
        used = set(ref_keys_in(JS)) | {m for m in re.findall(r"\bt\(\"(ref_[a-z_0-9]+)\"", JS)} | {k for k in re.findall(r'"(ref_[a-z_0-9]+)"', function_source("lwRefProblem"))}
        used |= {f"ref_check_{code.lower()}" for code in re.search(r"LW_REF_KNOWN_CHECKS = \[([^\]]+)\]", JS).group(1).replace('"', "").replace(" ", "").split(",")}
        used |= {"ref_purpose_additional", "ref_purpose_detail", "ref_purpose_main"}
        ko, en = table(KO), table(EN)
        for key in sorted(used):
            self.assertIn(f"lw.{key}", ko, key)
            self.assertIn(f"lw.{key}", en, key)
        self.assertEqual(set(ko), set(en))

    def test_placeholders_match_between_languages_and_are_not_repeated(self):
        ko, en = table(KO), table(EN)
        for key in ko:
            a, b = sorted(re.findall(r"\{(\w+)\}", ko[key])), sorted(re.findall(r"\{(\w+)\}", en[key]))
            self.assertEqual(a, b, key)
            self.assertEqual(len(a), len(set(a)), key)

    def test_the_main_warning_and_notes_say_what_matters(self):
        ko = table(KO)
        self.assertIn("시연용", ko["lw.ref_demo_note"])
        self.assertIn("워터마크", ko["lw.ref_no_watermark_note"])
        self.assertIn("이미지 자동 수정 동의", ko["lw.ref_wing_note"])
        self.assertIn("반려", ko["lw.ref_purpose_main"])
        self.assertEqual(ko["lw.ref_btn"], "참고 사진으로 만들기")


if __name__ == "__main__":
    unittest.main()
