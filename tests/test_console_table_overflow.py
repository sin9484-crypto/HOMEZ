"""
=========================================================
Homez OS

File : tests/test_console_table_overflow.py

Gate X-3(2026-08-12) — 항목 9 "긴 상품명·이메일·오류 문구의 overflow
방지" 검증. `tbody td`(전체 표에 공통 적용되는 기본 셀 규칙)에 공백
없는 긴 문자열이 셀 하나만 억지로 넓혀 행이 옆으로 밀리지 않도록
줄바꿈 규칙이 있는지 정적으로 확인한다. 다른 *_ui.py 테스트 파일과
동일하게 CSS 소스 텍스트만 확인한다 — 실제 브라우저를 띄우지 않는다.
=========================================================
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConsoleTableCellOverflowTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.css")
        with open(path, encoding="utf-8") as f:
            cls.css = f.read()

    def test_base_table_cell_wraps_long_unbroken_text(self):

        start = self.css.index("tbody td {")
        end = self.css.index("}", start)
        rule_body = self.css[start:end]

        self.assertIn("overflow-wrap: break-word", rule_body)

    def test_table_wrap_still_scrolls_for_wide_tables(self):
        """
        셀 하나의 줄바꿈과는 별개로, 컬럼 수 자체가 많아 테이블
        전체가 넓어지는 경우를 위한 가로 스크롤은 그대로 유지돼야
        한다(이번 수정으로 제거하지 않았는지 확인).
        """

        start = self.css.index(".table-wrap {")
        end = self.css.index("}", start)
        rule_body = self.css[start:end]

        self.assertIn("overflow-x: auto", rule_body)


if __name__ == "__main__":
    unittest.main()
