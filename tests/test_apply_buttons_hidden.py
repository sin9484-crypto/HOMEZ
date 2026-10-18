"""
=========================================================
Homez OS

File : tests/test_apply_buttons_hidden.py

2026-10-18 — URL로 가져오기 화면의 두 "판매가 제안 적용" 버튼이 **계산 전에는 실제로 숨겨지는지**를 고정한다(바깥 호출 0, 소스 수준).

원인: `.btn`이 `display: inline-flex`를 정해서 `hidden` 속성만으로는 버튼이 숨겨지지 않는다(`.btn[hidden]` 규칙이 없다). 그래서 이 두 버튼은 계산 전에도 보였다.
고침: 처음에는 `style="display:none"`, 계산 결과가 있을 때만 `style.display`로 보인다. 다른 화면의 `hidden` 사용은 이번 범위 밖이다.
=========================================================
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
CSS = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app/web").glob("*.css"))


class ApplyButtonTests(unittest.TestCase):

    def test_both_buttons_start_hidden_by_style_not_by_the_attribute(self):
        for marker in ('id="lw-ui-apply-btn"', "data-aia-apply"):
            tag = re.search(r"<button[^>]*" + re.escape(marker) + r"[^>]*>", CONSOLE).group(0)
            self.assertIn('style="display:none"', tag, tag)
            self.assertNotRegex(tag, r"\shidden[\s>]", tag)

    def test_they_are_shown_only_by_style_when_there_is_a_result(self):
        self.assertIn('el("lw-ui-apply-btn").style.display = result.proposed_sale_price == null ? "none" : "";', CONSOLE)
        self.assertIn('applyBtn.style.display = (report.scenarios || []).some((s) => s.sale_price) ? "" : "none";', CONSOLE)
        self.assertNotIn('el("lw-ui-apply-btn").hidden', CONSOLE)
        self.assertNotIn("applyBtn.hidden = !(report.scenarios", CONSOLE)

    def test_the_reason_still_holds_so_the_fix_is_not_dead_code(self):
        # .btn이 display를 정하고 .btn[hidden] 규칙은 없다 — 이 전제가 바뀌면(전역 규칙이 생기면) 이 시험이 알려 준다.
        self.assertRegex(CSS, r"\.btn\s*\{[^}]*display:\s*inline-flex")
        self.assertNotRegex(CSS, r"\.btn\[hidden\]")
        self.assertNotRegex(CSS, r"(^|\n)\[hidden\]\s*\{")


if __name__ == "__main__":
    unittest.main()
