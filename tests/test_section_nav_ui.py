"""
=========================================================
Homez OS

File : tests/test_section_nav_ui.py

업무 중심 메뉴(2026-10-15, UI 개선 시안)를 고정한다. 사이드바는 6개 업무 메뉴 + 도움말이고, 업무 안의 화면은 위쪽 탭으로 옮긴다.
기존 .nav-item 버튼은 DOM에 그대로 남아 권한 숨김·가이드 ID·화면 이동의 기준이 된다.

보증:
- 기존 메뉴의 모든 화면이 어느 업무 탭에서든 도달 가능하다(화면이 사라지지 않는다).
- 탭 이름은 기존 번역 키를 재사용하며 ko/en 모두에 있다.
- 권한은 새로 만들지 않고 기존 버튼의 hidden 상태를 따른다.
- 가이드(투어)가 접힌 기존 메뉴를 가리키면 펼친다.
네트워크·DB 없음. 실제 console.js의 순수 함수를 Node에서 실행한다.
=========================================================
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app/web/console.html").read_text(encoding="utf-8")
JS = (ROOT / "app/web/console.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/web/console.css").read_text(encoding="utf-8")
KO = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8")
EN = (ROOT / "app/web/i18n/en-US.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def section_array_source() -> str:
    start = JS.index("  const SECTION_NAV = [")
    end = JS.index("  const SECTION_HELP_VIEW")
    return JS[start:end]


def function_source(name: str) -> str:
    match = re.search(r"function " + re.escape(name) + r"\(", JS)
    assert match, name
    start = match.start()
    # 인자 목록의 기본값(예: opts = {})에 있는 중괄호를 본문으로 오인하지 않도록 닫는 괄호 뒤부터 찾는다.
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


def tabs_from_source():
    """Node로 SECTION_NAV를 평가해 [{id, tabs:[[view,key],…]}] 를 돌려준다."""

    if not NODE:
        return None
    harness = section_array_source() + "\nconsole.log(JSON.stringify(SECTION_NAV.map(s => ({id: s.id, labelKey: s.labelKey, tabs: s.tabs}))));"
    proc = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def legacy_nav_views():
    views = set()
    for m in re.finditer(r'<button class="nav-item[^"]*"([^>]*)>', HTML):
        attrs = m.group(1)
        if "nav-item-placeholder" in m.group(0):
            continue
        v = re.search(r'data-view="([^"]+)"', attrs)
        if v:
            views.add(v.group(1))
    return views


@unittest.skipUnless(NODE, "node가 없어 화면 설정을 평가할 수 없다")
class SectionConfigTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sections = tabs_from_source()

    def test_there_are_six_work_menus_in_the_designed_order(self):
        self.assertEqual([s["id"] for s in self.sections], ["today", "products", "orders", "money", "analysis", "settings"])

    def test_every_existing_screen_stays_reachable_from_some_tab(self):
        reachable = {view for s in self.sections for view, _ in s["tabs"]}
        missing = legacy_nav_views() - reachable - {"guides"}  # 가이드는 도움말 버튼이 맡는다
        self.assertEqual(missing, set(), "탭에서 도달할 수 없게 된 화면")

    def test_every_tab_points_at_a_real_menu_button(self):
        legacy = legacy_nav_views()
        for s in self.sections:
            for view, _ in s["tabs"]:
                self.assertIn(view, legacy, f"{s['id']} 탭 {view}에 대응하는 기존 메뉴가 없다")

    def test_no_screen_is_listed_twice_in_the_same_menu(self):
        for s in self.sections:
            views = [v for v, _ in s["tabs"]]
            self.assertEqual(len(views), len(set(views)), s["id"])

    def test_tab_and_menu_labels_exist_in_both_languages(self):
        for s in self.sections:
            for key in [s["labelKey"]] + [k for _, k in s["tabs"]]:
                self.assertRegex(KO, r'"' + re.escape(key) + r'":', f"ko에 {key} 없음")
                self.assertRegex(EN, r'"' + re.escape(key) + r'":', f"en에 {key} 없음")

    def test_new_section_keys_are_symmetric_and_all_used(self):
        keys_ko = set(re.findall(r'"(nav\.(?:section_[a-z]+|sections_aria|show_all_menu|hide_all_menu))"', KO))
        keys_en = set(re.findall(r'"(nav\.(?:section_[a-z]+|sections_aria|show_all_menu|hide_all_menu))"', EN))
        self.assertEqual(keys_ko, keys_en)
        used = set(re.findall(r'"(nav\.(?:section_[a-z]+|sections_aria|show_all_menu|hide_all_menu))"', JS))
        self.assertEqual(keys_ko - used, set(), "번역만 있고 쓰이지 않는 키")
        self.assertEqual(used - keys_ko, set(), "쓰는데 번역이 없는 키")


@unittest.skipUnless(NODE, "node가 없어 화면 함수를 실행해 볼 수 없다")
class SectionLogicTests(unittest.TestCase):

    def _run(self, expression: str):
        harness = (
            section_array_source()
            + "\n" + function_source("sectionTabViewFor") + "\n" + function_source("sectionIdFor")
            + "\nconsole.log(JSON.stringify(" + expression + "));"
        )
        proc = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_a_screen_belongs_to_its_menu(self):
        self.assertEqual(self._run("[sectionIdFor('shipment'), sectionIdFor('trend'), sectionIdFor('safety'), sectionIdFor('overview')]"),
                         ["orders", "analysis", "settings", "today"])

    def test_a_detail_screen_counts_as_its_list_tab(self):
        self.assertEqual(self._run("[sectionTabViewFor('purchase-task-detail'), sectionIdFor('purchase-task-detail')]"),
                         ["purchase-task", "orders"])

    def test_a_screen_in_two_menus_keeps_the_menu_you_came_from(self):
        self.assertEqual(self._run("[sectionIdFor('settlement-differences', 'money'), sectionIdFor('settlement-differences', 'today'), sectionIdFor('settlement-differences', null)]"),
                         ["money", "today", "today"])

    def test_unknown_screens_belong_to_no_menu(self):
        self.assertEqual(self._run("[sectionIdFor('coming-soon'), sectionTabViewFor('nope-detail')]"), [None, None])


class WiringTests(unittest.TestCase):

    def test_the_menu_is_built_after_permissions_and_refreshed_on_every_navigation(self):
        shell = function_source("showShell")
        self.assertLess(shell.index("applyPermissionGatedNav();"), shell.index("buildSectionNav();"))
        self.assertIn("refreshSectionNav(currentView);", shell)
        nav = function_source("navigateTo")
        self.assertIn("refreshSectionNav(viewName);", nav)
        self.assertLess(nav.index("loader(opts)"), nav.index("refreshSectionNav(viewName);"))

    def test_permissions_follow_the_existing_buttons_not_a_new_rule(self):
        body = function_source("isSectionTabVisible")
        self.assertIn("legacy.hidden", body)
        self.assertNotIn("permission", body.lower())
        # 업무 메뉴 버튼은 data-view/data-permission을 갖지 않는다 — 기존 권한 처리·초기 연결 대상이 아니다.
        build = function_source("buildSectionNav")
        self.assertNotIn("dataset.view", build)
        self.assertNotIn("dataset.permission", build)

    def test_a_menu_with_no_visible_tab_is_hidden(self):
        body = function_source("refreshSectionNav")
        self.assertIn("btn.hidden = !section.tabs.some(([v]) => isSectionTabVisible(v));", body)
        self.assertIn("bar.hidden = visibleTabs.length < 2;", body)

    def test_legacy_menus_are_folded_but_kept_in_the_dom_and_can_be_reopened(self):
        self.assertIn(".sidenav.sections-on > .nav-group:not(.nav-sections) { display: none; }", CSS)
        self.assertIn(".sidenav.sections-on.legacy-open > .nav-group:not(.nav-sections) { display: block; }", CSS)
        self.assertIn('toggle.setAttribute("aria-expanded"', function_source("buildSectionNav"))

    def test_a_guided_tour_aimed_at_a_folded_menu_opens_it(self):
        body = function_source("tourResolveTarget")
        self.assertIn('classList.add("legacy-open")', body)
        self.assertLess(body.index('classList.add("legacy-open")'), body.index("getBoundingClientRect()"))

    def test_tabs_and_menus_are_keyboard_and_screen_reader_friendly(self):
        body = function_source("refreshSectionNav") + function_source("buildSectionNav")
        for needle in ('setAttribute("role", "tab")', "aria-selected", "aria-current", 'setAttribute("aria-label"'):
            self.assertIn(needle, body)
        self.assertIn(":focus-visible", CSS)

    def test_the_old_menu_markup_is_untouched(self):
        # 기존 테스트(권한 게이트·가이드 ID)가 읽는 기준이다.
        for needle in ('data-view="overview"', 'data-view="candidates"', 'data-guide-id="nav-products"', 'data-view="safety"'):
            self.assertIn(needle, HTML)


if __name__ == "__main__":
    unittest.main()
