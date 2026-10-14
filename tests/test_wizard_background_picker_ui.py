"""
=========================================================
Homez OS

File : tests/test_wizard_background_picker_ui.py

위저드 3단계(이미지)의 "제품 색에 맞춘 배경 고르기" 화면(2026-10-14)을 고정한다.
실제 `app/web/console.js`의 순수 함수(미리보기 색, 제안 타일, 완료 알림)를 Node에서 실행해 확인하고, 배선(제안 조회 →
선택 → 생성 → 처음 화면 복귀)은 소스로 확인한다. 네트워크·DB 없음.
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


def i18n_table() -> dict:
    table = {}
    for line in KO.splitlines():
        m = re.match(r'\s*"(lw\.[a-z_.]+)":\s*(".*"),?\s*$', line)
        if m:
            table[m.group(1)] = json.loads(m.group(2))
    return table


HARNESS = r"""
const HomezI18n = { t(key, params) {
  const table = JSON.parse(process.env.I18N_JSON);
  let text = table[key] !== undefined ? table[key] : key;
  Object.keys(params || {}).forEach((k) => { text = text.split("{" + k + "}").join(String(params[k])); });
  return text;
} };
const escapeHtml = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
let lwBgNotice = process.env.NOTICE_JSON === "null" ? null : JSON.parse(process.env.NOTICE_JSON);
__SOURCES__
const suggestion = JSON.parse(process.env.SUGGESTION_JSON);
const out = {
  swatch: lwBgSwatchCss(suggestion),
  tile: lwBgTileHtml(suggestion),
  notice1: lwBgNoticeHtml(),
  notice2: lwBgNoticeHtml(),
};
console.log(JSON.stringify(out));
"""


def suggestion(**overrides):
    base = {
        "code": "ADAPT_COMPLEMENT", "label_ko": "보색 파스텔", "reason_ko": "제품이 또렷하게 떠 보입니다.",
        "kind": "GRADIENT", "color_hex": "#E8F7FA", "color_hex_end": "#C9EAF0", "contrast": 41,
        "min_delta_e": 20.0, "low_contrast": False, "shadow_recommended": False,
    }
    base.update(overrides)
    return base


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class PureFunctionTests(unittest.TestCase):

    def _run(self, sug, notice=None):
        sources = "\n".join(function_source(n) for n in ("lwBgSwatchCss", "lwBgTileHtml", "lwBgNoticeHtml"))
        env = {
            **os.environ, "I18N_JSON": json.dumps(i18n_table()), "SUGGESTION_JSON": json.dumps(sug),
            "NOTICE_JSON": json.dumps(notice),
        }
        proc = subprocess.run(
            [NODE, "-e", HARNESS.replace("__SOURCES__", sources)], env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_each_background_kind_gets_the_matching_preview(self):
        self.assertEqual(self._run(suggestion(kind="WHITE", color_hex="#FFFFFF", color_hex_end=None))["swatch"], "#FFFFFF")
        self.assertEqual(self._run(suggestion(kind="SOLID", color_hex="#2A2D33", color_hex_end=None))["swatch"], "#2A2D33")
        self.assertEqual(self._run(suggestion())["swatch"], "linear-gradient(to bottom, #E8F7FA, #C9EAF0)")
        self.assertEqual(
            self._run(suggestion(kind="RADIAL", color_hex="#FFFFFF", color_hex_end="#DDEEFF"))["swatch"],
            "radial-gradient(circle, #FFFFFF, #DDEEFF)",
        )

    def test_an_invalid_color_never_reaches_the_style_attribute(self):
        evil = '#FFFFFF;"><script>alert(1)</script>'
        result = self._run(suggestion(color_hex=evil, color_hex_end="red; background:url(x)"))
        self.assertEqual(result["swatch"], "linear-gradient(to bottom, #FFFFFF, #FFFFFF)")
        self.assertNotIn("script", result["tile"].lower())
        self.assertNotIn("url(x)", result["tile"])

    def test_the_tile_shows_the_name_score_reason_and_a_make_button(self):
        tile = self._run(suggestion())["tile"]
        self.assertIn("보색 파스텔", tile)
        self.assertIn("대비 41", tile)
        self.assertIn("제품이 또렷하게 떠 보입니다.", tile)
        self.assertIn('class="btn btn-primary btn-sm lw-bg-make-btn" data-code="ADAPT_COMPLEMENT"', tile)
        self.assertNotIn("lw-bg-warn", tile)

    def test_a_buried_product_gets_a_warning_and_a_helpful_one_gets_a_tip(self):
        warned = self._run(suggestion(low_contrast=True, shadow_recommended=True))["tile"]
        self.assertIn("lw-bg-warn", warned)
        self.assertIn("제품이 배경에 묻힐 수 있어요", warned)
        self.assertNotIn("lw-bg-tip", warned)
        tipped = self._run(suggestion(shadow_recommended=True))["tile"]
        self.assertIn("lw-bg-tip", tipped)
        self.assertNotIn("lw-bg-warn", tipped)

    def test_server_text_is_escaped(self):
        tile = self._run(suggestion(label_ko="<img src=x onerror=alert(1)>", reason_ko="<b>x</b>"))["tile"]
        self.assertNotIn("<img", tile)
        self.assertNotIn("<b>", tile)
        self.assertIn("&lt;img", tile)

    def test_the_completion_notice_names_the_new_image_and_shows_only_once(self):
        result = self._run(suggestion(), notice={"id": 131, "label": "선명한 색 배경"})
        self.assertIn("이미지 #131", result["notice1"])
        self.assertIn("선명한 색 배경", result["notice1"])
        self.assertIn('role="status"', result["notice1"])
        self.assertEqual(result["notice2"], "")
        self.assertEqual(self._run(suggestion())["notice1"], "")


class WiringTests(unittest.TestCase):

    def test_the_compose_button_opens_the_picker_instead_of_a_one_click_white_background(self):
        self.assertIn("lwOpenBackgroundPanel(btn.dataset.assetId, content)", CONSOLE)
        handler = CONSOLE.split('content.querySelectorAll(".lw-compose-btn")')[1].split("});")[0]
        self.assertNotIn("background-composition", handler)
        self.assertNotIn('background_kind: "WHITE"', handler)

    def test_the_picker_reads_suggestions_then_creates_with_the_chosen_one(self):
        source = function_source("lwOpenBackgroundPanel")
        self.assertIn('"/media-assets/background-suggestions"', source)
        self.assertIn('"/media-assets/adaptive-composition"', source)
        self.assertIn("suggestion_code: btn.dataset.code", source)
        self.assertIn("max_upscale: state.upscale ? 2.0 : 1.0", source)
        self.assertIn("(state.variation + 1) % 12", source)
        # 사용자가 고른 용도(대표/추가)를 그대로 서버에 보내 서버가 대표 이미지는 흰 배경만 허용하게 한다.
        self.assertIn("purpose: state.purpose", source)

    def test_finishing_returns_to_the_list_with_a_visible_confirmation(self):
        source = function_source("lwOpenBackgroundPanel")
        self.assertIn("lwBgNotice = { id: asset.id", source)
        self.assertIn("await lwRenderMediaStep(content);", source)
        self.assertIn("window.scrollTo", source)
        self.assertIn("${lwBgNoticeHtml()}", function_source("lwRenderMediaStep"))

    def test_every_asset_card_has_a_picker_panel_and_a_failure_stays_in_the_panel(self):
        self.assertIn('id="lw-bg-panel-${a.id}"', CONSOLE)
        source = function_source("lwOpenBackgroundPanel")
        self.assertIn("errEl.textContent = err.message", source)

    def test_an_open_picker_widens_its_card_to_the_full_grid_row_and_closing_restores_it(self):
        # 좁은 카드(약 140px) 안에서는 제안 타일과 글자가 세로로 쪼개지므로 열린 카드는 그리드 한 줄을 쓴다.
        source = function_source("lwOpenBackgroundPanel")
        self.assertIn('panel.closest(".lw-media-card")', source)
        self.assertIn('card.style.gridColumn = "1 / -1"', source)
        self.assertIn('card.style.gridColumn = ""', source)
        # 닫기 분기가 열기 분기보다 먼저 와야 같은 버튼으로 접을 수 있다.
        self.assertLess(source.index('card.style.gridColumn = ""'), source.index('card.style.gridColumn = "1 / -1"'))

    def test_translations_exist_in_both_languages_and_are_all_used(self):
        keys_ko = set(re.findall(r'"(lw\.bg_[a-z_]+)"', KO))
        keys_en = set(re.findall(r'"(lw\.bg_[a-z_]+)"', EN))
        self.assertEqual(keys_ko, keys_en)
        used = {f"lw.{k}" for k in re.findall(r'\bt\("(bg_[a-z_]+)"', CONSOLE)}
        used |= {f"lw.{k}" for k in re.findall(r'"lw\.(bg_[a-z_]+)"', CONSOLE)}
        self.assertEqual(used - keys_ko, set(), "화면이 쓰는데 번역이 없는 키")
        self.assertEqual(keys_ko - used, set(), "번역만 있고 화면이 쓰지 않는 키")


if __name__ == "__main__":
    unittest.main()
