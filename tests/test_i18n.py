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


class ShipmentTimelineTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시안 06 배송 관리 참고) — 배송 상태 라벨과
    진행 단계 표시가 `` `ship.status.${status.toLowerCase()}` ``
    형태의 템플릿 리터럴로 동적 조합되므로(console.js
    buildShipmentTimelineSteps/statusPillHtmlLabeled),
    test_all_js_t_call_keys_exist_in_catalog(리터럴 문자열만 인식)가
    자동으로 잡지 못한다. PrecheckContractMessageTestCase와 동일한
    이유로 SHIPMENT_STATUS_OPTIONS의 모든 값에 대응하는 키를 직접
    고정한다(2026-08-30 사전검사 이슈 빈 문장 사고의 재발 방지 패턴).
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

    def test_every_shipment_status_option_has_a_label_key(self):

        start = self.js.index("const SHIPMENT_STATUS_OPTIONS = [")
        end = self.js.index("];", start)
        options_src = self.js[start:end]
        statuses = re.findall(r'"([A-Z_]+)"', options_src)
        self.assertGreater(len(statuses), 0)

        for status in statuses:
            key = f"ship.status.{status.lower()}"
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
            self.assertTrue(self.ko[key].strip())
            self.assertTrue(self.en[key].strip())

    def test_timeline_heading_key_exists(self):

        self.assertIn("ship.timeline_heading", self.ko)
        self.assertIn("ship.timeline_heading", self.en)
        self.assertTrue(self.ko["ship.timeline_heading"].strip())
        self.assertTrue(self.en["ship.timeline_heading"].strip())

    def test_status_pill_labeled_falls_back_to_raw_status_when_key_missing(self):
        """
        statusPillHtmlLabeled()가 번역 누락 시 빈 라벨이 아니라 원문
        상태 코드로 fallback하는지 소스 패턴으로 확인한다(빈 문자열
        노출 사고 재발 방지 — 위 클래스 docstring 참고).
        """

        start = self.js.index("function statusPillHtmlLabeled(")
        end = self.js.index("\n  }\n", start)
        body = self.js[start:end]

        self.assertIn("translated || raw", body)


class ReturnOrderTimelineTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시안 07 취소·반품 관리 참고) —
    ShipmentTimelineTranslationTestCase와 동일한 이유(동적 템플릿
    리터럴 키, PrecheckContractMessageTestCase 선례)로
    RETURN_STATUS_OPTIONS의 모든 값에 대응하는 키를 직접 고정한다.
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

    def test_every_return_status_option_has_a_label_key(self):

        start = self.js.index("const RETURN_STATUS_OPTIONS = [")
        end = self.js.index("];", start)
        options_src = self.js[start:end]
        statuses = re.findall(r'"([A-Z_]+)"', options_src)
        self.assertGreater(len(statuses), 0)

        for status in statuses:
            key = f"ret.status.{status.lower()}"
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
            self.assertTrue(self.ko[key].strip())
            self.assertTrue(self.en[key].strip())

    def test_timeline_heading_key_exists(self):

        self.assertIn("ret.timeline_heading", self.ko)
        self.assertIn("ret.timeline_heading", self.en)
        self.assertTrue(self.ko["ret.timeline_heading"].strip())
        self.assertTrue(self.en["ret.timeline_heading"].strip())


class RefundUiTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시안 07 "환불 확인" 참고, UI-8 감사 중 발견 —
    docs/HOMEZ_V7_UI_IMPLEMENTATION_AUDIT.md 참고) — Refund 화면
    신규 추가. `refund.status.*`/`refund.type.*`는
    `` `refund.status.${status.toLowerCase()}` ``처럼 동적으로
    조합되므로 위 두 TranslationTestCase와 동일한 이유로 별도
    고정한다. `refund.type.*`는 console.js에 대응하는 하드코딩
    배열이 없어(서버가 내려준 refund_type을 그대로 쓴다) 백엔드
    `app.domains.refund.constants.RefundType.ALL`을 직접 참조해
    드리프트를 방지한다.
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

    def _assert_key_in_both(self, key):

        self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
        self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
        self.assertTrue(self.ko[key].strip())
        self.assertTrue(self.en[key].strip())

    def test_every_refund_status_option_has_a_label_key(self):

        start = self.js.index("const REFUND_STATUS_OPTIONS = [")
        end = self.js.index("];", start)
        statuses = re.findall(r'"([A-Z_]+)"', self.js[start:end])
        self.assertGreater(len(statuses), 0)

        for status in statuses:
            self._assert_key_in_both(f"refund.status.{status.lower()}")

    def test_every_refund_type_has_a_label_key(self):

        from app.domains.refund.constants import RefundType

        self.assertGreater(len(RefundType.ALL), 0)
        for refund_type in RefundType.ALL:
            self._assert_key_in_both(f"refund.type.{refund_type.lower()}")

    def test_timeline_heading_key_exists(self):

        self._assert_key_in_both("refund.timeline_heading")

    def test_fake_notice_key_exists_and_mentions_no_real_transfer(self):
        """
        Refund 승인·실행확인이 FakeRefundExecutor만 호출한다는 사실을
        화면에서 숨기지 않는지 고정한다(HOMEZ 전반의 Fake Provider
        정직 공개 원칙, app/domains/refund/router.py 상단 주석 참고).
        """

        self._assert_key_in_both("refund.fake_notice")
        self.assertIn("refund.fake_notice", self.js)


class ChannelSettlementTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시안 08 정산·손익 관리 참고) — 정산 차이/
    정산 상태 라벨을 동적 키로 번역하도록 바꿨다. 위 여러
    TranslationTestCase와 동일한 이유(동적 템플릿 리터럴 키)로
    RECONCILIATION_STATUS_OPTIONS와 SETTLEMENT_STATUS_OPTIONS
    전체 값에 대응하는 키를 직접 고정한다.
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

    def _statuses_from(self, array_name):

        start = self.js.index(f"const {array_name} = [")
        end = self.js.index("];", start)
        statuses = re.findall(r'"([A-Z_]+)"', self.js[start:end])
        self.assertGreater(len(statuses), 0)
        return statuses

    def test_every_reconciliation_status_option_has_a_label_key(self):

        for status in self._statuses_from("RECONCILIATION_STATUS_OPTIONS"):
            key = f"stl.status.{status.lower()}"
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
            self.assertTrue(self.ko[key].strip())
            self.assertTrue(self.en[key].strip())

    def test_every_settlement_status_option_has_a_label_key(self):

        for status in self._statuses_from("SETTLEMENT_STATUS_OPTIONS"):
            key = f"stl.status.{status.lower()}"
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
            self.assertTrue(self.ko[key].strip())
            self.assertTrue(self.en[key].strip())


class PaymentMethodUiTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시안 05 매입처·결제 설정 참고, UI-6 감사 중
    발견 — docs/HOMEZ_V7_UI_IMPLEMENTATION_AUDIT.md 참고) — Payment
    화면 신규 추가. `pay.type.*`는 동적 템플릿 리터럴 키라 위 여러
    TranslationTestCase와 동일한 이유로 별도 고정한다.
    `app.domains.payment.constants.PaymentMethodType.ALL`을 직접
    참조해 백엔드와의 드리프트를 방지한다.
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

    def _assert_key_in_both(self, key):

        self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
        self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
        self.assertTrue(self.ko[key].strip())
        self.assertTrue(self.en[key].strip())

    def test_every_payment_method_type_has_a_label_key(self):

        from app.domains.payment.constants import PaymentMethodType

        self.assertGreater(len(PaymentMethodType.ALL), 0)
        for method_type in PaymentMethodType.ALL:
            self._assert_key_in_both(f"pay.type.{method_type.lower()}")

    def test_js_method_types_array_matches_backend_constant(self):
        """
        console.js의 PAY_METHOD_TYPES가 백엔드
        PaymentMethodType.ALL과 정확히 같은 집합인지 확인한다 —
        둘이 갈라지면 등록 폼의 종류 선택지가 백엔드가 실제로
        받는 값과 어긋나게 된다.
        """

        from app.domains.payment.constants import PaymentMethodType

        start = self.js.index("const PAY_METHOD_TYPES = [")
        end = self.js.index("];", start)
        js_types = set(re.findall(r'"([A-Z_]+)"', self.js[start:end]))

        self.assertEqual(js_types, set(PaymentMethodType.ALL))

    def test_fake_notice_key_exists_and_warns_against_real_card_entry(self):
        """
        raw_details가 저장되지 않는다는 사실을 화면에서 숨기지
        않는지 고정한다 — 실제 카드번호를 입력하도록 유도하지
        않는다는 이 화면의 핵심 설계 판단(app/domains/payment/
        schema.py 주석 참고).
        """

        self._assert_key_in_both("pay.fake_notice")
        self.assertIn("pay.fake_notice", self.js)

    def test_daily_limit_not_enforced_notice_key_exists(self):
        """
        daily_limit_amount가 저장만 되고 실제로 집계·적용되지
        않는다는 사실(app/domains/payment/service.py::
        verify_auto_payment_allowed 주석 참고)을 화면에서 숨기지
        않는지 고정한다.
        """

        self._assert_key_in_both("pay.daily_limit_not_enforced_notice")
        self.assertIn("pay.daily_limit_not_enforced_notice", self.js)

    def test_register_request_never_sends_a_real_card_field(self):
        """
        등록 요청 바디에 raw_details를 항상 빈 객체로 보내는지(실제
        카드 정보 입력 필드 자체가 없다는 설계를 코드 레벨에서도
        고정) 소스 패턴으로 확인한다.
        """

        start = self.js.index('await apiFetch("/payments/methods", {')
        end = self.js.index("});", start)
        body = self.js[start:end]

        self.assertIn("raw_details: {}", body)


class CurrencyUiTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시스템 차원 발견 — docs/HOMEZ_V7_UI_
    IMPLEMENTATION_AUDIT.md "4. 시스템 차원 발견" 절) — Currency
    화면 신규 추가. 백엔드 `KNOWN_CURRENCIES`와의 드리프트 방지,
    두 정직성 고지(외부 API 미연동, 허용률 판정 미연결) 키 고정.
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

    def _assert_key_in_both(self, key):

        self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
        self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
        self.assertTrue(self.ko[key].strip())
        self.assertTrue(self.en[key].strip())

    def test_js_known_currencies_matches_backend_constant(self):

        from app.domains.currency.constants import KNOWN_CURRENCIES

        start = self.js.index("const CURR_KNOWN_CURRENCIES = [")
        end = self.js.index("];", start)
        js_currencies = set(re.findall(r'"([A-Z]+)"', self.js[start:end]))

        self.assertEqual(js_currencies, set(KNOWN_CURRENCIES))

    def test_fake_notice_and_not_wired_notice_keys_exist(self):

        self._assert_key_in_both("curr.fake_notice")
        self._assert_key_in_both("curr.tolerance_not_wired_notice")
        self.assertIn("curr.fake_notice", self.js)
        self.assertIn("curr.tolerance_not_wired_notice", self.js)


class SupplierCapabilityUiTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시스템 차원 발견) — SupplierCapability 화면
    신규 추가. `SupplierCapabilityFlag.ALL`/`CapabilitySupport.ALL`
    과의 드리프트 방지.
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

    def _js_array(self, array_name):

        start = self.js.index(f"const {array_name} = [")
        end = self.js.index("];", start)
        return set(re.findall(r'"([A-Z_]+)"', self.js[start:end]))

    def test_js_flags_array_matches_backend_constant(self):

        from app.domains.supplier_capability.constants import SupplierCapabilityFlag

        js_flags = self._js_array("SPC_FLAGS")
        self.assertEqual(js_flags, set(SupplierCapabilityFlag.ALL))
        for flag in SupplierCapabilityFlag.ALL:
            key = f"spc.flag.{flag.lower()}"
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")

    def test_js_support_options_matches_backend_constant(self):

        from app.domains.supplier_capability.constants import CapabilitySupport

        js_support = self._js_array("SPC_SUPPORT_OPTIONS")
        self.assertEqual(js_support, set(CapabilitySupport.ALL))
        for support in CapabilitySupport.ALL:
            key = f"spc.support.{support.lower()}"
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")


class PriceStockSafetyUiTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시스템 차원 발견) — PriceStockSafety 화면
    신규 추가. 두 가지 미연결 사실(가상재고 자동조회 없음, 검토주기
    스케줄러 미연결 — app/domains/price_stock_safety/service.py
    모듈 docstring 참고)을 화면에서 숨기지 않는지 고정한다.
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

    def test_not_wired_notice_keys_exist_and_are_referenced(self):

        for key in ("pss.threshold_not_wired_notice", "pss.review_cycle_not_wired_notice"):
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
            self.assertTrue(self.ko[key].strip())
            self.assertTrue(self.en[key].strip())
            self.assertIn(key, self.js)


class AiLearningUiTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시스템 차원 발견) — AiLearning 화면 신규
    추가. `ModelCandidateStatus.ALL`과의 드리프트 방지 및 "승인=
    실제 적용 아님" 정직 고지 키 고정(app/domains/ai_learning/
    constants.py docstring 참고).
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

    def test_js_status_options_matches_backend_constant(self):

        from app.domains.ai_learning.constants import ModelCandidateStatus

        start = self.js.index("const AIL_STATUS_OPTIONS = [")
        end = self.js.index("];", start)
        js_statuses = set(re.findall(r'"([A-Z_]+)"', self.js[start:end]))

        expected = {
            ModelCandidateStatus.DRAFT, ModelCandidateStatus.OFFLINE_EVALUATED,
            ModelCandidateStatus.REGRESSION_COMPARED, ModelCandidateStatus.APPROVED,
            ModelCandidateStatus.REJECTED,
        }
        self.assertEqual(js_statuses, expected)

        for status in expected:
            key = f"ail.status.{status.lower()}"
            self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
            self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")

    def test_approved_label_discloses_no_live_application(self):
        """
        백엔드가 명시한 "APPROVED조차 실제 라이브 적용을 의미하지
        않는다"는 사실이 라벨에서 사라지지 않는지 고정한다.
        """

        self.assertIn("별도", self.ko["ail.status.approved"])
        self.assertIn("separate", self.en["ail.status.approved"].lower())

    def test_fake_notice_key_exists_and_referenced(self):

        self.assertIn("ail.fake_notice", self.ko)
        self.assertIn("ail.fake_notice", self.en)
        self.assertIn("ail.fake_notice", self.js)


class MarginAnalysisComparisonTableTranslationTestCase(unittest.TestCase):
    """
    2026-09-10 UI 개선(시안 08 정산·손익 관리 참고) — margin-analysis
    화면에 "예상 대비 확정" 비교표를 추가했다. `prc.item.*`/
    `prc.margin_type.*`는 동적 템플릿 리터럴 키라 위 여러
    TranslationTestCase와 동일한 이유로 별도 고정한다. 백엔드
    `MarginSnapshotResponse`/`MarginType`과의 드리프트도 방지한다.
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

    def _assert_key_in_both(self, key):

        self.assertIn(key, self.ko, f"ko-KR.js에 '{key}'가 없습니다.")
        self.assertIn(key, self.en, f"en-US.js에 '{key}'가 없습니다.")
        self.assertTrue(self.ko[key].strip())
        self.assertTrue(self.en[key].strip())

    def test_every_comparison_item_has_a_label_key(self):

        start = self.js.index("const PRC_COMPARISON_ITEMS = [")
        end = self.js.index("];", start)
        items = re.findall(r'"([a-z_]+)"', self.js[start:end])
        self.assertGreater(len(items), 0)

        for item in items:
            self._assert_key_in_both(f"prc.item.{item}")
        # margin_rate는 PRC_COMPARISON_ITEMS에 없지만(별도 % 처리)
        # prcComparisonTableHtml()이 직접 참조한다.
        self._assert_key_in_both("prc.item.margin_rate")

    def test_margin_type_keys_match_backend_constant(self):

        from app.domains.pricing.constants import MarginType

        for value in (MarginType.EXPECTED, MarginType.ACTUAL):
            self._assert_key_in_both(f"prc.margin_type.{value.lower()}")

    def test_comparison_items_cover_every_margin_snapshot_money_field(self):
        """
        `MarginSnapshotResponse`의 모든 금액 필드(margin_rate·
        estimated_components_json 등 비금액 필드 제외)가
        PRC_COMPARISON_ITEMS에 빠짐없이 포함되는지 확인한다 —
        새 필드가 스키마에 추가됐는데 화면에 반영되지 않는
        드리프트를 방지한다.
        """

        from app.domains.pricing.schema import MarginSnapshotResponse

        non_money_fields = {
            "id", "company_id", "listing_id", "margin_type", "order_id",
            "settlement_id", "reason", "quantity_basis", "margin_rate",
            "estimated_components_json", "created_at",
        }
        money_fields = {
            name for name in MarginSnapshotResponse.model_fields
            if name not in non_money_fields
        }

        start = self.js.index("const PRC_COMPARISON_ITEMS = [")
        end = self.js.index("];", start)
        js_items = set(re.findall(r'"([a-z_]+)"', self.js[start:end]))

        self.assertEqual(js_items, money_fields)


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
