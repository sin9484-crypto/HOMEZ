"""
=========================================================
Homez OS

File : tests/test_console_nav_permission_gating.py

Gate X-3(2026-08-12) — nav-item Permission 메타데이터 정적 검증.
다른 *_ui.py 테스트 파일과 동일하게 console.html/console.js 소스
텍스트만 확인한다 — 실제 서버/브라우저를 띄우지 않는다.

배경: 이 저장소의 거의 모든 API가 admin_guard(ADMIN/SUPER_ADMIN
전용)로 막혀 있는데도(listing_wizard.* 몇 개만 예외) nav-item에는
`data-permission=""`(항상 표시)가 붙어 있어, Manager/Staff/Viewer가
메뉴를 눌러도 결국 403만 뜨는 죽은 메뉴가 대부분이었다(Gate X-1/X-3
감사). 이 테스트는 그 회귀를 잠근다 — "__admin_only__" 센티널이
실제로 붙어 있는지, 그리고 console.js가 그 센티널을 permission_
catalog.py에 없는 가짜 Permission 코드로 흉내 내지 않고 명시적으로
분기하는지 확인한다.
=========================================================
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# listing-wizard만 세분화된 Permission 코드(listing_wizard.view)를
# 쓴다 — 그 외 실제 data-view를 가진 메뉴는 전부 admin_guard 전용
# API만 호출하므로 "__admin_only__"여야 한다(app/web/router.py,
# app/domains/*/router.py의 admin_guard 사용을 근거로 Gate X-3에서
# 확인함).
EXPECTED_NAV_PERMISSION = {
    "overview": "__admin_only__",
    "candidates": "__admin_only__",
    "listing-package": "__admin_only__",
    "listing-wizard": "listing_wizard.view",
    "listing-status-sync": "__admin_only__",
    "store-connection": "__admin_only__",
    "marketplace-listing": "__admin_only__",
    "decision": "__admin_only__",
    "trend": "__admin_only__",
    "new-product": "__admin_only__",
    "finance": "__admin_only__",
    "safety": "__admin_only__",
    "system": "__admin_only__",
    # V7 Gate 7(2026-08-15) — 이전에는 nav-item-placeholder(준비 중)
    # 였던 6개 항목이 Gate 3~5 백엔드(inventory/order/purchase/
    # shipment/return_order/pricing/settlement)에 실제로 연결되며
    # 활성 nav-item으로 전환됐다. 전 엔드포인트가 admin_guard만
    # 쓰므로(app/domains/{inventory,order,purchase,shipment,
    # return_order,pricing}/router.py) 동일하게 __admin_only__.
    "inventory": "__admin_only__",
    "order-fulfillment": "__admin_only__",
    "shipment": "__admin_only__",
    "return-order": "__admin_only__",
    "margin-analysis": "__admin_only__",
    "channel-settlement": "__admin_only__",
}


class ConsoleNavPermissionHtmlTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()

    def test_expected_nav_items_have_expected_permission_metadata(self):

        for view, expected in EXPECTED_NAV_PERMISSION.items():
            needle = f'data-view="{view}" data-permission="{expected}"'
            self.assertIn(
                needle, self.html,
                f"nav-item[data-view={view}]가 기대한 Permission 메타데이터"
                f"({expected})를 갖고 있지 않다.",
            )

    def test_account_security_nav_items_are_admin_only(self):

        # account-security는 data-view가 같은 버튼 2개가 있다(사용자·
        # 권한 / 설정·계정 및 보안 — console.js:navigateTo 주석 참고,
        # 의도된 구조다). 둘 다 admin_only여야 한다.
        count = self.html.count('data-view="account-security" data-permission="__admin_only__"')
        self.assertEqual(
            count, 2,
            "account-security nav-item 2개가 모두 __admin_only__여야 한다.",
        )

    def test_no_real_nav_item_left_with_empty_permission(self):

        # 준비 중(nav-item-placeholder) 버튼은 data-view/data-permission
        # 자체가 없다(API를 호출하지 않으므로 무해함) — 그 외 실제
        # data-view를 가진 버튼 중 빈 문자열(항상 표시)로 남아있는
        # 것이 없어야 한다(Gate X-3 이전 상태로의 회귀 방지).
        for view in EXPECTED_NAV_PERMISSION:
            self.assertNotIn(f'data-view="{view}" data-permission=""', self.html)


class ConsoleNavHiddenCssEnforcementTestCase(unittest.TestCase):
    """
    2026-08-13 독립 재감사에서 발견 — 위 두 TestCase는 HTML/JS
    "소스 텍스트"만 확인해, applyPermissionGatedNav()가 실제로
    element.hidden = true를 정확히 실행하는지는 검증했지만, 그 결과가
    실제 화면에서 시각적으로도 숨겨지는지는 전혀 검증하지 않았다.

    실제로는 .nav-item { display: flex } 규칙이 네이티브
    [hidden]{display:none} UA 규칙과 캐스케이드 동점이라, 15개 관리자
    전용 메뉴가 hidden=true인데도 실제 브라우저에서 display:flex로
    그대로 렌더링되고 클릭까지 가능했다(실제 manager 계정으로 실측
    확인 — API 자체는 401/403으로 막혀 있어 데이터 접근 문제는
    아니었지만, 메뉴 노출 자체가 결함이었다). 이 결함은 정확히
    console.css의 `.login-gate[hidden], .shell[hidden]`(2026-07-30)와
    `.tour-practice-badge[hidden]`(2026-08-13)에서 이미 두 번 발견돼
    고쳐진 것과 동일한 CSS 특이성 패턴이다 — .nav-item만 그 대응이
    빠져 있었다. 이 테스트는 그 회귀를 코드 레벨에서 고정한다(실제
    브라우저 렌더링 확인은 스크린샷으로 별도 완료).
    """

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.css")
        with open(path, encoding="utf-8") as f:
            cls.css = f.read()

    def test_nav_item_hidden_attribute_forces_display_none(self):

        self.assertIn(
            ".nav-item[hidden]", self.css,
            ".nav-item[hidden] 규칙이 없다 — element.hidden = true로 숨긴 "
            "관리자 전용 메뉴가 실제로는 display:flex로 계속 보이고 "
            "클릭까지 가능한 결함이 재발한다.",
        )
        start = self.css.index(".nav-item[hidden]")
        block = self.css[start:start + 120]
        self.assertIn("display: none", block)


class ConsoleNavPermissionJsTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        path = os.path.join(REPO_ROOT, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_admin_only_sentinel_defined_and_used(self):

        self.assertIn('NAV_PERMISSION_ADMIN_ONLY = "__admin_only__"', self.js)
        self.assertIn("required === NAV_PERMISSION_ADMIN_ONLY", self.js)

    def test_first_visible_nav_view_helper_exists_and_is_wired(self):

        self.assertIn("function firstVisibleNavView()", self.js)
        # 로그인 성공 직후 두 진입점(비밀번호 로그인 / 세션 복구) 모두
        # 하드코딩된 "overview" 대신 이 헬퍼를 써야 한다 — 그렇지
        # 않으면 admin_only 메뉴가 숨겨진 역할이 빈 대시보드에서 403만
        # 보게 된다.
        self.assertNotIn('navigateTo("overview")', self.js)
        self.assertEqual(self.js.count("navigateTo(firstVisibleNavView())"), 2)


if __name__ == "__main__":
    unittest.main()
