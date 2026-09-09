"""
=========================================================
Homez OS

File : tests/test_i18n.py

Gate F-1(2026-08-06) 다국어(i18n) 기반 검증.

1) ko-KR/en-US 번역 카탈로그가 서로 완전히 동일한 키 집합을 갖는지
   (한쪽에만 있는 키가 없는지)
2) 카탈로그 값에 빈 문자열이 없는지(실수로 빈 번역을 넣지 않았는지)
3) console.html/console.js에서 실제로 참조하는 모든 i18n 키가 두
   카탈로그 모두에 존재하는지(누락 키가 화면에 노출되는 사고 방지)
4) app/web/router.py의 신규 정적 라우트(i18n.js/ko-KR.js/en-US.js)가
   실제로 파일을 서빙하고 경로 조작을 막는지
5) console.html의 <script> 로드 순서(카탈로그 → i18n.js → console.js)
6) node가 있는 환경에서 3개 JS 파일의 문법 오류 여부

실제 homez.db는 사용하지 않는다 — 전부 정적 파일 검사.
=========================================================
"""

import os
import re
import shutil
import subprocess
import unittest

from fastapi import HTTPException

import app.web.router as web_router

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(REPO_ROOT, "app", "web")
I18N_DIR = os.path.join(WEB_DIR, "i18n")


def _load_js_object_literal(path, var_name):
    """
    `window.<var_name> = { ... };` 형태의 파일에서 문자열 키만
    파싱한다(파일이 순수 JS 객체 리터럴이라 완전한 파서는 불필요 —
    `"key": "value"` 패턴만 정규식으로 뽑는다).

    2026-08-07 Gate G: 이전에는 값을 `.encode().decode(
    "unicode_escape")`로 재인코딩했다 — 이 카탈로그 파일들은
    `\\uXXXX` 이스케이프가 아니라 원문 UTF-8 한글을 그대로 담고
    있어서, 그 재인코딩이 멀티바이트 문자를 깨뜨리는 실제 결함이었다
    (tests/test_migration_restricted_mode.py에서 값을 문자열 그대로
    비교하는 테스트를 처음 추가하며 발견함 — 이 파일의 기존 테스트는
    키 존재 여부·대칭성만 확인해 지금까지 드러나지 않았을 뿐이다).
    파일을 이미 encoding="utf-8"로 열어 올바르게 디코드했으므로
    추가 변환은 불필요하다.
    """

    with open(path, encoding="utf-8") as f:
        content = f.read()

    start = content.index(f"window.{var_name}")
    body = content[start:]

    pairs = re.findall(r'"([a-zA-Z0-9_.]+)":\s*"((?:[^"\\]|\\.)*)"', body)
    return dict(pairs)


class TranslationCatalogTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.ko = _load_js_object_literal(
            os.path.join(I18N_DIR, "ko-KR.js"), "HOMEZ_I18N_CATALOG_KO_KR",
        )
        cls.en = _load_js_object_literal(
            os.path.join(I18N_DIR, "en-US.js"), "HOMEZ_I18N_CATALOG_EN_US",
        )

    def test_catalogs_are_not_empty(self):

        self.assertGreater(len(self.ko), 0)
        self.assertGreater(len(self.en), 0)

    def test_ko_and_en_have_identical_key_sets(self):

        ko_keys = set(self.ko)
        en_keys = set(self.en)

        self.assertEqual(
            ko_keys - en_keys, set(),
            "en-US.js에 없는 키(ko-KR.js에만 존재)",
        )
        self.assertEqual(
            en_keys - ko_keys, set(),
            "ko-KR.js에 없는 키(en-US.js에만 존재) — ko-KR은 기본 fallback"
            "이므로 반드시 모든 키를 가지고 있어야 한다",
        )

    def test_no_empty_translation_values(self):

        for locale_name, catalog in (("ko-KR", self.ko), ("en-US", self.en)):
            for key, value in catalog.items():
                self.assertTrue(
                    value.strip(),
                    f"{locale_name}.js의 '{key}' 값이 비어 있습니다.",
                )

    def test_key_names_are_dot_separated_lowercase(self):
        """
        요구사항 — 의미 기반 키 이름(auth.login.title 형태), 영어
        문구 자체를 키로 쓰지 않는다(휴리스틱 검사 — 키에 공백이
        없고 소문자/숫자/점/밑줄만 있는지 확인).
        """

        key_pattern = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
        for key in self.ko:
            self.assertRegex(key, key_pattern, f"'{key}'가 명명 규칙에 맞지 않습니다.")


class ConsoleDomI18nKeyUsageTestCase(unittest.TestCase):
    """
    console.html/console.js가 실제로 참조하는 모든 i18n 키가 두
    카탈로그 모두에 존재하는지 확인한다 — "번역 키 누락 시 화면에
    키 문자열이나 undefined를 노출하지 않는다"는 요구사항을, 애초에
    누락이 생기지 않도록 빌드 타임(테스트 타임)에 강제한다.
    """

    @classmethod
    def setUpClass(cls):

        with open(os.path.join(WEB_DIR, "console.html"), encoding="utf-8") as f:
            cls.html = f.read()
        with open(os.path.join(WEB_DIR, "console.js"), encoding="utf-8") as f:
            cls.js = f.read()

        cls.ko = _load_js_object_literal(
            os.path.join(I18N_DIR, "ko-KR.js"), "HOMEZ_I18N_CATALOG_KO_KR",
        )

    def _referenced_html_keys(self):

        keys = set()
        # data-i18n="key"
        keys.update(re.findall(r'data-i18n="([a-zA-Z0-9_.]+)"', self.html))
        # data-i18n-attr="attr:key,attr2:key2"
        for spec in re.findall(r'data-i18n-attr="([^"]+)"', self.html):
            for pair in spec.split(","):
                if ":" in pair:
                    keys.add(pair.split(":", 1)[1].strip())
        return keys

    def _referenced_js_keys(self):
        """
        `HomezI18n.t("key")` 단순 호출뿐 아니라
        `HomezI18n.t(cond ? "a.b" : "c.d")` 같은 삼항 분기 호출도
        잡아야 한다 — 각 `HomezI18n.t(...)` 호출의 괄호 안 구간만
        잘라내 그 안의 dot-key 형태 문자열 리터럴을 전부 모은다.
        """

        keys = set()
        key_literal = re.compile(r'"([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)"')

        for m in re.finditer(r"HomezI18n\.t\(", self.js):
            start = m.end()
            depth = 1
            i = start
            while i < len(self.js) and depth > 0:
                if self.js[i] == "(":
                    depth += 1
                elif self.js[i] == ")":
                    depth -= 1
                i += 1
            call_args = self.js[start:i]
            keys.update(key_literal.findall(call_args))

        return keys

    def test_all_html_data_i18n_keys_exist_in_catalog(self):

        missing = self._referenced_html_keys() - set(self.ko)
        self.assertEqual(missing, set(), f"console.html이 참조하지만 카탈로그에 없는 키: {missing}")

    def test_all_js_t_call_keys_exist_in_catalog(self):

        missing = self._referenced_js_keys() - set(self.ko)
        self.assertEqual(missing, set(), f"console.js가 참조하지만 카탈로그에 없는 키: {missing}")

    def test_sidenav_items_are_translated(self):

        start = self.html.index('class="sidenav"')
        end = self.html.index("</nav>", start)
        section = self.html[start:end]

        for key in (
            # Gate UI(2026-08-09) — 사이드바가 업무 그룹으로 재구성되며
            # "개요"(nav.overview)가 "대시보드"(nav.dashboard)로,
            # "마켓 등록"(nav.marketplace_listing)이 "채널별 판매
            # 방식"으로, "채널별 등록 현황"이 "등록 현황"으로 문구가
            # 바뀌었다 — 키 자체는 그대로 재사용하거나(marketplace_
            # listing/listing_status_sync) 새 키로 대체됐다(dashboard).
            "nav.dashboard", "nav.candidates", "nav.trend", "nav.new_product",
            "nav.safety", "nav.decision", "nav.listing_package",
            "nav.listing_wizard",
            "nav.marketplace_listing", "nav.listing_status_sync",
            "nav.store_connection", "nav.finance", "nav.system",
            "nav.account_security", "nav.user_permission",
            "nav.group_product", "nav.group_channel", "nav.group_order",
            "nav.group_analytics", "nav.group_settlement", "nav.group_system",
            "nav.product_list", "nav.order_status", "nav.shipment",
            "nav.return_exchange", "nav.keyword_analysis",
            "nav.margin_analysis", "nav.channel_settlement",
        ):
            self.assertIn(f'data-i18n="{key}"', section)

    def test_topbar_badges_have_title_translation(self):

        start = self.html.index('id="topbar-status"')
        end = self.html.index("</div>", start)
        section = self.html[start:end]

        for key in (
            "topbar.badge_version_title", "topbar.badge_conn_title",
            "topbar.badge_schema_title", "topbar.badge_mode_title",
            "topbar.badge_estop_title",
        ):
            self.assertIn(key, section)

    def test_confirm_dialog_is_translated(self):

        start = self.html.index('id="confirm-dialog"')
        end = self.html.index("</dialog>", start)
        section = self.html[start:end]

        for key in (
            "dialog.confirm.default_title", "dialog.confirm.memo_label",
            "dialog.confirm.reason_label", "dialog.confirm.cancel",
            "dialog.confirm.ok",
        ):
            self.assertIn(f'data-i18n="{key}"', section)

    def test_account_security_view_panels_are_translated(self):
        """
        Gate F-3(2026-08-06) — 계정/회사/권한/보안 화면의 각 패널이
        실제로 data-i18n 키를 쓰는지 확인한다(대표 키만 — 전체 90여
        개는 test_all_html_data_i18n_keys_exist_in_catalog가 이미
        전수 검증한다).
        """

        start = self.html.index('id="view-account-security"')
        end = self.html.index("</section>", start)
        section = self.html[start:end]

        for key in (
            "settings.title", "settings.password.title",
            "settings.password.submit", "settings.sessions.title",
            "settings.recovery_codes.title", "settings.company.title",
            "settings.registration_requests.title",
            "settings.invitations.title", "settings.admin_users.title",
            "settings.create_user.title",
        ):
            self.assertIn(f'data-i18n="{key}"', section, f"{key}가 설정 화면에 없습니다.")

    def test_rejection_reason_map_uses_i18n_keys_not_hardcoded_korean(self):

        self.assertIn("REJECTION_REASON_KEYS", self.js)
        self.assertNotIn('INVALID_INVITATION: "유효하지 않은 초대"', self.js)

    def test_login_gate_has_language_switch_buttons(self):

        start = self.html.index('id="login-gate"')
        end = self.html.index('id="register-gate"')
        section = self.html[start:end]

        self.assertIn('data-lang="ko-KR"', section)
        self.assertIn('data-lang="en-US"', section)

    def test_settings_view_has_language_switch_buttons(self):

        start = self.html.index('id="view-account-security"')
        section = self.html[start:start + 2000]

        self.assertIn('data-lang="ko-KR"', section)
        self.assertIn('data-lang="en-US"', section)


class ConsoleScriptLoadOrderTestCase(unittest.TestCase):

    def test_i18n_scripts_load_before_console_js(self):

        with open(os.path.join(WEB_DIR, "console.html"), encoding="utf-8") as f:
            html = f.read()

        ko_pos = html.index("/console/static/i18n/ko-KR.js")
        en_pos = html.index("/console/static/i18n/en-US.js")
        i18n_pos = html.index("/console/static/i18n/i18n.js")
        console_pos = html.index("/console/static/console.js")

        self.assertLess(ko_pos, i18n_pos)
        self.assertLess(en_pos, i18n_pos)
        self.assertLess(i18n_pos, console_pos)


class I18nStaticRouteTestCase(unittest.TestCase):

    def test_i18n_js_served(self):

        resp = web_router.console_i18n_asset("i18n.js")
        self.assertTrue(os.path.exists(resp.path))
        self.assertTrue(resp.path.endswith("i18n.js"))

    def test_ko_kr_catalog_served(self):

        resp = web_router.console_i18n_asset("ko-KR.js")
        self.assertTrue(os.path.exists(resp.path))

    def test_en_us_catalog_served(self):

        resp = web_router.console_i18n_asset("en-US.js")
        self.assertTrue(os.path.exists(resp.path))

    def test_unknown_filename_rejected(self):

        with self.assertRaises(HTTPException) as ctx:
            web_router.console_i18n_asset("not-a-real-file.js")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_path_traversal_rejected(self):

        with self.assertRaises(HTTPException) as ctx:
            web_router.console_i18n_asset("../console.js")
        self.assertEqual(ctx.exception.status_code, 404)


class I18nJsSyntaxTestCase(unittest.TestCase):

    def test_syntax_is_valid_when_node_available(self):

        node_path = shutil.which("node")
        if node_path is None:
            self.skipTest("Node.js를 찾을 수 없어 문법 검사를 건너뜁니다.")

        for filename in ("i18n.js", "ko-KR.js", "en-US.js"):
            path = os.path.join(I18N_DIR, filename)
            result = subprocess.run(
                [node_path, "--check", path],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, f"{filename}: {result.stderr}")


class PrecheckContractMessageTestCase(unittest.TestCase):
    """
    2026-08-31 V7 후속 결함 수정 — `listing_wizard_precheck.py`가
    `"listing_wizard.precheck.contract." + code.lower()`로 동적으로
    합성하는 키는 `test_all_js_t_call_keys_exist_in_catalog`가 잡지
    못한다(console.js 소스에 리터럴 문자열로 존재하지 않으므로) —
    이 키들이 실제 빈 문장으로 렌더링됐던 사고(2026-08-30)의 재발
    방지용 전용 검증.
    """

    @classmethod
    def setUpClass(cls):

        cls.ko = _load_js_object_literal(
            os.path.join(I18N_DIR, "ko-KR.js"), "HOMEZ_I18N_CATALOG_KO_KR",
        )
        cls.en = _load_js_object_literal(
            os.path.join(I18N_DIR, "en-US.js"), "HOMEZ_I18N_CATALOG_EN_US",
        )
        with open(os.path.join(WEB_DIR, "console.js"), encoding="utf-8") as f:
            cls.js = f.read()

    def test_the_three_previously_missing_contract_keys_exist_and_are_natural(self):

        required_keys = (
            "listing_wizard.precheck.contract.brand_no_brand_value_missing",
            "listing_wizard.precheck.contract.item_required",
            "listing_wizard.precheck.contract.contents_required",
        )
        for key in required_keys:
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
            self.assertTrue(self.ko[key].strip())
            self.assertTrue(self.en[key].strip())
            # 오류코드를 그대로 직역/노출하지 않아야 한다(예: "BRAND NO
            # BRAND VALUE MISSING" 같은 코드 나열이 아니라 자연스러운
            # 안내문이어야 한다).
            code_suffix = key.rsplit(".", 1)[-1]
            self.assertNotIn(code_suffix, self.ko[key].lower())

    def test_unknown_issue_fallback_key_exists_with_code_param(self):

        self.assertIn("lw.precheck_unknown_issue_fallback", self.ko)
        self.assertIn("lw.precheck_unknown_issue_fallback", self.en)
        self.assertIn("{code}", self.ko["lw.precheck_unknown_issue_fallback"])
        self.assertIn("{code}", self.en["lw.precheck_unknown_issue_fallback"])

    def test_issue_list_rendering_uses_fallback_when_translation_missing(self):
        """console.js의 사전검사 이슈 렌더링이 빈 문자열을 그대로
        방치하지 않고 fallback으로 대체하는지, 소스 패턴으로
        확인한다(2026-08-30 사고 — 3개 issue가 빈 <span>으로 렌더링
        됐던 결함의 재발 방지)."""

        start = self.js.index("resultEl.innerHTML = `<div class=\"lw-issue-list\">")
        end = self.js.index("</div>`;", start)
        section = self.js[start:end]

        self.assertIn("lw.precheck_unknown_issue_fallback", section)
        self.assertIn("issue.code", section)


class ConsoleMoneyFormattingLocaleTestCase(unittest.TestCase):
    """
    Gate X-3(2026-08-12) — fmtMoney()가 "ko-KR"을 하드코딩해, 언어를
    en-US로 바꿔도 숫자 서식(천단위 구분자 등)이 한국어 스타일로
    고정돼 있던 결함을 고정한다. HomezI18n.formatNumber()는 현재
    활성 locale을 그대로 따른다.
    """

    @classmethod
    def setUpClass(cls):

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(repo_root, "app", "web", "console.js")
        with open(path, encoding="utf-8") as f:
            cls.js = f.read()

    def test_fmt_money_uses_locale_aware_formatter(self):

        start = self.js.index("function fmtMoney(")
        end = self.js.index("\n  }\n", start)
        body = self.js[start:end]

        self.assertIn("HomezI18n.formatNumber(", body)
        self.assertNotIn("toLocaleString", body)


if __name__ == "__main__":
    unittest.main()
