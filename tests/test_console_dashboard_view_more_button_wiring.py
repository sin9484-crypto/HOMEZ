"""
=========================================================
Homez OS

File : tests/test_console_dashboard_view_more_button_wiring.py

2026-08-13 죽은 UI 전수 재검증(독립 재감사, 새 세션) — Phase B에서
이미 발견해 문서화했던 결함을 이번 세션에서 실제 브라우저(CDP)로
재확인하고 직접 고쳤다: 대시보드 "AI 추천 상품" 카드의 "더보기"
버튼(app/web/console.html의 class="link-btn" data-view="decision")은
.nav-item이 아니므로, initNav()의 예전 셀렉터
`.nav-item[data-view]`로는 클릭 리스너가 걸리지 않아 눌러도 화면이
전환되지 않았다(죽은 UI).

고친 방법: querySelectorAll 셀렉터를 `.nav-item[data-view]`에서
`[data-view]` 전체로 넓혔다 — 바로 아래 [data-refresh] 위임과 동일한
패턴이며, console.html에서 data-view를 가진 요소는 전부 nav-item
아니면 이 "더보기" 버튼뿐이므로(정적 감사로 확인) 범위를 넓혀도
부작용이 없다.

이 테스트는 그 회귀를 소스 텍스트 레벨에서 잠근다 — 실제 브라우저
클릭 재현은 이번 세션의 scratchpad/dead_ui_full_audit.py로 별도
수행했다(decision_view_active=True로 확인, admin/manager 양쪽 재현).
=========================================================
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConsoleDashboardViewMoreButtonWiringTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        html_path = os.path.join(REPO_ROOT, "app", "web", "console.html")
        with open(html_path, encoding="utf-8") as f:
            cls.html = f.read()

        js_path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(js_path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_dashboard_view_more_button_exists_and_is_not_a_nav_item(self):

        # 대시보드 "AI 추천 상품" 카드의 "더보기" 버튼 — class="link-btn"
        # (class="nav-item"이 아님)이면서 data-view="decision"을 갖는다.
        needle = 'class="link-btn" data-view="decision"'
        self.assertIn(
            needle, self.html,
            "대시보드 '더보기' 버튼 마크업을 찾을 수 없다 — 테스트 전제가 "
            "바뀌었으면 이 테스트도 함께 갱신해야 한다.",
        )

    def test_init_nav_click_delegation_selector_is_not_scoped_to_nav_item_only(self):

        # 회귀 방지: initNav()의 클릭 위임 셀렉터가 다시
        # ".nav-item[data-view]"로 좁아지면 "더보기" 버튼이 다시 죽는다.
        # [data-refresh]와 동일하게 클래스에 의존하지 않는 범용
        # "[data-view]" 셀렉터를 써야 한다.
        fn_start = self.js.index("function initNav()")
        # 다음 함수 정의 전까지만 본다(initNav() 함수 본문 범위로 한정).
        fn_end = self.js.index("\n  function ", fn_start + 1)
        fn_body = self.js[fn_start:fn_end]

        m = re.search(
            r'document\.querySelectorAll\((["\'])(.*?)\1\)\.forEach',
            fn_body,
        )
        self.assertIsNotNone(
            m, "initNav() 안의 첫 querySelectorAll(...) 호출을 찾을 수 없다.",
        )
        selector = m.group(2)
        self.assertEqual(
            selector, "[data-view]",
            f"initNav()의 data-view 클릭 위임 셀렉터가 '{selector}'로 "
            "nav-item에만 좁게 스코프되어 있다 — class='link-btn'인 "
            "'더보기' 버튼처럼 nav-item이 아닌 data-view 버튼은 클릭 "
            "리스너가 걸리지 않는 회귀가 재발한다.",
        )

    def test_no_other_data_view_element_outside_nav_item_besides_view_more_button(self):

        # 셀렉터를 [data-view] 전체로 넓힌 것이 안전하려면, console.html에서
        # data-view를 가진 요소가 전부 .nav-item이거나 이 "더보기" 버튼
        # 뿐이어야 한다(그렇지 않으면 셀렉터 확장으로 의도치 않은 다른
        # 요소에도 클릭 리스너가 걸릴 수 있다).
        for m in re.finditer(r'<(\w+)([^>]*\bdata-view="[^"]+"[^>]*)>', self.html):
            tag, attrs = m.group(1), m.group(2)
            is_nav_item = 'class="nav-item"' in attrs or re.search(r'class="[^"]*\bnav-item\b[^"]*"', attrs)
            is_known_view_more_btn = 'class="link-btn"' in attrs and 'data-view="decision"' in attrs
            self.assertTrue(
                is_nav_item or is_known_view_more_btn,
                f"[data-view] 셀렉터 확장의 부작용 후보 발견: <{tag} {attrs}> — "
                "nav-item도 아니고 알려진 '더보기' 버튼도 아니다. 이 테스트와 "
                "initNav() 셀렉터 확장의 안전성 근거를 함께 재검토해야 한다.",
            )


if __name__ == "__main__":
    unittest.main()
