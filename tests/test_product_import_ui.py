"""
=========================================================
Homez OS

File : tests/test_product_import_ui.py

URL 상품 가져오기(2026-10-14) 화면 배선을 정적으로 고정한다(외부 호출·DB 없음).

 - 상품 등록 화면에 "URL로 가져오기" 버튼과 패널이 있다
 - 화면은 /product-import 3개 엔드포인트만 쓰고 쿠팡 전송 경로를 부르지 않는다
 - 초안 만들기는 앱 안 확인창을 거치고, 최소 마진 미달이면 서버 호출 전에 멈춘다
 - 모든 문구가 한국어·영어 번역을 갖는다
=========================================================
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
HTML = (APP / "web" / "console.html").read_text(encoding="utf-8")
JS = (APP / "web" / "console.js").read_text(encoding="utf-8")
KO = (APP / "web" / "i18n" / "ko-KR.js").read_text(encoding="utf-8")
EN = (APP / "web" / "i18n" / "en-US.js").read_text(encoding="utf-8")


def _block(start_marker: str, end_marker: str) -> str:
    start = JS.index(start_marker)
    return JS[start:JS.index(end_marker, start)]


URLIMP_JS = _block("// ---- URL로 상품 가져오기 (2026-10-14) ----", "  async function lwCreateNew() {")


class UrlImportScreenTests(unittest.TestCase):

    def test_button_and_panel_exist_in_the_wizard_screen(self):
        self.assertIn('id="lw-url-import-btn"', HTML)
        for element_id in ("lw-url-import-panel", "lw-ui-url", "lw-ui-search-btn", "lw-ui-close-btn", "lw-ui-result"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', HTML)
        section = HTML[HTML.index('id="view-listing-wizard"'):HTML.index('id="view-listing-status-sync"')]
        self.assertIn("lw-url-import-panel", section)

    def test_events_are_wired_and_the_panel_is_hidden_on_entry(self):
        self.assertIn('el("lw-url-import-btn").addEventListener("click", lwUrlImportOpen);', JS)
        self.assertIn('el("lw-ui-search-btn").addEventListener("click", lwUrlImportSearch);', JS)
        entry = JS[JS.index("async function loadListingWizardView()"):][:300]
        self.assertIn('el("lw-url-import-panel").hidden = true;', entry)

    def test_screen_only_calls_the_three_import_endpoints(self):
        urls = set(re.findall(r'apiFetch\("(/[^"]+)"', URLIMP_JS))
        self.assertEqual(urls, {"/product-import/search", "/product-import/margin-proposal", "/product-import/create-draft"})
        for forbidden in ("coupang", "submit", "approve", "send-live", "/listing-wizards"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, URLIMP_JS.replace("lwOpenWizard", "").replace("listing-wizard", ""))

    def test_creating_the_draft_goes_through_the_in_app_confirmation_first(self):
        create = _block("async function lwUrlImportCreate()", "function lwUrlImportRenderCreated")
        self.assertNotIn("window.confirm(", create)
        self.assertLess(create.index("await askConfirm("), create.index('apiFetch("/product-import/create-draft"'))
        self.assertIn("if (!confirmed) return;", create)

    def test_low_margin_is_stopped_before_the_create_call(self):
        create = _block("async function lwUrlImportCreate()", "function lwUrlImportRenderCreated")
        self.assertLess(create.index("meets_min_margin === false"), create.index('apiFetch("/product-import/create-draft"'))

    def test_user_must_review_values_the_screen_never_auto_confirms_brand_or_rights(self):
        # 이 화면은 브랜드 확인·사용권 확인·정보고시 확인·승인을 호출하지 않는다.
        for token in ("confirm-rights", "brand", "notice_confirmed", "rights_status"):
            with self.subTest(token=token):
                self.assertNotIn(token, URLIMP_JS.lower().replace("brandstate", ""))

    def test_every_urlimp_key_used_by_the_screen_exists_in_both_locales(self):
        used = set(re.findall(r'lwUiT\("([a-z_]+)"', URLIMP_JS)) | set(re.findall(r'lwUiT\(\s*"([a-z_]+)"', URLIMP_JS))
        used |= set(re.findall(r'data-i18n="lw\.urlimp\.([a-z_]+)"', HTML))
        self.assertGreater(len(used), 30)
        for key in sorted(used):
            with self.subTest(key=key):
                self.assertIn(f'"lw.urlimp.{key}"', KO)
                self.assertIn(f'"lw.urlimp.{key}"', EN)

    def test_untrusted_page_text_is_always_escaped_before_it_reaches_innerhtml(self):
        # 읽어 온 문자열(상품명·설명·경고·이미지 주소 등)은 전부 escapeHtml을 거쳐야 한다.
        self.assertIn('src="${escapeHtml(img.url)}"', URLIMP_JS)
        for needle in ("data.suggested_product_name", "data.suggested_description", "w)}</li>", "r.label", "i.error_message"):
            with self.subTest(needle=needle):
                for match in re.finditer(re.escape(needle), URLIMP_JS):
                    window = URLIMP_JS[max(0, match.start() - 40):match.end() + 5]
                    self.assertIn("escapeHtml(", window, window)


if __name__ == "__main__":
    unittest.main()
