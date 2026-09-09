"""
=========================================================
Homez OS

File : tests/test_console_dashboard_period_select_disabled.py

배경(2026-08-14, 독립 재감사 후속 조치): 대시보드
`dash-period-select`(기간 선택 드롭다운, app/web/console.html)의 값을
바꿔도 `change` 이벤트에 반응하는 코드가 console.js에 전혀 없어
KPI가 갱신되지 않는 죽은 UI였다. 원인을 끝까지 추적한 결과:

  - `/console/api/overview`(app/web/router.py)는 애초에 기간 파라미터를
    받지 않고, 기간과 무관하게 매출/주문/마진 KPI는 항상 명시적으로
    null(빈 상태)이다 — Settlement/Order 도메인 집계 자체가 아직
    구현되지 않았기 때문이다(console.js::loadOverview()의 주석
    "실제 집계 API가 없으므로 전부 명시적 빈 상태" 참고).
  - 따라서 기간을 바꿔도 애초에 바뀔 실제 데이터가 없다 — "만드는 것이
    이번 작업 범위를 넘어서는 규모"(새 Settlement/Order 집계 로직 전체)
    에 해당해, 실제 기간별 집계 API를 새로 만드는 대신(B안) select를
    disabled 처리하고 정직한 안내 문구를 붙였다.

select 자체를 DOM에서 제거하지 않고 disabled로 남긴 이유: "기간 설정"
UI가 향후(Settlement/Order 도메인 완성 후) 실제로 쓰일 위치를 화면에
남겨 의도를 드러내는 것이, 완전히 지우는 것보다 사용자에게 더 정직하다고
판단했다(레이아웃/디자인은 그대로 유지, 상태만 정직하게 표시).

이 테스트는 소스 텍스트 레벨에서 다음을 잠근다:
  1. select가 disabled + aria-disabled="true" 상태이고, 안내 문구
     i18n 키(title 속성)가 붙어 있다.
  2. select 옆에 눈에 보이는 안내 텍스트(.field-hint)가 있다(title
     속성 tooltip 하나에만 의존하지 않는다 — 접근성).
  3. 안내 문구 i18n 키가 ko-KR/en-US 카탈로그 양쪽에 모두 존재한다.
  4. console.js 어디에도 dash-period-select를 참조하는 코드가 없다
     — 참조가 생기면 그건 곧 "죽어있지 않은 것처럼 보이지만 실제로는
     가짜 데이터를 흉내내는" 회귀일 가능성이 높으므로, 그런 배선이
     추가되려면 이 테스트를 의도적으로 갱신해야만 한다(우연한 재도입
     방지).
=========================================================
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConsoleDashboardPeriodSelectDisabledTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        html_path = os.path.join(REPO_ROOT, "app", "web", "console.html")
        with open(html_path, encoding="utf-8") as f:
            cls.html = f.read()

        js_path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(js_path, encoding="utf-8") as f:
            cls.js = f.read()

        ko_path = os.path.join(REPO_ROOT, "app", "web", "i18n", "ko-KR.js")
        with open(ko_path, encoding="utf-8") as f:
            cls.ko = f.read()

        en_path = os.path.join(REPO_ROOT, "app", "web", "i18n", "en-US.js")
        with open(en_path, encoding="utf-8") as f:
            cls.en = f.read()

    def _select_tag(self):

        m = re.search(r"<select id=\"dash-period-select\"[^>]*>", self.html)
        self.assertIsNotNone(
            m, "dash-period-select <select> 태그를 찾을 수 없다 — 마크업이 "
            "바뀌었으면 이 테스트도 함께 갱신해야 한다.",
        )
        return m.group(0)

    def test_period_select_is_disabled(self):

        tag = self._select_tag()
        self.assertIn(
            " disabled", tag,
            "dash-period-select가 disabled 속성을 잃었다 — 이 select는 "
            "실제 기간별 집계 API가 아직 없어 의도적으로 비활성화되어 "
            "있어야 한다(2026-08-14 결정). 실제 기간별 집계를 새로 "
            "구현했다면 disabled를 없애면서 change 리스너와 로딩/오류/빈 "
            "상태 처리를 함께 추가하고 이 테스트를 갱신해야 한다.",
        )
        self.assertIn(
            'aria-disabled="true"', tag,
            "dash-period-select에 aria-disabled=\"true\"가 없다 — "
            "스크린 리더 사용자에게도 비활성 상태가 전달돼야 한다.",
        )

    def test_period_select_has_translated_title_hint(self):

        tag = self._select_tag()
        self.assertIn(
            'data-i18n-attr="title:dashboard.period_disabled_hint"', tag,
            "dash-period-select에 준비 중 안내를 위한 title 속성 "
            "i18n 배선(data-i18n-attr)이 없다.",
        )

    def test_period_field_has_visible_hint_text_not_just_title_tooltip(self):

        # title 속성 tooltip은 마우스 hover에만 의존해 접근성이 약하다 —
        # 이 코드베이스의 다른 안내 문구(field-hint)와 동일하게 항상
        # 보이는 텍스트도 함께 둔다.
        self.assertIn(
            'class="field-hint dash-period-hint"', self.html,
            "select 옆에 항상 보이는 .field-hint 안내 텍스트가 없다.",
        )
        self.assertIn(
            'data-i18n="dashboard.period_disabled_hint"', self.html,
            "dash-period-hint 안내 텍스트가 dashboard.period_disabled_hint "
            "i18n 키로 번역되고 있지 않다.",
        )

    def test_period_disabled_hint_key_exists_in_both_locale_catalogs(self):

        for label, catalog_src in (("ko-KR", self.ko), ("en-US", self.en)):
            self.assertIn(
                '"dashboard.period_disabled_hint"', catalog_src,
                f"{label} 카탈로그에 dashboard.period_disabled_hint 키가 없다.",
            )

    def test_console_js_has_no_wiring_referencing_dash_period_select(self):

        # 2026-08-14 이전 상태(죽은 UI)의 근본 원인은 loadOverview()가
        # 애초에 기간 파라미터를 받지 않는 구조라는 점이었다. 이번 결정은
        # "가짜로 동작하는 척"을 만들지 않는 것이므로, console.js
        # 어디에도 이 id를 참조하는 코드가 없어야 한다 — 참조가 생겼다는
        # 것은 (a) 실제 기간별 집계 API를 만들어 select를 다시 활성화
        # 했거나, (b) select만 활성화된 것처럼 흉내내는 회귀 둘 중
        # 하나다. (a)라면 이 테스트 자체를 의도적으로 교체해야 한다.
        self.assertNotIn(
            "dash-period-select", self.js,
            "console.js가 dash-period-select를 참조하기 시작했다 — 실제 "
            "기간별 집계 API를 새로 구현해 select를 재활성화한 것이라면 "
            "이 테스트(및 disabled 관련 테스트들)를 의도적으로 갱신해야 "
            "한다. 그렇지 않다면 select가 동작하는 것처럼 보이지만 실제로는"
            "아무것도 하지 않는 회귀일 수 있다.",
        )

    def test_overview_kpis_are_still_explicitly_empty_not_faked(self):

        # loadOverview()의 매출/주문/마진 KPI 3개는 실제 집계 API가 없어
        # 항상 명시적으로 null(빈 상태)이어야 한다 — period select를
        # 비활성화하는 김에 누군가 이 자리에 그럴듯한 더미 숫자를 채워
        # "기간을 바꾸면 뭔가 바뀌는 것처럼" 보이게 만드는 회귀를 막는다.
        fn_start = self.js.index("async function loadOverview()")
        fn_end = self.js.index("\n  // --------------------------------------------------", fn_start)
        fn_body = self.js[fn_start:fn_end]

        for kpi_key in ("dashboard.kpi_revenue", "dashboard.kpi_orders", "dashboard.kpi_margin"):
            m = re.search(
                r'kpiCard\(\s*HomezI18n\.t\("' + re.escape(kpi_key) + r'"\),\s*(null|undefined)\s*,',
                fn_body,
            )
            self.assertIsNotNone(
                m,
                f"loadOverview()의 '{kpi_key}' KPI 카드가 더 이상 명시적 "
                "null이 아니다 — 실제 기간별/누적 집계 API 없이 값이 채워지고 "
                "있다면 더미 데이터일 가능성이 높다.",
            )


if __name__ == "__main__":
    unittest.main()
